"""攻略上传与管理接口的隔离测试，不连接数据库或百炼。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from trippilot.api import guides
from trippilot.db.models import Guide
from trippilot.rag import vectorization
from trippilot.utils.login_util import get_current_user


def _guide(**changes: Any) -> Guide:
    now = datetime.now(UTC)
    values = {
        "doc_id": "guide-1",
        "user_id": 7,
        "city": "杭州",
        "category": "动线指南",
        "title": "杭州两日游",
        "summary": "杭州旅行攻略",
        "content": "# 杭州两日游\n第一天游览西湖。",
        "source_id": "upload-1",
        "source_url": "local://guides/guide-1",
        "content_sha256": "a" * 64,
        "doc_metadata": {},
        "original_filename": "hangzhou.md",
        "file_type": "md",
        "file_size": 40,
        "vector_status": "pending",
        "vector_total_chunks": 2,
        "vector_completed_chunks": 0,
        "vector_error": None,
        "vector_started_at": None,
        "vector_finished_at": None,
        "create_time": now,
        "update_time": now,
    }
    values.update(changes)
    return Guide(**values)


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(guides.GuideUploadSizeLimitMiddleware)
    application.include_router(guides.router)

    async def current_user() -> dict:
        return {"uid": 7, "sub": "tester"}

    application.dependency_overrides[get_current_user] = current_user
    return application


@pytest.fixture
async def client(app: FastAPI):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


async def test_upload_txt_only_parses_and_chunks_without_embedding(client, monkeypatch):
    captured: dict[str, Any] = {}

    async def create_guide(**kwargs):
        captured.update(kwargs)
        return _guide(
            doc_id=kwargs["document"].doc_id,
            title=kwargs["document"].title,
            city=kwargs["document"].city,
            category=kwargs["document"].category,
            summary=kwargs["document"].summary,
            content=kwargs["document"].content,
            original_filename=kwargs["original_filename"],
            file_type=kwargs["file_type"],
            file_size=kwargs["file_size"],
            vector_total_chunks=len(kwargs["chunks"]),
        )

    async def forbidden_embedding(*args, **kwargs):
        raise AssertionError("上传不得调用 Embedding")

    monkeypatch.setattr(guides.guide_repo, "create_uploaded_guide", create_guide)
    monkeypatch.setattr(
        "trippilot.rag.vectorization.DashScopeEmbeddingClient.embed_texts",
        forbidden_embedding,
    )

    response = await client.post(
        "/api/guides/upload",
        files={
            "file": ("weekend.txt", "第一天游览西湖。\n\n第二天参观博物馆。".encode(), "text/plain")
        },
        data={"city": "杭州", "title": "周末攻略"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["vector_status"] == "pending"
    assert body["is_vectorized"] is False
    assert body["vector_progress"] == 0
    assert body["title"] == "周末攻略"
    assert captured["user_id"] == 7
    assert captured["document"].content_sha256
    assert len(captured["chunks"]) == 1
    for chunk in captured["chunks"]:
        assert captured["document"].content[chunk.start_offset : chunk.end_offset] == chunk.content


async def test_upload_markdown_replaces_frontmatter_doc_id(client, monkeypatch):
    captured: dict[str, Any] = {}
    source = """---
{"doc_id":"reused-id","city":"杭州","category":"动线指南","title":"原标题","summary":"摘要","source_id":"source","source_url":"https://example.test/guide"}
---
# 新标题
正文内容。
"""

    async def create_guide(**kwargs):
        captured.update(kwargs)
        return _guide(doc_id=kwargs["document"].doc_id)

    monkeypatch.setattr(guides.guide_repo, "create_uploaded_guide", create_guide)
    response = await client.post(
        "/api/guides/upload",
        files={"file": ("guide.md", source.encode(), "text/markdown")},
        data={"title": "覆盖标题"},
    )

    assert response.status_code == 201
    assert captured["document"].doc_id != "reused-id"
    assert captured["document"].title == "覆盖标题"
    assert captured["document"].metadata["doc_id"] == captured["document"].doc_id
    assert captured["document"].metadata["title"] == "覆盖标题"
    assert captured["document"].content == "# 新标题\n正文内容。"


async def test_markdown_horizontal_rule_is_not_treated_as_frontmatter(client, monkeypatch):
    captured: dict[str, Any] = {}

    async def create_guide(**kwargs):
        captured.update(kwargs)
        return _guide(content=kwargs["document"].content)

    monkeypatch.setattr(guides.guide_repo, "create_uploaded_guide", create_guide)
    response = await client.post(
        "/api/guides/upload",
        files={"file": ("guide.md", "---\n正文内容。".encode(), "text/markdown")},
    )

    assert response.status_code == 201
    assert captured["document"].content == "---\n正文内容。"


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("guide.pdf", b"not allowed"),
        ("empty.txt", b""),
        ("invalid.txt", b"\xff\xfe"),
        ("nul.txt", b"bad\x00text"),
        ("heading.md", b"# title\n"),
    ],
)
async def test_upload_rejects_invalid_files(client, monkeypatch, filename, content):
    async def forbidden_create(**kwargs):
        raise AssertionError("非法文件不得落库")

    monkeypatch.setattr(guides.guide_repo, "create_uploaded_guide", forbidden_create)
    response = await client.post("/api/guides/upload", files={"file": (filename, content)})
    assert response.status_code == 400


async def test_upload_rejects_file_over_limit(client, monkeypatch):
    monkeypatch.setattr(
        guides,
        "get_settings",
        lambda: SimpleNamespace(guide_upload_max_bytes=4),
    )
    response = await client.post("/api/guides/upload", files={"file": ("big.txt", b"12345")})
    assert response.status_code == 413


async def test_upload_middleware_limits_streamed_body_before_route(app, monkeypatch):
    monkeypatch.setattr(
        guides,
        "get_settings",
        lambda: SimpleNamespace(guide_upload_max_bytes=4),
    )
    monkeypatch.setattr(guides, "_MULTIPART_OVERHEAD_BYTES", 0)
    bodies = iter(
        [
            b"--b",
            (
                b"oundary\r\n"
                b'Content-Disposition: form-data; name="file"; filename="big.txt"\r\n'
                b"Content-Type: text/plain\r\n\r\n"
                b"123456\r\n"
                b"--boundary--\r\n"
            ),
        ]
    )
    sent: list[dict] = []

    async def receive():
        body = next(bodies)
        return {"type": "http.request", "body": body, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/guides/upload",
        "raw_path": b"/api/guides/upload",
        "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)

    assert sent[0]["status"] == 413
    assert b"Content Too Large" in sent[1]["body"]


async def test_upload_middleware_rejects_large_content_length_without_reading(app, monkeypatch):
    monkeypatch.setattr(
        guides,
        "get_settings",
        lambda: SimpleNamespace(guide_upload_max_bytes=4),
    )
    sent: list[dict] = []

    async def receive():
        raise AssertionError("Content-Length 超限时不得读取请求体")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/guides/upload",
        "raw_path": b"/api/guides/upload",
        "query_string": b"",
        "headers": [(b"content-length", b"999999")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)

    assert sent[0]["status"] == 413


async def test_list_and_detail_expose_progress(client, monkeypatch):
    calls: list[dict[str, Any]] = []

    async def list_guides(**kwargs):
        calls.append(kwargs)
        return [_guide(vector_completed_chunks=1)], True

    async def get_guide(**kwargs):
        calls.append(kwargs)
        return _guide(vector_status="completed", vector_completed_chunks=2)

    monkeypatch.setattr(guides.guide_repo, "list_user_guides", list_guides)
    monkeypatch.setattr(guides.guide_repo, "get_user_guide", get_guide)

    response = await client.get("/api/guides?limit=1")
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["vector_progress"] == 50.0
    assert body["has_more"] is True
    assert body["next_cursor"].endswith("|guide-1")
    assert calls[0]["user_id"] == 7

    detail = await client.get("/api/guides/guide-1")
    assert detail.status_code == 200
    assert detail.json()["is_vectorized"] is True
    assert detail.json()["vector_progress"] == 100.0
    assert "第一天" in detail.json()["content"]


async def test_detail_is_scoped_to_current_user(client, monkeypatch):
    async def get_guide(*, doc_id: str, user_id: int):
        assert user_id == 7

    monkeypatch.setattr(guides.guide_repo, "get_user_guide", get_guide)
    response = await client.get("/api/guides/someone-elses-guide")
    assert response.status_code == 404


async def test_manual_trigger_runs_mocked_vectorization_and_blocks_repeat(client, monkeypatch):
    state = _guide()
    embedded_batches: list[list[str]] = []
    claims: list[dict[str, Any]] = []

    async def get_guide(**kwargs):
        return state

    async def claim(**kwargs):
        claims.append(kwargs)
        if state.vector_status == "processing":
            return None
        state.vector_status = "processing"
        state.vector_started_at = datetime.now(UTC)
        return state.vector_total_chunks

    batches = [[SimpleNamespace(id=1, content="first")], []]

    async def get_pending(**kwargs):
        return batches.pop(0)

    class FakeClient:
        async def embed_texts(self, texts):
            embedded_batches.append(texts)
            return [[1.0, 0.0] for _ in texts]

    async def save_batch(**kwargs):
        state.vector_completed_chunks += len(kwargs["chunk_ids"])
        return len(kwargs["chunk_ids"])

    async def mark_completed(**kwargs):
        state.vector_status = "completed"
        state.vector_completed_chunks = state.vector_total_chunks
        return True

    monkeypatch.setattr(guides.guide_repo, "get_user_guide", get_guide)
    monkeypatch.setattr(guides.guide_repo, "claim_guide_vectorization", claim)
    monkeypatch.setattr(guides.guide_repo, "get_pending_chunks", get_pending)
    monkeypatch.setattr(guides.guide_repo, "save_embedding_batch", save_batch)
    monkeypatch.setattr(guides.guide_repo, "mark_guide_completed", mark_completed)
    monkeypatch.setattr(
        vectorization,
        "DashScopeEmbeddingClient",
        FakeClient,
    )
    monkeypatch.setattr(
        vectorization,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_batch_size=1,
            embedding_model="test-model",
            embedding_dimensions=2,
        ),
    )

    first = await client.post("/api/guides/guide-1/vectorize")
    second = await client.post("/api/guides/guide-1/vectorize")

    assert first.status_code == 202
    assert second.status_code == 409
    assert claims == [{"doc_id": "guide-1", "user_id": 7}]
    assert embedded_batches == [["first"]]
    assert state.vector_status == "completed"


async def test_guide_routes_require_authentication(app, client):
    app.dependency_overrides.clear()
    response = await client.get("/api/guides")
    assert response.status_code == 401


async def test_processing_document_cannot_be_deleted(client, monkeypatch):
    async def get_guide(**kwargs):
        return _guide(vector_status="processing")

    monkeypatch.setattr(guides.guide_repo, "get_user_guide", get_guide)
    response = await client.delete("/api/guides/guide-1")
    assert response.status_code == 409
