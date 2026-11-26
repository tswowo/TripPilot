from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from trippilot.utils.id_util import uuid7
from trippilot.utils.login_util import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = Field(default=None, min_length=1)


def get_graph(request: Request) -> Any:
    return request.app.state.graph


@router.post("/chat")
async def chat(
    request: ChatRequest,
    payload: Annotated[dict, Depends(get_current_user)],
    graph: Annotated[Any, Depends(get_graph)],
):
    conversation_id = request.conversation_id or str(uuid7())
    # TODO: 建 conversations 表后校验会话归属（user_id 必须等于 payload["uid"]）
    config = {"configurable": {"thread_id": conversation_id}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=request.message)]},
        config,
    )
    reply = result["messages"][-1].content
    return {"conversation_id": conversation_id, "content": str(reply)}