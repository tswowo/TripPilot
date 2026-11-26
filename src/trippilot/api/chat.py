import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langchain_core.messages import HumanMessage
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from trippilot.config import get_settings
from trippilot.utils.id_util import uuid7
from trippilot.utils.login_util import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = Field(default=None)


def get_graph(request: Request) -> Any:
    return request.app.state.graph


async def create_conversation(conversation_id: str, user_id: int, title: str) -> None:
    """新建会话并绑定当前用户"""
    settings = get_settings()
    async with (
        await AsyncConnection.connect(settings.postgres_uri) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)",
            (conversation_id, user_id, title[:200]),
        )
        await conn.commit()


async def check_conversations(conversation_id: str, user_id: int) -> bool:
    """
    校验：会话存在 并且 归属是当前用户
    返回 True = 合法；False = 不存在或者不属于该用户
    """
    settings = get_settings()
    async with (
        await AsyncConnection.connect(settings.postgres_uri) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT id, user_id FROM conversations WHERE id = %s",
            (conversation_id,),
        )
        row = await cur.fetchone()
    if not row:
        return False
    return row["user_id"] == user_id


async def touch_conversation(conversation_id: str) -> None:
    """续聊时更新会话的 update_time，配合 idx_conversations_user_updated 索引排序"""
    settings = get_settings()
    async with (
        await AsyncConnection.connect(settings.postgres_uri) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "UPDATE conversations SET update_time = now() WHERE id = %s",
            (conversation_id,),
        )
        await conn.commit()


@router.post("/chat")
async def chat(
    request: ChatRequest,
    payload: Annotated[dict, Depends(get_current_user)],
    graph: Annotated[Any, Depends(get_graph)],
):
    user_id: int = payload["uid"]

    # 1. 新建会话 vs 已有会话归属校验
    if request.conversation_id is None:
        conversation_id = str(uuid7())
        await create_conversation(conversation_id, user_id, request.message)
    else:
        conversation_id = str(request.conversation_id)
        if not await check_conversations(conversation_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权访问",
            )
        await touch_conversation(conversation_id)

    # 2. 调用 LangGraph（当前非流式，直接返回最终回复；后续 SSE 见 /api/plan/stream）
    config = {"configurable": {"thread_id": conversation_id}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=request.message)]},
        config,
    )
    reply = result["messages"][-1].content
    return {"conversation_id": conversation_id, "content": str(reply)}

