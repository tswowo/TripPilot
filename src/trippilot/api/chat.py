import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from trippilot.db import session_scope
from trippilot.models import Conversation, Message
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


def build_step_record(step_seq: int, node_name: str, node_output: Any) -> dict[str, Any]:
    """将单个 Superstep 节点的增量输出提炼为轻量 JSON 摘要（挂载至 messages.steps JSONB）。"""
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


async def init_turn_transaction(
    *,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    user_id: int,
    user_message: str,
    is_new_conversation: bool,
) -> bool:
    """流开始前的短事务（执行完立即 commit 并归还连接池，绝不在 LLM 流式期间占用连接）：
    1. 新会话创建 Conversation，或旧会话校验归属并刷新 update_time；
    2. 写入同 turn_id 的 user 消息（completed）与空 assistant 占位消息（streaming）。
    """
    async with session_scope() as db:
        if is_new_conversation:
            db.add(
                Conversation(
                    id=conversation_id,
                    user_id=user_id,
                    title=user_message[:200],
                )
            )
        else:
            conv = await db.get(Conversation, conversation_id)
            if conv is None or conv.user_id != user_id:
                return False
            conv.update_time = func.now()

        db.add_all(
            [
                Message(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    role="user",
                    content=user_message,
                    steps=[],
                    status="completed",
                ),
                Message(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    role="assistant",
                    content="",
                    steps=[],
                    status="streaming",
                ),
            ]
        )
    return True


async def finalize_assistant_message(
    *,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    content: str,
    steps: list[dict[str, Any]],
    msg_status: str = "completed",
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """流结束（或中断/异常）后的短事务：一次性全量回写 assistant 的 content、steps 与状态。"""
    async with session_scope() as db:
        await db.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.turn_id == turn_id,
                Message.role == "assistant",
            )
            .values(
                content=content,
                steps=steps,
                status=msg_status,
                error_code=error_code,
                error_message=error_message,
                update_time=func.now(),
            )
        )
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(update_time=func.now())
        )


@router.get("/conversations")
async def list_conversations(payload: Annotated[dict, Depends(get_current_user)]):
    """查询当前用户的历史会话列表（按最近更新时间倒序）。"""
    user_id: int = payload["uid"]
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.update_time.desc())
            )
        ).all()
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "update_time": c.update_time.isoformat(),
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    payload: Annotated[dict, Depends(get_current_user)],
):
    """查询指定会话的历史消息及 Superstep 轨迹（含归属权校验）。"""
    user_id: int = payload["uid"]
    async with session_scope() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权访问",
            )
        rows = (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.asc())
            )
        ).all()
    return [
        {
            "turn_id": str(m.turn_id),
            "role": m.role,
            "content": m.content,
            "steps": m.steps,
            "status": m.status,
            "error_code": m.error_code,
        }
        for m in rows
    ]


@router.post("/chat")
async def chat(
    request: ChatRequest,
    raw_request: Request,
    payload: Annotated[dict, Depends(get_current_user)],
    graph: Annotated[Any, Depends(get_graph)],
):
    user_id: int = payload["uid"]
    is_new = request.conversation_id is None
    conv_uuid = uuid7() if is_new else request.conversation_id
    turn_uuid = uuid7()
    conv_id_str = str(conv_uuid)
    turn_id_str = str(turn_uuid)

    # 1. 流前短事务：校验/创建会话 + 落库同 turn_id 的 user 消息与空 assistant 消息
    ok = await init_turn_transaction(
        conversation_id=conv_uuid,
        turn_id=turn_uuid,
        user_id=user_id,
        user_message=request.message,
        is_new_conversation=is_new,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )

    config = {"configurable": {"thread_id": conv_id_str}}

    # 2. SSE 双模式流式生成器：messages 推逐字 delta，updates 推 Superstep 轨迹，结束时短事务全量落盘
    async def event_generator() -> AsyncIterator[str]:
        yield format_sse("meta", {"conversation_id": conv_id_str, "turn_id": turn_id_str})
        chunks: list[str] = []
        steps: list[dict[str, Any]] = []

        try:
            async for mode, data in graph.astream(
                {"messages": [HumanMessage(content=request.message)]},
                config,
                stream_mode=["messages", "updates"],
            ):
                if await raw_request.is_disconnected():
                    await asyncio.shield(
                        finalize_assistant_message(
                            conversation_id=conv_uuid,
                            turn_id=turn_uuid,
                            content="".join(chunks),
                            steps=steps,
                            msg_status="failed",
                            error_code="CLIENT_DISCONNECTED",
                            error_message="客户端已断开连接",
                        )
                    )
                    return

                if mode == "messages":
                    chunk, _metadata = data
                    if isinstance(chunk, AIMessageChunk) and chunk.content:
                        piece = str(chunk.content)
                        chunks.append(piece)
                        yield format_sse("delta", {"content": piece})

                elif mode == "updates" and isinstance(data, dict):
                    for node_name, node_output in data.items():
                        if node_name.startswith("__"):
                            continue
                        step_item = build_step_record(len(steps) + 1, node_name, node_output)
                        steps.append(step_item)
                        yield format_sse("step", step_item)

            await asyncio.shield(
                finalize_assistant_message(
                    conversation_id=conv_uuid,
                    turn_id=turn_uuid,
                    content="".join(chunks),
                    steps=steps,
                    msg_status="completed",
                )
            )
            yield format_sse("done", {"conversation_id": conv_id_str, "turn_id": turn_id_str})

        except asyncio.CancelledError:
            await asyncio.shield(
                finalize_assistant_message(
                    conversation_id=conv_uuid,
                    turn_id=turn_uuid,
                    content="".join(chunks),
                    steps=steps,
                    msg_status="failed",
                    error_code="CANCELLED",
                    error_message="用户中止生成",
                )
            )
            raise
        except Exception as exc:
            await asyncio.shield(
                finalize_assistant_message(
                    conversation_id=conv_uuid,
                    turn_id=turn_uuid,
                    content="".join(chunks),
                    steps=steps,
                    msg_status="failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:500],
                )
            )
            yield format_sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
