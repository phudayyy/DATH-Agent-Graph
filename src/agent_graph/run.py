"""Chạy một tập câu CRONQUESTIONS qua đồ thị.

    python -m agent_graph.run --kg data/wikidata_big/kg --questions locked.jsonl --out runs/a.jsonl

Không có `--dsn` ⇒ dựng một Postgres nhúng (pgserver) trong thư mục tạm, xoá khi xong.
Mỗi câu ghi một dòng JSON; cuối cùng in độ phủ đáp án theo loại câu — "đáp án có nằm trong
gói không", trần trên của Hits@1 cho mọi cách trả lời chỉ đọc gói này.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from agent_graph.backend import LocalBackend, answer_in
from agent_graph.cronquestions import as_of_for, load, read_grants, read_questions
from agent_graph.db import apply_schema, connect, has_schema, new_tenant
from brain.clock import utc_now


@contextmanager
def _embedded_postgres() -> Iterator[str]:
    from pgserver.postgres_server import get_server

    data_dir = Path(tempfile.mkdtemp(prefix="agent-graph-pg-"))
    server = get_server(data_dir, cleanup_mode="delete")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()
        shutil.rmtree(data_dir, ignore_errors=True)


async def run(args: argparse.Namespace, dsn: str) -> dict[str, dict[str, int]]:
    conn = await connect(dsn)
    try:
        # ⛔ Không dựng đè lên một database đã có bảng: `--dsn` có thể trỏ nhầm vào dữ liệu thật.
        if await has_schema(conn):
            raise SystemExit(f"{dsn}: database đã có bảng `entities` — cần một database trống")
        await apply_schema(conn)
        tenant_id = await new_tenant(conn, "cronquestions")
        grants = read_grants(args.acl) if args.acl else None
        report = await load(conn, tenant_id=tenant_id, kg_dir=args.kg, grants=grants)
        print("load:", json.dumps(asdict(report)))

        questions = read_questions(args.questions)
        if args.limit:
            questions = questions[: args.limit]
        backend = LocalBackend(conn, tenant_id)
        now = utc_now()
        totals: Counter[str] = Counter()
        hits: Counter[str] = Counter()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as out:
            for q in questions:
                as_of = as_of_for(q, now=now)
                reply = await backend.ask(q, identity=args.identity, as_of=as_of)
                hit = answer_in(reply, q)
                totals[q.type] += 1
                hits[q.type] += hit
                out.write(json.dumps({
                    "qid": q.qid, "type": q.type, "answer_type": q.answer_type,
                    "identity": args.identity, "as_of": as_of.isoformat(),
                    "answer_in_pack": hit, "pack": reply.text,
                }, ensure_ascii=False) + "\n")
    finally:
        await conn.close()
    return {t: {"questions": totals[t], "answer_in_pack": hits[t]} for t in sorted(totals)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_graph.run")
    parser.add_argument("--kg", type=Path, required=True, help="thư mục kg/ của data_v2")
    parser.add_argument("--questions", type=Path, required=True, help=".jsonl, hoặc .pickle của đề")
    parser.add_argument("--out", type=Path, required=True, help="mỗi câu một dòng JSON")
    parser.add_argument("--acl", type=Path, help="mạng lưới quyền; bỏ trống ⇒ thế giới mở")
    parser.add_argument("--identity", default="anyone", help="người hỏi: email, hoặc `anyone`")
    parser.add_argument("--limit", type=int, default=0, help="chỉ chạy N câu đầu")
    parser.add_argument("--dsn", help="Postgres TRỐNG để dùng; bỏ trống ⇒ dựng Postgres nhúng")
    args = parser.parse_args(argv)

    if args.dsn:
        summary = asyncio.run(run(args, args.dsn))
    else:
        with _embedded_postgres() as dsn:
            summary = asyncio.run(run(args, dsn))

    total = sum(s["questions"] for s in summary.values())
    found = sum(s["answer_in_pack"] for s in summary.values())
    for kind, s in summary.items():
        print(f"{kind:>14}: {s['answer_in_pack']:>5} / {s['questions']:<5} đáp án nằm trong gói")
    print(f"{'tổng':>14}: {found:>5} / {total:<5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
