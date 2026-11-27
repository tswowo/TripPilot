"""LangGraph 编排护栏中间件层：
1. DanglingToolCallMiddleware：中断后悬空 tool_calls 自动补齐自愈；
2. ToolOutputMiddleware：MCP 列表内容块 UTF-8 展平（防 Unicode 转义膨胀）与超长字符预算兜底；
3. ContextCompactionMiddleware：两级渐进式上下文管理（Tier-1 历史轮次 0ms 脱水 + Tier-2 按轮次对齐的 LLM 摘要与 RemoveMessage 瘦身）。
"""

from trippilot.middlewares.context_compaction import (
    COMPACT_SUMMARY_FLAG,
    COMPACT_SUMMARY_MSG_ID,
    COMPACT_SUMMARY_PROMPT,
    DEFAULT_COMPACT_TRIGGER_CHARS,
    DEFAULT_FOLDED_TOOL_PREVIEW_CHARS,
    DEFAULT_KEEP_RAW_TOOL_TURNS,
    DEFAULT_KEEP_RECENT_TURNS,
    DEFAULT_MAX_OLD_TOOL_CHARS,
    CompactionResult,
    acompact_messages_if_needed,
    assemble_model_messages,
    dehydrate_historical_turns,
    estimate_messages_chars,
    find_turn_boundaries,
    is_compact_summary_message,
)
from trippilot.middlewares.dangling_tool_call import patch_dangling_tool_calls
from trippilot.middlewares.tool_output import (
    MAX_TOOL_OUTPUT_CHARS,
    normalize_tool_content,
    unwrap_mcp_tool_call,
)

__all__ = [
    "COMPACT_SUMMARY_FLAG",
    "COMPACT_SUMMARY_MSG_ID",
    "COMPACT_SUMMARY_PROMPT",
    "DEFAULT_COMPACT_TRIGGER_CHARS",
    "DEFAULT_FOLDED_TOOL_PREVIEW_CHARS",
    "DEFAULT_KEEP_RAW_TOOL_TURNS",
    "DEFAULT_KEEP_RECENT_TURNS",
    "DEFAULT_MAX_OLD_TOOL_CHARS",
    "MAX_TOOL_OUTPUT_CHARS",
    "CompactionResult",
    "acompact_messages_if_needed",
    "assemble_model_messages",
    "dehydrate_historical_turns",
    "estimate_messages_chars",
    "find_turn_boundaries",
    "is_compact_summary_message",
    "normalize_tool_content",
    "patch_dangling_tool_calls",
    "unwrap_mcp_tool_call",
]
