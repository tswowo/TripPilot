# tests/test_pgdb_connection.py
import os

import psycopg
import pytest

DB_URI = os.getenv("POSTGRES_URI")


def test_db_connection():
    if not DB_URI:
        pytest.fail("环境变量 POSTGRES_URI 未设置，请检查 .env 文件或当前运行环境")

    with psycopg.connect(DB_URI) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1;")
        assert cur.fetchone() == (1,)
