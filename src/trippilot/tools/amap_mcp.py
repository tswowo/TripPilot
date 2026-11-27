"""高德官方 MCP 兼容门面（具体实现已解耦至 trippilot.tools.mcp_adapter 与 trippilot.tools.amap）。"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from trippilot.tools.amap.client import (
    AMAP_ADAPTATION_POLICY,
    _amap_tool_interceptor,
    _client_factory,
    build_client,
    load_amap_tools,
    load_curated_amap_tools,
)
from trippilot.tools.amap.specs import (
    AMAP_DESCRIPTION_PATCHES,
    AMAP_VIRTUAL_ARG_SCHEMAS,
    DEFAULT_SEARCH_COUNT,
    EXCLUDED_AMAP_TOOLS,
    LOW_PRECISION_GEO_LEVELS,
    MAX_MAIN_ROADS,
    MAX_SEARCH_COUNT,
    MAX_TRANSIT_ROUTES,
    SEARCH_COUNT_TOOLS,
)
from trippilot.tools.amap.transformers import (
    format_duration_cn,
    inject_geo_warning,
    sanitize_amap_payload,
    slice_search_pois,
    slim_path_direction,
    slim_transit_direction,
    transform_amap_payload,
)
from trippilot.tools.mcp_adapter import (
    enrich_tool_schema,
)
from trippilot.tools.mcp_adapter import (
    parse_mcp_text_payload as parse_mcp_payload,
)


def _enrich_tool_schema(tool: BaseTool) -> BaseTool:
    return enrich_tool_schema(tool, AMAP_ADAPTATION_POLICY)


__all__ = [
    "AMAP_ADAPTATION_POLICY",
    "AMAP_DESCRIPTION_PATCHES",
    "AMAP_VIRTUAL_ARG_SCHEMAS",
    "DEFAULT_SEARCH_COUNT",
    "EXCLUDED_AMAP_TOOLS",
    "LOW_PRECISION_GEO_LEVELS",
    "MAX_MAIN_ROADS",
    "MAX_SEARCH_COUNT",
    "MAX_TRANSIT_ROUTES",
    "SEARCH_COUNT_TOOLS",
    "_amap_tool_interceptor",
    "_client_factory",
    "_enrich_tool_schema",
    "build_client",
    "format_duration_cn",
    "inject_geo_warning",
    "load_amap_tools",
    "load_curated_amap_tools",
    "parse_mcp_payload",
    "sanitize_amap_payload",
    "slice_search_pois",
    "slim_path_direction",
    "slim_transit_direction",
    "transform_amap_payload",
]
