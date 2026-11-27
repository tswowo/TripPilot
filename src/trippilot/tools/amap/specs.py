"""高德官方 MCP 专属声明式元数据与策略常量（零第三方 SDK 依赖）。"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 1. 工具黑名单（15 减 4，保留 11 个纯数据查询工具）
# ---------------------------------------------------------------------------
EXCLUDED_AMAP_TOOLS: frozenset[str] = frozenset(
    {
        "maps_ip_location",          # 服务端公网 IP 定位，对用户行程规划无意义
        "maps_schema_navi",          # 仅返回手机端 amapuri:// 协议，Web 浏览器无法打开
        "maps_schema_take_taxi",     # 仅返回手机端 amapuri:// 协议，Web 浏览器无法打开
        "maps_schema_personal_map",  # 仅返回手机端 amapuri:// 协议，Web 浏览器无法打开
    }
)

# ---------------------------------------------------------------------------
# 2. 搜索条数与路线裁剪阈值
# ---------------------------------------------------------------------------
SEARCH_COUNT_TOOLS: frozenset[str] = frozenset({"maps_text_search", "maps_around_search"})
DEFAULT_SEARCH_COUNT = 10
MAX_SEARCH_COUNT = 20
MAX_TRANSIT_ROUTES = 3
MAX_MAIN_ROADS = 12

_COUNT_PROPERTY_SCHEMA: dict[str, Any] = {
    "type": "integer",
    "description": f"返回的最大 POI 条数（1-{MAX_SEARCH_COUNT}，默认 {DEFAULT_SEARCH_COUNT}）",
    "default": DEFAULT_SEARCH_COUNT,
    "minimum": 1,
    "maximum": MAX_SEARCH_COUNT,
}

AMAP_VIRTUAL_ARG_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    tool_name: {"count": _COUNT_PROPERTY_SCHEMA}
    for tool_name in SEARCH_COUNT_TOOLS
}

# ---------------------------------------------------------------------------
# 3. 工具描述契约补丁
# ---------------------------------------------------------------------------
AMAP_DESCRIPTION_PATCHES: dict[str, str] = {
    "maps_geo": (
        "【使用约束】仅用于将精确门牌地址转为 GCJ-02 坐标。"
        "查询景点、酒店、商场、车站时严禁使用本工具（易错配到同名住宅区或道路），"
        "请改用 maps_text_search + maps_search_detail 获取权威 POI 坐标。"
    ),
    "maps_regeocode": (
        "【输出说明】输入 GCJ-02 经纬度（lon,lat），返回行政区划信息；province 与 city 均为字符串。"
    ),
    "maps_text_search": (
        "【使用提示】支持可选参数 count（1-20，默认 10）控制返回 POI 条数。"
        "如需某 POI 的权威 GCJ-02 坐标（location）、评分（rating）与营业时间（opening_hours），"
        "请拿返回项的 id 进一步调用 maps_search_detail。"
    ),
    "maps_around_search": (
        "【使用提示】以 GCJ-02 坐标为中心检索周边 POI；支持可选参数 count（1-20，默认 10）控制返回条数。"
        "如需营业时间或评分，请用返回的 id 调用 maps_search_detail。"
    ),
    "maps_search_detail": (
        "【权威数据源】返回的 location 为该 POI 唯一权威 GCJ-02 坐标；"
        "包含 rating（评分）与 opening_hours（营业时间/闭馆日）。"
        "注意：高德不提供门票实时余票、预约名额与放票时间，切勿凭空编造票务信息。"
    ),
    "maps_distance": (
        "【参数枚举】测量 GCJ-02 坐标间距离（单位：米）与耗时（单位：秒）。"
        "type 取值：'0'=直线距离，'1'=驾车导航距离（默认），'3'=步行规划距离（注意无 '2'）。"
    ),
    "maps_direction_transit_integrated": (
        "【使用约束】综合公共交通（地铁/公交/火车）换乘规划（GCJ-02 坐标）。"
        "无论同城还是跨城，均必须同时传入起点城市名 city 与终点城市名 cityd。"
    ),
    "maps_direction_driving": "【输出说明】驾车路线规划（GCJ-02 坐标），返回总距离（米）、总耗时（秒及可读文本）与途经主干道列表。",
    "maps_direction_walking": "【输出说明】步行路线规划（GCJ-02 坐标，限 100km 内），返回总距离（米）、总耗时与途经主干道列表。",
    "maps_direction_bicycling": "【输出说明】骑行路线规划（GCJ-02 坐标，限 500km 内），返回总距离（米）、总耗时与途经主干道列表。",
    "maps_weather": (
        "【字段说明】查询城市天气预报；forecasts 中 week='1'~'7' 对应周一至周日，"
        "daytemp/nighttemp 单位为℃，daypower/nightpower 为风力等级（级）。"
    ),
}

# ---------------------------------------------------------------------------
# 4. 地理编码低精度级别与清洗字段集
# ---------------------------------------------------------------------------
LOW_PRECISION_GEO_LEVELS: frozenset[str] = frozenset(
    {
        "省",
        "市",
        "区县",
        "开发区",
        "乡镇",
        "村庄",
        "住宅区",
        "热点商圈",
        "道路",
        "交叉路口",
        "未知",
    }
)

# 真正的数组容器字段名（即使为空列表 [] 也保持为列表）
LIST_CONTAINER_KEYS: frozenset[str] = frozenset(
    {
        "pois",
        "results",
        "forecasts",
        "casts",
        "paths",
        "transits",
        "segments",
        "steps",
        "buslines",
        "via_stops",
        "main_roads",
    }
)

# 需要从纯数字字符串转为 int 的字段
INT_FIELDS: frozenset[str] = frozenset(
    {
        "distance",
        "duration",
        "walking_distance",
        "origin_distance",
        "destination_distance",
        "via_num",
    }
)

# 需要从数字字符串转为 float 的字段
FLOAT_FIELDS: frozenset[str] = frozenset({"rating", "taxi_cost", "cost"})
