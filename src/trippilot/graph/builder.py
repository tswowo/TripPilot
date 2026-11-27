"""LangGraph 状态图拓扑组装器（仅负责连接节点、边与挂载护栏中间件）。"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from trippilot.config import get_settings
from trippilot.graph.llm import build_model_pair
from trippilot.graph.prompts import SYSTEM_PROMPT
from trippilot.middlewares import (
    MAX_TOOL_OUTPUT_CHARS,
    patch_dangling_tool_calls,
)
from trippilot.middlewares import (
    normalize_tool_content as _normalize_tool_content,
)
from trippilot.middlewares import (
    unwrap_mcp_tool_call as _unwrap_mcp_tool_call,
)
from trippilot.tools.deepseek_web import build_deepseek_web_tools


def build_graph(
        checkpointer: BaseCheckpointSaver | None = None,
        tools: Sequence[BaseTool] | None = None,
):
    settings = get_settings()
    active_tools = list(build_deepseek_web_tools(settings) if tools is None else tools)
    fast_model, thinking_model = build_model_pair(settings, active_tools)

    async def call_model(state: MessagesState, config: RunnableConfig):
        healed_history = patch_dangling_tool_calls(state["messages"])
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + healed_history
        thinking = bool(config.get("configurable", {}).get("thinking", False))
        chosen_model = thinking_model if thinking else fast_model
        response = await chosen_model.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")

    if active_tools:
        graph.add_node(
            "tools",
            ToolNode(
                active_tools,
                handle_tool_errors=True,
                awrap_tool_call=_unwrap_mcp_tool_call,
            ),
        )
        graph.add_conditional_edges("model", tools_condition)
        graph.add_edge("tools", "model")

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "MAX_TOOL_OUTPUT_CHARS",
    "SYSTEM_PROMPT",
    "_normalize_tool_content",
    "_unwrap_mcp_tool_call",
    "build_graph",
    "patch_dangling_tool_calls",
]
