"""攻略上传、文档管理与手动向量化接口。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from trippilot.config import get_settings
from trippilot.db import guide_repo
from trippilot.db.models import Guide
from trippilot.rag.chunking import chunk_document
from trippilot.rag.uploads import parse_uploaded_document
from trippilot.rag.vectorization import vectorize_document
from trippilot.utils.login_util import get_current_user

router = APIRouter(prefix="/api/guides", tags=["guides"])

_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class GuideUploadSizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != f"{router.prefix}/upload":
            await self.app(scope, receive, send)
            return
        max_body_bytes = get_settings().guide_upload_max_bytes + _MULTIPART_OVERHEAD_BYTES
        await RequestBodyLimitMiddleware(self.app, max_body_bytes)(scope, receive, send)


class GuideSummary(BaseModel):
    doc_id: str
    title: str
    city: str
    category: str
    summary: str
    original_filename: str | None
    file_type: str | None
    file_size: int | None
    vector_status: str
    is_vectorized: bool
    vector_total_chunks: int
    vector_completed_chunks: int
    vector_progress: float
    vector_error: str | None
    vector_started_at: datetime | None
    vector_finished_at: datetime | None
    create_time: datetime
    update_time: datetime


class GuideDetail(GuideSummary):
    content: str
    metadata: dict[str, Any]


class GuideList(BaseModel):
    items: list[GuideSummary]
    has_more: bool
    next_cursor: str | None


def guide_summary(guide: Guide) -> GuideSummary:
    total = guide.vector_total_chunks
    progress = round(guide.vector_completed_chunks / total * 100, 1) if total else 0.0
    return GuideSummary(
        doc_id=guide.doc_id,
        title=guide.title,
        city=guide.city,
        category=guide.category,
        summary=guide.summary,
        original_filename=guide.original_filename,
        file_type=guide.file_type,
        file_size=guide.file_size,
        vector_status=guide.vector_status,
        is_vectorized=guide.vector_status == "completed",
        vector_total_chunks=total,
        vector_completed_chunks=guide.vector_completed_chunks,
        vector_progress=progress,
        vector_error=guide.vector_error,
        vector_started_at=guide.vector_started_at,
        vector_finished_at=guide.vector_finished_at,
        create_time=guide.create_time,
        update_time=guide.update_time,
    )


def guide_detail(guide: Guide) -> GuideDetail:
    return GuideDetail(
        **guide_summary(guide).model_dump(),
        content=guide.content,
        metadata=guide.doc_metadata,
    )


@router.post("/upload", response_model=GuideSummary, status_code=status.HTTP_201_CREATED)
async def upload_guide(
    file: Annotated[UploadFile, File()],
    payload: Annotated[dict, Depends(get_current_user)],
    title: Annotated[str | None, Form(max_length=200)] = None,
    city: Annotated[str | None, Form(max_length=100)] = None,
    category: Annotated[str | None, Form(max_length=32)] = None,
) -> GuideSummary:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if file_type not in {"md", "txt"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 .md 或 .txt 文件")

    max_bytes = get_settings().guide_upload_max_bytes
    raw = await file.read(max_bytes + 1)
    await file.close()
    if len(raw) > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "文件超过大小限制")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件必须使用 UTF-8 编码") from exc

    try:
        document = parse_uploaded_document(
            source=source,
            filename=filename,
            file_type=file_type,
            title=title,
            city=city,
            category=category,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    chunks = chunk_document(document)
    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文档没有可检索的正文")
    guide = await guide_repo.create_uploaded_guide(
        user_id=int(payload["uid"]),
        document=document,
        chunks=chunks,
        original_filename=filename,
        file_type=file_type,
        file_size=len(raw),
    )
    return guide_summary(guide)


@router.get("", response_model=GuideList)
async def list_guides(
    payload: Annotated[dict, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> GuideList:
    try:
        guides, has_more = await guide_repo.list_user_guides(
            user_id=int(payload["uid"]), limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    items = [guide_summary(guide) for guide in guides]
    next_cursor = None
    if has_more and items:
        next_cursor = f"{items[-1].create_time.isoformat()}|{items[-1].doc_id}"
    return GuideList(items=items, has_more=has_more, next_cursor=next_cursor)


@router.get("/{doc_id}", response_model=GuideDetail)
async def get_guide(
    doc_id: str,
    payload: Annotated[dict, Depends(get_current_user)],
) -> GuideDetail:
    guide = await guide_repo.get_user_guide(doc_id=doc_id, user_id=int(payload["uid"]))
    if guide is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    return guide_detail(guide)


@router.post(
    "/{doc_id}/vectorize", response_model=GuideSummary, status_code=status.HTTP_202_ACCEPTED
)
async def trigger_vectorization(
    doc_id: str,
    background_tasks: BackgroundTasks,
    payload: Annotated[dict, Depends(get_current_user)],
) -> GuideSummary:
    user_id = int(payload["uid"])
    guide = await guide_repo.get_user_guide(doc_id=doc_id, user_id=user_id)
    if guide is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    if guide.vector_status in {"processing", "completed"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "文档正在向量化或已完成向量化")
    if guide.vector_total_chunks == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "文档没有可向量化的内容")

    background_tasks.add_task(vectorize_document, doc_id=doc_id, user_id=user_id)
    return guide_summary(guide)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_guide(
    doc_id: str,
    payload: Annotated[dict, Depends(get_current_user)],
) -> Response:
    user_id = int(payload["uid"])
    guide = await guide_repo.get_user_guide(doc_id=doc_id, user_id=user_id)
    if guide is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    if guide.vector_status == "processing":
        raise HTTPException(status.HTTP_409_CONFLICT, "向量化进行中，暂不能删除文档")
    if not await guide_repo.delete_user_guide(doc_id=doc_id, user_id=user_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "文档状态已变更，请刷新后重试")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
