"""收据抽取正则的回归测试。

docs/THIRD_PARTY_INTEGRATION.md 声称这些正则已在真实样本上验证——这个文件就是凭据。
美团包版本或返回排版一变，这里先红，而不是等 ItineraryValidator 拿着空收据做预算核验时才发现。
"""

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

TRAFFIC = re.compile(
    r"(?P<no>[A-Z]{1,2}\d{1,4})\s*(?P<from>\S+?)→(?P<to>\S+?)\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2})\s*"
    r"(?P<dep>\d{2}:\d{2})→(?P<arr>\d{2}:\d{2})\s*总?(?P<dur>[^\s]+?)\s+(?P<seat>\S+?)\s*¥(?P<price>[\d.]+)"
)

HOTEL = re.compile(r"\*\*(?P<room>[^*]+?)\*\*每晚约\*\*(?P<price>\d+(?:\.\d+)?)元\*\*")


def data_of(name: str) -> str:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]


def test_flight_receipts():
    md = data_of("meituan_flight_cli.json")
    hits = [m.groupdict() for m in TRAFFIC.finditer(md)]
    assert len(hits) == md.count("¥") == 6
    assert hits[0]["no"].startswith(("CA", "MU", "KN", "CZ", "9C", "HU"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", hits[0]["date"])


def test_train_receipts():
    """高铁与机票的两处差异：日期是 MM.DD 无年份，时长前有个 `总`。"""
    md = data_of("meituan_train_cli.json")
    hits = [m.groupdict() for m in TRAFFIC.finditer(md)]
    assert len(hits) == md.count("¥") == 6
    assert all(re.fullmatch(r"\d{1,2}\.\d{1,2}", h["date"]) for h in hits)
    assert all(re.fullmatch(r"\d+m|\d+h(\d+m)?", h["dur"]) for h in hits)
    assert all(int(h["price"]) > 0 for h in hits)


def test_hotel_room_prices():
    md = data_of("meituan_hotel_cli.json")
    hits = [(m["room"], m["price"]) for m in HOTEL.finditer(md)]
    assert len(hits) == 6
    assert all(float(price) > 0 for _, price in hits)


def test_category_regexes_do_not_cross_match():
    """两类排版互不命中，才说明按品类分叉是必要的而不是过度设计。"""
    assert HOTEL.findall(data_of("meituan_flight_cli.json")) == []
    assert TRAFFIC.findall(data_of("meituan_hotel_cli.json")) == []
