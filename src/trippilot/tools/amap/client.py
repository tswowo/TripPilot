"""高德官方 MCP 客户端连接与策略装配（Streamable HTTP）。"""

from __future__ import annotations

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from trippilot.config import Settings, get_settings
from trippilot.tools.amap.specs import (
    AMAP_DESCRIPTION_PATCHES,
    AMAP_VIRTUAL_ARG_SCHEMAS,
    EXCLUDED_AMAP_TOOLS,
)
from trippilot.tools.amap.transformers import adapt_amap_payload
from trippilot.tools.mcp_adapter import (
    MCPAdaptationPolicy,
    build_policy_interceptor,
    curate_mcp_tools,
)

AMAP_ADAPTATION_POLICY = MCPAdaptationPolicy(
    excluded_tools=EXCLUDED_AMAP_TOOLS,
    description_patches=AMAP_DESCRIPTION_PATCHES,
    virtual_arg_schemas=AMAP_VIRTUAL_ARG_SCHEMAS,
    payload_transformer=adapt_amap_payload,
)

_amap_tool_interceptor = build_policy_interceptor(AMAP_ADAPTATION_POLICY)


def _client_factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
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
        },
        tool_interceptors=[_amap_tool_interceptor],
    )


async def load_amap_tools(settings: Settings | None = None) -> list[BaseTool]:
    return await build_client(settings).get_tools()


async def load_curated_amap_tools(settings: Settings | None = None) -> list[BaseTool]:
    """拉取高德 MCP 工具，并交由通用 MCP 治理底座执行黑名单过滤、Schema 增强与字典序排列。"""
    raw_tools = await load_amap_tools(settings)
    return curate_mcp_tools(raw_tools, AMAP_ADAPTATION_POLICY)
