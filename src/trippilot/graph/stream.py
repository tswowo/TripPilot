"""核心 Agent 流式执行与 Superstep 轨迹提取（与 Web / 数据库解耦）。"""

import time
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage


def build_step_record(step_seq: int, node_name: str, node_output: Any) -> dict[str, Any]:
    """将单个 Superstep 节点的增量输出提炼为轻量 JSON 摘要。"""
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
    tool_results: list[dict[str, Any]] = []

    for m in msgs:
        if isinstance(m, AIMessage) and m.tool_calls:
            tool_calls.extend(tc["name"] for tc in m.tool_calls)
        elif isinstance(m, ToolMessage):
            tool_results.append(
                {
                    "tool": m.name or "unknown",
                    "status": getattr(m, "status", "success"),
                }
            )

    if tool_calls:
        record["action"] = "call_tools"
        record["tools"] = tool_calls
    elif tool_results:
        record["action"] = "tool_results"
        record["results"] = tool_results
    else:
        record["action"] = "node_completed"

    if "validation_errors" in node_output:
        record["validation_errors"] = node_output["validation_errors"]

    return record


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
    async for mode, data in graph.astream(
        {"messages": [HumanMessage(content=user_message)]},
        config,
        stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            chunk, _metadata = data
            if isinstance(chunk, AIMessageChunk):
                delta: dict[str, Any] = {}
                if reasoning_content := chunk.additional_kwargs.get("reasoning_content"):
                    delta["reasoning_content"] = str(reasoning_content)
                if chunk.content:
                    delta["content"] = str(chunk.content)
                if delta:
                    yield "delta", delta
        elif mode == "updates" and isinstance(data, dict):
            for node_name, node_output in data.items():
                if node_name.startswith("__"):
                    continue
                step_seq += 1
                yield "step", build_step_record(step_seq, node_name, node_output)
