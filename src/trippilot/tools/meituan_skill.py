"""美团酒旅官方 Skill 适配层——严格按 `src/skills/meituan-travel/SKILL.md` 规定的形态调用 CLI。

不直连接口：Skill 明文 "严禁使用 curl、fetch、axios 等方式直接调用 API，必须通过 ht-ai CLI"。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

PKG = "@meituan-travel/ht-ai"
PINNED_VERSION = "0.0.6"  # SKILL.md 示例写 @latest；2026-11-25 该 tag 解析为 0.0.6
CHANNEL = "meituan-developer"

# SKILL.md 核心执行流程第 3 步：调用前必须先向用户发送等待提示。
WAITING_HINT = "🔍 正在连接美团酒旅数据接口为您规划，耗时约 1-2 分钟，请稍候..."

EXIT_AUTH = 3
EXIT_CHANNEL = 4


class MeituanSkillError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def build_command(query: str, origin_query: str | None = None, city: str | None = None) -> list[str]:
    """拼官方示例的命令形态。列表传参、不经 shell，因此不需要 SKILL.md 第 2 步的单引号转义——
    真去转义反而会把 \\'' 当作字面量塞进 query。"""
    cmd = [PKG, "query", "--query", query, "--origin-query", query if origin_query is None else origin_query,
           "--channel", CHANNEL]
    if city:
        cmd += ["--city", city]
    return cmd


def _npx() -> str:
    path = shutil.which("npx") or shutil.which("npx.cmd")
    if not path:
        raise MeituanSkillError("未找到 npx，运行环境需要 Node 运行时", exit_code=127)
    return path


def _env() -> dict[str, str]:
    """剔除代理变量：本机 HTTP_PROXY 指向 127.0.0.1 的调试代理，会让请求直接 400。"""
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "NODE_USE_ENV_PROXY"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
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


def run_query(query: str, origin_query: str | None = None, city: str | None = None,
              timeout: float = 120.0) -> str:
    """返回 Markdown 正文。失败抛 MeituanSkillError，由护栏中间件转成结构化 ToolMessage。"""
    cmd = [_npx(), "-y", f"{PKG}@{PINNED_VERSION}", *build_command(query, origin_query, city)[1:], "-o", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          env=_env(), timeout=timeout, shell=False, check=False)
    body = _payload(proc.stdout or "", proc.stderr or "")

    if proc.returncode == EXIT_AUTH:
        raise MeituanSkillError("鉴权失败：检查 MEITUAN_HT_TOKEN", exit_code=EXIT_AUTH)
    if proc.returncode == EXIT_CHANNEL:
        raise MeituanSkillError("渠道签名验证失败：检查 --channel", exit_code=EXIT_CHANNEL)
    if body.get("status") != "success" or not body.get("data"):
        detail = (body.get("error") or {}).get("message") or "无结果"
        raise MeituanSkillError(f"美团酒旅查询失败：{detail}", exit_code=proc.returncode or 1)
    return body["data"]


async def arun_query(query: str, origin_query: str | None = None, city: str | None = None,
                     timeout: float = 120.0) -> str:
    """subprocess 是阻塞调用，直接放进事件循环会把整条 SSE 流卡死。"""
    return await asyncio.to_thread(run_query, query, origin_query, city, timeout)
