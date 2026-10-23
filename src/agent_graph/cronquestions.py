"""Đọc CRONQUESTIONS (bản `data_v2` của CronKGQA) và nạp vào đồ thị bi-temporal.

Định dạng đối chiếu với mã của CronKGQA (github.com/apoorvumang/CronKGQA), không đoán:

- `kg/full.txt`: mỗi dòng năm cột cách nhau bằng tab — chủ thể, quan hệ, đối tượng, năm bắt
  đầu, năm kết thúc. `loadTKG` của họ bỏ dòng không đủ năm cột.
- Năm kết thúc TÍNH CẢ năm đó: `discretizeKG` của họ duyệt `range(start, end + 1)`.
- `kg/wd_id2entity_text.txt`, `kg/wd_id2relation_text.txt`: `id<TAB>nhãn`; dòng một cột ⇒ nhãn
  rỗng (`readDict` trong `utils.py`).
- `questions/{split}.pickle`: một list dict; ở đây đọc các khoá `question`, `type`,
  `answer_type`, `entities`, `times`, `answers`, `relations` (`qa_datasets.py`).

Ánh xạ thời gian: năm Y ↦ khoảng nửa mở [1/1/Y, 1/1/(Y+1)) theo UTC. Fact [start, end] thành
[1/1/start, 1/1/(end+1)), nên "đúng tại as_of" ⇔ start ≤ năm của as_of ≤ end — đúng ngữ nghĩa
của đề, và đúng vị từ `valid_from <= as_of < valid_to` mà lõi đồ thị vẫn dùng.
"""

from __future__ import annotations

import argparse
import json
import pickle
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from brain.clock import utc_now
from brain.platforms import PUBLIC_PRINCIPAL
from brain.resolve.hardkey import normalize_email

# `datetime` chỉ biểu diễn năm 1–9999, và khoảng của năm Y kết thúc ở 1/1/(Y+1).
MIN_YEAR = 1
MAX_YEAR = 9998


