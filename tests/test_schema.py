"""Danh sách loại nút khai ở HAI chỗ — `platforms.KINDS` và CHECK của `schema.sql` — và chúng phải khớp.

Vị từ ACL chỉ chặn những loại nó biết. Một loại có trong bảng mà vắng khỏi `KINDS` thì không
bị chặn: lọt cổng phân quyền, im lặng.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg

from brain import platforms


async def test_the_kind_check_lists_exactly_the_declared_kinds(
    db: psycopg.AsyncConnection[dict[str, Any]],
) -> None:
    row = await (await db.execute(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname = 'entities_kind_ck'"
    )).fetchone()
    assert row is not None
    declared = sorted(re.findall(r"'([^']+)'::text", row["def"]))
    assert declared == sorted(platforms.ALL_KINDS), row["def"]


def test_a_dataset_item_carries_its_own_acl() -> None:
    assert "item" in platforms.ACL_BEARING_KINDS
    assert "'item'" in platforms.VISIBLE_ENTITY_SQL
