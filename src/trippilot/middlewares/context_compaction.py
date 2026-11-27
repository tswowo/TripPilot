"""两级渐进式上下文压缩与生命周期管理中间件（ContextCompactionMiddleware）。

针对重度工具调用（ReAct 多跳循环）场景下的上下文膨胀问题，提供两级治理：
1. Tier-1 零延迟历史轮次脱水（dehydrate_historical_turns，每次调模型必执行，0ms 不调 LLM）：
   - 严格区分“历史已完成 Turn”与“当前正在执行的 Turn”（以最后一条 HumanMessage 为界）；
   - 当前 Turn 内的 reasoning_content 与原始 ToolMessage 100% 完整保留，支撑当前多跳工具推理；
   - 历史已完成 Turn 自动剥离过时的 reasoning_content（对齐 DeepSeek 官方跨轮次规范），
     并将超过阈值（默认 >600 字符）的历史巨型 ToolMessage 折叠为精简回执摘要。
2. Tier-2 轮次对齐的 LLM 结构化摘要与 Checkpoint 原地瘦身（acompact_messages_if_needed）：
   - 当 Tier-1 脱水后总字符数仍超过阈值（默认 24,000 字符）且存在可归档的历史 Turn 时触发；
   - 切分点严格对齐到 HumanMessage 边界，绝不切碎 AIMessage(tool_calls) 与 ToolMessage 配对（防 400 报错）；
   - 调用轻量非思考模型增量提炼结构化《旅行上下文记忆摘要》，并通过 LangGraph RemoveMessage 原地清理旧状态；
   - 最终组装模型输入时，保持静态 SYSTEM_PROMPT 固定在首位，将历史摘要合并于静态指令尾部（单 SystemMessage），
     既保护大模型 Prefix KV Cache，又避免多 SystemMessage 引发网关兼容性报错。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)

COMPACT_SUMMARY_MSG_ID = "trippilot_context_summary"
COMPACT_SUMMARY_FLAG = "is_compact_summary"

DEFAULT_MAX_OLD_TOOL_CHARS = 600
DEFAULT_FOLDED_TOOL_PREVIEW_CHARS = 300
DEFAULT_KEEP_RAW_TOOL_TURNS = 0
DEFAULT_COMPACT_TRIGGER_CHARS = 24000
DEFAULT_KEEP_RECENT_TURNS = 2

COMPACT_SUMMARY_PROMPT = (
    "你是 TripPilot 旅行规划引擎的上下文记忆压缩器。请将给定的早期历史对话压缩为一份高密度、"
    "零事实丢失的【旅行上下文摘要】。\n"
    "必须完整保留以下四类结构化事实（禁止编造未出现的信息）：\n"
    "1. 【核心行程约束】：出发城市、目的地、具体日期/天数、同行人数与人群特征、预算上限；\n"
    "2. 【已核实交通与酒旅数据】：已查实的航班号/高铁车次、起降时刻、真实票价、候选酒店名称、价格区间与位置；\n"
    "3. 【已核实景点与空间数据】：权威 POI ID、精确经纬度坐标、通勤耗时、营业时间、闭馆日与预约放票规则；\n"
    "4. 【用户偏好与决策演进】：用户明确采纳或已否决的方案、饮食/住宿/节奏特殊要求。\n"
    "若输入中包含【既有历史摘要】，请将其与新增历史对话增量合并，直接输出更新后的摘要正文（控制在 800 字以内）。"
)


@dataclass
class CompactionResult:
    """单次上下文治理结果：同时提供喂给 LLM 的干净消息列表与需回写 Checkpointer 的状态增量。"""

    model_messages: list[BaseMessage]
    state_updates: list[BaseMessage] = field(default_factory=list)
    compacted: bool = False
    before_chars: int = 0
    after_chars: int = 0


def is_compact_summary_message(msg: BaseMessage) -> bool:
    """判断消息是否为持久化在 Checkpoint 中的历史压缩摘要。"""
    if getattr(msg, "id", None) == COMPACT_SUMMARY_MSG_ID:
        return True
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    return bool(kwargs.get(COMPACT_SUMMARY_FLAG))


def estimate_messages_chars(messages: Sequence[BaseMessage]) -> int:
    """估算消息序列的总字符开销（涵盖 content、reasoning_content 与 tool_calls 参数）。"""
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += sum(len(str(part)) for part in content)

        kwargs = getattr(msg, "additional_kwargs", None) or {}
        if reasoning := kwargs.get("reasoning_content"):
            total += len(str(reasoning))

        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                total += len(str(tc.get("name", ""))) + len(str(tc.get("args", "")))
    return total


def find_turn_boundaries(messages: Sequence[BaseMessage]) -> list[int]:
    """定位所有 HumanMessage 的起始下标，确保任何切分点严格对齐完整的用户轮次边界。"""
    return [idx for idx, msg in enumerate(messages) if isinstance(msg, HumanMessage)]


def dehydrate_historical_turns(
    messages: Sequence[BaseMessage],
    *,
    keep_raw_tool_turns: int = DEFAULT_KEEP_RAW_TOOL_TURNS,
    max_old_tool_chars: int = DEFAULT_MAX_OLD_TOOL_CHARS,
    folded_preview_chars: int = DEFAULT_FOLDED_TOOL_PREVIEW_CHARS,
) -> list[BaseMessage]:
    """Tier-1（0ms 零延迟）：剥离历史已完成 Turn 的 reasoning_content 并折叠超长历史 ToolMessage。

    当前正在执行的最后一个 Turn（最后一条 HumanMessage 及其后的所有消息）100% 原样保留。
    """
    turn_starts = find_turn_boundaries(messages)
    if not turn_starts:
        return list(messages)

    current_turn_start = turn_starts[-1]
    # 允许保留最近 keep_raw_tool_turns 个已完成历史 Turn 的原始 ToolMessage 不折叠
    fold_cutoff_turn_idx = max(0, len(turn_starts) - 1 - max(0, keep_raw_tool_turns))
    fold_tool_before_idx = turn_starts[fold_cutoff_turn_idx]

    result: list[BaseMessage] = []
    for idx, msg in enumerate(messages):
        # 当前正在执行的 Turn：100% 原样保留
        if idx >= current_turn_start:
            result.append(msg)
            continue

        # 历史已完成 Turn 中的 AIMessage：剥离过时的 reasoning_content
        if isinstance(msg, AIMessage):
            kwargs = dict(msg.additional_kwargs or {})
            if "reasoning_content" in kwargs:
                kwargs.pop("reasoning_content", None)
                result.append(msg.model_copy(update={"additional_kwargs": kwargs}))
            else:
                result.append(msg)
            continue

        # 历史已完成 Turn 中的超长 ToolMessage：折叠中间原始大载荷，保留头部预览
        if (
            idx < fold_tool_before_idx
            and isinstance(msg, ToolMessage)
            and isinstance(msg.content, str)
            and len(msg.content) > max_old_tool_chars
        ):
            orig_len = len(msg.content)
            preview = msg.content[:folded_preview_chars].rstrip()
            tool_label = msg.name or "tool"
            folded_text = (
                f"{preview}\n...[历史工具 {tool_label} 原始输出共 {orig_len} 字符已折叠]"
            )
            result.append(msg.model_copy(update={"content": folded_text}))
            continue

        result.append(msg)

    return result


def _format_messages_for_summary(
    existing_summary: str,
    messages_to_summarize: Sequence[BaseMessage],
) -> str:
    """将待归档的历史轮次整理为紧凑的对话脚本供摘要模型提炼。"""
    lines: list[str] = []
    if existing_summary.strip():
        lines.append(f"【既有历史摘要】\n{existing_summary.strip()}\n")

    lines.append("【新增待归档对话】")
    for msg in messages_to_summarize:
        if isinstance(msg, HumanMessage):
            lines.append(f"用户: {str(msg.content).strip()}")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                tool_desc = ", ".join(
                    f"{tc.get('name')}({tc.get('args')})" for tc in msg.tool_calls
                )
                lines.append(f"助手调用工具: {tool_desc}")
            if msg.content and str(msg.content).strip():
                lines.append(f"助手回答: {str(msg.content).strip()}")
        elif isinstance(msg, ToolMessage):
            snippet = str(msg.content).strip()
            if len(snippet) > 500:
                snippet = f"{snippet[:500]}...[已截断]"
            lines.append(f"工具返回({msg.name or 'tool'}): {snippet}")

    return "\n".join(lines)


def assemble_model_messages(
    system_prompt: str,
    conversation_messages: Sequence[BaseMessage],
    *,
    summary_text: str = "",
) -> list[BaseMessage]:
    """组装最终发往大模型的消息列表：
    1. 静态 system_prompt 永远位于单个 SystemMessage 的起始前缀，保护 Prefix KV Cache；
    2. 如有历史压缩摘要，追加在静态 system_prompt 尾部（不产生第二个 SystemMessage，防网关 400）。
    """
    clean_summary = summary_text.strip()
    if clean_summary:
        merged_system = (
            f"{system_prompt}\n\n"
            f"<trip_context_summary>\n"
            f"以下是本会话早期轮次已确认的旅行上下文摘要，请在后续规划中严格继承这些事实与约束：\n"
            f"{clean_summary}\n"
            f"</trip_context_summary>"
        )
    else:
        merged_system = system_prompt

    non_summary = [m for m in conversation_messages if not is_compact_summary_message(m)]
    return [SystemMessage(content=merged_system)] + non_summary


async def acompact_messages_if_needed(
    messages: Sequence[BaseMessage],
    *,
    system_prompt: str,
    summarizer_llm: Runnable | None = None,
    trigger_chars: int = DEFAULT_COMPACT_TRIGGER_CHARS,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
    keep_raw_tool_turns: int = DEFAULT_KEEP_RAW_TOOL_TURNS,
    max_old_tool_chars: int = DEFAULT_MAX_OLD_TOOL_CHARS,
) -> CompactionResult:
    """执行 Tier-1 零延迟脱水，并在超阈值时触发 Tier-2 轮次对齐 LLM 摘要与 Checkpoint RemoveMessage 瘦身。"""
    before_chars = estimate_messages_chars(messages)

    existing_summaries: list[str] = []
    dialogue_messages: list[BaseMessage] = []
    for msg in messages:
        if is_compact_summary_message(msg):
            if isinstance(msg.content, str) and msg.content.strip():
                existing_summaries.append(msg.content.strip())
        else:
            dialogue_messages.append(msg)

    current_summary = "\n".join(existing_summaries)

    # 第一级：0ms 零延迟脱水（剥离旧 reasoning_content + 折叠旧 ToolMessage）
    dehydrated = dehydrate_historical_turns(
        dialogue_messages,
        keep_raw_tool_turns=keep_raw_tool_turns,
        max_old_tool_chars=max_old_tool_chars,
    )
    dehydrated_chars = estimate_messages_chars(dehydrated) + len(current_summary)
    turn_starts = find_turn_boundaries(dehydrated)

    # 判断是否需要触发第二级 LLM 结构化摘要：
    # 条件：提供 summarizer_llm + 脱水后字符数仍超阈值 + 历史已完成 Turn 数量 > keep_recent_turns
    min_turns_for_compact = max(1, keep_recent_turns) + 1
    if (
        summarizer_llm is None
        or dehydrated_chars <= trigger_chars
        or len(turn_starts) < min_turns_for_compact
    ):
        model_msgs = assemble_model_messages(
            system_prompt,
            dehydrated,
            summary_text=current_summary,
        )
        return CompactionResult(
            model_messages=model_msgs,
            state_updates=[],
            compacted=False,
            before_chars=before_chars,
            after_chars=dehydrated_chars,
        )

    # 切分点严格对齐到倒数第 (keep_recent_turns + 1) 个 HumanMessage 边界
    cutoff_turn_idx = len(turn_starts) - 1 - max(0, keep_recent_turns)
    split_pos = turn_starts[cutoff_turn_idx]
    to_summarize = dehydrated[:split_pos]
    kept_dialogue = dehydrated[split_pos:]

    transcript = _format_messages_for_summary(current_summary, to_summarize)
    try:
        summary_resp: Any = await summarizer_llm.ainvoke(
            [
                SystemMessage(content=COMPACT_SUMMARY_PROMPT),
                HumanMessage(content=transcript),
            ]
        )
        new_summary_text = str(getattr(summary_resp, "content", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("上下文压缩 LLM 摘要调用失败，优雅降级为 Tier-1 脱水视图: %s", exc)
        model_msgs = assemble_model_messages(
            system_prompt,
            dehydrated,
            summary_text=current_summary,
        )
        return CompactionResult(
            model_messages=model_msgs,
            state_updates=[],
            compacted=False,
            before_chars=before_chars,
            after_chars=dehydrated_chars,
        )

    if not new_summary_text:
        new_summary_text = current_summary

    summary_msg = SystemMessage(
        id=COMPACT_SUMMARY_MSG_ID,
        content=new_summary_text,
        additional_kwargs={COMPACT_SUMMARY_FLAG: True},
    )
    removals: list[BaseMessage] = [
        RemoveMessage(id=str(m.id))
        for m in dialogue_messages[:split_pos]
        if getattr(m, "id", None)
    ]
    state_updates: list[BaseMessage] = [*removals, summary_msg]

    model_msgs = assemble_model_messages(
        system_prompt,
        kept_dialogue,
        summary_text=new_summary_text,
    )
    after_chars = estimate_messages_chars(kept_dialogue) + len(new_summary_text)
    logger.info(
        "已触发 Tier-2 上下文压缩：归档 %d 条历史消息，字符数 %d -> %d",
        len(to_summarize),
        before_chars,
        after_chars,
    )
    return CompactionResult(
        model_messages=model_msgs,
        state_updates=state_updates,
        compacted=True,
        before_chars=before_chars,
        after_chars=after_chars,
    )
