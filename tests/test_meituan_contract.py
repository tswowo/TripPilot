"""美团酒旅 Skill 的响应契约（2026-11-25 由官方 CLI 形态调用实测固化）。

只有 CLI 的 `{status, data}` / `{status, error}` 两种信封：服务端另有 6 键信封与
`errorInfo` 结构，但 CLI 不透出，运行期不得依赖。
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
OK_FILES = ["meituan_flight_cli.json", "meituan_train_cli.json", "meituan_hotel_cli.json"]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=OK_FILES)
def ok(request):
    return load(request.param)


def test_success_envelope_has_only_two_keys(ok):
    assert set(ok) == {"status", "data"}


def test_data_is_markdown_not_json(ok):
    md = ok["data"]
    assert isinstance(md, str) and "##" in md
    assert md.rstrip().endswith("</answer>")
    with pytest.raises(json.JSONDecodeError):
        json.loads(md)


def test_error_envelope_exposes_no_server_reason():
    """CLI 把服务端 msg 换成了 HTTP statusText，所以容错只能建立在退出码上。"""
    bad = load("meituan_error_cli.json")
    assert set(bad) == {"status", "error"}
    assert bad["status"] == "error"
    assert set(bad["error"]) == {"code", "message"}
    assert "reason" not in bad["error"] and "retryable" not in bad["error"]


def test_hotel_uses_yuan_and_no_cny_sign():
    md = load("meituan_hotel_cli.json")["data"]
    assert "元" in md and "¥" not in md


def test_traffic_uses_cny_sign():
    for name in ("meituan_flight_cli.json", "meituan_train_cli.json"):
        assert "¥" in load(name)["data"]
