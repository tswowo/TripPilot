"""适配层离线测试：命令形态、双流取 JSON、退出码分流。"""

import json
from types import SimpleNamespace

import pytest

from trippilot.tools import meituan_skill as ms


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


def test_env_strips_proxy():
    env = ms._env()
    assert "HTTP_PROXY" not in env and "NODE_USE_ENV_PROXY" not in env
    assert env["NO_PROXY"] == "*"


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


def test_error_envelope_message_is_surfaced(monkeypatch):
    _patch(monkeypatch, code=1, stderr=json.dumps(
        {"status": "error", "error": {"code": "E_API_ERROR", "message": "Bad Request"}}))
    with pytest.raises(ms.MeituanSkillError, match="Bad Request"):
        ms.run_query("x")


def test_empty_output_is_not_silently_success(monkeypatch):
    _patch(monkeypatch, code=0, stdout="", stderr="")
    with pytest.raises(ms.MeituanSkillError):
        ms.run_query("x")
