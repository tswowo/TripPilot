"""攻略文档与 Chunk 的数据访问层。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import load_only

from trippilot.db.models import Guide, GuideChunk
from trippilot.db.session import session_scope
from trippilot.rag.models import GuideChunk as GuideChunkData
from trippilot.rag.models import GuideDocument


async def create_uploaded_guide(
    *,
    user_id: int,
    document: GuideDocument,
    chunks: Sequence[GuideChunkData],
    original_filename: str,
    file_type: str,
    file_size: int,
) -> Guide:
    guide = Guide(
        doc_id=document.doc_id,
        user_id=user_id,
        city=document.city,
        category=document.category,
        title=document.title,
        summary=document.summary,
        content=document.content,
        source_id=document.source_id,
        source_url=document.source_url,
        content_sha256=document.content_sha256,
        doc_metadata=document.metadata,
        original_filename=original_filename,
        file_type=file_type,
        file_size=file_size,
        vector_status="pending",
        vector_total_chunks=len(chunks),
        vector_completed_chunks=0,
    )
    async with session_scope() as db:
        db.add(guide)
        await db.flush()
        db.add_all(
            [
                GuideChunk(
                    doc_id=chunk.doc_id,
                    chunk_no=chunk.chunk_no,
                    heading_path=chunk.heading_path,
                    content=chunk.content,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    content_sha256=chunk.content_sha256,
                    chunking_version=chunk.chunking_version,
                )
                for chunk in chunks
            ]
        )
    return guide


async def list_user_guides(
    *,
    user_id: int,
    limit: int = 20,
    cursor: str | None = None,
) -> tuple[list[Guide], bool]:
    stmt = (
        select(Guide)
        .options(
            load_only(
                Guide.doc_id,
                Guide.city,
                Guide.category,
                Guide.title,
                Guide.summary,
                Guide.original_filename,
                Guide.file_type,
                Guide.file_size,
                Guide.vector_status,
                Guide.vector_total_chunks,
                Guide.vector_completed_chunks,
                Guide.vector_error,
                Guide.vector_started_at,
                Guide.vector_finished_at,
                Guide.create_time,
                Guide.update_time,
            )
        )
        .where(Guide.user_id == user_id)
    )
    if cursor is not None:
        try:
            cursor_time_text, cursor_doc_id = cursor.rsplit("|", 1)
            cursor_time = datetime.fromisoformat(cursor_time_text)
        except ValueError as exc:
            raise ValueError("cursor 格式无效") from exc
        stmt = stmt.where(
            (Guide.create_time < cursor_time)
            | ((Guide.create_time == cursor_time) & (Guide.doc_id < cursor_doc_id))
        )
    stmt = stmt.order_by(Guide.create_time.desc(), Guide.doc_id.desc()).limit(limit + 1)
    async with session_scope() as db:
        rows = list((await db.scalars(stmt)).all())
    return rows[:limit], len(rows) > limit


async def get_user_guide(*, doc_id: str, user_id: int) -> Guide | None:
    async with session_scope() as db:
        return await db.scalar(
            select(Guide).where(Guide.doc_id == doc_id, Guide.user_id == user_id)
        )


async def claim_guide_vectorization(*, doc_id: str, user_id: int) -> int | None:
    async with session_scope() as db:
        claimed = await db.scalar(
            update(Guide)
            .where(
                Guide.doc_id == doc_id,
                Guide.user_id == user_id,
                Guide.vector_status.in_(("pending", "failed")),
            )
            .values(
                vector_status="processing",
                vector_error=None,
                vector_started_at=func.now(),
                vector_finished_at=None,
                update_time=func.now(),
            )
            .returning(Guide.doc_id)
        )
        if claimed is None:
            return None
        return await db.scalar(
            select(func.count())
            .select_from(GuideChunk)
            .where(GuideChunk.doc_id == doc_id, GuideChunk.embedding.is_(None))
        )


async def get_pending_chunks(*, doc_id: str, limit: int) -> list[GuideChunk]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(
                    select(GuideChunk)
                    .where(GuideChunk.doc_id == doc_id, GuideChunk.embedding.is_(None))
                    .order_by(GuideChunk.chunk_no)
                    .limit(limit)
                )
            ).all()
        )


async def save_embedding_batch(
    *,
    doc_id: str,
    chunk_ids: Sequence[int],
    embeddings: Sequence[Sequence[float]],
    model: str,
    dimensions: int,
) -> int:
    if len(chunk_ids) != len(embeddings):
        raise ValueError("Chunk 数量与向量数量不一致")

    saved = 0
    async with session_scope() as db:
        for chunk_id, embedding in zip(chunk_ids, embeddings, strict=True):
            result = await db.execute(
                update(GuideChunk)
                .where(
                    GuideChunk.id == chunk_id,
                    GuideChunk.doc_id == doc_id,
                    GuideChunk.embedding.is_(None),
                )
                .values(
                    embedding=list(embedding),
                    embedding_model=model,
                    embedding_dimensions=dimensions,
                )
            )
            saved += result.rowcount
        if saved:
            await db.execute(
                update(Guide)
                .where(Guide.doc_id == doc_id, Guide.vector_status == "processing")
                .values(
                    vector_completed_chunks=Guide.vector_completed_chunks + saved,
                    update_time=func.now(),
                )
            )
    return saved


async def mark_guide_completed(*, doc_id: str) -> bool:
    async with session_scope() as db:
        total = await db.scalar(
            select(func.count()).select_from(GuideChunk).where(GuideChunk.doc_id == doc_id)
        )
        result = await db.execute(
            update(Guide)
            .where(Guide.doc_id == doc_id, Guide.vector_status == "processing")
            .values(
                vector_status="completed",
                vector_total_chunks=total or 0,
                vector_completed_chunks=total or 0,
                vector_error=None,
                vector_finished_at=func.now(),
                update_time=func.now(),
            )
        )
        return result.rowcount > 0


async def mark_guide_failed(*, doc_id: str, error_message: str) -> bool:
    async with session_scope() as db:
        result = await db.execute(
            update(Guide)
            .where(Guide.doc_id == doc_id, Guide.vector_status == "processing")
            .values(
                vector_status="failed",
                vector_error=error_message,
                vector_finished_at=func.now(),
                update_time=func.now(),
            )
        )
        return result.rowcount > 0


async def delete_user_guide(*, doc_id: str, user_id: int) -> bool:
    async with session_scope() as db:
        result = await db.execute(
            delete(Guide)
            .where(
                Guide.doc_id == doc_id,
                Guide.user_id == user_id,
                Guide.vector_status != "processing",
            )
            .returning(Guide.doc_id)
        )
        return result.scalar_one_or_none() is not None
