"""连真实高德 MCP，刷新工具契约基线并做一次实调验证。

    uv run python scripts/probe_amap_mcp.py

输出 tests/fixtures/amap_tools.json 与 amap_tools.args.json，供离线单测比对。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trippilot.tools.amap_mcp import build_client

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def main() -> int:
    tools = await build_client().get_tools()
    names = sorted(t.name for t in tools)
    args = {t.name: sorted(t.args) for t in tools}
    print(f"拉取到 {len(names)} 个工具")

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "amap_tools.json").write_text(json.dumps(names, ensure_ascii=False, indent=2), "utf-8")
    (FIXTURES / "amap_tools.args.json").write_text(json.dumps(args, ensure_ascii=False, indent=2), "utf-8")

    geo = next(t for t in tools if t.name == "maps_geo")
    result = await geo.ainvoke({"address": "上海虹桥火车站", "city": "上海"})
    print("maps_geo 实调:", str(result)[:220])

    weather = next(t for t in tools if t.name == "maps_weather")
    print("maps_weather 实调:", str(await weather.ainvoke({"city": "南京"}))[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
