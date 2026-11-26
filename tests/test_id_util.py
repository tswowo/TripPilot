import time
import uuid

from trippilot.utils.id_util import uuid7


def test_uuid7_layout():
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122
    ts = int(u.hex[:12], 16)
    assert abs(ts - time.time_ns() // 1_000_000) < 1000


def test_uuid7_monotonic_across_ms():
    ids = []
    for _ in range(20):
        ids.append(str(uuid7()))
        time.sleep(0.002)
    assert ids == sorted(ids)


def test_uuid7_uniqueness():
    assert len({uuid7() for _ in range(1000)}) == 1000