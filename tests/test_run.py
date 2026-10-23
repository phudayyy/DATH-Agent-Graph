"""Bộ chạy, từ đầu tới cuối: nạp đề, hỏi từng câu, ghi kết quả, in độ phủ theo loại câu."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agent_graph.run import main

DATA = Path(__file__).parent / "data"


def _fresh_database(pg_uri: str) -> str:
    name = f"run_{uuid4().hex[:8]}"
    with psycopg.connect(pg_uri, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_conninfo(pg_uri, dbname=name)


def _args(out: Path) -> list[str]:
    return ["--kg", str(DATA / "kg"), "--questions", str(DATA / "questions.jsonl"), "--out", str(out)]


def test_the_runner_records_whether_each_answer_is_in_the_pack(pg_uri: str, tmp_path: Path) -> None:
    out = tmp_path / "run.jsonl"
    assert main([*_args(out), "--dsn", _fresh_database(pg_uri)]) == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    # Hai câu có năm ⇒ có đáp án. Hai câu không năm ⇒ nhìn đồ thị ở hiện tại, nơi mọi chức vụ
    # đã kết thúc — ngữ nghĩa ảnh chụp KHÔNG trả lời được chúng, và kết quả phải nói ra điều đó.
    assert {r["qid"]: r["answer_in_pack"] for r in rows} == {
        "q1": True, "q2": True, "q3": False, "q4": False,
    }
    assert rows[0]["as_of"] == "1994-01-01T00:00:00+00:00"


def test_the_runner_starts_its_own_postgres_when_given_none(tmp_path: Path) -> None:
    out = tmp_path / "run.jsonl"
    assert main([*_args(out), "--limit", "1"]) == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [(r["qid"], r["answer_in_pack"]) for r in rows] == [("q1", True)]


def test_the_runner_will_not_build_over_existing_tables(pg_uri: str, tmp_path: Path) -> None:
    dsn = _fresh_database(pg_uri)
    assert main([*_args(tmp_path / "a.jsonl"), "--dsn", dsn]) == 0
    with pytest.raises(SystemExit, match="database đã có bảng"):
        main([*_args(tmp_path / "b.jsonl"), "--dsn", dsn])
