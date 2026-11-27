import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent

from trippilot.config import Settings
from trippilot.tools import amap_mcp

FIXTURE = Path(__file__).parent / "fixtures" / "amap_tools.json"

# 2026-11-25 从 https://mcp.amap.com/mcp 实测拉取的工具契约。
# 高德改工具名/增删工具时，scripts/probe_amap_mcp.py 会报漂移；这里是离线基线。
EXPECTED_TOOLS = {
    "maps_geo",
    "maps_regeocode",
    "maps_ip_location",
    "maps_weather",
    "maps_direction_bicycling",
    "maps_direction_walking",
    "maps_direction_driving",
    "maps_direction_transit_integrated",
    "maps_distance",
    "maps_text_search",
    "maps_around_search",
    "maps_search_detail",
    "maps_schema_personal_map",
    "maps_schema_navi",
    "maps_schema_take_taxi",
}


def _settings(key: str = "test-key") -> Settings:
    return Settings(amap_maps_api_key=key)


def test_missing_key_fails_fast():
    with pytest.raises(RuntimeError, match="AMAP_MAPS_API_KEY"):
        amap_mcp.build_client(_settings(""))


def test_key_is_passed_in_query_string():
    conn = amap_mcp.build_client(_settings("abc123")).connections["amap"]
    assert conn["transport"] == "streamable_http"
    assert conn["url"] == "https://mcp.amap.com/mcp?key=abc123"


def test_client_factory_bypasses_env_proxy():
    client = amap_mcp._client_factory()
    try:
        assert client._trust_env is False
    finally:
        asyncio.run(client.aclose())


