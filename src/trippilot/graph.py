from langchain_core.messages import SystemMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import START, MessagesState, StateGraph

from trippilot.config import get_settings

SYSTEM_PROMPT = (
    "你是旅行规划助手。根据用户需求给出清晰的旅行建议。"
    "你没有实时查询能力，不要编造具体车次、票价、酒店价格或余票；"
    "不确定的信息要明确说明。"
)


def build_graph(checkpointer: AsyncPostgresSaver):
    settings = get_settings()
    model = ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        temperature=0.3,
    )

    async def call_model(state: MessagesState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        response = await model.ainvoke(messages)
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")
    return graph.compile(checkpointer=checkpointer)