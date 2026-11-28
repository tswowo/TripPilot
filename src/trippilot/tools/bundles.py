"""高层复合工具打包层（Macro-Tool Bundling Layer）。

设计目标：
1. 【抢救 Prefix KV Cache】：将 14 个细碎原子工具收敛为固定 6 个正交高层工具，并按确定性顺序排列，
   确保单主规划核在轮内多跳与跨轮对话中的 Tools Schema 前缀 100% 恒定不变。
2. 【防止轻量模型选工具失误】：用确定性的 Python 异步流水线封装高德 MCP 的多步强耦合操作：
   - `discover_places`：关键词/周边检索 + 自动并发调用 `maps_search_detail` 补齐 Top POI 的评分、营业/闭馆时间与权威坐标；
   - `plan_commute_route`：接收中文起止地名或坐标，内部自动并发解析为权威 GCJ-02 坐标后调用地铁公交/驾车/步行/骑行路线规划。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from trippilot.tools.mcp_adapter import parse_mcp_text_payload

logger = logging.getLogger(__name__)

_COORD_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")

# 被高层复合工具内部接管并隐藏的 10 个底层高德原子工具
WRAPPED_AMAP_ATOMIC_TOOLS: frozenset[str] = frozenset(
    {
        "maps_text_search",
        "maps_around_search",
        "maps_search_detail",
        "maps_geo",
        "maps_regeocode",
        "maps_distance",
        "maps_direction_transit_integrated",
        "maps_direction_driving",
        "maps_direction_walking",
        "maps_direction_bicycling",
    }
)

# 固定挂载顺序（确保每次请求 Tools Schema 序列 100% 字节级一致，保护 Prefix KV Cache）
CANONICAL_TOOL_ORDER: tuple[str, ...] = (
    "web_search",
    "web_fetch",
    "meituan_travel_search",
    "maps_weather",
    "discover_places",
    "plan_commute_route",
)


def is_coord_string(val: str | None) -> bool:
    """判断字符串是否已为 'lon,lat' 经纬度坐标格式。"""
    if not val or not isinstance(val, str):
        return False
    return bool(_COORD_RE.match(val))


def normalize_coord_string(val: str) -> str:
    """规整坐标字符串中的多余空格（如 '118.79, 32.06' -> '118.79,32.06'）。"""
    parts = [p.strip() for p in val.split(",")]
    return ",".join(parts)


def parse_tool_output(raw: Any) -> dict[str, Any]:
    """将底层 BaseTool.ainvoke 返回的 dict / JSON str / MCP 内容块统一解析为 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"text": raw}
    return parse_mcp_text_payload(raw)


async def _resolve_place_to_coord(
    place_or_coord: str,
    *,
    city: str,
    tool_map: dict[str, BaseTool],
) -> dict[str, str]:
    """将中文地名（如 '南京禄口机场'、'夫子庙'）解析为权威 GCJ-02 坐标。

    优先级：
    1. 若输入本身已是 'lon,lat' 坐标，直接规整返回；
    2. 调用 `maps_text_search` + `maps_search_detail` 获取权威 POI 坐标；
    3. 兜底调用 `maps_geo` 获取门牌地址坐标。
    """
    cleaned_input = (place_or_coord or "").strip()
    if is_coord_string(cleaned_input):
        coord = normalize_coord_string(cleaned_input)
        return {"query": cleaned_input, "name": cleaned_input, "location": coord, "source": "direct_coord"}

    text_search = tool_map.get("maps_text_search")
    detail_tool = tool_map.get("maps_search_detail")
    geo_tool = tool_map.get("maps_geo")

    if text_search is not None:
        search_args: dict[str, Any] = {"keywords": cleaned_input, "count": 3}
        if city:
            search_args["city"] = city
        try:
            search_res = parse_tool_output(await text_search.ainvoke(search_args))
            pois = search_res.get("pois")
            if isinstance(pois, list) and pois:
                top_poi = pois[0] if isinstance(pois[0], dict) else {}
                poi_name = str(top_poi.get("name") or cleaned_input)
                poi_loc = str(top_poi.get("location") or "").strip()
                if is_coord_string(poi_loc):
                    return {
                        "query": cleaned_input,
                        "name": poi_name,
                        "location": normalize_coord_string(poi_loc),
                        "source": "poi_search",
                    }
                poi_id = str(top_poi.get("id") or "").strip()
                if poi_id and detail_tool is not None:
                    detail_res = parse_tool_output(await detail_tool.ainvoke({"id": poi_id}))
                    detail_loc = str(detail_res.get("location") or "").strip()
                    if is_coord_string(detail_loc):
                        return {
                            "query": cleaned_input,
                            "name": str(detail_res.get("name") or poi_name),
                            "location": normalize_coord_string(detail_loc),
                            "source": "poi_detail",
                        }
        except Exception as exc:  # noqa: BLE001
            logger.debug("POI 坐标检索降级: %s", exc)

    if geo_tool is not None:
        geo_args: dict[str, Any] = {"address": cleaned_input}
        if city:
            geo_args["city"] = city
        try:
            geo_res = parse_tool_output(await geo_tool.ainvoke(geo_args))
            candidates = geo_res.get("results") or geo_res.get("geocodes")
            if isinstance(candidates, list) and candidates:
                first = candidates[0] if isinstance(candidates[0], dict) else {}
                geo_loc = str(first.get("location") or "").strip()
                if is_coord_string(geo_loc):
                    return {
                        "query": cleaned_input,
                        "name": cleaned_input,
                        "location": normalize_coord_string(geo_loc),
                        "source": "maps_geo_fallback",
                    }
        except Exception as exc:  # noqa: BLE001
            logger.debug("maps_geo 坐标解析降级: %s", exc)

    return {"query": cleaned_input, "name": cleaned_input, "location": "", "source": "unresolved"}