def test_recorded_tool_contract_is_complete():
    recorded = set(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert recorded == EXPECTED_TOOLS


def test_excluded_tools_drops_ip_and_mobile_schema_tools():
    curated_names = EXPECTED_TOOLS - amap_mcp.EXCLUDED_AMAP_TOOLS
    assert len(curated_names) == 11
    assert "maps_ip_location" not in curated_names
    assert "maps_schema_navi" not in curated_names
    assert "maps_schema_take_taxi" not in curated_names
    assert "maps_schema_personal_map" not in curated_names


def test_transit_tool_requires_both_cities():
    args = json.loads(FIXTURE.with_suffix(".args.json").read_text(encoding="utf-8"))
    assert {"origin", "destination", "city", "cityd"} <= set(args["maps_direction_transit_integrated"])


def test_parse_mcp_payload_unwraps_json_text():
    payload = [{"type": "text", "text": '{"results":[{"location":"121.31,31.19"}]}'}]
    assert amap_mcp.parse_mcp_payload(payload) == {"results": [{"location": "121.31,31.19"}]}


def test_parse_mcp_payload_falls_back_to_plain_text():
    assert amap_mcp.parse_mcp_payload([{"type": "text", "text": "配额已用完"}]) == {"text": "配额已用完"}


def test_parse_mcp_payload_accepts_already_parsed_dict():
    assert amap_mcp.parse_mcp_payload({"city": "南京市"}) == {"city": "南京市"}


# ---------------------------------------------------------------------------
# 回归单测：5 大本地治理模块
# ---------------------------------------------------------------------------
def test_enrich_tool_schema_injects_count_and_description():
    dummy_tool = StructuredTool(
        name="maps_text_search",
        description="关键词搜 POI",
        args_schema={
            "type": "object",
            "properties": {"keywords": {"type": "string"}, "city": {"type": "string"}},
            "required": ["keywords"],
        },
        func=lambda **kw: kw,
    )
    enriched = amap_mcp._enrich_tool_schema(dummy_tool)
    assert "count" in enriched.args_schema["properties"]
    assert enriched.args_schema["properties"]["count"]["default"] == 10
    assert "maps_search_detail" in enriched.description


def test_regeocode_empty_array_city_becomes_string():
    """回归用例 3：regeocode 直辖市返回 city: [] 时应转为字符串，且其他空数组字段被清理。"""
    raw = {
        "country": "中国",
        "province": "上海市",
        "city": [],
        "district": "黄浦区",
        "street": [],
        "number": [],
    }
    cleaned = amap_mcp.transform_amap_payload("maps_regeocode", raw)
    assert cleaned["city"] == "上海市"
    assert isinstance(cleaned["city"], str)
    assert "street" not in cleaned
    assert "number" not in cleaned


def test_search_detail_merges_opening_hours_and_coerces_numbers():
    """回归用例 P0-5 & P1-8：opentime2 优先合并为 opening_hours，rating 转为 float，空 cost 剔除。"""
    raw = {
        "id": "B00155MF55",
        "name": "上海豫园",
        "location": "121.492497,31.227714",
        "cost": "",
        "opentime2": "周一全天关闭；周二至周日 09:00-16:30",
        "open_time": "09:00-16:30",
        "rating": "4.8",
        "ticket_ordering": "0",
        "photo": "http://store.is.autonavi.com/showpic/xxx",
    }
    cleaned = amap_mcp.transform_amap_payload("maps_search_detail", raw)
    assert cleaned["opening_hours"] == "周一全天关闭；周二至周日 09:00-16:30"
    assert "opentime2" not in cleaned
    assert "open_time" not in cleaned
    assert "ticket_ordering" not in cleaned
    assert "photo" not in cleaned
    assert "cost" not in cleaned
    assert cleaned["rating"] == 4.8


def test_geo_low_precision_injects_warning():
    """回归用例 2：geo '上海外滩' 命中住宅区时，自动注入 _warning 提醒改用 maps_text_search。"""
    raw = {
        "results": [
            {
                "country": "中国",
                "province": "上海市",
                "city": "上海市",
                "district": "虹口区",
                "street": [],
                "number": [],
                "location": "121.493167,31.245385",
                "level": "住宅区",
            }
        ]
    }
    cleaned = amap_mcp.transform_amap_payload("maps_geo", raw)
    assert "_warning" in cleaned
    assert "住宅区" in cleaned["_warning"]
    assert "maps_text_search" in cleaned["_warning"]


def test_text_search_slices_by_count_and_strips_empty_suggestion():
    """回归用例 5：text_search 加 count=5 只返回 5 条，并附带 total_available。"""
    raw = {
        "suggestion": {"keywords": "", "ciytes": {"suggestion": []}},
        "pois": [
            {"id": f"B00{i}", "name": f"景点{i}", "photo": "http://img"}
            for i in range(20)
        ],
    }
    cleaned = amap_mcp.transform_amap_payload("maps_text_search", raw, count=5)
    assert "suggestion" not in cleaned
    assert cleaned["total_available"] == 20
    assert cleaned["count"] == 5
    assert len(cleaned["pois"]) == 5
    assert "photo" not in cleaned["pois"][0]


def test_transit_direction_slims_below_3000_chars():
    """回归用例 1：豫园 -> 天文馆 transit 瘦身后 <3000 字符，含可读总时长与换乘线路摘要。"""
    heavy_steps = [
        {"instruction": f"沿道路步行{i}米左转", "road": "人民路", "distance": "50", "action": "左转"}
        for i in range(40)
    ]
    heavy_via = [{"name": f"途经站{i}", "id": f"BV{i}", "location": "121.5,31.2"} for i in range(25)]
    raw_transit = {
        "origin": "121.4925,31.2272",
        "destination": "121.9197,30.9169",
        "distance": "75190",
        "taxi_cost": "246",
        "transits": [
            {
                "duration": "8982",
                "walking_distance": "2895",
                "cost": "10",
                "segments": [
                    {
                        "walking": {"distance": "794", "duration": "1102", "steps": heavy_steps},
                        "bus": {
                            "buslines": [
                                {
                                    "name": "地铁14号线(封浜--桂桥路)",
                                    "distance": "1490",
                                    "duration": "450",
                                    "departure_stop": {"name": "豫园"},
                                    "arrival_stop": {"name": "陆家嘴"},
                                    "via_stops": [],
                                }
                            ]
                        },
                        "entrance": {"name": "7号口"},
                        "exit": {"name": ""},
                        "railway": {"name": "", "trip": ""},
                    },
                    {
                        "walking": {"distance": "408", "duration": "411", "steps": heavy_steps},
                        "bus": {
                            "buslines": [
                                {
                                    "name": "地铁2号线",
                                    "distance": "6920",
                                    "duration": "930",
                                    "departure_stop": {"name": "陆家嘴"},
                                    "arrival_stop": {"name": "龙阳路"},
                                    "via_stops": heavy_via[:4],
                                }
                            ]
                        },
                    },
                    {
                        "walking": {"distance": "227", "duration": "374", "steps": heavy_steps},
                        "bus": {
                            "buslines": [
                                {
                                    "name": "地铁16号线(龙阳路--滴水湖)",
                                    "distance": "56200",
                                    "duration": "4320",
                                    "departure_stop": {"name": "龙阳路"},
                                    "arrival_stop": {"name": "临港大道"},
                                    "via_stops": heavy_via,
                                }
                            ]
                        },
                        "exit": {"name": "1号口"},
                    },
                ],
            }
            for _ in range(5)
        ],
    }
    cleaned = amap_mcp.transform_amap_payload("maps_direction_transit_integrated", raw_transit)
    serialized = json.dumps(cleaned, ensure_ascii=False)
    assert len(serialized) < 3000
    assert cleaned["distance"] == 75190
    assert cleaned["total_routes_found"] == 5
    assert len(cleaned["transits"]) == 3
    first = cleaned["transits"][0]
    assert first["duration"] == 8982
    assert first["duration_text"] == "2小时30分"
    assert "地铁14号线" in first["summary"]
    assert "地铁16号线" in first["summary"]


def test_driving_and_walking_direction_unified_and_slimmed():
    """driving 与 walking（嵌套在 route 下）均统一拉平，数值转 int 并提炼 main_roads。"""
    raw_walking = {
        "route": {
            "origin": "121.4925,31.2272",
            "destination": "121.5015,31.2335",
            "paths": [
                {
                    "distance": "1412",
                    "duration": "1140",
                    "steps": [
                        {"instruction": "沿福佑路步行", "road": "福佑路", "distance": "500"},
                        {"instruction": "沿福佑路继续步行", "road": "福佑路", "distance": "300"},
                        {"instruction": "沿人民路步行", "road": "人民路", "distance": "612"},
                    ],
                }
            ],
        }
    }
    cleaned = amap_mcp.transform_amap_payload("maps_direction_walking", raw_walking)
    path0 = cleaned["paths"][0]
    assert path0["distance"] == 1412
    assert path0["duration"] == 1140
    assert path0["duration_text"] == "19分"
    assert path0["main_roads"] == ["福佑路", "人民路"]
    assert path0["steps_count"] == 3
    assert "steps" not in path0


def test_interceptor_strips_count_arg_before_remote_call():
    """验证拦截器在请求远端高德前剥离本地扩展的 count 参数，并在返回后按 count 切片。"""
    raw_payload = {
        "pois": [{"id": f"id_{i}", "name": f"POI_{i}"} for i in range(15)],
    }
    mock_handler = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text=json.dumps(raw_payload, ensure_ascii=False))],
            isError=False,
        )
    )
    req = MCPToolCallRequest(
        name="maps_text_search",
        args={"keywords": "外滩", "city": "上海", "count": 5},
        server_name="amap",
    )

    async def _run():
        return await amap_mcp._amap_tool_interceptor(req, mock_handler)

    res = asyncio.run(_run())
    # 发给远端 handler 的请求 args 中不能包含 count
    called_req = mock_handler.call_args[0][0]
    assert "count" not in called_req.args
    assert called_req.args == {"keywords": "外滩", "city": "上海"}

    # 返回的 JSON 已被切片为 5 条
    returned_data = json.loads(res.content[0].text)
    assert returned_data["count"] == 5
    assert returned_data["total_available"] == 15
    assert len(returned_data["pois"]) == 5
