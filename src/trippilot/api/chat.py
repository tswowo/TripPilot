"""核心对话 SSE 流式网关路由（薄 Controller，串联持久化 Repo 与 Agent 核心流）。"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from trippilot.db.conversation_repo import finalize_turn, init_turn
from trippilot.graph.stream import TimelineAssembler, stream_graph_events
from trippilot.utils.id_util import uuid7
from trippilot.utils.login_util import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_SENTINEL = object()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = Field(default=None)
    thinking: bool = Field(default=False)


def get_graph(request: Request) -> Any:
    return request.app.state.graph


def format_sse(event: str, data: dict[str, Any]) -> str:
    """格式化标准 SSE 帧：event + JSON data + 双换行。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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

    ok = await init_turn(
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

    async def event_generator() -> AsyncIterator[str]:
        yield format_sse(
            "meta",
            {
                "conversation_id": conv_id_str,
                "turn_id": turn_id_str,
                "is_new": is_new,
                "title": request.message[:200],
            },
        )
        timeline = TimelineAssembler()
        # todo 以后换成mq
        queue: asyncio.Queue[Any] = asyncio.Queue()
        client_disconnected = False

        async def run_graph() -> None:
            try:
                async for kind, event_data in stream_graph_events(
                        graph,
                        thread_id=conv_id_str,
                        user_message=request.message,
                        thinking=request.thinking,
                ):
                    await queue.put((kind, event_data))
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                await queue.put(exc)
            finally:
                await queue.put(_SENTINEL)

        async def watch_disconnect(target_task: asyncio.Task[None]) -> None:
            nonlocal client_disconnected
            while not target_task.done():
                if await raw_request.is_disconnected():
                    client_disconnected = True
                    target_task.cancel()
                    return
                await asyncio.sleep(0.2)

        graph_task = asyncio.create_task(run_graph())
        watcher_task = asyncio.create_task(watch_disconnect(graph_task))

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, asyncio.CancelledError):
                    if client_disconnected:
                        await asyncio.shield(
                            finalize_turn(
                                conversation_id=conv_uuid,
                                turn_id=turn_uuid,
                                content="".join(timeline.content_chunks),
                                reasoning_content="".join(timeline.reasoning_chunks),
                                steps=timeline.finalize(cancelled=True),
                                msg_status="failed",
                                error_code="CLIENT_DISCONNECTED",
                                error_message="已停止生成",
                            )
                        )
                        return
                    raise item
                if isinstance(item, BaseException):
                    raise item

                kind, event_data = item
                if kind == "delta":
                    timeline.on_delta(event_data)
                    yield format_sse("delta", event_data)
                elif kind == "step":
                    timeline.on_step(event_data)
                    yield format_sse("step", event_data)

            await asyncio.shield(
                finalize_turn(
                    conversation_id=conv_uuid,
                    turn_id=turn_uuid,
                    content="".join(timeline.content_chunks),
                    reasoning_content="".join(timeline.reasoning_chunks),
                    steps=timeline.finalize(cancelled=False),
                    msg_status="completed",
                )
            )
            yield format_sse("done", {"conversation_id": conv_id_str, "turn_id": turn_id_str})

        except asyncio.CancelledError:
            graph_task.cancel()
            await asyncio.shield(
                finalize_turn(
                    conversation_id=conv_uuid,
                    turn_id=turn_uuid,
                    content="".join(timeline.content_chunks),
                    reasoning_content="".join(timeline.reasoning_chunks),
                    steps=timeline.finalize(cancelled=True),
                    msg_status="failed",
                    error_code="CANCELLED",
                    error_message="已停止生成",
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001
            await asyncio.shield(
                finalize_turn(
                    conversation_id=conv_uuid,
                    turn_id=turn_uuid,
                    content="".join(timeline.content_chunks),
                    reasoning_content="".join(timeline.reasoning_chunks),
                    steps=timeline.finalize(cancelled=True),
                    msg_status="failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:500],
                )
            )
            yield format_sse("error", {"message": str(exc)})
        finally:
            watcher_task.cancel()
            if not graph_task.done():
                graph_task.cancel()
            with contextlib.suppress(BaseException):
                await watcher_task
            with contextlib.suppress(BaseException):
                await graph_task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