class DiscoverPlacesInput(BaseModel):
    """高层景点/美食/酒店/商圈检索与详情一步获取入参。"""

    keywords: str = Field(
        ...,
        description="检索关键词，例如：'历史古迹 景点'、'南京博物院'、'秦淮特色小吃'、'夫子庙酒店'。",
    )
    city: str = Field(
        default="",
        description="目标城市名称（如 '南京'、'哈尔滨'）。常规关键词搜索时必填。",
    )
    around_location: str = Field(
        default="",
        description=(
            "可选。若需在某地点周边搜索（如某酒店或景点周边搜餐厅/景点），"
            "可传入中心点中文名称（如 '南京夫子庙'）或 '经度,纬度' 坐标（如 '118.789,32.021'）。"
        ),
    )
    radius: int = Field(
        default=3000,
        ge=100,
        le=50000,
        description="周边搜索半径（单位：米，默认 3000，仅当传入 around_location 时生效）。",
    )
    count: int = Field(
        default=8,
        ge=1,
        le=15,
        description="返回的最大候选 POI 条数（1~15，默认 8）。",
    )
    enrich_detail_top_k: int = Field(
        default=4,
        ge=0,
        le=6,
        description="自动并发补齐权威详情（含评分 rating、营业时间/闭馆日 opening_hours、精确坐标 location）的前 N 个 POI 数量（0~6，默认 4）。",
    )


class PlanCommuteRouteInput(BaseModel):
    """高层跨点交通路线与通勤耗时规划入参（支持直接传中文地名）。"""

    origin: str = Field(
        ...,
        description="起点中文名称（如 '南京禄口国际机场'、'南京南站'、'夫子庙亚朵酒店'）或 '经度,纬度' 坐标。",
    )
    destination: str = Field(
        ...,
        description="终点中文名称（如 '南京夫子庙'、'南京博物院'、'中山陵'）或 '经度,纬度' 坐标。",
    )
    city: str = Field(
        ...,
        description="起点所在城市名称（如 '南京'）。",
    )
    cityd: str = Field(
        default="",
        description="终点所在城市名称（同城无需重复填写，默认与 city 相同；跨城路线必填）。",
    )
    mode: Literal["transit", "driving", "walking", "bicycling"] = Field(
        default="transit",
        description=(
            "出行方式：'transit'=地铁/公交公共交通换乘（默认），"
            "'driving'=打车/自驾（含打车预估费用与主干道），"
            "'walking'=步行路线，'bicycling'=骑行路线。"
        ),
    )


