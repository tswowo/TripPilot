"""工具输出规范化与预算控制中间件（ToolOutputMiddleware）。

挂载于 LangGraph ToolNode 的 awrap_tool_call 洋葱切面：
1. 将 MCP 适配器返回的 [{'type': 'text', 'text': '...'}] 块列表展平为纯 UTF-8 字符串，
   防止 ChatOpenAI / ChatDeepSeek 序列化 list content 时触发 json.dumps(ensure_ascii=True)
   将中文转义为 \\uXXXX 导致 Token 暴涨 3 倍；
2. 对单次工具输出实施最大字符预算兜底截断（MAX_TOOL_OUTPUT_CHARS）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import ToolMessage

MAX_TOOL_OUTPUT_CHARS = 12000


def normalize_tool_content(
    content: Any,
    *,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> str:
    """将工具返回的 str / list 内容块统一规整为 UTF-8 字符串并限制最大字符预算。"""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        text = "\n".join(parts)
    else:
        text = str(content)

    if len(text) > max_chars:
        return f"{text[:max_chars]}\n\n...[工具输出超过 {max_chars} 字符，已截断]"
    return text


async def unwrap_mcp_tool_call(
    request: Any,
    execute: Callable[[Any], Awaitable[Any]],
) -> Any:
    """ToolNode 洋葱拦截器：统一将 MCP 内容块展平为干净字符串并执行长度预算兜底。"""
    result = await execute(request)
    if isinstance(result, ToolMessage) and not isinstance(result.content, str):
        return result.model_copy(update={"content": normalize_tool_content(result.content)})
    if isinstance(result, ToolMessage) and len(result.content) > MAX_TOOL_OUTPUT_CHARS:
        return result.model_copy(update={"content": normalize_tool_content(result.content)})
    return result
