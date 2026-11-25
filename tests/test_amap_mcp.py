import asyncio
import json
from pathlib import Path

import pytest

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
