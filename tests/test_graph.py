"""Một câu hỏi đi qua đồ thị: đúng năm, đúng quyền của lúc đó, và không lộ gì qua một nút bị ẩn."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from agent_graph.cronquestions import Grant, load
from agent_graph.graph import ask

KG = Path(__file__).parent / "data" / "kg"

# Mạng lưới quyền nhỏ: chức thị trưởng và hai đội bóng ai cũng thấy; Alice chỉ chính Alice thấy;
# Bob cả miền lab.test thấy; Carol chỉ Carol thấy, và chỉ trong 2001–2002.
GRANTS = [
    Grant("Q1", "anyone"),
    Grant("Q20", "anyone"),
    Grant("Q21", "anyone"),
    Grant("Q10", "alice@lab.test"),
    Grant("Q11", "domain:lab.test"),
    Grant("Q12", "carol@lab.test", 2001, 2002),
]


def year(y: int) -> datetime:
    return datetime(y, 1, 1, tzinfo=UTC)


async def test_the_holder_in_a_year_is_the_one_whose_years_include_it(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str
) -> None:
    await load(db, tenant_id=tenant_id, kg_dir=KG)
    in_1994 = (await ask(db, tenant_id=tenant_id, identities=["anyone"], seed_keys=["Q1"],
                         as_of=year(1994))).render()
    assert "- Alice Ng --position held--> Mayor of Springfield (since 1990-01-01)" in in_1994
    assert "Bob Tran" not in in_1994
    # Năm kết thúc của Alice là 1994, TÍNH CẢ năm đó — nên sang 1995 thì chỉ còn Bob.
    in_1995 = (await ask(db, tenant_id=tenant_id, identities=["anyone"], seed_keys=["Q1"],
                         as_of=year(1995))).render()
    assert "- Bob Tran --position held--> Mayor of Springfield (since 1995-01-01)" in in_1995
    assert "Alice Ng" not in in_1995


async def test_a_hidden_neighbour_is_counted_and_never_named(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str
) -> None:
    await load(db, tenant_id=tenant_id, kg_dir=KG, grants=GRANTS)
    eve = (await ask(db, tenant_id=tenant_id, identities=["eve@lab.test"], seed_keys=["Q1"],
                     as_of=year(1994))).render()
    assert "Alice" not in eve
    assert "- 1 related objects: outside your permissions" in eve
    alice = (await ask(db, tenant_id=tenant_id, identities=["alice@lab.test"], seed_keys=["Q1"],
                       as_of=year(1994))).render()
    assert "Alice Ng --position held--> Mayor of Springfield" in alice


async def test_nothing_is_reached_through_a_hidden_seed(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str
) -> None:
    """Springfield FC ai cũng thấy — nhưng Eve chỉ tới được nó QUA Alice, mà Alice bị ẩn với Eve.

    Lan xuyên Alice thì gói nêu tên Springfield FC dù không nêu tên Alice, tức để lộ rằng
    thực thể Eve hỏi có nối với một đội bóng.
    """
    await load(db, tenant_id=tenant_id, kg_dir=KG, grants=GRANTS)
    pack = await ask(db, tenant_id=tenant_id, identities=["eve@lab.test"], seed_keys=["Q10"],
                     as_of=year(1990))
    text = pack.render()
    assert "Springfield FC" not in text and "Alice" not in text
    assert pack.entities == [] and pack.relations == []
    assert "- 1 related objects: outside your permissions" in text


async def test_a_permission_counts_only_in_its_own_years(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str
) -> None:
    """Quyền cũng bi-temporal: Carol thấy chính mình từ 2001, dù đã làm thị trưởng từ 2000."""
    await load(db, tenant_id=tenant_id, kg_dir=KG, grants=GRANTS)
    in_2000 = (await ask(db, tenant_id=tenant_id, identities=["carol@lab.test"], seed_keys=["Q1"],
                         as_of=year(2000))).render()
    in_2001 = (await ask(db, tenant_id=tenant_id, identities=["carol@lab.test"], seed_keys=["Q1"],
                         as_of=year(2001))).render()
    assert "Carol Le" not in in_2000 and "outside your permissions" in in_2000
    assert "- Carol Le --position held--> Mayor of Springfield (since 2000-01-01)" in in_2001


async def test_no_identity_means_an_empty_pack(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str
) -> None:
    await load(db, tenant_id=tenant_id, kg_dir=KG)
    pack = await ask(db, tenant_id=tenant_id, identities=[], seed_keys=["Q1"], as_of=year(1994))
    assert pack.entities == [] and pack.relations == []
    assert "- caller identity: no principal — returning empty, fail-closed" in pack.render()
