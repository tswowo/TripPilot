"""手动触发、单进程执行的攻略向量化任务。"""

import asyncio
import logging

from trippilot.config import get_settings
from trippilot.db import guide_repo
from trippilot.rag.embeddings import DashScopeEmbeddingClient, EmbeddingError

logger = logging.getLogger(__name__)


async def vectorize_document(*, doc_id: str, user_id: int) -> None:
    total = await guide_repo.claim_guide_vectorization(doc_id=doc_id, user_id=user_id)
    if total is None:
        return

    try:
        settings = get_settings()
        client = DashScopeEmbeddingClient()
        while True:
            chunks = await guide_repo.get_pending_chunks(
                doc_id=doc_id,
                limit=settings.embedding_batch_size,
            )
            if not chunks:
                break
            embeddings = await client.embed_texts([chunk.content for chunk in chunks])
            saved = await guide_repo.save_embedding_batch(
                doc_id=doc_id,
                chunk_ids=[chunk.id for chunk in chunks],
                embeddings=embeddings,
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
            if saved != len(chunks):
                raise RuntimeError("向量化进度写入不完整")

        completed = await guide_repo.mark_guide_completed(doc_id=doc_id)
        if not completed:
            raise RuntimeError("文档状态已变更，无法完成向量化")
    except asyncio.CancelledError:
        await guide_repo.mark_guide_failed(doc_id=doc_id, error_message="向量化任务已取消")
        raise
    except Exception as exc:  # noqa: BLE001 - 后台任务顶层必须把异常落为失败态
        message = (
            str(exc) if isinstance(exc, EmbeddingError) else f"向量化失败（{type(exc).__name__}）"
        )
        logger.error("Guide vectorization failed doc_id=%s error=%s", doc_id, message)
        await guide_repo.mark_guide_failed(doc_id=doc_id, error_message=message)
