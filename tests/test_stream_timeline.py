"""多轮交错 Timeline 组装器、工具参数摘要与流式事件过滤单测。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from trippilot.graph.stream import (
    TimelineAssembler,
    build_step_record,
    stream_graph_events,
    summarize_tool_args,
)


def test_summarize_tool_args_prioritizes_city_and_keywords():
    assert summarize_tool_args({"city": "上海", "keywords": "上海天文馆", "count": 5}) == "上海天文馆"
    assert summarize_tool_args({"city": "南京", "keywords": "夫子庙"}) == "南京 · 夫子庙"
    assert summarize_tool_args({"origin": "121.49,31.24", "destination": "121.91,30.91"}) == (
        "121.49,31.24 → 121.91,30.91"
    )
    assert summarize_tool_args({"city": "杭州"}) == "杭州"
    long_q = "帮我查询明天从上海虹桥出发前往北京南站的高铁二等座所有车次和余票"
    summary = summarize_tool_args({"query": long_q}, max_len=16)
    assert len(summary) == 17 and summary.endswith("…")


def test_build_step_record_includes_call_ids_and_summaries():
    ai_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_weather_1",
                "name": "maps_weather",
                "args": {"city": "南京"},
            },
            {
                "id": "call_search_2",
                "name": "maps_text_search",
                "args": {"city": "南京", "keywords": "南京博物院"},
            },
        ],
    )
    rec = build_step_record(1, "model", {"messages": [ai_msg]})
    assert rec["action"] == "call_tools"
    assert rec["tools"] == ["maps_weather", "maps_text_search"]
    assert rec["calls"] == [
        {"id": "call_weather_1", "name": "maps_weather", "summary": "南京"},
        {"id": "call_search_2", "name": "maps_text_search", "summary": "南京博物院"},
    ]

    tool_msgs = [
        ToolMessage(content="晴", name="maps_weather", tool_call_id="call_weather_1", status="success"),
        ToolMessage(content="error", name="maps_text_search", tool_call_id="call_search_2", status="error"),
    ]
    res_rec = build_step_record(2, "tools", {"messages": tool_msgs})
    assert res_rec["action"] == "tool_results"
    assert res_rec["results"] == [
        {"id": "call_weather_1", "tool": "maps_weather", "status": "success"},
        {"id": "call_search_2", "tool": "maps_text_search", "status": "error"},
    ]


def test_timeline_assembler_interleaves_rounds_and_isolates_thinking_timers():
    tl = TimelineAssembler()

    # 第 1 轮思考（耗时 3 秒：t=1000ms -> t=4000ms）
    tl.on_delta({"reasoning_content": "先查南京天气"}, now_ms=1000)
    tl.on_delta({"reasoning_content": "和夫子庙坐标"}, now_ms=4000)
    tl.on_delta({"thinking_done": True}, now_ms=4000)

    # 第 1 轮发起工具调用（t=5000ms，中间 1s 的 JSON 参数生成不计入思考耗时）
    tl.on_step(
        {
            "step": 1,
            "node": "model",
            "action": "call_tools",
            "ts_ms": 5000,
            "calls": [
                {"id": "c1", "name": "maps_weather", "summary": "南京"},
                {"id": "c2", "name": "maps_text_search", "summary": "南京 · 夫子庙"},
            ],
        },
        now_ms=5000,
    )
    assert tl.blocks[1]["type"] == "tool_round"
    assert [c["status"] for c in tl.blocks[1]["calls"]] == ["running", "running"]

    # 第 1 轮工具返回（t=6500ms，工具执行耗时 1500ms）
    tl.on_step(
        {
            "step": 2,
            "node": "tools",
            "action": "tool_results",
            "ts_ms": 6500,
            "results": [
                {"id": "c1", "tool": "maps_weather", "status": "success"},
                {"id": "c2", "tool": "maps_text_search", "status": "success"},
            ],
        },
        now_ms=6500,
    )

    # 第 2 轮思考（耗时 2 秒：t=7000ms -> t=9000ms）
    tl.on_delta({"reasoning_content": "拿到坐标，继续查地铁换乘"}, now_ms=7000)
    tl.on_delta({"reasoning_content": "路线"}, now_ms=9000)

    # 第 2 轮工具调用与返回（t=9200ms -> t=10000ms）
    tl.on_step(
        {
            "step": 3,
            "node": "model",
            "action": "call_tools",
            "ts_ms": 9200,
            "calls": [{"id": "c3", "name": "maps_direction_transit_integrated", "summary": "南京"}],
        },
        now_ms=9200,
    )
    tl.on_step(
        {
            "step": 4,
            "node": "tools",
            "action": "tool_results",
            "ts_ms": 10000,
            "results": [{"id": "c3", "tool": "maps_direction_transit_integrated", "status": "success"}],
        },
        now_ms=10000,
    )

    # 第 3 轮思考（耗时 4 秒：t=10500ms -> t=14500ms）+ 最终正文
    tl.on_delta({"reasoning_content": "信息齐全，开始撰写行程"}, now_ms=10500)
    tl.on_delta({"content": "### 南京周末两日游\n"}, now_ms=14500)
    tl.on_delta({"content": "- Day 1: 夫子庙"}, now_ms=15000)

    blocks = tl.finalize(cancelled=False, now_ms=15000)
    assert [b["type"] for b in blocks] == [
        "reasoning",
        "tool_round",
        "reasoning",
        "tool_round",
        "reasoning",
        "content",
    ]
    # 每轮思考耗时独立且排除了工具等待耗时
    assert blocks[0]["duration_s"] == 3
    assert blocks[1]["duration_ms"] == 1500
    assert [c["status"] for c in blocks[1]["calls"]] == ["success", "success"]

    assert blocks[2]["duration_s"] == 2
    assert blocks[3]["duration_ms"] == 800
    assert blocks[3]["calls"][0]["status"] == "success"

    assert blocks[4]["duration_s"] == 4
    assert blocks[5]["text"] == "### 南京周末两日游\n- Day 1: 夫子庙"
    assert "".join(tl.reasoning_chunks) == (
        "先查南京天气和夫子庙坐标\n\n拿到坐标，继续查地铁换乘路线\n\n信息齐全，开始撰写行程"
    )


def test_timeline_assembler_marks_running_tools_cancelled_on_abort():
    tl = TimelineAssembler()
    tl.on_delta({"reasoning_content": "准备调用美团酒旅"}, now_ms=1000)
    tl.on_step(
        {
            "step": 1,
            "node": "model",
            "action": "call_tools",
            "ts_ms": 3000,
            "calls": [{"id": "mt_1", "name": "meituan_travel_search", "summary": "南京 · 酒店"}],
        },
        now_ms=3000,
    )
    blocks = tl.finalize(cancelled=True, now_ms=5000)
    assert blocks[0]["duration_s"] == 2
    assert blocks[1]["calls"][0]["status"] == "cancelled"


class _FakeGraph:
    async def astream(self, _inputs: Any, _config: Any, *, stream_mode: list[str]):
        assert stream_mode == ["messages", "updates"]
        # 1. 思考 chunk
        yield "messages", (
            AIMessageChunk(content="", additional_kwargs={"reasoning_content": "第一轮思考"}),
            {},
        )
        # 2. 开始流式吐 tool_call_chunks（应触发 thinking_done）
        yield "messages", (
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "maps_weather", "args": '{"city":"南京"}', "id": "c1", "index": 0}],
            ),
            {},
        )
        # 3. model 节点发起工具调用
        yield "updates", {
            "model": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{"id": "c1", "name": "maps_weather", "args": {"city": "南京"}}],
                    )
                ]
            }
        }
        # 4. tools 节点返回结果
        yield "updates", {
            "tools": {
                "messages": [
                    ToolMessage(content="晴", name="maps_weather", tool_call_id="c1", status="success")
                ]
            }
        }
        # 5. 第二轮直接输出正文并在结束时触发 model node_completed（该幽灵 step 应被过滤）
        yield "messages", (AIMessageChunk(content="南京今天晴天。"), {})
        yield "updates", {"model": {"messages": [AIMessage(content="南京今天晴天。")]}}


@pytest.mark.asyncio
async def test_stream_graph_events_emits_thinking_done_and_filters_trailing_node_completed():
    events = [
        (kind, payload)
        async for kind, payload in stream_graph_events(
            _FakeGraph(),
            thread_id="t-1",
            user_message="南京天气",
            thinking=True,
        )
    ]
    kinds_and_actions = [
        (k, p.get("action") if k == "step" else list(p.keys()))
        for k, p in events
    ]
    assert kinds_and_actions == [
        ("delta", ["reasoning_content"]),
        ("delta", ["thinking_done"]),
        ("step", "call_tools"),
        ("step", "tool_results"),
        ("delta", ["content"]),
    ]