def year_start(year: int) -> datetime:
    return datetime(year, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Fact:
    head: str
    relation: str
    tail: str
    start: int
    end: int  # tính cả năm này


@dataclass(slots=True)
class LoadReport:
    """Đếm mọi dòng bị bỏ hoặc bị gộp — một bộ nạp lặng lẽ bỏ dữ liệu là một bộ nạp nói dối."""

    lines: int = 0
    malformed: int = 0  # không đủ năm cột, hoặc năm không phải số
    out_of_range: int = 0  # năm ngoài 1–9998
    reversed: int = 0  # năm bắt đầu sau năm kết thúc
    self_loop: int = 0  # chủ thể trùng đối tượng — lõi cấm cạnh tự trỏ
    merged: int = 0  # khoảng chồng lên một khoảng khác của cùng (chủ thể, quan hệ, đối tượng)
    facts: int = 0  # số cạnh ghi vào đồ thị
    entities: int = 0
    grants: int = 0
    unknown_grant_items: int = 0  # hàng quyền nhắc tới thực thể không có trong đồ thị


def read_kg(path: Path, report: LoadReport) -> list[Fact]:
    facts: list[Fact] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            report.lines += 1
            cols = line.strip().split("\t")  # đúng như `loadTKG` của CronKGQA
            if len(cols) != 5:
                report.malformed += 1
                continue
            head, relation, tail, start_text, end_text = cols
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                report.malformed += 1
                continue
            if not (MIN_YEAR <= start <= MAX_YEAR and MIN_YEAR <= end <= MAX_YEAR):
                report.out_of_range += 1
            elif start > end:
                report.reversed += 1
            elif head == tail:
                report.self_loop += 1
            else:
                facts.append(Fact(head, relation, tail, start, end))
    return facts


def merge_intervals(facts: Iterable[Fact], report: LoadReport) -> list[Fact]:
    """Gộp các khoảng CHỒNG nhau của cùng (chủ thể, quan hệ, đối tượng).

    Thay cho ràng buộc `EXCLUDE` của bản gốc (xem `schema.sql`). Hai khoảng chỉ CHẠM nhau —
    [1990, 1994] và [1995, 1999] — không chồng: dạng nửa mở của chúng là [1990, 1995) và
    [1995, 2000), nên được giữ riêng, đúng như phép `&&` của Postgres.
    """
    by_key: dict[tuple[str, str, str], list[Fact]] = defaultdict(list)
    for fact in facts:
        by_key[(fact.head, fact.relation, fact.tail)].append(fact)
    out: list[Fact] = []
    for group in by_key.values():
        group.sort(key=lambda f: (f.start, f.end))
        current = group[0]
        for nxt in group[1:]:
            if nxt.start <= current.end:  # chung ít nhất một năm
                current = Fact(current.head, current.relation, current.tail,
                               current.start, max(current.end, nxt.end))
                report.merged += 1
            else:
                out.append(current)
                current = nxt
        out.append(current)
    return out


def read_labels(path: Path) -> dict[str, str]:
    """`id<TAB>nhãn` → dict. Như `readDict` của CronKGQA: dòng một cột ⇒ nhãn rỗng."""
    labels: dict[str, str] = {}
    if not path.exists():
        return labels
    with path.open(encoding="utf-8") as f:
        for line in f:
            cols = line.strip().split("\t")
            if cols[0]:
                labels[cols[0]] = cols[1] if len(cols) > 1 else ""
    return labels


@dataclass(frozen=True, slots=True)
class Grant:
    """Một hàng của mạng lưới quyền: `item` đọc được bởi `principal` trong [start, end]."""

    item: str
    principal: str
    start: int | None = None  # None ⇒ không giới hạn phía trước
    end: int | None = None  # None ⇒ tới nay; tính cả năm này, như năm của fact


def read_grants(path: Path) -> list[Grant]:
    """Đọc mạng lưới quyền: `item<TAB>principal[<TAB>năm bắt đầu<TAB>năm kết thúc]`.

    Cột năm để trống ⇒ không giới hạn phía đó. `principal` là một email, `group:x`, `domain:x`
    hoặc `anyone` — đúng bốn dạng mà `expand_principals` nở danh tính người hỏi ra.

    ⛔ Dòng hỏng thì DỪNG, không bỏ qua: một hàng quyền bị lặng lẽ bỏ là một thực thể bị ẩn
    nhầm, và kết quả đo về sau sai mà không chỗ nào báo.
    """
    grants: list[Grant] = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) not in (2, 4):
                raise ValueError(f"{path}:{n}: cần 2 hoặc 4 cột, có {len(cols)}")
            start = int(cols[2]) if len(cols) == 4 and cols[2] else None
            end = int(cols[3]) if len(cols) == 4 and cols[3] else None
            for year in (start, end):
                if year is not None and not MIN_YEAR <= year <= MAX_YEAR:
                    raise ValueError(f"{path}:{n}: năm {year} nằm ngoài {MIN_YEAR}–{MAX_YEAR}")
            if start is not None and end is not None and start > end:
                raise ValueError(f"{path}:{n}: năm bắt đầu {start} sau năm kết thúc {end}")
            grants.append(Grant(cols[0], cols[1], start, end))
    return grants


def principal_ref(raw: str) -> str:
    """Viết một principal đúng như `expand_principals` sẽ viết nó — lệch một chữ là không bao giờ khớp."""
    p = raw.strip().casefold()
    if p == PUBLIC_PRINCIPAL or p.startswith(("group:", "domain:")) or "@" not in p:
        return p
    return normalize_email(p)


async def load(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    kg_dir: Path,
    grants: list[Grant] | None = None,
) -> LoadReport:
    """Nạp đồ thị của đề vào `tenant_id`.

    `grants` là None ⇒ thế giới MỞ: mọi thực thể cấp cho `anyone`, ai hỏi cũng thấy hết — mốc
    so sánh khi chưa có mạng lưới quyền. Có `grants` ⇒ đúng những hàng ấy, và thực thể không
    hàng nào nhắc tới thì KHÔNG AI thấy: `item` mang ACL, fail-closed.
    """
    report = LoadReport()
    facts = merge_intervals(read_kg(kg_dir / "full.txt", report), report)
    names = read_labels(kg_dir / "wd_id2entity_text.txt")
    relation_names = read_labels(kg_dir / "wd_id2relation_text.txt")
    items = sorted({f.head for f in facts} | {f.tail for f in facts})

    async with conn.cursor() as cur, cur.copy(
        "COPY entities (tenant_id, kind, primary_key, canonical_name) FROM STDIN"
    ) as copy:
        for key in items:
            await copy.write_row((tenant_id, "item", key, names.get(key) or key))
    cur = await conn.execute(
        "SELECT id::text AS id, primary_key FROM entities WHERE tenant_id = %s AND kind = 'item'",
        (tenant_id,),
    )
    id_of = {r["primary_key"]: r["id"] for r in await cur.fetchall()}
    report.entities = len(id_of)

    observed = utc_now()
    async with conn.cursor() as cur, cur.copy(
        "COPY edges (tenant_id, src_entity_id, dst_entity_id, relation,"
        " valid_from, valid_to, observed_at, attrs) FROM STDIN"
    ) as copy:
        for f in facts:
            await copy.write_row((
                tenant_id, id_of[f.head], id_of[f.tail],
                relation_names.get(f.relation) or f.relation,
                year_start(f.start), year_start(f.end + 1), observed,
                Jsonb({"pid": f.relation}),
            ))
    report.facts = len(facts)

    if grants is None:
        grants = [Grant(key, PUBLIC_PRINCIPAL) for key in items]
    rows = []
    for g in grants:
        entity_id = id_of.get(g.item)
        if entity_id is None:
            report.unknown_grant_items += 1
            continue
        rows.append((
            tenant_id, entity_id, principal_ref(g.principal), "reader",
            year_start(g.start if g.start is not None else MIN_YEAR),
            year_start(g.end + 1) if g.end is not None else None,
            observed,
        ))
    async with conn.cursor() as cur, cur.copy(
        "COPY acl (tenant_id, object_entity_id, principal_ref, role,"
        " valid_from, valid_to, observed_at) FROM STDIN"
    ) as copy:
        for row in rows:
            await copy.write_row(row)
    report.grants = len(rows)
    return report


