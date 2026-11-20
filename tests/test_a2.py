"""A2 bản đối chiếu: truy vấn đồ thị mang vị từ quyền và as-of trả về đúng những gì A0 sẽ lấy
nếu A0 chỉ nhìn thấy phần đồ thị người hỏi được thấy tại mốc hỏi."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from agent_graph.a2 import (
    GraphRetriever,
    Scope,
    connect_sync,
    create_tenant,
    load_events,
    principal_for_role,
    question_as_of,
)
from agent_graph.cronquestions import year_start
from evaluation.kg_index import KGEventIndex, event_is_valid

LINES = [
    "Q1\tP54\tQ100\t1990\t1994",  # 0: cả hai đầu là thực thể của câu hỏi
    "Q2\tP54\tQ100\t1992\t1996",  # 1
    "Q3\tP54\tQ100\t1995\t1999",  # 2
    "Q1\tP39\tQ200\t1990\t1999",  # 3: quan hệ khác, không bao giờ được lấy
    "Q2\tP54\tQ101\t1990\t1991",  # 4: chỉ tới được qua bước lan từ Q2
    "Q4\tP54\tQ101\t1990\t1991",  # 5: không nối với câu hỏi
    "Q3\tP54\tQ100\t2000\t1999",  # 6: khoảng rỗng, lược đồ không chứa được
    "Q100\tP54\tQ100\t1990\t1990",  # 7: tự trỏ, lược đồ không chứa được
    "Q5\tP54\tQ100\t1993\t1993",  # 8: fact điểm
]
ROLES = {"role_a": [0, 1, 4, 8], "role_b": [2], "role_c": []}
QUESTION = {
    "question": "Who was Q1's team member in Q100",
    "entities": ["Q1", "Q100"],
    "relations": ["P54"],
    "times": [],
}
LOADED = {0, 1, 2, 3, 4, 5, 8}


@pytest.fixture
def kg_dir(tmp_path: Path) -> Path:
    (tmp_path / "full.txt").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    (tmp_path / "wd_id2entity_text.txt").write_text("", encoding="utf-8")
    (tmp_path / "wd_id2relation_text.txt").write_text("", encoding="utf-8")
    return tmp_path


@pytest.fixture
def retriever(pg_uri: str, kg_dir: Path) -> Iterator[GraphRetriever]:
    conn = connect_sync(pg_uri)
    try:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        tenant_id = create_tenant(conn, "a2-test")
        report = load_events(
            conn,
            tenant_id=tenant_id,
            kg_dir=kg_dir,
            grants={principal_for_role(role): ids for role, ids in ROLES.items()},
        )
        assert (report.edges, report.empty_interval, report.self_loop) == (7, 1, 1)
        yield GraphRetriever(conn, tenant_id)
    finally:
        conn.close()


class _VisibleLists:
    def __init__(self, index: KGEventIndex, keep: Callable[[int], bool]) -> None:
        self._index = index
        self._keep = keep

    def get(self, key: str, default: Sequence[int] = ()) -> list[int]:
        return [eid for eid in self._index.by_entity.get(key, default) if self._keep(eid)]


class _VisibleGraph:
    """KGEventIndex nhìn qua một bộ lọc; `retrieve_question` của A0 chạy nguyên văn trên nó."""

    def __init__(self, index: KGEventIndex, keep: Callable[[int], bool]) -> None:
        self.events = index.events
        self.by_entity = _VisibleLists(index, keep)


def _ids(events: Sequence[tuple[int, str, str, str, int, int]]) -> list[int]:
    return [event[0] for event in events]


def _a0_on_visible_graph(kg_dir: Path, role: str | None, year: int | None) -> list[int]:
    index = KGEventIndex.from_file(kg_dir / "full.txt")
    allowed = None if role is None else set(ROLES[role])

    def keep(event_id: int) -> bool:
        *_, start, end = index.events[event_id]
        return (
            event_id in LOADED
            and (allowed is None or event_id in allowed)
            and (year is None or event_is_valid(start, end, year))
        )

    return _ids(KGEventIndex.retrieve_question(_VisibleGraph(index, keep), QUESTION))  # type: ignore[arg-type]


def _scope(role: str | None, year: int | None) -> Scope:
    return Scope(
        principal=None if role is None else principal_for_role(role),
        as_of=None if year is None else year_start(year),
    )


@pytest.mark.parametrize(
    ("role", "year", "expected"),
    [
        (None, None, [0, 1, 2, 8, 4]),
        ("role_a", None, [0, 1, 8, 4]),
        ("role_a", 1994, [0, 1]),
        ("role_a", 1995, [1]),  # năm kết thúc 1994 của sự kiện 0 không đúng tại 1/1/1995
        ("role_b", 1994, []),
        ("role_b", 1995, [2]),
        ("role_c", None, []),  # không có hàng quyền nào: không thấy gì
        (None, 1993, [0, 1, 8]),
    ],
)
def test_the_graph_query_returns_what_a0_would_retrieve_from_the_visible_graph(
    retriever: GraphRetriever, kg_dir: Path, role: str | None, year: int | None, expected: list[int]
) -> None:
    got = _ids(retriever.retrieve(QUESTION, _scope(role, year)))
    assert got == expected
    assert got == _a0_on_visible_graph(kg_dir, role, year)


def test_the_as_of_boundary_is_left_closed(retriever: GraphRetriever) -> None:
    just_before = year_start(1995) - timedelta(microseconds=1)
    scope = Scope(principal=principal_for_role("role_a"), as_of=just_before)
    assert _ids(retriever.retrieve(QUESTION, scope)) == [0, 1]


def test_the_budget_keeps_the_top_of_the_ranking(retriever: GraphRetriever) -> None:
    assert _ids(retriever.retrieve(QUESTION, _scope(None, None), limit=2)) == [0, 1]


def test_with_both_predicates_off_only_the_unloadable_lines_separate_a2_from_a0(
    retriever: GraphRetriever, kg_dir: Path
) -> None:
    a0 = _ids(KGEventIndex.from_file(kg_dir / "full.txt").retrieve_question(QUESTION))
    assert a0 == [0, 7, 1, 2, 6, 8, 4]
    a2 = _ids(retriever.retrieve(QUESTION, _scope(None, None)))
    assert a2 == [event_id for event_id in a0 if event_id in LOADED]


def test_the_as_of_of_a_question_is_its_latest_year_like_the_evaluator() -> None:
    assert question_as_of({"times": [1994, 1990]}) == year_start(1994)
    assert question_as_of({"times": []}) is None
