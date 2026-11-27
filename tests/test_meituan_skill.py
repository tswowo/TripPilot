"""适配层离线测试：命令形态、双流取 JSON、退出码分流、Token 注入、超时处理与 @tool 封装。"""

import json
import subprocess
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from trippilot.config import Settings
from trippilot.tools import meituan_skill as ms
from trippilot.tools.registry import load_runtime_tools


def test_command_follows_official_shape():
    cmd = ms.build_command("上海到南京的高铁", "帮我规划南京周末游", "南京")
    assert cmd[:2] == [ms.PKG, "query"]
    assert cmd[cmd.index("--query") + 1] == "上海到南京的高铁"
    assert cmd[cmd.index("--origin-query") + 1] == "帮我规划南京周末游"
    assert cmd[cmd.index("--channel") + 1] == "meituan-developer"
    assert cmd[cmd.index("--city") + 1] == "南京"


def test_origin_query_falls_back_to_query():
    cmd = ms.build_command("北京到上海的机票", None)
    assert cmd[cmd.index("--origin-query") + 1] == "北京到上海的机票"


def test_env_strips_proxy_and_injects_token(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("NODE_USE_ENV_PROXY", "1")
    env = ms._env(token="mt-test-token-123")
    assert "HTTP_PROXY" not in env and "NODE_USE_ENV_PROXY" not in env
    assert env["NO_PROXY"] == "*"
    assert env["MEITUAN_HT_TOKEN"] == "mt-test-token-123"


def test_payload_reads_stdout_first_then_stderr():
    good = json.dumps({"status": "success", "data": "## x"})
    assert ms._payload(good, "")["status"] == "success"
    bad = json.dumps({"status": "error", "error": {"code": "E_API_ERROR", "message": "Bad Request"}})
    assert ms._payload("", f"🔍 查询中\n{bad}")["error"]["code"] == "E_API_ERROR"
    assert ms._payload("", "no json here") == {}


def _patch(monkeypatch, *, code=0, stdout="", stderr=""):
    monkeypatch.setattr(ms.shutil, "which", lambda _: "npx")
    monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=code, stdout=stdout, stderr=stderr))


def test_run_query_returns_markdown(monkeypatch):
    _patch(monkeypatch, stdout=json.dumps({"status": "success", "data": "## 车次"}))
    assert ms.run_query("上海到南京的高铁") == "## 车次"


def test_auth_failure_maps_to_exit_3(monkeypatch):
    _patch(monkeypatch, code=3, stderr="{}")
    with pytest.raises(ms.MeituanSkillError) as e:
        ms.run_query("x")
    assert e.value.exit_code == ms.EXIT_AUTH


def test_channel_failure_maps_to_exit_4(monkeypatch):
    _patch(monkeypatch, code=4, stderr="{}")
    with pytest.raises(ms.MeituanSkillError) as e:
        ms.run_query("x")
    assert e.value.exit_code == ms.EXIT_CHANNEL


def test_timeout_expired_maps_to_exit_124(monkeypatch):
    monkeypatch.setattr(ms.shutil, "which", lambda _: "npx")

    def _raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["npx"], timeout=120.0)

    monkeypatch.setattr(ms.subprocess, "run", _raise_timeout)
    with pytest.raises(ms.MeituanSkillError) as e:
        ms.run_query("上海到南京的高铁", timeout=120.0)
    assert e.value.exit_code == ms.EXIT_TIMEOUT
    assert "超时" in str(e.value)


def test_error_envelope_message_is_surfaced(monkeypatch):
    _patch(monkeypatch, code=1, stderr=json.dumps(
        {"status": "error", "error": {"code": "E_API_ERROR", "message": "Bad Request"}}))
    with pytest.raises(ms.MeituanSkillError, match="Bad Request"):
        ms.run_query("x")


def test_empty_output_is_not_silently_success(monkeypatch):
    _patch(monkeypatch, code=0, stdout="", stderr="")
    with pytest.raises(ms.MeituanSkillError):
        ms.run_query("x")


def test_clean_meituan_markdown_strips_answer_tags():
    raw = "\n<answer>\n## 南京酒店推荐\n- **豪华大床房**每晚约**399元**\n</answer>\n"
    cleaned = ms.clean_meituan_markdown(raw)
    assert cleaned == "## 南京酒店推荐\n- **豪华大床房**每晚约**399元**"


@pytest.mark.asyncio
async def test_meituan_tool_invokes_arun_query_and_cleans_tags(monkeypatch):
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        payload = {"status": "success", "data": "<answer>\n## G7002 上海→南京 ¥140\n</answer>"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(ms.shutil, "which", lambda _: "npx")
    monkeypatch.setattr(ms.subprocess, "run", _fake_run)

    cfg = Settings(meituan_ht_token="token-from-settings", amap_maps_api_key="", deepseek_api_key="ds-key")
    tools = ms.build_meituan_skill_tools(cfg)
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "meituan_travel_search"

    result = await tool.ainvoke(
        {
            "query": "明天上海到南京的高铁票",
            "city": "南京",
            "origin_query": "帮我查明天上海到南京的高铁票",
        }
    )
    assert result == "## G7002 上海→南京 ¥140"
    assert "--city" in captured["cmd"]
    assert captured["env"]["MEITUAN_HT_TOKEN"] == "token-from-settings"


@pytest.mark.asyncio
async def test_meituan_tool_surfaces_skill_error_as_tool_exception(monkeypatch):
    _patch(monkeypatch, code=3, stderr="{}")
    cfg = Settings(meituan_ht_token="invalid-token")
    tool = ms.build_meituan_skill_tools(cfg)[0]

    with pytest.raises(ToolException, match="鉴权失败"):
        await tool.ainvoke({"query": "北京酒店"})


@pytest.mark.asyncio
async def test_registry_loads_meituan_tool_when_token_configured():
    cfg_with_token = Settings(
        deepseek_api_key="ds-test",
        meituan_ht_token="mt-test",
        amap_maps_api_key="",
    )
    tools_with = await load_runtime_tools(cfg_with_token)
    assert [t.name for t in tools_with] == ["web_search", "web_fetch", "meituan_travel_search"]

    cfg_without_token = Settings(
        deepseek_api_key="ds-test",
        meituan_ht_token="",
        amap_maps_api_key="",
    )
    tools_without = await load_runtime_tools(cfg_without_token)
    assert [t.name for t in tools_without] == ["web_search", "web_fetch"]
