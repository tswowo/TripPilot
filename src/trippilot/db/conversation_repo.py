"""会话与消息表数据访问层（含逻辑删除过滤、短事务状态流转与双游标分页）。"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from trippilot.db.models import Conversation, Message
from trippilot.db.session import session_scope

ACTIVE = 0
DELETED = 1


def paginate_conversations(rows: Sequence[Conversation], limit: int) -> dict[str, Any]:
    """根据 limit+1 查询结果组装会话列表游标分页响应（按 update_time 倒序）。"""
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        {
            "id": str(c.id),
            "title": c.title,
            "update_time": c.update_time.isoformat(),
        }
        for c in page
    ]
    next_cursor = items[-1]["update_time"] if has_more and items else None
    return {"items": items, "has_more": has_more, "next_cursor": next_cursor}


def paginate_messages(rows_desc: Sequence[Message], limit: int) -> dict[str, Any]:
    """根据按 id DESC 查出的 limit+1 结果，切片后反转为时间正序（旧->新）并计算最老一条 id 作为游标。"""
    has_more = len(rows_desc) > limit
    page = list(rows_desc[:limit])
    page.reverse()
    items = [
        {
            "id": m.id,
            "turn_id": str(m.turn_id),
            "role": m.role,
            "content": m.content,
            "steps": m.steps,
            "status": m.status,
            "error_code": m.error_code,
            "error_message": m.error_message,
            "create_time": m.create_time.isoformat(),
        }
        for m in page
    ]
    next_cursor = items[0]["id"] if has_more and items else None
    return {"items": items, "has_more": has_more, "next_cursor": next_cursor}


async def init_turn(
    *,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    user_id: int,
    user_message: str,
    is_new_conversation: bool,
) -> bool:
    """流开始前的短事务：
    1. 新会话创建 Conversation，或旧会话校验归属、未逻辑删除并刷新 update_time；
    2. 写入同 turn_id 的 user 消息（completed）与空 assistant 占位消息（streaming）。
    """
    async with session_scope() as db:
        if is_new_conversation:
            db.add(
                Conversation(
                    id=conversation_id,
                    user_id=user_id,
                    title=user_message[:200],
                    is_deleted=ACTIVE,
                )
            )
        else:
            conv = await db.get(Conversation, conversation_id)
            if conv is None or conv.user_id != user_id or conv.is_deleted != ACTIVE:
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
                    is_deleted=ACTIVE,
                ),
                Message(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    role="assistant",
                    content="",
                    steps=[],
                    status="streaming",
                    is_deleted=ACTIVE,
                ),
            ]
        )
    return True


async def finalize_turn(
    *,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    content: str,
    steps: list[dict[str, Any]],
    msg_status: str = "completed",
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """流结束（或中断/异常）后的短事务：仅对未逻辑删除的记录回写最终内容与状态。"""
    async with session_scope() as db:
        await db.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.turn_id == turn_id,
                Message.role == "assistant",
                Message.is_deleted == ACTIVE,
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
            .where(
                Conversation.id == conversation_id,
                Conversation.is_deleted == ACTIVE,
            )
            .values(update_time=func.now())
        )


async def list_user_conversations(
    *,
    user_id: int,
    limit: int = 20,
    cursor: datetime | None = None,
) -> dict[str, Any]:
    """游标分页查询指定用户的有效会话列表（is_deleted = 0）。"""
    stmt = select(Conversation).where(
        Conversation.user_id == user_id,
        Conversation.is_deleted == ACTIVE,
    )
    if cursor is not None:
        stmt = stmt.where(Conversation.update_time < cursor)
    stmt = stmt.order_by(Conversation.update_time.desc()).limit(limit + 1)

    async with session_scope() as db:
        rows = (await db.scalars(stmt)).all()
    return paginate_conversations(rows, limit)


async def rename_user_conversation(
    *,
    conversation_id: uuid.UUID,
    user_id: int,
    title: str,
) -> dict[str, Any] | None:
    stmt = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.is_deleted == ACTIVE,
        )
        .values(
            title=title,
        )
        .returning(Conversation)
    )
    async with session_scope() as db:
        row = (await db.scalars(stmt)).one_or_none()
        if row is None:
            return None
        return {
            "id": str(row.id),
            "title": row.title,
            "update_time": row.update_time.isoformat(),
        }


async def soft_delete_user_conversation(
    *,
    conversation_id: uuid.UUID,
    user_id: int,
) -> bool:
    """在应用层单事务内级联逻辑删除会话及其下属消息（0 -> 1）。"""
    conv_stmt = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.is_deleted == ACTIVE,
        )
        .values(is_deleted=DELETED, update_time=func.now())
        .returning(Conversation.id)
    )
    msg_stmt = (
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_deleted == ACTIVE,
        )
        .values(is_deleted=DELETED, update_time=func.now())
    )
    async with session_scope() as db:
        deleted_id = (await db.scalars(conv_stmt)).one_or_none()
        if deleted_id is None:
            return False
        await db.execute(msg_stmt)
        return True


async def list_conversation_messages(
    *,
    conversation_id: uuid.UUID,
    user_id: int,
    limit: int = 20,
    cursor: int | None = None,
) -> dict[str, Any] | None:
    """游标分页查询指定有效会话的有效历史消息；若会话不存在、已删除或无权访问则返回 None。"""
    async with session_scope() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user_id or conv.is_deleted != ACTIVE:
            return None

        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.is_deleted == ACTIVE,
        )
        if cursor is not None:
            stmt = stmt.where(Message.id < cursor)
        stmt = stmt.order_by(Message.id.desc()).limit(limit + 1)
        rows = (await db.scalars(stmt)).all()

    return paginate_messages(rows, limit)
