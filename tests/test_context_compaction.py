"""两级渐进式上下文压缩中间件（ContextCompactionMiddleware）离线单测。

验证四大核心契约：
1. Tier-1 历史轮次 0ms 脱水：剥离已完成历史 Turn 的 reasoning_content、折叠超长历史 ToolMessage，
   同时 100% 完整保留当前正在执行的最新 Turn 的 reasoning_content 与 ToolMessage；
2. 轮次边界对齐（find_turn_boundaries）：任何切分点严格落在 HumanMessage 上，绝不切碎 AIMessage(tool_calls) 与 ToolMessage；
3. Tier-2 超阈值 LLM 结构化摘要与 RemoveMessage 状态机瘦身（含增量合并既有摘要与 LLM 异常降级）；
4. 静态 SYSTEM_PROMPT 前缀保护（assemble_model_messages）：单 SystemMessage 首位固定静态指令，保护 Prefix KV Cache。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages

from trippilot.middlewares.context_compaction import (
    COMPACT_SUMMARY_FLAG,
    COMPACT_SUMMARY_MSG_ID,
    acompact_messages_if_needed,
    assemble_model_messages,
    dehydrate_historical_turns,
    find_turn_boundaries,
    is_compact_summary_message,
)


def _build_two_turn_history():
    """构造 2 轮对话：第 1 轮已完成（含大段 reasoning 与巨型 ToolMessage），第 2 轮正在进行中。"""
    big_tool_payload = "酒店A: 580元/晚\n" + ("详情数据填充行 " * 120)
    current_tool_payload = "景点B: 周一闭馆\n" + ("当前轮次工具详情 " * 120)

    return [
        # Turn 1 (已完成历史轮次)
        HumanMessage(id="h1", content="下周末哈尔滨去南京两人游"),
        AIMessage(
            id="a1_call",
            content="先查酒店",
            additional_kwargs={"reasoning_content": "第一轮长达几千字的历史思考链..." * 40},
            tool_calls=[{"id": "tc_1", "name": "meituan_travel_search", "args": {"city": "南京"}}],
        ),
        ToolMessage(
            id="t1",
            content=big_tool_payload,
            name="meituan_travel_search",
            tool_call_id="tc_1",
        ),
        AIMessage(
            id="a1_final",
            content="已为您查到夫子庙亚朵酒店约 580 元/晚。",
            additional_kwargs={"reasoning_content": "第一轮总结思考链..."},
        ),
        # Turn 2 (当前正在执行的轮次)
        HumanMessage(id="h2", content="南京博物院什么时间开馆？"),
        AIMessage(
            id="a2_call",
            content="",
            additional_kwargs={"reasoning_content": "当前第二轮正在进行的思考链，必须完整保留！"},
            tool_calls=[{"id": "tc_2", "name": "maps_search_detail", "args": {"id": "B001907TGR"}}],
        ),
        ToolMessage(
            id="t2",
            content=current_tool_payload,
            name="maps_search_detail",
            tool_call_id="tc_2",
        ),
    ]


def test_tier1_dehydrate_strips_old_reasoning_and_folds_old_tools_while_keeping_current_turn():
    history = _build_two_turn_history()
    dehydrated = dehydrate_historical_turns(
        history,
        keep_raw_tool_turns=0,
        max_old_tool_chars=200,
        folded_preview_chars=80,
    )

    # 1. 原消息对象不可被原地修改（Immutability）
    assert "reasoning_content" in history[1].additional_kwargs
    assert len(history[2].content) > 600

    # 2. 历史 Turn 1 的 reasoning_content 已全部剥离，且超长 ToolMessage 已折叠
    assert "reasoning_content" not in dehydrated[1].additional_kwargs
    assert "reasoning_content" not in dehydrated[3].additional_kwargs
    assert "历史工具 meituan_travel_search 原始输出共" in dehydrated[2].content
    assert len(dehydrated[2].content) < 200
    assert dehydrated[2].content.startswith("酒店A: 580元/晚")

    # 3. 当前 Turn 2（h2, a2_call, t2）的 reasoning_content 与巨型 ToolMessage 100% 完整保留
    assert dehydrated[4].content == "南京博物院什么时间开馆？"
    assert (
        dehydrated[5].additional_kwargs.get("reasoning_content")
        == "当前第二轮正在进行的思考链，必须完整保留！"
    )
    assert dehydrated[6].content == history[6].content


def test_turn_boundaries_strictly_align_with_human_messages():
    history = _build_two_turn_history()
    assert find_turn_boundaries(history) == [0, 4]


def test_assemble_model_messages_preserves_static_system_prefix_and_single_system_message():
    static_sys = "你是 TripPilot 智能旅行规划助手。"
    msgs = [
        SystemMessage(
            id=COMPACT_SUMMARY_MSG_ID,
            content="【核心行程约束】哈尔滨->南京，11/28-11/30，两人预算19999元。",
            additional_kwargs={COMPACT_SUMMARY_FLAG: True},
        ),
        HumanMessage(id="h3", content="帮我把酒店换成新街口"),
    ]
    assembled = assemble_model_messages(
        static_sys,
        msgs,
        summary_text="【核心行程约束】哈尔滨->南京，11/28-11/30，两人预算19999元。",
    )
    # 只有一个 SystemMessage 且严格位于 index 0，静态 SYSTEM_PROMPT 为精确前缀
    system_msgs = [m for m in assembled if isinstance(m, SystemMessage)]
    assert len(system_msgs) == 1
    assert isinstance(assembled[0], SystemMessage)
    assert assembled[0].content.startswith(static_sys)
    assert "<trip_context_summary>" in assembled[0].content
    assert isinstance(assembled[1], HumanMessage)


class _FakeSummarizer:
    def __init__(self, summary_reply: str = "【归档摘要】哈尔滨至南京2人游，已选HO1897航班与夫子庙亚朵。"):
        self.summary_reply = summary_reply
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self.summary_reply)


@pytest.mark.asyncio
async def test_tier2_compaction_triggers_at_turn_boundary_and_emits_remove_messages():
    # 构造 4 个 Turn（Turn 1, Turn 2, Turn 3 已完成，Turn 4 正在进行），设置 keep_recent_turns=1
    messages = [
        # Turn 1 (应被归档并 RemoveMessage)
        HumanMessage(id="h1", content="下周末哈尔滨去南京两人游，预算19999"),
        AIMessage(
            id="a1_tc",
            content="",
            tool_calls=[{"id": "tc1", "name": "meituan_travel_search", "args": {"query": "机票"}}],
        ),
        ToolMessage(id="t1", content="HO1897 ¥1025", name="meituan_travel_search", tool_call_id="tc1"),
        AIMessage(id="a1_ans", content="去程推荐吉祥航空 HO1897，单人 1025 元。" * 20),
        # Turn 2 (应被归档并 RemoveMessage)
        HumanMessage(id="h2", content="酒店住夫子庙附近哪里好？"),
        AIMessage(id="a2_ans", content="推荐夫子庙秦淮游船码头亚朵酒店，约 580 元/晚。" * 20),
        # Turn 3 (最近 1 个已完成历史 Turn，应完整保留)
        HumanMessage(id="h3", content="周日去南京博物院还是总统府？"),
        AIMessage(id="a3_ans", content="建议周日上午去南京博物院，需提前7天在小程序预约。"),
        # Turn 4 (当前正在执行的 Turn，应完整保留)
        HumanMessage(id="h4", content="那帮我算下从亚朵酒店到南京博物院的地铁耗时"),
    ]

    summarizer = _FakeSummarizer()
    res = await acompact_messages_if_needed(
        messages,
        system_prompt="STATIC_SYS_PROMPT",
        summarizer_llm=summarizer,
        trigger_chars=300,  # 低阈值以触发 Tier-2
        keep_recent_turns=1,
    )

    assert res.compacted is True
    assert len(summarizer.calls) == 1

    # 验证 RemoveMessage 仅移除 Turn 1 和 Turn 2 的 6 条消息（h1, a1_tc, t1, a1_ans, h2, a2_ans）
    removed_ids = [m.id for m in res.state_updates if isinstance(m, RemoveMessage)]
    assert removed_ids == ["h1", "a1_tc", "t1", "a1_ans", "h2", "a2_ans"]

    # 验证生成了固定 ID 的持久化摘要消息
    summary_updates = [m for m in res.state_updates if is_compact_summary_message(m)]
    assert len(summary_updates) == 1
    assert summary_updates[0].id == COMPACT_SUMMARY_MSG_ID

    # 验证发往大模型的消息列表：[SystemMessage(STATIC_SYS_PROMPT + summary), h3, a3_ans, h4]
    assert len(res.model_messages) == 4
    assert isinstance(res.model_messages[0], SystemMessage)
    assert res.model_messages[0].content.startswith("STATIC_SYS_PROMPT")
    assert "【归档摘要】" in res.model_messages[0].content
    assert [m.id for m in res.model_messages[1:]] == ["h3", "a3_ans", "h4"]

    # 验证通过 LangGraph add_messages reducer 应用 state_updates 后状态干净且幂等
    new_state = add_messages(messages, [*res.state_updates, AIMessage(id="a4_ans", content="地铁约35分钟")])
    remaining_ids = [m.id for m in new_state]
    assert "h1" not in remaining_ids and "t1" not in remaining_ids and "h2" not in remaining_ids
    assert COMPACT_SUMMARY_MSG_ID in remaining_ids
    assert "h3" in remaining_ids and "h4" in remaining_ids and "a4_ans" in remaining_ids


@pytest.mark.asyncio
async def test_tier2_compaction_gracefully_falls_back_when_summarizer_raises():
    class _BrokenSummarizer:
        async def ainvoke(self, _messages: Any):
            raise RuntimeError("LLM network timeout")

    messages = [
        HumanMessage(id="h1", content="第一轮" * 100),
        AIMessage(id="a1", content="第一轮回答" * 100),
        HumanMessage(id="h2", content="第二轮" * 100),
        AIMessage(id="a2", content="第二轮回答" * 100),
        HumanMessage(id="h3", content="第三轮提问"),
    ]
    res = await acompact_messages_if_needed(
        messages,
        system_prompt="SYS",
        summarizer_llm=_BrokenSummarizer(),
        trigger_chars=100,
        keep_recent_turns=1,
    )
    assert res.compacted is False
    assert res.state_updates == []
    assert len(res.model_messages) == 6
