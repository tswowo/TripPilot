"""高德官方 MCP 响应清洗、路线瘦身与低精度告警纯函数集（零网络与 SDK 依赖）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trippilot.tools.amap.specs import (
    DEFAULT_SEARCH_COUNT,
    FLOAT_FIELDS,
    INT_FIELDS,
    LIST_CONTAINER_KEYS,
    LOW_PRECISION_GEO_LEVELS,
    MAX_MAIN_ROADS,
    MAX_SEARCH_COUNT,
    MAX_TRANSIT_ROUTES,
    SEARCH_COUNT_TOOLS,
)


def format_duration_cn(seconds: int | None) -> str:
    """将秒数格式化为易读中文时长（如 7740 -> '2小时9分'）。"""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}秒"
    minutes = round(seconds / 60)
    hours, mins = divmod(minutes, 60)
    if hours > 0 and mins > 0:
        return f"{hours}小时{mins}分"
    if hours > 0:
        return f"{hours}小时"
    return f"{mins}分"


def to_int(val: Any) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return None


def to_number(val: Any) -> int | float | Any:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return val
        if s.isdigit():
            return int(s)
        try:
            return float(s)
        except ValueError:
            return val
    return val


def sanitize_amap_payload(data: Any) -> Any:
    """递归清洗高德 PHP 风格脏数据：
    1. 直辖市 city: [] 自动回填为同级 province 字符串（无 province 则回填 ""）；
    2. 非容器字段的空数组 [] 统一剔除；
    3. 合并 opentime2 / open_time -> opening_hours，剔除无用空 suggestion / ticket_ordering；
    4. 将 distance / duration 等数字字符串统一转为 int / float。
    """
    if isinstance(data, list):
        return [sanitize_amap_payload(item) for item in data]

    if not isinstance(data, dict):
        return data

    cleaned: dict[str, Any] = {}

    raw_city = data.get("city")
    province_val = data.get("province")
    province_str = province_val if isinstance(province_val, str) else ""

    has_open_fields = "opentime2" in data or "open_time" in data
    raw_ot2 = data.get("opentime2")
    raw_ot1 = data.get("open_time")
    ot2_str = raw_ot2.strip() if isinstance(raw_ot2, str) else ""
    ot1_str = raw_ot1.strip() if isinstance(raw_ot1, str) else ""
    merged_opening_hours = ot2_str or ot1_str

    for k, v in data.items():
        if k in ("opentime2", "open_time", "ticket_ordering", "meal_ordering"):
            continue

        if k == "suggestion" and isinstance(v, dict) and not v.get("keywords"):
            continue

        if k == "city" and isinstance(v, list) and len(v) == 0:
            cleaned["city"] = province_str
            continue

        if isinstance(v, list) and len(v) == 0 and k not in LIST_CONTAINER_KEYS:
            continue

        v_clean = sanitize_amap_payload(v)

        if k in INT_FIELDS:
            coerced = to_int(v_clean)
            if coerced is not None:
                v_clean = coerced
        elif k in FLOAT_FIELDS:
            if isinstance(v_clean, str) and not v_clean.strip():
                continue
            v_clean = to_number(v_clean)

        cleaned[k] = v_clean

    if has_open_fields and merged_opening_hours:
        cleaned["opening_hours"] = merged_opening_hours

    if raw_city == [] and "city" not in cleaned:
        cleaned["city"] = province_str

    return cleaned


def _extract_main_roads(steps: list[dict[str, Any]]) -> list[str]:
    """从逐米转向的 steps 中提取去重保序的途经主干道名称。"""
    roads: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        road = step.get("road")
        if isinstance(road, str):
            road_name = road.strip()
            if road_name and road_name not in seen:
                seen.add(road_name)
                roads.append(road_name)
                if len(roads) >= MAX_MAIN_ROADS:
                    break
    return roads


def slim_path_direction(data: dict[str, Any]) -> dict[str, Any]:
    """瘦身驾车 / 步行 / 骑行规划结果（统一拉平 walking/bicycling 的外层 route 嵌套）。"""
    route_obj = data.get("route") if isinstance(data.get("route"), dict) else data
    raw_paths = route_obj.get("paths")
    if not isinstance(raw_paths, list):
        return data

    slimmed_paths: list[dict[str, Any]] = []
    for path in raw_paths[:2]:
        if not isinstance(path, dict):
            continue
        dist = to_int(path.get("distance"))
        dur = to_int(path.get("duration"))
        steps = path.get("steps") if isinstance(path.get("steps"), list) else []
        item: dict[str, Any] = {}
        if dist is not None:
            item["distance"] = dist
        if dur is not None:
            item["duration"] = dur
            item["duration_text"] = format_duration_cn(dur)
        main_roads = _extract_main_roads(steps)
        if main_roads:
            item["main_roads"] = main_roads
        item["steps_count"] = len(steps)
        for extra_key in ("tolls", "toll_distance", "traffic_lights"):
            if extra_key in path and path[extra_key] not in ("", "0", 0, [], None):
                item[extra_key] = to_number(path[extra_key])
        slimmed_paths.append(item)

    result: dict[str, Any] = {
        "origin": route_obj.get("origin") or data.get("origin", ""),
        "destination": route_obj.get("destination") or data.get("destination", ""),
        "paths": slimmed_paths,
    }
    taxi_cost = route_obj.get("taxi_cost") or data.get("taxi_cost")
    if taxi_cost not in (None, "", []):
        result["taxi_cost"] = to_number(taxi_cost)
    return result


def _slim_transit_segment(seg: dict[str, Any]) -> dict[str, Any]:
    """提炼单段公共交通换乘信息，砍掉 walking.steps 与 buslines.via_stops 详情。"""
    item: dict[str, Any] = {}

    walking = seg.get("walking")
    if isinstance(walking, dict):
        w_dist = to_int(walking.get("distance"))
        w_dur = to_int(walking.get("duration"))
        if w_dist and w_dist > 0:
            item["walking"] = {"distance": w_dist, "duration": w_dur or 0}

    bus = seg.get("bus")
    buslines = bus.get("buslines") if isinstance(bus, dict) else None
    if isinstance(buslines, list) and buslines:
        primary = buslines[0] if isinstance(buslines[0], dict) else {}
        dep = primary.get("departure_stop")
        arr = primary.get("arrival_stop")
        via_stops = primary.get("via_stops") if isinstance(primary.get("via_stops"), list) else []
        b_dist = to_int(primary.get("distance"))
        b_dur = to_int(primary.get("duration"))
        transit_info: dict[str, Any] = {
            "line_name": primary.get("name", ""),
            "departure_stop": dep.get("name", "") if isinstance(dep, dict) else "",
            "arrival_stop": arr.get("name", "") if isinstance(arr, dict) else "",
            "via_stops_count": len(via_stops),
        }
        if b_dist is not None:
            transit_info["distance"] = b_dist
        if b_dur is not None:
            transit_info["duration"] = b_dur
        if len(buslines) > 1:
            alt_names = [
                b.get("name")
                for b in buslines[1:3]
                if isinstance(b, dict) and b.get("name")
            ]
            if alt_names:
                transit_info["alternative_lines"] = alt_names
        item["transit"] = transit_info

    for gate_key in ("entrance", "exit"):
        gate = seg.get(gate_key)
        if isinstance(gate, dict) and gate.get("name"):
            item[gate_key] = gate["name"]

    railway = seg.get("railway")
    if isinstance(railway, dict) and railway.get("name"):
        item["railway"] = {
            k: v for k, v in railway.items() if v not in ("", [], None)
        }

    return item


def slim_transit_direction(data: dict[str, Any]) -> dict[str, Any]:
    """瘦身公交/地铁综合换乘结果，将 15KB+ 原始输出压缩至 <2KB 结构化摘要。"""
    raw_transits = data.get("transits")
    if not isinstance(raw_transits, list):
        return data

    slimmed_transits: list[dict[str, Any]] = []
    for tr in raw_transits[:MAX_TRANSIT_ROUTES]:
        if not isinstance(tr, dict):
            continue
        dur = to_int(tr.get("duration"))
        w_dist = to_int(tr.get("walking_distance"))
        raw_segs = tr.get("segments") if isinstance(tr.get("segments"), list) else []
        segments = [
            s_clean
            for s in raw_segs
            if isinstance(s, dict) and (s_clean := _slim_transit_segment(s))
        ]
        transit_lines = [
            s["transit"]["line_name"]
            for s in segments
            if "transit" in s and s["transit"].get("line_name")
        ]
        route_item: dict[str, Any] = {}
        if dur is not None:
            route_item["duration"] = dur
            route_item["duration_text"] = format_duration_cn(dur)
        if w_dist is not None:
            route_item["walking_distance"] = w_dist
        if tr.get("cost") not in (None, "", []):
            route_item["cost"] = to_number(tr["cost"])
        if transit_lines:
            route_item["summary"] = " → ".join(transit_lines)
        route_item["segments"] = segments
        slimmed_transits.append(route_item)

    result: dict[str, Any] = {
        "origin": data.get("origin", ""),
        "destination": data.get("destination", ""),
        "distance": to_int(data.get("distance")) or data.get("distance"),
        "total_routes_found": len(raw_transits),
        "transits": slimmed_transits,
    }
    if data.get("taxi_cost") not in (None, "", []):
        result["taxi_cost"] = to_number(data["taxi_cost"])
    return result


def inject_geo_warning(data: dict[str, Any]) -> dict[str, Any]:
    """检测 maps_geo 低精度命中（如住宅区、区县、道路），自动注入 _warning 提示。"""
    results = data.get("results") or data.get("geocodes")
    if not isinstance(results, list):
        return data

    low_levels: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        level = item.get("level")
        if isinstance(level, str) and level in LOW_PRECISION_GEO_LEVELS:
            low_levels.append(level)

    if low_levels:
        joined = " / ".join(dict.fromkeys(low_levels))
        data["_warning"] = (
            f"⚠️ 低精度告警：当前 maps_geo 命中级别为 [{joined}]，坐标可能偏离目标景点/机构的真实位置！"
            "若是查询景点、酒店、商场或车站，请改用 maps_text_search + maps_search_detail 获取权威 POI 坐标。"
        )
    return data


def slice_search_pois(data: dict[str, Any], count: int = DEFAULT_SEARCH_COUNT) -> dict[str, Any]:
    """对 maps_text_search / maps_around_search 的 pois 列表按 count 切片并移除冗余大图 URL。"""
    pois = data.get("pois")
    if not isinstance(pois, list):
        return data

    total_available = len(pois)
    sliced: list[dict[str, Any]] = []
    for poi in pois[:count]:
        if isinstance(poi, dict):
            poi_copy = {k: v for k, v in poi.items() if k != "photo"}
            sliced.append(poi_copy)
        else:
            sliced.append(poi)

    data["total_available"] = total_available
    data["count"] = len(sliced)
    data["pois"] = sliced
    return data


def slim_search_detail(data: dict[str, Any]) -> dict[str, Any]:
    """移除 maps_search_detail 中纯文本大模型无需消费的图片链接。"""
    data.pop("photo", None)
    return data


# 按工具名分发的后置转换器表（告别一长串 if/elif）
TOOL_SPECIFIC_TRANSFORMERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "maps_direction_transit_integrated": slim_transit_direction,
    "maps_direction_driving": slim_path_direction,
    "maps_direction_walking": slim_path_direction,
    "maps_direction_bicycling": slim_path_direction,
    "maps_geo": inject_geo_warning,
    "maps_search_detail": slim_search_detail,
}


def transform_amap_payload(
    tool_name: str,
    data: Any,
    *,
    count: int = DEFAULT_SEARCH_COUNT,
) -> Any:
    """高德 MCP 单工具响应治理总入口（通用清洗 -> 查表分发专属瘦身/告警）。"""
    cleaned = sanitize_amap_payload(data)
    if not isinstance(cleaned, dict):
        return cleaned

    if tool_name in SEARCH_COUNT_TOOLS:
        return slice_search_pois(cleaned, count=count)

    transformer = TOOL_SPECIFIC_TRANSFORMERS.get(tool_name)
    if transformer is not None:
        return transformer(cleaned)

    return cleaned


def adapt_amap_payload(
    tool_name: str,
    data: Any,
    virtual_args: dict[str, Any],
) -> Any:
    """适配通用 MCPAdaptationPolicy.payload_transformer 签名的桥接函数。"""
    requested_count = DEFAULT_SEARCH_COUNT
    if "count" in virtual_args:
        coerced = to_int(virtual_args["count"])
        if coerced is not None:
            requested_count = max(1, min(coerced, MAX_SEARCH_COUNT))
    return transform_amap_payload(tool_name, data, count=requested_count)
