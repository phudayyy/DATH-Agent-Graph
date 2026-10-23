"""Postgres nhúng cho test: một cụm cho cả phiên, một lược đồ sạch cho mỗi test.

`pgserver` là phụ thuộc BẮT BUỘC, nên import thẳng chứ không `importorskip`: thiếu nó thì cả
bộ test phải đỏ, không phải lặng lẽ bỏ qua — một test bị bỏ qua đọc y như một test đã qua.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from pgserver.postgres_server import get_server

from agent_graph.db import apply_schema, connect, new_tenant


@pytest.fixture(scope="session")
def pg_uri() -> Iterator[str]:
    data_dir = Path(tempfile.mkdtemp(prefix="agent-graph-test-"))
    server = get_server(data_dir, cleanup_mode="delete")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def db(pg_uri: str) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
    conn = await connect(pg_uri)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def tenant_id(db: psycopg.AsyncConnection[dict[str, Any]]) -> str:
    return await new_tenant(db, "test")
