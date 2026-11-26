from collections.abc import Sequence

from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from trippilot.config import get_settings
from trippilot.tools.deepseek_web import build_deepseek_web_tools

SYSTEM_PROMPT = (
    "你是 TripPilot 智能旅行规划助手，负责为用户提供准确、可落地的出行建议。\n"
    "- 涉及景区开放/放票规则、最新公告、时令活动或不确定的事实信息时，"
    "优先调用 `web_search` 联网检索；若摘要信息不足，再对具体链接调用 `web_fetch` 精读正文。\n"
    "- 严禁凭空捏造具体车次、航班号、实时票价、余票或酒店房价；未通过工具核实的价格与余票必须明确说明。"
)


def build_graph(
        checkpointer: BaseCheckpointSaver | None = None,
        tools: Sequence[BaseTool] | None = None,
):
    settings = get_settings()
    active_tools = list(build_deepseek_web_tools(settings) if tools is None else tools)

    model = ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        api_base=settings.deepseek_base_url,
        temperature=0.3,
    )
    bound_model = model.bind_tools(active_tools) if active_tools else model

    async def call_model(state: MessagesState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        response = await bound_model.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")
    if active_tools:
        graph.add_node("tools", ToolNode(active_tools, handle_tool_errors=True))
        graph.add_conditional_edges("model", tools_condition)
        graph.add_edge("tools", "model")

    return graph.compile(checkpointer=checkpointer)
