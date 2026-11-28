"""LangGraph 状态图拓扑组装器（纵向三段式：IntentRouter -> Core Planner -> Evaluator）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from trippilot.config import get_settings
from trippilot.graph.evaluator import (
    aevaluate_itinerary,
    build_self_healing_prompt,
)
from trippilot.graph.intent import (
    aclassify_intent,
    build_default_clarify_question,
)
from trippilot.graph.llm import (
    build_chat_only_model,
    build_model_pair,
    build_summarizer_model,
)
from trippilot.graph.prompts import SYSTEM_PROMPT
from trippilot.middlewares import (
    MAX_TOOL_OUTPUT_CHARS,
    acompact_messages_if_needed,
    patch_dangling_tool_calls,
)
from trippilot.middlewares import (
    normalize_tool_content as _normalize_tool_content,
)
from trippilot.middlewares import (
    unwrap_mcp_tool_call as _unwrap_mcp_tool_call,
)
from trippilot.tools.bundles import bundle_runtime_tools
from trippilot.tools.deepseek_web import build_deepseek_web_tools


class TripPilotState(MessagesState):
    """扩展自 MessagesState 的纵向三段式编排状态。"""

    intent_info: dict[str, Any]
    eval_retry_count: int
    eval_feedback: str
    eval_issues: list[str]


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    tools: Sequence[BaseTool] | None = None,
):
    settings = get_settings()
    raw_tools = list(build_deepseek_web_tools(settings) if tools is None else tools)
    active_tools = bundle_runtime_tools(raw_tools)

    fast_model, thinking_model = build_model_pair(settings, active_tools)
    chat_only_model = build_chat_only_model(settings)
    summarizer_model = build_summarizer_model(settings)

    async def intent_router(state: TripPilotState):
        """前置两级意图与最小必填槽位门禁节点（自动继承合并跨轮次历史槽位）。"""
        healed = patch_dangling_tool_calls(state["messages"])
        classification = await aclassify_intent(
            healed,
            summarizer_model,
            prior_info=state.get("intent_info"),
        )
        return {
            "intent_info": classification.model_dump(),
            "eval_retry_count": 0,
            "eval_feedback": "",
            "eval_issues": [],
        }

    def route_after_intent(state: TripPilotState) -> str:
        info = state.get("intent_info") or {}
        if info.get("intent") == "clarify":
            return "clarify"
        return "model"

    async def clarify_node(state: TripPilotState):
        """缺核心硬槽位（目的地/出发地/日期）时的 0 工具秒级追问节点。"""
        info = state.get("intent_info") or {}
        question = str(info.get("clarify_question") or "").strip()
        if not question:
            from trippilot.graph.intent import TravelSlots

            slots = TravelSlots.model_validate(info.get("slots") or {})
            missing = info.get("missing_hard_slots") or ["destination", "origin", "date_or_days"]
            question = build_default_clarify_question(missing, slots)
        return {"messages": [AIMessage(content=question)]}

    async def call_model(state: TripPilotState, config: RunnableConfig):
        """单主规划 ReAct 核心节点（固定挂载打包后的高层复合工具，100% 保护 Prefix KV Cache）。"""
        # 1. 悬空 tool_calls 自愈
        healed_history = patch_dangling_tool_calls(state["messages"])
        # 2. 锯齿形上下文压缩（仅在 HumanMessage 新轮次入口且超阈值时批量压缩，ReAct 多跳途中严格锁死前缀）
        compaction = await acompact_messages_if_needed(
            healed_history,
            system_prompt=SYSTEM_PROMPT,
            summarizer_llm=summarizer_model,
        )

        intent_info = state.get("intent_info") or {}
        intent_type = intent_info.get("intent", "full_planning")
        model_messages = list(compaction.model_messages)

        # 3. 若由后置 Evaluator 触发了自愈重排：
        #    - 强制使用无工具绑定的 chat_only_model 输出修正说明段落，杜绝自愈中途再次发起 tool_calls；
        #    - 复用上一条 AIMessage 的 id 将修正段落原地合并进该条 AIMessage，彻底避免在 Checkpoint 中留下连续两条 AIMessage！
        eval_feedback = str(state.get("eval_feedback") or "").strip()
        if eval_feedback:
            healing_prompt = build_self_healing_prompt(
                eval_feedback,
                state.get("eval_issues") or [],
            )
            healing_messages = [*model_messages, HumanMessage(content=healing_prompt)]
            patch_resp = await chat_only_model.ainvoke(healing_messages)
            patch_text = str(getattr(patch_resp, "content", "") or "").strip()

            prev_ai = next(
                (m for m in reversed(healed_history) if isinstance(m, AIMessage) and not m.tool_calls),
                None,
            )
            if prev_ai is not None and getattr(prev_ai, "id", None) and patch_text:
                merged_content = f"{str(prev_ai.content or '').rstrip()}\n\n{patch_text}"
                merged_msg = prev_ai.model_copy(update={"content": merged_content})
                return {
                    "messages": [*compaction.state_updates, merged_msg],
                    "eval_feedback": "",
                }
            return {
                "messages": [*compaction.state_updates, patch_resp],
                "eval_feedback": "",
            }

        if intent_type == "chitchat" or not active_tools:
            chosen_model = chat_only_model
        else:
            thinking = bool(config.get("configurable", {}).get("thinking", False))
            chosen_model = thinking_model if thinking else fast_model

        response = await chosen_model.ainvoke(model_messages)
        return {
            "messages": [*compaction.state_updates, response],
            "eval_feedback": "",
        }

    def route_after_model(state: TripPilotState) -> str:
        messages = state.get("messages") or []
        if messages:
            last_msg = messages[-1]
            if (
                isinstance(last_msg, AIMessage)
                and getattr(last_msg, "tool_calls", None)
                and active_tools
            ):
                return "tools"

        intent_info = state.get("intent_info") or {}
        retry_count = int(state.get("eval_retry_count") or 0)
        if intent_info.get("intent") == "full_planning" and retry_count < 1:
            return "evaluator"
        return END

    async def evaluator_node(state: TripPilotState):
        """后置独立行程质检打分节点（核验闭馆日冲突、预算透支、时刻表冲突）。"""
        eval_res = await aevaluate_itinerary(
            state["messages"],
            intent_info=state.get("intent_info"),
            evaluator_llm=summarizer_model,
        )
        if eval_res.passed:
            return {
                "eval_feedback": "",
                "eval_issues": [],
            }
        return {
            "eval_retry_count": int(state.get("eval_retry_count") or 0) + 1,
            "eval_feedback": eval_res.fix_instruction,
            "eval_issues": eval_res.issues,
            "validation_errors": eval_res.issues,
        }

    def route_after_evaluator(state: TripPilotState) -> str:
        if str(state.get("eval_feedback") or "").strip():
            return "model"
        return END

    graph = StateGraph(TripPilotState)
    graph.add_node("intent_router", intent_router)
    graph.add_node("clarify", clarify_node)
    graph.add_node("model", call_model)
    graph.add_node("evaluator", evaluator_node)

    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router",
        route_after_intent,
        {"clarify": "clarify", "model": "model"},
    )
    graph.add_edge("clarify", END)

    if active_tools:
        graph.add_node(
            "tools",
            ToolNode(
                active_tools,
                handle_tool_errors=True,
                awrap_tool_call=_unwrap_mcp_tool_call,
            ),
        )
        graph.add_conditional_edges(
            "model",
            route_after_model,
            {"tools": "tools", "evaluator": "evaluator", END: END},
        )
        graph.add_edge("tools", "model")
    else:
        graph.add_conditional_edges(
            "model",
            route_after_model,
            {"evaluator": "evaluator", END: END},
        )

    graph.add_conditional_edges(
        "evaluator",
        route_after_evaluator,
        {"model": "model", END: END},
    )

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "MAX_TOOL_OUTPUT_CHARS",
    "SYSTEM_PROMPT",
    "TripPilotState",
    "_normalize_tool_content",
    "_unwrap_mcp_tool_call",
    "build_graph",
    "patch_dangling_tool_calls",
]
