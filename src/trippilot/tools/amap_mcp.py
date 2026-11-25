"""高德官方 MCP Server 接入（Streamable HTTP）。

高德 2026-03-17 已下线 SSE 传输，只留 Streamable HTTP，别再照旧教程写 /sse?key=。
"""

from __future__ import annotations

import json

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from trippilot.config import Settings, get_settings


def _client_factory(headers=None, timeout=None, auth=None):
    """禁用 trust_env：本机/CI 常带 HTTP_PROXY，国内 MCP 走代理会被回 400。"""
    return httpx.AsyncClient(trust_env=False, headers=headers, timeout=timeout, auth=auth)


def build_client(settings: Settings | None = None) -> MultiServerMCPClient:
    settings = settings or get_settings()
    if not settings.amap_maps_api_key:
        raise RuntimeError("缺少 AMAP_MAPS_API_KEY，无法连接高德 MCP")
    return MultiServerMCPClient(
        {
            "amap": {
                "transport": "streamable_http",
                "url": f"{settings.amap_mcp_base_url}?key={settings.amap_maps_api_key}",
                "httpx_client_factory": _client_factory,
            }
        }
    )


async def load_amap_tools(settings: Settings | None = None) -> list[BaseTool]:
    return await build_client(settings).get_tools()


def parse_mcp_payload(result):
    """MCP 工具统一返回 [{"type":"text","text":"<JSON 字符串>"}]，拆成可直接校验的对象。

    高德给的是结构化 JSON，拆完就能做确定性校验；美团给的是 Markdown，
    拆完只会拿到 {"text": ...}，需要另写抽取器。
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, list) and result and isinstance(result[0], dict):
        text = result[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return {"text": str(result)}