@dataclass(frozen=True, slots=True)
class Question:
    qid: str
    text: str
    type: str  # simple_entity · simple_time · before_after · first_last · time_join
    answer_type: str  # "entity" hoặc "time"
    entities: tuple[str, ...]  # Wikidata id — đề cho sẵn, CronKGQA giả định nhận diện thực thể hoàn hảo
    times: tuple[int, ...]
    answers: tuple[str, ...]  # Wikidata id, hoặc năm viết thành chuỗi
    relations: tuple[str, ...]


def question_from_dict(qid: str, raw: dict[str, Any]) -> Question:
    return Question(
        qid=qid,
        text=str(raw["question"]),
        type=str(raw.get("type", "")),
        answer_type=str(raw["answer_type"]),
        entities=tuple(sorted(str(e) for e in raw.get("entities", ()))),
        times=tuple(sorted(int(t) for t in raw.get("times", ()))),
        answers=tuple(sorted(str(a) for a in raw["answers"])),
        relations=tuple(sorted(str(r) for r in raw.get("relations", ()))),
    )


def read_questions(path: Path) -> list[Question]:
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [question_from_dict(str(row["qid"]), row) for row in rows]
    if path.suffix == ".pickle":
        # ⚠️ pickle CHẠY mã nằm trong file lúc đọc. Chỉ mở `questions/*.pickle` tải từ nguồn
        # chính thức của CronKGQA, và đổi MỘT lần sang JSONL (`convert` bên dưới) — mọi lần
        # chạy sau đọc JSONL.
        with path.open("rb") as f:
            raw = pickle.load(f)  # noqa: S301 — định dạng chính thức của đề, xem cảnh báo trên
        return [question_from_dict(str(d.get("uniq_id", i)), d) for i, d in enumerate(raw)]
    raise ValueError(f"không đọc được {path}: chỉ nhận .jsonl hoặc .pickle")


def write_questions(questions: Iterable[Question], path: Path) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps({
                "qid": q.qid, "question": q.text, "type": q.type, "answer_type": q.answer_type,
                "entities": list(q.entities), "times": list(q.times),
                "answers": list(q.answers), "relations": list(q.relations),
            }, ensure_ascii=False) + "\n")
            n += 1
    return n


def as_of_for(question: Question, *, now: datetime) -> datetime:
    """Đồ thị được nhìn ở mốc nào cho câu này.

    Câu có năm ⇒ 1/1 của năm sớm nhất trong câu: một fact đúng trong năm Y ⇔ nó đúng tại 1/1/Y.
    Câu không có năm (hỏi "khi nào", "đầu tiên/cuối cùng", "trước/sau") ⇒ `now`. Đó là ngữ nghĩa
    ẢNH CHỤP của lõi đồ thị, và nó không trả lời được những câu ấy — bộ chạy để lộ điều đó ra
    thành số, chứ không che đi.
    """
    return year_start(question.times[0]) if question.times else now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_graph.cronquestions")
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert", help="đổi questions/*.pickle sang JSONL, một lần")
    convert.add_argument("source", type=Path)
    convert.add_argument("target", type=Path)
    args = parser.parse_args(argv)
    n = write_questions(read_questions(args.source), args.target)
    print(f"{n} câu → {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
