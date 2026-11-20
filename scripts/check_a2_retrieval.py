"""Kiểm, trên dữ liệu thật, rằng A2 truy hồi đúng bằng A0 trên phần đồ thị người hỏi được thấy.

Với mỗi câu của tập đóng băng và mỗi mức độ chặt, so danh sách sự kiện (đúng thứ tự) do truy vấn
SQL của A2 trả về với `KGEventIndex.retrieve_question` — đúng hàm A0 dùng — chạy trên khung nhìn
chỉ gồm sự kiện vai trò của câu được thấy và đúng tại mốc hỏi. Khớp hết thì giữa A0 và A2 chỉ còn
khác nhau ở chỗ đặt chặn. Ô `none` (tắt cả hai vị từ) còn đếm số câu A2 lấy khác A0 vì những dòng
lược đồ không chứa được (khoảng rỗng, tự trỏ).

    uv run python scripts/check_a2_retrieval.py
    uv run python scripts/check_a2_retrieval.py --levels strict --limit 100
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from a2_common import LEVELS, load_frozen_roles  # noqa: E402

from agent_graph.a2 import (  # noqa: E402
    EventLoadReport,
    GraphRetriever,
    Scope,
    connect_sync,
    create_tenant,
    load_events,
    principal_for_role,
    question_as_of,
    read_events,
)
from agent_graph.run import embedded_postgres  # noqa: E402
from evaluation.kg_index import KGEventIndex, event_is_valid  # noqa: E402


class _VisibleLists:
    def __init__(self, index, keep):
        self._index, self._keep = index, keep

    def get(self, key, default=()):
        return [eid for eid in self._index.by_entity.get(key, default) if self._keep(eid)]


class _VisibleGraph:
    """KGEventIndex nhìn qua một bộ lọc; `retrieve_question` của A0 chạy nguyên văn trên nó."""

    def __init__(self, index, keep):
        self.events = index.events
        self.by_entity = _VisibleLists(index, keep)


def _ids(events):
    return [event[0] for event in events]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels", nargs="+", choices=LEVELS, default=list(LEVELS))
    parser.add_argument("--limit", type=int, default=0, help="chỉ kiểm N câu đầu")
    parser.add_argument("--budget", type=int, default=64)
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg-dir", type=Path, default=ROOT / "data" / "wikidata_big" / "kg")
    args = parser.parse_args()

    questions = json.loads(args.eval_set.read_text(encoding="utf-8"))["questions"]
    if args.limit:
        questions = questions[: args.limit]
    n = len(questions)
    index = KGEventIndex.from_file(args.kg_dir / "full.txt")
    loaded = {event[0] for event in read_events(args.kg_dir / "full.txt", EventLoadReport())}
    frozen = {level: load_frozen_roles(level) for level in args.levels}
    grants = {principal_for_role(role_id): ids
              for _, allowed in frozen.values() for role_id, ids in allowed.items()}

    failures = 0
    with embedded_postgres() as dsn:
        conn = connect_sync(dsn)
        try:
            tenant_id = create_tenant(conn, "a2-check")
            started = time.perf_counter()
            report = load_events(conn, tenant_id=tenant_id, kg_dir=args.kg_dir, grants=grants)
            print("load:", json.dumps(asdict(report)), f"({time.perf_counter() - started:.1f}s)")
            retriever = GraphRetriever(conn, tenant_id)

            for cell in ("none", *args.levels):
                same = empty = size = differs_from_a0 = 0
                sql_seconds = 0.0
                mismatched = []
                for question in questions:
                    qid = int(question["uniq_id"])
                    if cell == "none":
                        allowed, as_of, principal = None, None, None
                    else:
                        role_of, allowed_by_role = frozen[cell]
                        allowed = set(allowed_by_role[role_of[qid]])
                        as_of = question_as_of(question)
                        principal = principal_for_role(role_of[qid])
                    year = None if as_of is None else as_of.year

                    def keep(event_id, allowed=allowed, year=year):
                        *_, start, end = index.events[event_id]
                        return (event_id in loaded
                                and (allowed is None or event_id in allowed)
                                and (year is None or event_is_valid(start, end, year)))

                    expected = _ids(KGEventIndex.retrieve_question(
                        _VisibleGraph(index, keep), question, args.budget))
                    started = time.perf_counter()
                    got = _ids(retriever.retrieve(question, Scope(principal, as_of), args.budget))
                    sql_seconds += time.perf_counter() - started
                    if got == expected:
                        same += 1
                    else:
                        mismatched.append(qid)
                    if allowed is not None and not set(got) <= allowed:
                        mismatched.append(qid)
                    size += len(got)
                    empty += not got
                    if cell == "none" and got != _ids(index.retrieve_question(question, args.budget)):
                        differs_from_a0 += 1
                line = (f"{cell:>7}: khớp {same}/{n} | {size / n:.1f} sự kiện/câu | "
                        f"{empty} câu không lấy được sự kiện nào | SQL {1000 * sql_seconds / n:.0f} ms/câu")
                if cell == "none":
                    line += f" | khác A0 vì dòng không nạp được: {differs_from_a0} câu"
                print(line, flush=True)
                if mismatched:
                    failures += len(mismatched)
                    print("   lệch ở câu:", sorted(set(mismatched))[:10])
        finally:
            conn.close()
    print("A2 RETRIEVAL CHECK:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
