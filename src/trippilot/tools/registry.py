"""运行时工具统一装配注册表（Tool Registry）。

将工具源的发现、容错降级与聚合从 FastAPI 网关入口（api/main.py）中解耦。
支持按配置动态聚合：
1. 本地 @tool（DeepSeek 联网搜索与网页抓取）
2. 远端 MCP（高德地图官方 MCP 治理后工具集）
3. 后续扩展：Agent Skills（如美团酒旅 Skill）与其他第三方 MCP
"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

from trippilot.config import Settings, get_settings
from trippilot.tools.amap import load_curated_amap_tools
from trippilot.tools.bundles import bundle_runtime_tools
from trippilot.tools.deepseek_web import build_deepseek_web_tools
from trippilot.tools.meituan_skill import build_meituan_skill_tools

logger = logging.getLogger(__name__)


async def load_runtime_tools(
    settings: Settings | None = None,
    *,
    bundle: bool = True,
) -> list[BaseTool]:
    """加载并聚合当前环境可用的全部 Agent 工具；默认打包为 6 个高层复合工具以保护 KV Cache。"""
    cfg = settings or get_settings()
    tools: list[BaseTool] = list(build_deepseek_web_tools(cfg))

    if cfg.meituan_ht_token:
        meituan_tools = build_meituan_skill_tools(cfg)
        tools.extend(meituan_tools)
        logger.info("已挂载美团酒旅 Skill 工具 %d 个", len(meituan_tools))
    else:
        logger.warning("未配置 MEITUAN_HT_TOKEN，跳过挂载美团酒旅 Skill 工具")

    if cfg.amap_maps_api_key:
        try:
            amap_tools = await load_curated_amap_tools(cfg)
            tools.extend(amap_tools)
            logger.info("已拉取高德远程 MCP 原始工具 %d 个", len(amap_tools))
        except Exception as exc:  # noqa: BLE001
            logger.warning("高德 MCP 工具拉取失败，降级为仅使用本地工具: %s", exc)
    else:
        logger.warning("未配置 AMAP_MAPS_API_KEY，跳过挂载高德 MCP 工具")

    if bundle:
        bundled = bundle_runtime_tools(tools)
        logger.info("工具打包完成：由 %d 个原子工具收敛为 %d 个高层工具", len(tools), len(bundled))
        return bundled

    return tools
