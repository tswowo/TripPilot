import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from trippilot.config import get_settings
from trippilot.utils.id_util import uuid7
from trippilot.utils.login_util import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 禁用 Nginx 反代缓冲，防止 SSE 被攒批才吐出
}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = Field(default=None)


def get_graph(request: Request) -> Any:
    return request.app.state.graph


def format_sse(event: str, data: dict[str, Any]) -> str:
    """格式化标准 SSE 帧：event + JSON data + 双换行"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
        raw_request: Request,
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

    config = {"configurable": {"thread_id": conversation_id}}

    # 2. 构造 SSE 异步生成器，支持逐 Token 推送与客户端断连优雅取消
    async def event_generator() -> AsyncIterator[str]:
        yield format_sse("meta", {"conversation_id": conversation_id})
        try:
            async for chunk, _metadata in graph.astream(
                    {"messages": [HumanMessage(content=request.message)]},
                    config,
                    stream_mode="messages",
            ):
                if await raw_request.is_disconnected():
                    break
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    yield format_sse("delta", {"content": str(chunk.content)})
            yield format_sse("done", {"conversation_id": conversation_id})
        except asyncio.CancelledError:
            # 客户端主动断连（如点击“停止”按钮），向上抛出以及时终止下游 LLM 生成，防止白烧 Token
            raise
        except Exception as exc:
            yield format_sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
