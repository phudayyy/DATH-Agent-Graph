"""Postgres cho đồ án: mở kết nối, dựng lược đồ, tạo tenant."""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA_SQL = files("agent_graph").joinpath("schema.sql").read_text(encoding="utf-8")


async def connect(dsn: str) -> psycopg.AsyncConnection[dict[str, Any]]:
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    # Mọi mốc của CRONQUESTIONS nằm đúng nửa đêm 1/1 UTC. Ghim phiên về UTC để không
    # chỗ nào đọc ra 31/12 năm trước chỉ vì máy chạy ở múi giờ phía tây.
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


async def has_schema(conn: psycopg.AsyncConnection[dict[str, Any]]) -> bool:
    row = await (await conn.execute("SELECT to_regclass('entities') IS NOT NULL AS present")).fetchone()
    return bool(row and row["present"])


async def apply_schema(conn: psycopg.AsyncConnection[dict[str, Any]]) -> None:
    await conn.execute(SCHEMA_SQL)


async def new_tenant(conn: psycopg.AsyncConnection[dict[str, Any]], name: str) -> str:
    row = await (
        await conn.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (name,))
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT … RETURNING không trả về dòng nào")
    return str(row["id"])
