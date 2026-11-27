from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import SecretStr

from trippilot.config import get_settings
from trippilot.graph.llm import PatchedChatDeepSeek
from trippilot.tools.deepseek_web import build_deepseek_web_tools

SYSTEM_PROMPT = (
    "你是 TripPilot 智能旅行规划助手，负责为用户提供准确、可落地的出行建议。\n"
    "- 涉及景区开放/放票规则、最新公告、时令活动或不确定的事实信息时，"
    "优先调用 `web_search` 联网检索；若摘要信息不足，再对具体链接调用 `web_fetch` 精读正文。\n"
    "- 严禁凭空捏造具体车次、航班号、实时票价、余票或酒店房价；未通过工具核实的价格与余票必须明确说明。"
)


def patch_dangling_tool_calls(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """为带有 tool_calls 的悬空 AIMessage 补齐取消态 ToolMessage，实现会话无损自愈。"""
    existing_tool_ids = {
        m.tool_call_id for m in messages if isinstance(m, ToolMessage) and m.tool_call_id
    }
    repaired: list[BaseMessage] = []
    for msg in messages:
        repaired.append(msg)
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_ids:
                    repaired.append(
                        ToolMessage(
                            content="该工具调用因用户中途停止生成而被取消。",
                            tool_call_id=tc_id,
                            name=tc.get("name") or "unknown",
                            status="error",
                        )
                    )
                    existing_tool_ids.add(tc_id)
    return repaired


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    tools: Sequence[BaseTool] | None = None,
):
    settings = get_settings()
    active_tools = list(build_deepseek_web_tools(settings) if tools is None else tools)
    secret_key = SecretStr(settings.deepseek_api_key)

    base_fast_model = PatchedChatDeepSeek(
        model=settings.deepseek_model,
        api_key=secret_key,
        api_base=settings.deepseek_base_url,
        temperature=0.3,
    )
    fast_model = base_fast_model.bind_tools(active_tools) if active_tools else base_fast_model

    base_thinking_model = PatchedChatDeepSeek(
        model=settings.deepseek_model,
        api_key=secret_key,
        api_base=settings.deepseek_base_url,
        extra_body={"thinking": {"type": "enabled"}},
    )
    thinking_model = (
        base_thinking_model.bind_tools(active_tools) if active_tools else base_thinking_model
    )

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
        graph.add_node("tools", ToolNode(active_tools, handle_tool_errors=True))
        graph.add_conditional_edges("model", tools_condition)
        graph.add_edge("tools", "model")
    return graph.compile(checkpointer=checkpointer)
