"""百炼 Embedding 客户端的 MockTransport 隔离测试。"""

from types import SimpleNamespace

import httpx
import pytest

from trippilot.rag import embeddings


def _settings(**changes):
    values = {
        "dashscope_api_key": "test-key",
        "dashscope_base_url": "https://example.test/v1",
        "embedding_model": "test-embedding-model",
        "embedding_dimensions": 2,
        "embedding_max_retries": 2,
        "request_timeout_s": 1,
    }
    values.update(changes)
    return SimpleNamespace(**values)


async def test_embedding_restores_response_order_and_sends_configuration(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    monkeypatch.setattr(embeddings, "get_settings", _settings)
    client = embeddings.DashScopeEmbeddingClient(transport=httpx.MockTransport(handler))
    vectors = await client.embed_texts(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert len(requests) == 1
    assert str(requests[0].url) == "https://example.test/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer test-key"


async def test_embedding_retries_rate_limit_without_real_sleep(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(embeddings, "get_settings", _settings)
    monkeypatch.setattr(embeddings.asyncio, "sleep", no_sleep)
    client = embeddings.DashScopeEmbeddingClient(transport=httpx.MockTransport(handler))
    assert await client.embed_texts(["hello"]) == [[1.0, 2.0]]
    assert attempts == 2


async def test_embedding_does_not_retry_bad_request(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": "bad request"})

    monkeypatch.setattr(embeddings, "get_settings", _settings)
    client = embeddings.DashScopeEmbeddingClient(transport=httpx.MockTransport(handler))
    with pytest.raises(embeddings.EmbeddingError, match="请求参数无效"):
        await client.embed_texts(["hello"])
    assert attempts == 1


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": [1.0]}]},
        {"data": [{"index": 1, "embedding": [1.0, 2.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, "bad"]}]},
    ],
)
async def test_embedding_rejects_malformed_response(monkeypatch, body):
    monkeypatch.setattr(embeddings, "get_settings", _settings)
    client = embeddings.DashScopeEmbeddingClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    with pytest.raises(embeddings.EmbeddingError, match="响应格式无效"):
        await client.embed_texts(["hello"])


async def test_embedding_requires_configuration(monkeypatch):
    monkeypatch.setattr(embeddings, "get_settings", lambda: _settings(dashscope_api_key=""))
    client = embeddings.DashScopeEmbeddingClient()
    with pytest.raises(embeddings.EmbeddingError, match="DASHSCOPE_API_KEY"):
        await client.embed_texts(["hello"])
