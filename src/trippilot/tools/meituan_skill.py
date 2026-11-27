"""美团酒旅官方 Skill 适配层——严格按 `src/skills/meituan-travel/SKILL.md` 规定的形态调用 CLI。

不直连接口：Skill 明文 "严禁使用 curl、fetch、axios 等方式直接调用 API，必须通过 ht-ai CLI"。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess

from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field

from trippilot.config import Settings, get_settings

PKG = "@meituan-travel/ht-ai"
PINNED_VERSION = "0.0.6"  # SKILL.md 示例写 @latest；2026-11-25 该 tag 解析为 0.0.6
CHANNEL = "meituan-developer"

# SKILL.md 核心执行流程第 3 步：调用前必须先向用户发送等待提示。
WAITING_HINT = "🔍 正在连接美团酒旅数据接口为您规划，耗时约 1-2 分钟，请稍候..."

EXIT_AUTH = 3
EXIT_CHANNEL = 4
EXIT_TIMEOUT = 124

_ANSWER_TAG_RE = re.compile(r"^\s*<answer>\s*|\s*</answer>\s*$", re.IGNORECASE)


class MeituanSkillError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def build_command(query: str, origin_query: str | None = None, city: str | None = None) -> list[str]:
    """拼官方示例的命令形态。列表传参、不经 shell，因此不需要 SKILL.md 第 2 步的单引号转义——
    真去转义反而会把 \\'' 当作字面量塞进 query。"""
    cleaned_query = query.strip()
    resolved_origin = (origin_query.strip() if origin_query and origin_query.strip() else cleaned_query)
    cmd = [
        PKG,
        "query",
        "--query",
        cleaned_query,
        "--origin-query",
        resolved_origin,
        "--channel",
        CHANNEL,
    ]
    if city and city.strip():
        cmd += ["--city", city.strip()]
    return cmd


def _npx() -> str:
    path = shutil.which("npx") or shutil.which("npx.cmd")
    if not path:
        raise MeituanSkillError("未找到 npx，运行环境需要 Node 运行时", exit_code=127)
    return path


def _env(token: str | None = None) -> dict[str, str]:
    """剔除代理变量并确保注入 MEITUAN_HT_TOKEN（兼容 .env 由 pydantic-settings 加载而未写入 os.environ 的场景）。"""
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "NODE_USE_ENV_PROXY"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"

    resolved_token = token if token is not None else (env.get("MEITUAN_HT_TOKEN") or get_settings().meituan_ht_token)
    if resolved_token:
        env["MEITUAN_HT_TOKEN"] = resolved_token
    return env


def _payload(stdout: str, stderr: str) -> dict:
    """成功 JSON 走 stdout，失败 JSON 走 stderr——两边都要捞。"""
    for stream in (stdout, stderr):
        start, end = stream.find("{"), stream.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(stream[start : end + 1])
            except json.JSONDecodeError:
                continue
    return {}


def clean_meituan_markdown(markdown_text: str) -> str:
    """剥离 CLI 响应首尾包裹的 <answer>...</answer> 标签，完整保留酒店/交通价格、评分与预订链接。"""
    return _ANSWER_TAG_RE.sub("", markdown_text or "").strip()


def run_query(
    query: str,
    origin_query: str | None = None,
    city: str | None = None,
    timeout: float = 120.0,
    *,
    token: str | None = None,
) -> str:
    """返回 Markdown 正文。失败抛 MeituanSkillError，由护栏或工具层转成结构化错误提示。"""
    if not query or not query.strip():
        raise MeituanSkillError("美团酒旅查询词不能为空", exit_code=1)

    cmd = [_npx(), "-y", f"{PKG}@{PINNED_VERSION}", *build_command(query, origin_query, city)[1:], "-o", "json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_env(token),
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MeituanSkillError(
            "美团酒旅查询超时（>120s）：当前查询人数较多，请精简查询条件或稍后再试",
            exit_code=EXIT_TIMEOUT,
        ) from exc

    body = _payload(proc.stdout or "", proc.stderr or "")

    if proc.returncode == EXIT_AUTH:
        raise MeituanSkillError("鉴权失败：检查 MEITUAN_HT_TOKEN", exit_code=EXIT_AUTH)
    if proc.returncode == EXIT_CHANNEL:
        raise MeituanSkillError("渠道签名验证失败：检查 --channel", exit_code=EXIT_CHANNEL)
    if body.get("status") != "success" or not body.get("data"):
        detail = (body.get("error") or {}).get("message") or "无结果"
        raise MeituanSkillError(f"美团酒旅查询失败：{detail}", exit_code=proc.returncode or 1)
    return body["data"]


async def arun_query(
    query: str,
    origin_query: str | None = None,
    city: str | None = None,
    timeout: float = 120.0,
    *,
    token: str | None = None,
) -> str:
    """subprocess 是阻塞调用，必须通过 asyncio.to_thread 放入线程池避免阻塞 FastAPI 事件循环。"""
    return await asyncio.to_thread(run_query, query, origin_query, city, timeout, token=token)


class MeituanTravelQueryInput(BaseModel):
    """对齐美团酒旅官方 Skill CLI 参数契约。"""

    query: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "面向美团酒旅的具体自然语言查询（越具体推荐越精准，建议包含城市/起止地、日期、预算、房型或出行人群，"
            "例如：'2026-05-01 上海到南京的高铁票'、'南京夫子庙附近预算400元以内的酒店'、'上海迪士尼两大一小门票'）。"
        ),
    )
    city: str | None = Field(
        default=None,
        max_length=32,
        description="目标城市或出发城市名称（如 '南京'、'上海'、'北京'），明确传入可显著提升酒旅检索准确度。",
    )
    origin_query: str | None = Field(
        default=None,
        max_length=200,
        description="用户的完整原始提问（可选，不传则默认使用 query）。",
    )


def build_meituan_skill_tools(
    settings: Settings | None = None,
    *,
    timeout: float = 120.0,
) -> list[BaseTool]:
    """构造绑定当前配置的 meituan_travel_search 工具实例。"""
    cfg = settings or get_settings()

    @tool("meituan_travel_search", args_schema=MeituanTravelQueryInput)
    async def meituan_travel_search(
        query: str,
        city: str | None = None,
        origin_query: str | None = None,
    ) -> str:
        """调用美团酒旅官方 Skill 查询实时酒店（房价/房型/评分/位置）、火车票/高铁（车次/时刻/票价）、
        机票（航班号/起降时间/票价）、景点门票与度假套餐。
        【使用边界】：
        1. 适用：查酒店住宿报价、查高铁/火车票与机票航班价格、查景点门票及预订链接、旅游目的地推荐。
        2. 不适用：查精确经纬度坐标、地铁公交步行导航耗时或实时天气请使用高德 maps_* 工具；查景区最新官方闭馆公告请用 web_search。
        3. 注意：该接口底层耗时较长（约 15~60 秒），请将同类酒旅需求合并为具体 query 调用，并保留返回中的真实价格与预订链接。"""
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ToolException("美团酒旅查询词不能为空。")

        try:
            raw_md = await arun_query(
                cleaned_query,
                origin_query=origin_query,
                city=city,
                timeout=timeout,
                token=cfg.meituan_ht_token or None,
            )
        except MeituanSkillError as exc:
            raise ToolException(str(exc)) from exc

        return clean_meituan_markdown(raw_md)

    return [meituan_travel_search]
