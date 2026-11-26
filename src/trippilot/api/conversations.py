"""历史会话列表、重命名、逻辑删除与增量消息记录路由。"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints

from trippilot.db.conversation_repo import (
    list_conversation_messages,
    list_user_conversations,
    rename_user_conversation,
    soft_delete_user_conversation,
)
from trippilot.utils.login_util import get_current_user

router = APIRouter(prefix="/api", tags=["conversations"])

ConversationTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class RenameConversationRequest(BaseModel):
    title: ConversationTitle


@router.get("/conversations")
async def list_conversations(
    payload: Annotated[dict, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[datetime | None, Query()] = None,
):
    return await list_user_conversations(
        user_id=payload["uid"],
        limit=limit,
        cursor=cursor,
    )


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: uuid.UUID,
    request: RenameConversationRequest,
    payload: Annotated[dict, Depends(get_current_user)],
):
    updated = await rename_user_conversation(
        conversation_id=conversation_id,
        user_id=payload["uid"],
        title=request.title,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )
    return updated


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    payload: Annotated[dict, Depends(get_current_user)],
):
    deleted = await soft_delete_user_conversation(
        conversation_id=conversation_id,
        user_id=payload["uid"],
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    payload: Annotated[dict, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[int | None, Query(ge=1)] = None,
):
    result = await list_conversation_messages(
        conversation_id=conversation_id,
        user_id=payload["uid"],
        limit=limit,
        cursor=cursor,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在或无权访问",
        )
    return result
