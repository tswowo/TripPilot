"""两级意图识别与槽位门禁（intent.py）及后置行程质检（evaluator.py）离线单元测试。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

from trippilot.graph.evaluator import (
    aevaluate_itinerary,
    build_self_healing_prompt,
    deterministic_redline_check,
)
from trippilot.graph.intent import (
    IntentClassification,
    TravelSlots,
    aclassify_intent,
    enforce_minimal_slot_gate,
    fast_rule_classify,
    inject_ephemeral_intent_hint,
)
from trippilot.graph.stream import stream_graph_events


def test_fast_rule_classify_chitchat_and_vague_clarify():
    # 1. 0ms 问候闲聊快筛
    res_hi = fast_rule_classify([HumanMessage(content="你好！")])
    assert res_hi is not None
    assert res_hi.intent == "chitchat"

    # 2. 0ms 首轮空泛规划请求直接拦截转 clarify
    res_vague = fast_rule_classify([HumanMessage(content="帮我做个旅游攻略")])
    assert res_vague is not None
    assert res_vague.intent == "clarify"
    assert set(res_vague.missing_hard_slots) == {"destination", "origin", "date_or_days"}
    assert "目的地" in res_vague.clarify_question

    # 3. 0ms 单点天气/路线快查
    res_weather = fast_rule_classify([HumanMessage(content="南京明天天气怎么样？")])
    assert res_weather is not None
    assert res_weather.intent == "quick_lookup"

    # 4. 0ms 多轮行程微调
    res_refine = fast_rule_classify(
        [
            HumanMessage(content="哈尔滨去南京玩3天"),
            AIMessage(content="这是为您规划的3天行程..."),
            HumanMessage(content="把酒店换成800一晚的"),
        ]
    )
    assert res_refine is not None
    assert res_refine.intent == "refinement"


def test_minimal_slot_gate_does_not_nag_for_soft_slots():
    # 当目的地、出发地、时间/天数三个硬槽位齐全，但没提预算、人数、偏好时，绝不拦截追问！
    cls_input = IntentClassification(
        intent="clarify",
        slots=TravelSlots(
            origin="哈尔滨",
            destination="南京",
            date_or_days="下周末3天",
            travelers="",
            budget="",
            preferences="",
        ),
        missing_hard_slots=[],
        clarify_question="请问您的预算是多少？",
    )
    calibrated = enforce_minimal_slot_gate(cls_input)
    assert calibrated.intent == "full_planning"
    assert calibrated.missing_hard_slots == []
    assert calibrated.clarify_question == ""


@pytest.mark.asyncio
async def test_aclassify_intent_downgrades_to_clarify_when_hard_slot_missing():
    # 用户只说了去南京玩，缺出发地和出行时间
    fake_llm = RunnableLambda(
        lambda _msgs: AIMessage(
            content=(
                '{"intent": "full_planning", '
                '"slots": {"origin": "", "destination": "南京", "date_or_days": ""}, '
                '"missing_hard_slots": ["origin", "date_or_days"]}'
            )
        )
    )
    res = await aclassify_intent(
        [HumanMessage(content="我想去南京玩，帮我排一套完整的旅行计划")],
        classifier_llm=fake_llm,
    )
    assert res.intent == "clarify"
    assert "origin" in res.missing_hard_slots
    assert "date_or_days" in res.missing_hard_slots
    assert "南京" in res.clarify_question


def test_inject_ephemeral_intent_hint_preserves_prefix_and_original_messages():
    orig_sys = SystemMessage(content="核心系统提示词")
    orig_h1 = HumanMessage(content="第一轮历史问题")
    orig_a1 = AIMessage(content="第一轮历史回答")
    orig_h2 = HumanMessage(content="下周末从哈尔滨去南京玩3天")
    state_msgs = [orig_sys, orig_h1, orig_a1, orig_h2]

    cls_obj = IntentClassification(
        intent="full_planning",
        slots=TravelSlots(origin="哈尔滨", destination="南京", date_or_days="下周末3天"),
    )
    injected = inject_ephemeral_intent_hint(state_msgs, cls_obj)

    # 1. 第 0 条 SystemMessage 与历史消息引用/内容 100% 不变（保护 Prefix KV Cache）
    assert injected[0].content == "核心系统提示词"
    assert injected[1].content == "第一轮历史问题"
    assert injected[2].content == "第一轮历史回答"

    # 2. 原 state_msgs 中的最后一条 HumanMessage 未被原地篡改（不污染持久化数据库）
    assert orig_h2.content == "下周末从哈尔滨去南京玩3天"

    # 3. 传给模型的内存副本最后一条末尾带上了路由提示
    assert "系统路由提示" in str(injected[-1].content)
    assert "出发地=哈尔滨" in str(injected[-1].content)


def test_deterministic_redline_check_catches_budget_overrun():
    itinerary = (
        "## 一、行程方案\n"
        "往返机票、住宿与餐饮安排如下...\n"
        "## 二、预算核算\n"
        "| 项目 | 金额 |\n"
        "| 合计 | 约 ¥7,240 |\n"
    )
    res = deterministic_redline_check(itinerary, budget_slot="5000元")
    assert res is not None
    assert res.passed is False
    assert res.score < 80
    assert any("预算超支" in issue for issue in res.issues)


@pytest.mark.asyncio
async def test_aevaluate_itinerary_catches_monday_closure_conflict():
    fake_eval_llm = RunnableLambda(
        lambda _msgs: AIMessage(
            content=(
                '{"score": 55, "passed": false, '
                '"issues": ["闭馆日冲突：南京博物院周一闭馆，但方案将其排在了周一上午"], '
                '"fix_instruction": "将南京博物院调整至周日游玩，周一改为直接前往机场"}'
            )
        )
    )
    msgs = [
        HumanMessage(content="下周末从哈尔滨去南京玩3天"),
        AIMessage(content="这是一份超过八十个字符的完整行程方案草案：周六抵达南京逛夫子庙，周日逛玄武湖，周一上午去参观南京博物院，下午乘机返回哈尔滨。预算合计约 ¥4,500。"),
    ]
    res = await aevaluate_itinerary(
        msgs,
        intent_info={"slots": {"budget": "10000元"}},
        evaluator_llm=fake_eval_llm,
    )
    assert res.passed is False
    assert res.score == 55
    healing = build_self_healing_prompt(res.fix_instruction, res.issues)
    assert "行程质检自动修正说明" in healing


@pytest.mark.asyncio
async def test_stream_graph_events_filters_internal_llm_and_emits_clarify_delta():
    """验证 stream_graph_events：
    1. 严格过滤带 internal_llm 标签或来自 intent_router / evaluator 节点的内部 JSON Token；
    2. 对 clarify 节点（0ms Python 直出）自动吐出标准 delta 事件。
    """

    class FakeGraph:
        async def astream(self, _inputs: Any, _config: Any, stream_mode: Any):
            assert "messages" in stream_mode and "updates" in stream_mode
            # 模拟 intent_router 内部 LLM 流出的 JSON chunk（必须被过滤！）
            yield (
                "messages",
                (
                    AIMessageChunk(content='{"intent": "clarify"}'),
                    {"tags": ["internal_llm"], "langgraph_node": "intent_router"},
                ),
            )
            # 模拟 clarify 节点返回追问消息
            yield (
                "updates",
                {
                    "clarify": {
                        "messages": [AIMessage(content="请问您计划从哪个城市出发、哪天出行？")]
                    }
                },
            )

    events = [
        ev
        async for ev in stream_graph_events(
            FakeGraph(),
            thread_id="t_test",
            user_message="我想去南京玩",
        )
    ]

    deltas = [payload for kind, payload in events if kind == "delta"]
    assert len(deltas) == 1
    assert deltas[0]["content"] == "请问您计划从哪个城市出发、哪天出行？"
