"""Agent 编排洋葱护栏中间件层。"""

from trippilot.middlewares.dangling_tool_call import patch_dangling_tool_calls
from trippilot.middlewares.tool_output import (
    MAX_TOOL_OUTPUT_CHARS,
    normalize_tool_content,
    unwrap_mcp_tool_call,
)

__all__ = [
    "MAX_TOOL_OUTPUT_CHARS",
    "normalize_tool_content",
    "patch_dangling_tool_calls",
    "unwrap_mcp_tool_call",
]
