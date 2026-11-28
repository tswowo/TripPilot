"""百炼 OpenAI-compatible Embedding 客户端。"""

import asyncio
import math
from collections.abc import Sequence
from typing import Any

import httpx

from trippilot.config import get_settings


class EmbeddingError(RuntimeError):
    pass


class DashScopeEmbeddingClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        settings = get_settings()
        if not settings.dashscope_api_key:
            raise EmbeddingError("未配置 DASHSCOPE_API_KEY")

        payload = {
            "model": settings.embedding_model,
            "input": list(texts),
        }
        headers = {"Authorization": f"Bearer {settings.dashscope_api_key}"}
        url = f"{settings.dashscope_base_url.rstrip('/')}/embeddings"
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_s,
            trust_env=False,
            transport=self._transport,
        ) as client:
            for attempt in range(settings.embedding_max_retries + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.RequestError as exc:
                    if attempt < settings.embedding_max_retries:
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    raise EmbeddingError("Embedding 服务连接失败") from exc

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < settings.embedding_max_retries:
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    raise EmbeddingError(f"Embedding 服务暂时不可用（HTTP {response.status_code}）")
                if not response.is_success:
                    if response.status_code in (401, 403):
                        raise EmbeddingError("Embedding 服务鉴权失败")
                    if response.status_code == 400:
                        raise EmbeddingError("Embedding 请求参数无效")
                    raise EmbeddingError(f"Embedding 请求失败（HTTP {response.status_code}）")

                try:
                    body: Any = response.json()
                    return _parse_embeddings(
                        body,
                        expected_count=len(texts),
                        dimensions=settings.embedding_dimensions,
                    )
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    raise EmbeddingError("Embedding 响应格式无效") from exc

        raise AssertionError("unreachable")


def _parse_embeddings(body: Any, *, expected_count: int, dimensions: int) -> list[list[float]]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise TypeError("missing data array")
    items = body["data"]
    if len(items) != expected_count:
        raise ValueError("embedding count mismatch")

    ordered: list[list[float] | None] = [None] * expected_count
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("invalid embedding item")
        index = item["index"]
        values = item["embedding"]
        if type(index) is not int or not 0 <= index < expected_count or ordered[index] is not None:
            raise ValueError("invalid embedding index")
        if not isinstance(values, list) or len(values) != dimensions:
            raise ValueError("embedding dimensions mismatch")
        vector: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("invalid embedding value")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("non-finite embedding value")
            vector.append(number)
        ordered[index] = vector

    if any(vector is None for vector in ordered):
        raise ValueError("missing embedding")
    return [vector for vector in ordered if vector is not None]
