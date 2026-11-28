"""核心 Agent 流式执行、Superstep 轨迹提取与多轮交错 Timeline 组装（与 Web / 数据库解耦）。"""

import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

_PRIORITY_ARG_KEYS = (
    "query",
    "keywords",
    "address",
    "location",
    "id",
    "url",
)


def summarize_tool_args(args: Any, *, max_len: int = 36) -> str:
    """从工具调用参数字典中提炼简短可读的业务摘要（如 '上海 · 上海天文馆'）。"""
    if not isinstance(args, dict) or not args:
        return ""

    city = str(args.get("city") or args.get("cityd") or "").strip()
    origin = str(args.get("origin") or args.get("origins") or "").strip()
    destination = str(args.get("destination") or "").strip()

    primary = ""
    for key in _PRIORITY_ARG_KEYS:
        val = args.get(key)
        if val is not None and str(val).strip():
            primary = str(val).strip()
            break

    if city and primary and city not in primary:
        text = f"{city} · {primary}"
    elif primary:
        text = primary
    elif origin and destination:
        text = f"{origin} → {destination}"
    elif city:
        text = city
    else:
        first_val = next((str(v).strip() for v in args.values() if v is not None and str(v).strip()), "")
        text = first_val

    if len(text) > max_len:
        return f"{text[:max_len]}…"
    return text


def build_step_record(step_seq: int, node_name: str, node_output: Any) -> dict[str, Any]:
    """将单个 Superstep 节点的增量输出提炼为轻量 JSON 摘要（含 tool_call_id 与参数摘要）。"""
    record: dict[str, Any] = {
        "step": step_seq,
        "node": node_name,
        "ts_ms": int(time.time() * 1000),
    }
    if not isinstance(node_output, dict):
        record["action"] = "node_completed"
        return record

    msgs = node_output.get("messages") or []
    tool_calls: list[str] = []
    calls_detail: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for m in msgs:
        if isinstance(m, AIMessage) and m.tool_calls:
            for idx, tc in enumerate(m.tool_calls):
                name = tc.get("name") or "unknown"
                tool_calls.append(name)
                calls_detail.append(
                    {
                        "id": tc.get("id") or f"call_{step_seq}_{idx}",
                        "name": name,
                        "summary": summarize_tool_args(tc.get("args")),
                    }
                )
        elif isinstance(m, ToolMessage):
            tool_results.append(
                {
                    "id": getattr(m, "tool_call_id", "") or "",
                    "tool": m.name or "unknown",
                    "status": getattr(m, "status", "success"),
                }
            )

    if tool_calls:
        record["action"] = "call_tools"
        record["tools"] = tool_calls
        record["calls"] = calls_detail
    elif tool_results:
        record["action"] = "tool_results"
        record["results"] = tool_results
    else:
        record["action"] = "node_completed"

    if "validation_errors" in node_output:
        record["validation_errors"] = node_output["validation_errors"]

    return record


