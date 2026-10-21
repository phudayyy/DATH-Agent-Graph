"""Bộ nạp đọc đúng định dạng của đề, đếm mọi dòng nó bỏ, và không bao giờ bỏ một hàng quyền."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from agent_graph.cronquestions import (
    Fact,
    LoadReport,
    as_of_for,
    load,
    merge_intervals,
    principal_ref,
    read_grants,
    read_kg,
    read_questions,
    write_questions,
)

DATA = Path(__file__).parent / "data"
KG = DATA / "kg"


def test_every_dropped_line_is_counted_by_its_reason() -> None:
    report = LoadReport()
    facts = read_kg(KG / "full.txt", report)
    assert (report.lines, report.malformed, report.out_of_range, report.reversed, report.self_loop) == (
        11, 2, 1, 1, 1,
    )
    assert len(facts) == 6
    assert report.lines == len(facts) + report.malformed + report.out_of_range + report.reversed + report.self_loop


def test_overlapping_years_merge_and_touching_years_stay_apart() -> None:
    report = LoadReport()
    merged = merge_intervals(
        [
            Fact("Q10", "P54", "Q20", 1985, 1989),
            Fact("Q10", "P54", "Q20", 1988, 1992),  # chồng 1988–1989 ⇒ gộp
            Fact("Q10", "P54", "Q20", 1993, 1995),  # chạm 1992|1993 ⇒ giữ riêng
        ],
        report,
    )
    assert [(f.start, f.end) for f in merged] == [(1985, 1992), (1993, 1995)]
    assert report.merged == 1


def test_a_malformed_grant_stops_the_load(tmp_path: Path) -> None:
    bad = tmp_path / "acl.tsv"
    bad.write_text("Q1\talice@lab.test\t1999\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2 hoặc 4 cột"):
        read_grants(bad)
    bad.write_text("Q1\talice@lab.test\t2000\t1990\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sau năm kết thúc"):
        read_grants(bad)


def test_a_principal_is_written_the_way_the_reader_expands_it() -> None:
    assert principal_ref(" A.B+lab@Gmail.com ") == "ab@gmail.com"
    assert principal_ref("Group:Council") == "group:council"
    assert principal_ref("ANYONE") == "anyone"


def test_the_official_pickle_converts_to_jsonl_unchanged(tmp_path: Path) -> None:
    # Đúng hình dạng của `questions/*.pickle`: entities/times/answers là SET.
    raw = [{
        "question": "Who held Mayor of Springfield in 1994", "type": "simple_entity",
        "answer_type": "entity", "entities": {"Q1"}, "times": {1994}, "answers": {"Q10"},
        "relations": {"P39"}, "template": "Who held {tail} in {time}",
    }]
    source = tmp_path / "test.pickle"
    source.write_bytes(pickle.dumps(raw))
    questions = read_questions(source)
    target = tmp_path / "test.jsonl"
    write_questions(questions, target)
    assert read_questions(target) == questions
    assert questions[0].entities == ("Q1",) and questions[0].times == (1994,)


def test_a_question_with_a_year_is_seen_on_new_year_of_that_year() -> None:
    q = read_questions(DATA / "questions.jsonl")
    now = datetime(2026, 10, 21, tzinfo=UTC)
    assert as_of_for(q[0], now=now) == datetime(1994, 1, 1, tzinfo=UTC)
    assert as_of_for(q[2], now=now) == now


async def test_the_load_writes_entities_edges_and_an_open_world(
    db: psycopg.AsyncConnection[dict[str, Any]], tenant_id: str
) -> None:
    report = await load(db, tenant_id=tenant_id, kg_dir=KG)
    assert (report.facts, report.entities, report.merged, report.grants) == (5, 6, 1, 6)
    rows = await (await db.execute(
        "SELECT s.primary_key AS src, e.relation, d.primary_key AS dst, e.valid_from, e.valid_to"
        "  FROM edges e JOIN entities s ON s.id = e.src_entity_id JOIN entities d ON d.id = e.dst_entity_id"
        " WHERE s.primary_key = 'Q10' AND d.primary_key = 'Q20'"
    )).fetchall()
    # [1985, 1989] gộp với [1988, 1992] ⇒ [1/1/1985, 1/1/1993): năm kết thúc tính CẢ năm đó.
    assert [(r["relation"], r["valid_from"].year, r["valid_to"].year) for r in rows] == [
        ("member of sports team", 1985, 1993)
    ]
    name = await (await db.execute(
        "SELECT canonical_name FROM entities WHERE primary_key = 'Q21'"
    )).fetchone()
    assert name is not None and name["canonical_name"] == "Q21", "nhãn rỗng ⇒ dùng chính id"