def build_amap_macro_tools(atomic_tools: Sequence[BaseTool]) -> list[BaseTool]:
    """基于高德底层原子工具构建高层复合业务工具（discover_places 与 plan_commute_route）。"""
    tool_map: dict[str, BaseTool] = {t.name: t for t in atomic_tools}
    macro_tools: list[BaseTool] = []

    has_search = "maps_text_search" in tool_map or "maps_around_search" in tool_map
    has_route = any(
        k in tool_map
        for k in (
            "maps_direction_transit_integrated",
            "maps_direction_driving",
            "maps_direction_walking",
            "maps_direction_bicycling",
            "maps_distance",
        )
    )

    if has_search:

        @tool("discover_places", args_schema=DiscoverPlacesInput)
        async def discover_places(
            keywords: str,
            city: str = "",
            around_location: str = "",
            radius: int = 3000,
            count: int = 8,
            enrich_detail_top_k: int = 4,
        ) -> str:
            """检索城市景点、餐厅美食、商圈或周边配套，并自动并发补齐核心候选点的评分（rating）、营业时间/闭馆日（opening_hours）与权威坐标（location）。

            一次调用即可同时拿到 POI 列表与前排候选的完整详情，无需再分步调用详情接口。
            """
            around_center_info: dict[str, str] | None = None
            raw_search_data: dict[str, Any] = {}

            if around_location.strip() and "maps_around_search" in tool_map:
                resolved = await _resolve_place_to_coord(
                    around_location.strip(),
                    city=city.strip(),
                    tool_map=tool_map,
                )
                around_center_info = resolved
                if resolved["location"]:
                    around_tool = tool_map["maps_around_search"]
                    around_args: dict[str, Any] = {
                        "keywords": keywords.strip(),
                        "location": resolved["location"],
                        "radius": str(radius),
                        "count": count,
                    }
                    raw_search_data = parse_tool_output(await around_tool.ainvoke(around_args))

            if not raw_search_data and "maps_text_search" in tool_map:
                text_tool = tool_map["maps_text_search"]
                combined_kw = (
                    f"{around_location.strip()} {keywords.strip()}".strip()
                    if around_location.strip()
                    else keywords.strip()
                )
                text_args: dict[str, Any] = {"keywords": combined_kw, "count": count}
                if city.strip():
                    text_args["city"] = city.strip()
                raw_search_data = parse_tool_output(await text_tool.ainvoke(text_args))

            pois = raw_search_data.get("pois")
            if not isinstance(pois, list):
                return json.dumps(raw_search_data, ensure_ascii=False)

            detail_tool = tool_map.get("maps_search_detail")
            top_k = max(0, min(enrich_detail_top_k, len(pois), 6))

            if detail_tool is not None and top_k > 0:

                async def _fetch_one_detail(poi_item: dict[str, Any]) -> dict[str, Any]:
                    poi_id = str(poi_item.get("id") or "").strip()
                    if not poi_id:
                        return poi_item
                    try:
                        detail_data = parse_tool_output(await detail_tool.ainvoke({"id": poi_id}))
                        merged = dict(poi_item)
                        for key in (
                            "location",
                            "rating",
                            "opening_hours",
                            "address",
                            "cost",
                            "city",
                            "type",
                        ):
                            val = detail_data.get(key)
                            if val not in (None, "", []):
                                merged[key] = val
                        return merged
                    except Exception:  # noqa: BLE001
                        return poi_item

                enriched_head = await asyncio.gather(
                    *[_fetch_one_detail(p if isinstance(p, dict) else {}) for p in pois[:top_k]]
                )
                pois = [*enriched_head, *pois[top_k:]]

            output: dict[str, Any] = {
                "query": {
                    "keywords": keywords,
                    "city": city,
                },
                "count": len(pois),
                "pois": pois,
            }
            if around_center_info is not None:
                output["around_center"] = around_center_info
            if "total_available" in raw_search_data:
                output["total_available"] = raw_search_data["total_available"]

            return json.dumps(output, ensure_ascii=False)

        macro_tools.append(discover_places)

    if has_route:

        @tool("plan_commute_route", args_schema=PlanCommuteRouteInput)
        async def plan_commute_route(
            origin: str,
            destination: str,
            city: str,
            cityd: str = "",
            mode: Literal["transit", "driving", "walking", "bicycling"] = "transit",
        ) -> str:
            """计算两个地点之间的地铁/公交换乘方案、打车/自驾耗时、步行或骑行路线。

            支持直接传入中文地名（如 origin='南京禄口机场', destination='夫子庙', city='南京'），
            工具内部会自动将中文地名解析为权威 GCJ-02 坐标并完成路线测算，一步返回耗时、距离与路线详情。
            """
            start_city = city.strip()
            end_city = (cityd or city).strip()

            origin_info, dest_info = await asyncio.gather(
                _resolve_place_to_coord(origin, city=start_city, tool_map=tool_map),
                _resolve_place_to_coord(destination, city=end_city, tool_map=tool_map),
            )

            if not origin_info["location"] or not dest_info["location"]:
                unresolved = []
                if not origin_info["location"]:
                    unresolved.append(f"起点 '{origin}'")
                if not dest_info["location"]:
                    unresolved.append(f"终点 '{destination}'")
                return json.dumps(
                    {
                        "error": f"未能解析{ '、'.join(unresolved) }的精确坐标，请补充更明确的地名或所在城市。",
                        "resolved_origin": origin_info,
                        "resolved_destination": dest_info,
                    },
                    ensure_ascii=False,
                )

            orig_coord = origin_info["location"]
            dest_coord = dest_info["location"]

            tool_name_by_mode = {
                "transit": "maps_direction_transit_integrated",
                "driving": "maps_direction_driving",
                "walking": "maps_direction_walking",
                "bicycling": "maps_direction_bicycling",
            }
            target_tool_name = tool_name_by_mode.get(mode, "maps_direction_transit_integrated")
            route_tool = tool_map.get(target_tool_name)

            if route_tool is None:
                # 降级到任意可用的方向或测距工具
                for fallback_name in (
                    "maps_direction_transit_integrated",
                    "maps_direction_driving",
                    "maps_distance",
                ):
                    if fallback_name in tool_map:
                        route_tool = tool_map[fallback_name]
                        target_tool_name = fallback_name
                        break

            if route_tool is None:
                return json.dumps({"error": "当前未挂载可用的路线规划工具"}, ensure_ascii=False)

            if target_tool_name == "maps_direction_transit_integrated":
                route_args: dict[str, Any] = {
                    "origin": orig_coord,
                    "destination": dest_coord,
                    "city": start_city,
                    "cityd": end_city,
                }
            elif target_tool_name == "maps_distance":
                dist_type = "3" if mode == "walking" else "1"
                route_args = {
                    "origins": orig_coord,
                    "destination": dest_coord,
                    "type": dist_type,
                }
            else:
                route_args = {
                    "origin": orig_coord,
                    "destination": dest_coord,
                }

            route_data = parse_tool_output(await route_tool.ainvoke(route_args))
            output = {
                "mode": mode,
                "resolved_origin": origin_info,
                "resolved_destination": dest_info,
                "route": route_data,
            }
            return json.dumps(output, ensure_ascii=False)

        macro_tools.append(plan_commute_route)

    return macro_tools


