"""悬空工具调用自愈中间件（DanglingToolCallMiddleware）。

当用户在工具执行中途点击“停止生成”或网络闪断时，Checkpointer 中可能残留已落盘的
AIMessage(tool_calls=[...])，而缺少配对的 ToolMessage。
在下一次调用大模型前，通过本中间件在内存视图中动态补齐取消态 ToolMessage，
避免 OpenAI / DeepSeek 兼容网关抛出 400 Bad Request。
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def patch_dangling_tool_calls(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """为带有 tool_calls 的悬空 AIMessage 补齐取消态 ToolMessage，实现会话无损自愈。"""
    existing_tool_ids = {
        m.tool_call_id for m in messages if isinstance(m, ToolMessage) and m.tool_call_id
    }
    repaired: list[BaseMessage] = []
    for msg in messages:
        repaired.append(msg)
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_ids:
                    repaired.append(
                        ToolMessage(
                            content="该工具调用因用户中途停止生成而被取消。",
                            tool_call_id=tc_id,
                            name=tc.get("name") or "unknown",
                            status="error",
                        )
                    )
                    existing_tool_ids.add(tc_id)
    return repaired
