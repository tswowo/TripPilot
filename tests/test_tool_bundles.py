"""高层复合工具打包层（bundles.py）离线单元测试。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from trippilot.tools.bundles import (
    CANONICAL_TOOL_ORDER,
    bundle_runtime_tools,
    is_coord_string,
)


def _build_mock_atomic_tools(call_log: list[tuple[str, dict[str, Any]]]) -> list[BaseTool]:
    """构造模拟的 14 个原始原子工具（11 个高德 + 1 个美团 + 2 个联网）。"""

    @tool("maps_text_search")
    async def maps_text_search(keywords: str, city: str = "", count: int = 10) -> str:
        """模拟关键词搜索。"""
        call_log.append(("maps_text_search", {"keywords": keywords, "city": city, "count": count}))
        if "禄口" in keywords or "机场" in keywords:
            return json.dumps(
                {"pois": [{"id": "B001_AIRPORT", "name": "南京禄口国际机场"}]},
                ensure_ascii=False,
            )
        if "夫子庙" in keywords:
            return json.dumps(
                {"pois": [{"id": "B001_FZM", "name": "夫子庙秦淮风光带"}]},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "total_available": 2,
                "pois": [
                    {"id": "B001907TGR", "name": "南京博物院", "address": "中山东路321号"},
                    {"id": "B00190BMRC", "name": "南京总统府", "address": "长江路292号"},
                ],
            },
            ensure_ascii=False,
        )

    @tool("maps_around_search")
    async def maps_around_search(
        keywords: str,
        location: str,
        radius: str = "3000",
        count: int = 10,
    ) -> str:
        """模拟周边搜索。"""
        call_log.append(
            (
                "maps_around_search",
                {"keywords": keywords, "location": location, "radius": radius},
            )
        )
        return json.dumps(
            {
                "pois": [
                    {"id": "B001_DUCK", "name": "奇芳阁(夫子庙店)", "address": "贡院西街12号"},
                ]
            },
            ensure_ascii=False,
        )

    @tool("maps_search_detail")
    async def maps_search_detail(id: str) -> str:
        """模拟 POI 权威详情查询。"""
        call_log.append(("maps_search_detail", {"id": id}))
        details = {
            "B001_AIRPORT": {
                "id": "B001_AIRPORT",
                "name": "南京禄口国际机场",
                "location": "118.862614,31.734753",
            },
            "B001_FZM": {
                "id": "B001_FZM",
                "name": "夫子庙秦淮风光带",
                "location": "118.789165,32.020671",
                "rating": 4.8,
                "opening_hours": "全天开放",
            },
            "B001907TGR": {
                "id": "B001907TGR",
                "name": "南京博物院",
                "location": "118.824614,32.040555",
                "rating": 4.9,
                "opening_hours": "周二至周日 09:00-17:00（周一闭馆）",
            },
            "B00190BMRC": {
                "id": "B00190BMRC",
                "name": "南京总统府",
                "location": "118.797532,32.044168",
                "rating": 4.9,
                "opening_hours": "周二至周日 08:30-17:00（周一闭馆）",
            },
            "B001_DUCK": {
                "id": "B001_DUCK",
                "name": "奇芳阁(夫子庙店)",
                "location": "118.788500,32.021100",
                "rating": 4.6,
                "opening_hours": "09:00-21:30",
            },
        }
        return json.dumps(details.get(id, {"id": id, "location": "118.800000,32.050000"}), ensure_ascii=False)

    @tool("maps_direction_transit_integrated")
    async def maps_direction_transit_integrated(
        origin: str,
        destination: str,
        city: str,
        cityd: str,
    ) -> str:
        """模拟公共交通路线。"""
        call_log.append(
            (
                "maps_direction_transit_integrated",
                {"origin": origin, "destination": destination, "city": city, "cityd": cityd},
            )
        )
        return json.dumps(
            {
                "distance": 41200,
                "transits": [
                    {
                        "duration": 3480,
                        "duration_text": "58分",
                        "summary": "地铁S1号线(机场线) → 地铁3号线",
                    }
                ],
            },
            ensure_ascii=False,
        )

    @tool("maps_direction_driving")
    async def maps_direction_driving(origin: str, destination: str) -> str:
        """模拟驾车路线。"""
        call_log.append(("maps_direction_driving", {"origin": origin, "destination": destination}))
        return json.dumps(
            {"taxi_cost": 115, "paths": [{"distance": 42000, "duration": 2700, "duration_text": "45分"}]},
            ensure_ascii=False,
        )

    @tool("maps_direction_walking")
    async def maps_direction_walking(origin: str, destination: str) -> str:
        """模拟步行路线。"""
        return json.dumps({"paths": [{"distance": 1200, "duration": 900}]}, ensure_ascii=False)

    @tool("maps_direction_bicycling")
    async def maps_direction_bicycling(origin: str, destination: str) -> str:
        """模拟骑行路线。"""
        return json.dumps({"paths": [{"distance": 1500, "duration": 450}]}, ensure_ascii=False)

    @tool("maps_distance")
    async def maps_distance(origins: str, destination: str, type: str = "1") -> str:
        """模拟距离测量。"""
        return json.dumps({"results": [{"distance": 1000, "duration": 300}]}, ensure_ascii=False)

    @tool("maps_geo")
    async def maps_geo(address: str, city: str = "") -> str:
        """模拟地理编码。"""
        return json.dumps({"results": [{"location": "118.790000,32.040000"}]}, ensure_ascii=False)

    @tool("maps_regeocode")
    async def maps_regeocode(location: str) -> str:
        """模拟逆地理编码。"""
        return json.dumps({"city": "南京市"}, ensure_ascii=False)

    @tool("maps_weather")
    async def maps_weather(city: str) -> str:
        """模拟天气查询。"""
        return json.dumps({"city": city, "forecasts": []}, ensure_ascii=False)

    @tool("meituan_travel_search")
    async def meituan_travel_search(query: str) -> str:
        """模拟美团搜索。"""
        return "美团结果"

    @tool("web_search")
    async def web_search(query: str) -> str:
        """模拟联网搜索。"""
        return "搜索结果"

    @tool("web_fetch")
    async def web_fetch(url: str) -> str:
        """模拟网页抓取。"""
        return "网页正文"

    return [
        web_fetch,
        maps_geo,
        maps_regeocode,
        maps_distance,
        maps_direction_bicycling,
        maps_direction_walking,
        maps_direction_driving,
        maps_direction_transit_integrated,
        maps_search_detail,
        maps_around_search,
        maps_text_search,
        maps_weather,
        meituan_travel_search,
        web_search,
    ]


def test_is_coord_string():
    assert is_coord_string("118.789165,32.020671") is True
    assert is_coord_string(" 118.789 , 32.020 ") is True
    assert is_coord_string("南京禄口机场") is False
    assert is_coord_string("") is False


def test_bundle_runtime_tools_collapses_14_into_6_canonical_order():
    call_log: list[tuple[str, dict[str, Any]]] = []
    raw_tools = _build_mock_atomic_tools(call_log)
    assert len(raw_tools) == 14

    bundled = bundle_runtime_tools(raw_tools)
    bundled_names = tuple(t.name for t in bundled)

    assert len(bundled) == 6
    assert bundled_names == CANONICAL_TOOL_ORDER


@pytest.mark.asyncio
async def test_discover_places_auto_enriches_top_details():
    call_log: list[tuple[str, dict[str, Any]]] = []
    bundled = bundle_runtime_tools(_build_mock_atomic_tools(call_log))
    discover_tool = next(t for t in bundled if t.name == "discover_places")

    raw_out = await discover_tool.ainvoke(
        {"keywords": "历史古迹 景点", "city": "南京", "count": 5, "enrich_detail_top_k": 2}
    )
    payload = json.loads(raw_out)

    assert payload["count"] == 2
    pois = payload["pois"]
    assert pois[0]["name"] == "南京博物院"
    assert pois[0]["rating"] == 4.9
    assert "周一闭馆" in pois[0]["opening_hours"]
    assert pois[0]["location"] == "118.824614,32.040555"

    assert pois[1]["name"] == "南京总统府"
    assert "周一闭馆" in pois[1]["opening_hours"]


@pytest.mark.asyncio
async def test_discover_places_around_search_resolves_chinese_center():
    call_log: list[tuple[str, dict[str, Any]]] = []
    bundled = bundle_runtime_tools(_build_mock_atomic_tools(call_log))
    discover_tool = next(t for t in bundled if t.name == "discover_places")

    raw_out = await discover_tool.ainvoke(
        {"keywords": "秦淮小吃", "city": "南京", "around_location": "夫子庙", "radius": 1500}
    )
    payload = json.loads(raw_out)

    assert payload["around_center"]["location"] == "118.789165,32.020671"
    assert payload["pois"][0]["name"] == "奇芳阁(夫子庙店)"
    assert payload["pois"][0]["rating"] == 4.6


@pytest.mark.asyncio
async def test_plan_commute_route_resolves_chinese_names_and_plans_transit():
    call_log: list[tuple[str, dict[str, Any]]] = []
    bundled = bundle_runtime_tools(_build_mock_atomic_tools(call_log))
    route_tool = next(t for t in bundled if t.name == "plan_commute_route")

    raw_out = await route_tool.ainvoke(
        {
            "origin": "南京禄口机场",
            "destination": "夫子庙",
            "city": "南京",
            "mode": "transit",
        }
    )
    payload = json.loads(raw_out)

    assert payload["mode"] == "transit"
    assert payload["resolved_origin"]["location"] == "118.862614,31.734753"
    assert payload["resolved_destination"]["location"] == "118.789165,32.020671"
    assert payload["route"]["transits"][0]["duration_text"] == "58分"

    # 验证传给底层 maps_direction_transit_integrated 的已经是精确 GCJ-02 坐标而非中文名
    transit_calls = [args for name, args in call_log if name == "maps_direction_transit_integrated"]
    assert len(transit_calls) == 1
    assert transit_calls[0]["origin"] == "118.862614,31.734753"
    assert transit_calls[0]["destination"] == "118.789165,32.020671"


@pytest.mark.asyncio
async def test_plan_commute_route_short_distance_auto_fallback_to_walking():
    """当短距离 transit 返回空 transits: [] 时，应在同一轮自动降级调用步行路线规划。"""

    @tool("maps_text_search")
    async def maps_text_search(keywords: str, city: str = "", count: int = 10) -> str:
        """模拟短途起点终点搜索。"""
        return json.dumps(
            {"pois": [{"id": "B1", "name": keywords, "location": "118.789,32.020"}]},
            ensure_ascii=False,
        )

    @tool("maps_direction_transit_integrated")
    async def maps_direction_transit_integrated(
        origin: str, destination: str, city: str, cityd: str
    ) -> str:
        """模拟距离过近返回空公交列表。"""
        return json.dumps({"distance": 650, "transits": []}, ensure_ascii=False)

    @tool("maps_direction_walking")
    async def maps_direction_walking(origin: str, destination: str) -> str:
        """模拟步行规划。"""
        return json.dumps({"paths": [{"distance": 650, "duration": 520}]}, ensure_ascii=False)

    bundled = bundle_runtime_tools(
        [maps_text_search, maps_direction_transit_integrated, maps_direction_walking]
    )
    route_tool = next(t for t in bundled if t.name == "plan_commute_route")
    raw_out = await route_tool.ainvoke(
        {"origin": "夫子庙", "destination": "老门东", "city": "南京", "mode": "transit"}
    )
    payload = json.loads(raw_out)
    assert payload["auto_fallback_to_walking"] is True
    assert payload["walking_route"]["paths"][0]["distance"] == 650

