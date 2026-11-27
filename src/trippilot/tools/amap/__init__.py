"""高德官方 MCP 接入与治理子包。"""

from trippilot.tools.amap.client import (
    AMAP_ADAPTATION_POLICY,
    build_client,
    load_amap_tools,
    load_curated_amap_tools,
)
from trippilot.tools.amap.specs import (
    AMAP_DESCRIPTION_PATCHES,
    AMAP_VIRTUAL_ARG_SCHEMAS,
    EXCLUDED_AMAP_TOOLS,
    LOW_PRECISION_GEO_LEVELS,
    SEARCH_COUNT_TOOLS,
)
from trippilot.tools.amap.transformers import (
    inject_geo_warning,
    sanitize_amap_payload,
    slice_search_pois,
    slim_path_direction,
    slim_transit_direction,
    transform_amap_payload,
)
from trippilot.tools.mcp_adapter import parse_mcp_text_payload as parse_mcp_payload

__all__ = [
    "AMAP_ADAPTATION_POLICY",
    "AMAP_DESCRIPTION_PATCHES",
    "AMAP_VIRTUAL_ARG_SCHEMAS",
    "EXCLUDED_AMAP_TOOLS",
    "LOW_PRECISION_GEO_LEVELS",
    "SEARCH_COUNT_TOOLS",
    "build_client",
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