def sort_tools_canonically(tools: Sequence[BaseTool]) -> list[BaseTool]:
    """按固定规范顺序排列工具列表（未在表中的工具按名称字典序排在末尾），保证 Prefix KV Cache 稳定。"""
    order_index = {name: idx for idx, name in enumerate(CANONICAL_TOOL_ORDER)}
    return sorted(
        tools,
        key=lambda t: (order_index.get(t.name, len(CANONICAL_TOOL_ORDER)), t.name),
    )


def bundle_runtime_tools(raw_tools: Sequence[BaseTool]) -> list[BaseTool]:
    """将原始工具集打包收敛为高层复合工具集（14 合 6）。

    - 若检测到高德底层检索/路线原子工具，自动合成 `discover_places` 与 `plan_commute_route`，
      并隐藏被封装的 10 个底层高德原子工具（仅保留 `maps_weather`）；
    - 若传入的工具集中已经是高层工具或测试桩工具，则直接保持并按固定顺序排序。
    """
    existing_names = {t.name for t in raw_tools}
    # 如果已经完成打包或不包含任何被封装的高德底层工具，仅执行规范排序
    if "discover_places" in existing_names or not (existing_names & WRAPPED_AMAP_ATOMIC_TOOLS):
        return sort_tools_canonically(raw_tools)

    macro_tools = build_amap_macro_tools(raw_tools)
    retained = [t for t in raw_tools if t.name not in WRAPPED_AMAP_ATOMIC_TOOLS]
    return sort_tools_canonically([*retained, *macro_tools])
