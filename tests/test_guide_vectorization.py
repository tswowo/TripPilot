"""手动向量化后台任务的纯 Mock 测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from trippilot.rag import vectorization
from trippilot.rag.embeddings import EmbeddingError


def _settings():
    return SimpleNamespace(
        embedding_batch_size=1,
        embedding_model="test-model",
        embedding_dimensions=2,
    )


async def test_vectorization_processes_batches_and_marks_completed(monkeypatch):
    pending = [
        [SimpleNamespace(id=1, content="first")],
        [SimpleNamespace(id=2, content="second")],
        [],
    ]
    batches: list[list[str]] = []
    saved: list[dict] = []
    completed: list[str] = []

    async def claim(**kwargs):
        assert kwargs == {"doc_id": "doc-1", "user_id": 7}
        return 2

    async def get_pending(**kwargs):
        assert kwargs["limit"] == 1
        return pending.pop(0)

    class FakeClient:
        async def embed_texts(self, texts):
            batches.append(texts)
            return [[float(index), 1.0] for index in range(len(texts))]

    async def save_batch(**kwargs):
        saved.append(kwargs)
        return len(kwargs["chunk_ids"])

    async def mark_completed(**kwargs):
        completed.append(kwargs["doc_id"])
        return True

    monkeypatch.setattr(vectorization.guide_repo, "claim_guide_vectorization", claim)
    monkeypatch.setattr(vectorization.guide_repo, "get_pending_chunks", get_pending)
    monkeypatch.setattr(vectorization.guide_repo, "save_embedding_batch", save_batch)
    monkeypatch.setattr(vectorization.guide_repo, "mark_guide_completed", mark_completed)
    monkeypatch.setattr(vectorization, "DashScopeEmbeddingClient", FakeClient)
    monkeypatch.setattr(vectorization, "get_settings", _settings)

    await vectorization.vectorize_document(doc_id="doc-1", user_id=7)

    assert batches == [["first"], ["second"]]
    assert [item["chunk_ids"] for item in saved] == [[1], [2]]
    assert all(item["model"] == "test-model" for item in saved)
    assert completed == ["doc-1"]


async def test_vectorization_persists_safe_failure_message(monkeypatch):
    failures: list[dict] = []

    async def claim(**kwargs):
        return 1

    async def get_pending(**kwargs):
        return [SimpleNamespace(id=1, content="first")]

    class FailingClient:
        async def embed_texts(self, texts):
            raise EmbeddingError("Embedding 服务暂时不可用（HTTP 429）")

    async def mark_failed(**kwargs):
        failures.append(kwargs)
        return True

    monkeypatch.setattr(vectorization.guide_repo, "claim_guide_vectorization", claim)
    monkeypatch.setattr(vectorization.guide_repo, "get_pending_chunks", get_pending)
    monkeypatch.setattr(vectorization.guide_repo, "mark_guide_failed", mark_failed)
    monkeypatch.setattr(vectorization, "DashScopeEmbeddingClient", FailingClient)
    monkeypatch.setattr(vectorization, "get_settings", _settings)

    await vectorization.vectorize_document(doc_id="doc-1", user_id=7)

    assert failures == [
        {
            "doc_id": "doc-1",
            "error_message": "Embedding 服务暂时不可用（HTTP 429）",
        }
    ]


async def test_vectorization_does_not_expose_unexpected_exception_details(monkeypatch):
    failures: list[dict] = []

    async def claim(**kwargs):
        return 1

    async def get_pending(**kwargs):
        raise RuntimeError("secret connection string")

    async def mark_failed(**kwargs):
        failures.append(kwargs)
        return True

    monkeypatch.setattr(vectorization.guide_repo, "claim_guide_vectorization", claim)
    monkeypatch.setattr(vectorization.guide_repo, "get_pending_chunks", get_pending)
    monkeypatch.setattr(vectorization.guide_repo, "mark_guide_failed", mark_failed)
    monkeypatch.setattr(vectorization, "get_settings", _settings)

    await vectorization.vectorize_document(doc_id="doc-1", user_id=7)

    assert failures[0]["error_message"] == "向量化失败（RuntimeError）"
    assert "secret" not in failures[0]["error_message"]


async def test_vectorization_marks_cancelled_task_as_failed(monkeypatch):
    failures: list[dict] = []

    async def claim(**kwargs):
        return 1

    async def get_pending(**kwargs):
        return [SimpleNamespace(id=1, content="first")]

    class CancelledClient:
        async def embed_texts(self, texts):
            raise asyncio.CancelledError

    async def mark_failed(**kwargs):
        failures.append(kwargs)
        return True

    monkeypatch.setattr(vectorization.guide_repo, "claim_guide_vectorization", claim)
    monkeypatch.setattr(vectorization.guide_repo, "get_pending_chunks", get_pending)
    monkeypatch.setattr(vectorization.guide_repo, "mark_guide_failed", mark_failed)
    monkeypatch.setattr(vectorization, "DashScopeEmbeddingClient", CancelledClient)
    monkeypatch.setattr(vectorization, "get_settings", _settings)

    with pytest.raises(asyncio.CancelledError):
        await vectorization.vectorize_document(doc_id="doc-1", user_id=7)
    assert failures == [{"doc_id": "doc-1", "error_message": "向量化任务已取消"}]


async def test_vectorization_skips_document_not_claimed(monkeypatch):
    async def claim(**kwargs):
        return None

    class ForbiddenClient:
        def __init__(self):
            raise AssertionError("未领取任务不得创建 Embedding 客户端")

    monkeypatch.setattr(vectorization.guide_repo, "claim_guide_vectorization", claim)
    monkeypatch.setattr(vectorization, "DashScopeEmbeddingClient", ForbiddenClient)

    await vectorization.vectorize_document(doc_id="doc-1", user_id=7)
