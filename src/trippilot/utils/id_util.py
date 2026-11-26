import secrets
import time
import uuid


def uuid7() -> uuid.UUID:
    ts_ms = time.time_ns() // 1_000_000
    b = bytearray(ts_ms.to_bytes(6, "big") + secrets.token_bytes(10))
    b[6] = (b[6] & 0x0F) | 0x70  # 高4位置为 0111，低4位保留随机
    b[8] = (b[8] & 0x3F) | 0x80  # 高2位置为 10，低6位保留随机
    return uuid.UUID(bytes=bytes(b))
