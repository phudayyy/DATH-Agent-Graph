"""Lan MỘT bước từ thực thể của câu hỏi, rồi dựng gói.

Bản tối thiểu của `graph/traverse.py` ở mốc — viết lại, không chép. Bản gốc lan theo tầng
tới hai bước, có trọng số quan hệ, trần theo loại và suy giảm theo tuổi, vì hạt giống của nó
đến từ khớp chữ và có thể trật. CRONQUESTIONS cho sẵn thực thể của câu hỏi, và các mẫu câu
của đề đều hỏi về một cạnh chạm vào thực thể ấy — nên ở đây chỉ lan một bước.

Hai điều GIỮ NGUYÊN từ bản gốc, vì chúng là phần bảo mật chứ không phải phần xếp hạng:

1. **Chỉ lan TỪ hạt người hỏi được thấy.** Lan xuyên một nút bị ẩn là để lộ những gì nối
   với nó, dù bản thân nút không hiện ra. Hạt bị ẩn vẫn đi vào gói để bước cắt quyền đếm nó.
2. **Mọi cạnh phải đúng tại `as_of`** — cùng vị từ thời gian với gói và với ACL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from psycopg import AsyncConnection

from brain.clock import require_utc
from brain.retrieve.acl import expand_principals, with_acl
from brain.retrieve.pack import ContextPack, build_pack

SEED_SCORE: Final = 1.0
HOP_SCORE: Final = 0.5


@dataclass(frozen=True, slots=True)
class Reached:
    entity_id: str
    depth: int
    score: float


_SEEDS = with_acl("""
SELECT e.id::text AS id, {acl} AS visible
  FROM entities e
 WHERE e.tenant_id = %(tenant_id)s
   AND e.primary_key = ANY(%(keys)s)
 ORDER BY e.id
""")

_NEIGHBORS = """
SELECT ed.dst_entity_id::text AS id
  FROM edges ed
 WHERE ed.tenant_id = %(tenant_id)s
   AND ed.src_entity_id = ANY(%(seed_ids)s::uuid[])
   AND ed.valid_from <= %(as_of)s
   AND (ed.valid_to   IS NULL OR ed.valid_to   > %(as_of)s)
   AND (ed.expired_at IS NULL OR ed.expired_at > %(as_of)s)
UNION
SELECT ed.src_entity_id::text
  FROM edges ed
 WHERE ed.tenant_id = %(tenant_id)s
   AND ed.dst_entity_id = ANY(%(seed_ids)s::uuid[])
   AND ed.valid_from <= %(as_of)s
   AND (ed.valid_to   IS NULL OR ed.valid_to   > %(as_of)s)
   AND (ed.expired_at IS NULL OR ed.expired_at > %(as_of)s)
 ORDER BY 1
"""


async def reach(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    principals: list[str],
    seed_keys: list[str],
    as_of: datetime,
) -> list[Reached]:
    cur = await conn.execute(
        _SEEDS,
        {"tenant_id": tenant_id, "keys": seed_keys, "principals": principals, "as_of": as_of},
    )
    seeds = [dict(r) for r in await cur.fetchall()]
    reached = {s["id"]: Reached(s["id"], 0, SEED_SCORE) for s in seeds}
    visible_seeds = [s["id"] for s in seeds if s["visible"]]
    if visible_seeds:
        cur = await conn.execute(
            _NEIGHBORS, {"tenant_id": tenant_id, "seed_ids": visible_seeds, "as_of": as_of}
        )
        for row in await cur.fetchall():
            reached.setdefault(row["id"], Reached(row["id"], 1, HOP_SCORE))
    return list(reached.values())


async def ask(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    identities: list[str],
    seed_keys: list[str],
    as_of: datetime,
    question: str = "",
) -> ContextPack:
    """Một câu hỏi, một lần gọi: nở danh tính → lan → cắt quyền → gói."""
    as_of = require_utc(as_of)
    principals = await expand_principals(
        conn, tenant_id=tenant_id, identities=identities, as_of=as_of
    )
    reached = (
        await reach(conn, tenant_id=tenant_id, principals=principals,
                    seed_keys=seed_keys, as_of=as_of)
        if principals else []
    )
    return await build_pack(
        conn, tenant_id=tenant_id, principals=principals, reached=reached,
        as_of=as_of, question=question,
    )