class TimelineAssembler:
    """按真实执行顺序组装多轮交错块流（reasoning / tool_round / content），用于持久化与历史回放。"""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self.content_chunks: list[str] = []
        self.reasoning_chunks: list[str] = []
        self._thinking_start_ms: int = 0
        self._thinking_elapsed_ms: int = 0
        self._active_reasoning_block: dict[str, Any] | None = None
        self._active_tool_round_block: dict[str, Any] | None = None
        self._tool_round_seq: int = 0
        self._need_reasoning_sep: bool = False

    def pause_thinking(self, now_ms: int | None = None) -> None:
        """结算并暂停当前思考块的秒表。"""
        ts = int(time.time() * 1000) if now_ms is None else now_ms
        if self._thinking_start_ms > 0:
            self._thinking_elapsed_ms += max(0, ts - self._thinking_start_ms)
            self._thinking_start_ms = 0
        if self._active_reasoning_block is not None and self._thinking_elapsed_ms > 0:
            self._active_reasoning_block["duration_s"] = max(
                1, round(self._thinking_elapsed_ms / 1000)
            )

    def on_delta(self, delta: dict[str, Any], *, now_ms: int | None = None) -> None:
        ts = int(time.time() * 1000) if now_ms is None else now_ms

        if reasoning_piece := delta.get("reasoning_content"):
            if self._need_reasoning_sep and self.reasoning_chunks:
                self.reasoning_chunks.append("\n\n")
                self._need_reasoning_sep = False
            self.reasoning_chunks.append(reasoning_piece)

            if self._active_reasoning_block is None:
                block: dict[str, Any] = {
                    "type": "reasoning",
                    "text": "",
                    "duration_s": 1,
                }
                self.blocks.append(block)
                self._active_reasoning_block = block
                self._thinking_start_ms = ts
                self._thinking_elapsed_ms = 0
            elif self._thinking_start_ms == 0:
                self._thinking_start_ms = ts

            self._active_reasoning_block["text"] += reasoning_piece
            elapsed = self._thinking_elapsed_ms + max(0, ts - self._thinking_start_ms)
            self._active_reasoning_block["duration_s"] = max(1, round(elapsed / 1000))

        if delta.get("thinking_done"):
            self.pause_thinking(ts)

        if content_piece := delta.get("content"):
            self.pause_thinking(ts)
            self._active_reasoning_block = None
            self._active_tool_round_block = None
            if self.reasoning_chunks:
                self._need_reasoning_sep = True

            self.content_chunks.append(content_piece)
            if not self.blocks or self.blocks[-1].get("type") != "content":
                self.blocks.append({"type": "content", "text": content_piece})
            else:
                self.blocks[-1]["text"] += content_piece

    def on_step(self, step_data: dict[str, Any], *, now_ms: int | None = None) -> None:
        ts = step_data.get("ts_ms") or (int(time.time() * 1000) if now_ms is None else now_ms)
        self.pause_thinking(ts)
        self._active_reasoning_block = None
        if self.reasoning_chunks:
            self._need_reasoning_sep = True

        action = step_data.get("action")
        if action == "call_tools":
            self._tool_round_seq += 1
            raw_calls = step_data.get("calls") or [
                {"id": f"call_{step_data.get('step', 0)}_{i}", "name": t, "summary": ""}
                for i, t in enumerate(step_data.get("tools") or [])
            ]
            calls = [
                {
                    "id": c.get("id") or f"call_{self._tool_round_seq}_{i}",
                    "name": c.get("name") or "unknown",
                    "summary": c.get("summary") or "",
                    "status": "running",
                }
                for i, c in enumerate(raw_calls)
            ]
            tool_block: dict[str, Any] = {
                "type": "tool_round",
                "round": self._tool_round_seq,
                "step": step_data.get("step", self._tool_round_seq),
                "start_ts_ms": ts,
                "duration_ms": None,
                "calls": calls,
            }
            self.blocks.append(tool_block)
            self._active_tool_round_block = tool_block

        elif action == "tool_results":
            target = self._active_tool_round_block
            if target is None:
                for b in reversed(self.blocks):
                    if b.get("type") == "tool_round":
                        target = b
                        break
            if target is not None:
                start_ts = target.get("start_ts_ms") or ts
                target["duration_ms"] = max(0, ts - start_ts)
                calls_list: list[dict[str, Any]] = target.get("calls") or []
                for res in step_data.get("results") or []:
                    res_id = res.get("id")
                    res_name = res.get("tool")
                    res_status = res.get("status", "success")
                    matched = False
                    if res_id:
                        for c in calls_list:
                            if c.get("id") == res_id:
                                c["status"] = res_status
                                matched = True
                                break
                    if not matched:
                        for c in calls_list:
                            if c.get("name") == res_name and c.get("status") == "running":
                                c["status"] = res_status
                                break

    def finalize(self, *, cancelled: bool = False, now_ms: int | None = None) -> list[dict[str, Any]]:
        """流结束或中断时收口所有思考计时与未完成工具状态。"""
        self.pause_thinking(now_ms)
        self._active_reasoning_block = None
        if cancelled:
            for b in self.blocks:
                if b.get("type") == "tool_round":
                    for c in b.get("calls") or []:
                        if c.get("status") == "running":
                            c["status"] = "cancelled"
        return self.blocks


async def stream_graph_events(
    graph: Any,
    *,
    thread_id: str,
    user_message: str,
    thinking: bool = False,
) -> AsyncIterator[tuple[str, Any]]:
    """驱动 LangGraph 双模式流式运行，向上游吐出 ('delta', dict) 与 ('step', dict) 事件。"""
    config = {
        "configurable": {
            "thread_id": thread_id,
            "thinking": thinking,
        }
    }
    step_seq = 0
    in_reasoning = False
    async for mode, data in graph.astream(
        {"messages": [HumanMessage(content=user_message)]},
        config,
        stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            chunk, _metadata = data
            meta = _metadata if isinstance(_metadata, dict) else {}
            tags = meta.get("tags") or []
            node_in_meta = meta.get("langgraph_node") or ""
            # 严格过滤内部辅助 LLM（意图分类器、上下文摘要器、后置质检打分器）的 JSON Token，防止串台到用户聊天框
            if "internal_llm" in tags or node_in_meta in ("intent_router", "evaluator"):
                continue
            if isinstance(chunk, AIMessageChunk):
                delta: dict[str, Any] = {}
                if reasoning_content := chunk.additional_kwargs.get("reasoning_content"):
                    delta["reasoning_content"] = str(reasoning_content)
                    in_reasoning = True
                if chunk.content:
                    delta["content"] = str(chunk.content)
                    in_reasoning = False
                if in_reasoning and not delta and getattr(chunk, "tool_call_chunks", None):
                    # 思考 Token 已结束，开始流式拼装工具调用 JSON，立即通知上下游停止思考秒表
                    delta["thinking_done"] = True
                    in_reasoning = False
                if delta:
                    yield "delta", delta
        elif mode == "updates" and isinstance(data, dict):
            for node_name, node_output in data.items():
                if node_name.startswith("__"):
                    continue
                in_reasoning = False
                # clarify 节点为 0ms Python 直出追问（不经 LLM 流式回调），在此将其追问文本作为标准 delta 推给前端
                if node_name == "clarify" and isinstance(node_output, dict):
                    for m in node_output.get("messages") or []:
                        if isinstance(m, AIMessage) and m.content:
                            yield "delta", {"content": str(m.content)}
                record = build_step_record(step_seq + 1, node_name, node_output)
                # 过滤纯内部完成事件（仅保留工具调用、工具结果或携带 validation_errors 的质检告警）
                if record.get("action") == "node_completed" and "validation_errors" not in record:
                    continue
                step_seq += 1
                yield "step", record
