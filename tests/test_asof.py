"""Kiểm thử bắt buộc số 3 (phiếu đề tài, mục 6): truy vấn as-of.

> Truy vấn as-of trên một thực thể giả có ba khoảng valid time trả về đúng ba giá trị khác
> nhau tại ba mốc, và tại đúng biên thì trả về theo quy ước nửa khoảng đóng bên trái đã ghi
> trong README.

Quy ước (README, mục *Quy ước thời gian*): fact `[start, end]` tính theo năm ↦ nửa khoảng
`[1/1/start, 1/1/(end+1))` theo UTC, và đúng tại `t` ⇔ `valid_from <= t < valid_to`. Ở độ mịn
năm — câu hỏi nêu năm Y được hỏi tại 1/1/Y — điều đó là `start <= Y <= end`: năm kết thúc ĐƯỢC
TÍNH, như `discretizeKG` của CronKGQA và như chính đáp án vàng của CRONQUESTIONS.

Quy ước ấy được hiện thực ở HAI nơi, và hai nơi phải khớp nhau từng năm:

- truy vấn đồ thị (`agent_graph.graph.ask`, Postgres): vị từ as-of đẩy thẳng vào truy vấn;
- khung đo (`evaluation.kg_index`): gán fact cho câu trả lời dạng năm khi chấm rò, và cho bộ
  lọc sau sinh của A1.

Lệch nhau một năm ở biên thì không gì sập; chỉ có con số sai.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from agent_graph.cronquestions import load
from agent_graph.graph import ask
from evaluation.kg_index import KGEventIndex
from evaluation.temporal_error import (
    TemporalErrorResult,
    check_temporal_error,
    compute_temporal_error_rate,
    left_closed_boundary_check,
)

# Thực thể giả Q10 ở ba đội trong ba khoảng liền nhau: ba giá trị khác nhau của cùng P54.
# Ba dòng sau mang ba HÌNH DẠNG có thật trong data_v2: fact điểm (48% số fact của đồ thị),
# khoảng ngược (652 dòng), và dòng duy nhất có năm kết thúc 0 (`Q500099 P793 Q1190554 600 0`).
FULL_TXT = (
    "Q10\tP54\tQ21\t1990\t1994\n"
    "Q10\tP54\tQ22\t1995\t1999\n"
    "Q10\tP54\tQ23\t2000\t2004\n"
    "Q10\tP166\tQ40\t1997\t1997\n"
    "Q10\tP1411\tQ60\t2003\t2001\n"
    "Q10\tP793\tQ50\t600\t0\n"
)
TICK = timedelta(microseconds=1)  # độ mịn của timestamptz


def utc(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


@pytest.fixture
def kg(tmp_path: Path) -> Path:
    kg_dir = tmp_path / "kg"
    kg_dir.mkdir()
    (kg_dir / "full.txt").write_text(FULL_TXT, encoding="utf-8")
    return kg_dir


async def held_at(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str, as_of: datetime
) -> dict[str, set[str]]:
    """Giá trị của Q10 tại `as_of` theo truy vấn đồ thị, gom theo quan hệ."""
    pack = await ask(db, tenant_id=tenant_id, identities=["anyone"], seed_keys=["Q10"], as_of=as_of)
    values: dict[str, set[str]] = {}
    for r in pack.relations:
        if r["src_key"] == "Q10":
            values.setdefault(r["relation"], set()).add(r["dst_key"])
    return values


async def test_three_intervals_give_three_values_and_the_boundary_is_left_closed(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str, kg: Path
) -> None:
    await load(db, tenant_id=tenant_id, kg_dir=kg)

    async def team(t: datetime) -> set[str]:
        return (await held_at(db, tenant_id, t)).get("P54", set())

    # Ba mốc, mỗi mốc giữa một khoảng ⇒ ba giá trị khác nhau.
    assert [await team(utc(y, 7, 1)) for y in (1992, 1997, 2002)] == [{"Q21"}, {"Q22"}, {"Q23"}]
    # ĐÚNG tại biên 1/1/1995: khoảng hai đã bắt đầu (đóng trái), khoảng một đã hết (mở phải).
    assert await team(utc(1995)) == {"Q22"}
    assert await team(utc(1995) - TICK) == {"Q21"}
    assert await team(utc(2000)) == {"Q23"}
    assert await team(utc(2000) - TICK) == {"Q22"}
    # Hai đầu ngoài cùng theo đúng luật ấy.
    assert await team(utc(1990)) == {"Q21"}
    assert await team(utc(1990) - TICK) == set()
    assert await team(utc(2005)) == set()  # 1/1/(2004 + 1): năm 2004 vừa hết
    assert await team(utc(2005) - TICK) == {"Q23"}


def test_the_evaluator_reads_the_same_three_values_and_counts_the_end_year(kg: Path) -> None:
    index = KGEventIndex.from_file(kg / "full.txt")

    def team(year: int) -> set[str]:
        return index.asof_answers("Q10", "P54", year)

    assert [team(1992), team(1997), team(2002)] == [{"Q21"}, {"Q22"}, {"Q23"}]
    # Biên 1/1/1995 nhìn ở độ mịn năm: 1994 vẫn là khoảng một, 1995 đã là khoảng hai.
    assert (team(1994), team(1995)) == ({"Q21"}, {"Q22"})
    assert (team(1999), team(2000)) == ({"Q22"}, {"Q23"})
    assert (team(1989), team(1990), team(2004), team(2005)) == (set(), {"Q21"}, {"Q23"}, set())


async def test_the_graph_query_and_the_evaluator_agree_on_every_year(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str, kg: Path
) -> None:
    """Hai hiện thực của một quy ước. Fact điểm Q40 chỉ đúng trong 1997; khoảng ngược Q60 và
    dòng năm kết thúc 0 (Q50) không bao giờ đúng — bộ nạp bỏ chúng và đếm vào báo cáo nạp."""
    await load(db, tenant_id=tenant_id, kg_dir=kg)
    index = KGEventIndex.from_file(kg / "full.txt")
    for year in range(1985, 2011):
        graph = await held_at(db, tenant_id, utc(year))
        for relation in ("P54", "P166", "P1411", "P793"):
            evaluator = index.asof_answers("Q10", relation, year)
            assert graph.get(relation, set()) == evaluator, (year, relation)
    assert index.asof_answers("Q10", "P166", 1997) == {"Q40"}
    assert index.asof_answers("Q10", "P166", 1996) == index.asof_answers("Q10", "P166", 1998) == set()


def test_the_year_level_check_is_the_same_left_closed_rule() -> None:
    # [2000, 2010] ↦ [1/1/2000, 1/1/2011)
    assert not left_closed_boundary_check(2000, 2010, 1999)
    assert left_closed_boundary_check(2000, 2010, 2000)  # đầu trái: đóng
    assert left_closed_boundary_check(2000, 2010, 2010)  # năm kết thúc được tính
    assert not left_closed_boundary_check(2000, 2010, 2011)  # 1/1/2011: đầu phải mở
    assert left_closed_boundary_check(1997, 1997, 1997)  # fact điểm: đúng trong năm của nó
    assert not left_closed_boundary_check(2003, 2001, 2002)  # khoảng ngược: rỗng
    assert not left_closed_boundary_check(600, 0, 2000)  # năm kết thúc 0 là nhiễu, không phải "mở"
    assert left_closed_boundary_check(2000, None, 2020)  # None: không ghi năm kết thúc


# ── Tỉ lệ lỗi thời: đúng theo trạng thái mới nhất nhưng sai theo mốc as-of được hỏi ──────────


def test_correct_at_asof_is_not_a_temporal_error() -> None:
    result = check_temporal_error(
        predicted="Q123", gold_at_asof={"Q123", "Q456"}, gold_latest={"Q123", "Q789"}, as_of_year=2005
    )
    assert not result.has_temporal_error
    assert result.error_type == "correct"


def test_correct_at_latest_but_wrong_at_asof_is_a_temporal_error() -> None:
    result = check_temporal_error(
        predicted="Q789", gold_at_asof={"Q123"}, gold_latest={"Q789"}, as_of_year=2005
    )
    assert result.has_temporal_error
    assert result.error_type == "temporal_error"


def test_wrong_at_both_is_another_error() -> None:
    result = check_temporal_error(
        predicted="Q999", gold_at_asof={"Q123"}, gold_latest={"Q789"}, as_of_year=2005
    )
    assert not result.has_temporal_error
    assert result.error_type == "other_error"


def test_a_qid_inside_prose_is_scored() -> None:
    result = check_temporal_error(
        predicted="The answer is Q789 based on latest data",
        gold_at_asof={"Q123"}, gold_latest={"Q789"}, as_of_year=2005,
    )
    assert result.error_type == "temporal_error"


def test_the_temporal_error_rate_counts_only_questions_with_an_asof_year() -> None:
    results = [
        TemporalErrorResult(1, False, 2005, "Q123", "Q123", "Q123", "correct"),
        TemporalErrorResult(2, True, 2005, "Q789", "Q123", "Q789", "temporal_error"),
        TemporalErrorResult(3, True, 2005, "Q789", "Q123", "Q789", "temporal_error"),
        TemporalErrorResult(4, False, 2005, "Q999", "Q123", "Q789", "other_error"),
        TemporalErrorResult(5, False, 0, "Q123", "Q123", "Q123", "correct"),  # không có mốc
    ]
    rates = compute_temporal_error_rate(results)
    assert (rates["with_asof"], rates["temporal_error_count"]) == (4, 2)
    assert rates["temporal_error_rate"] == 0.5
    assert (rates["correct_rate"], rates["other_error_rate"]) == (0.25, 0.25)
