"""Chạy A0 và A2 bản đối chiếu ở ba mức độ chặt, XEN KẼ từng câu, trong một tiến trình.

Một máy, một mô hình đã nạp, một vòng lặp: với mỗi câu chạy lần lượt A0, A2-loose, A2-medium,
A2-strict (thứ tự xoay vòng theo câu), rồi mới sang câu sau. Mọi trôi dạt của máy theo thời gian —
máy nóng lên rồi hạ xung, tiến trình khác chen vào — vì thế rơi đều lên cả bốn nhánh, và độ trễ của
chúng so được với nhau. Chạy trọn từng nhánh nối tiếp nhau thì không: trên Apple M5, độ trễ của A0
tăng từ 4 s lên 8–9 s/câu trong 20 phút đầu dù số token không đổi.

A0 lấy sự kiện bằng `KGEventIndex.retrieve_question` y như `run_a0.py`; A2 bằng truy vấn đồ thị
mang vị từ quyền và as-of (`agent_graph.a2`) y như `run_a2.py`. Cả hai gọi cùng một
`GenerativeKGQA.predict`. File đầu ra chỉ có đúng 5 trường của Output Contract, nên
`evaluate_outputs.py` và `run_a1.py --generations` dùng thẳng; vai trò, mốc as-of và các sự kiện A2
đã lấy nằm riêng ở `context_A2_{mức}.jsonl`. Chạy lại đúng lệnh thì tiếp tục từ chỗ còn thiếu.

    uv run --extra gen python scripts/run_interleaved.py --out-dir results/round2
"""

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from a2_common import LEVELS, load_frozen_roles  # noqa: E402

from a0.generative_qa import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    DEFAULT_RETRIEVAL_BUDGET,
    GenerativeKGQA,
)
from agent_graph.a2 import (  # noqa: E402
    GraphRetriever,
    Scope,
    connect_sync,
    create_tenant,
    load_events,
    principal_for_role,
    question_as_of,
)
from agent_graph.run import embedded_postgres  # noqa: E402
from evaluation.kg_index import KGEventIndex  # noqa: E402

BRANCHES = ("A0", *(f"A2-{level}" for level in LEVELS))


def _complete_rows(path: Path) -> list[str]:
    """Các dòng JSON đã ghi trọn; dòng cuối dở dang (tiến trình bị ngắt) bị bỏ."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if not line.endswith("\n"):
            break
        try:
            json.loads(line)
        except json.JSONDecodeError:
            break
        rows.append(line)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg-dir", type=Path, default=ROOT / "data" / "wikidata_big" / "kg")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--retrieval-budget", type=int, default=DEFAULT_RETRIEVAL_BUDGET)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="chỉ N câu đầu (chạy thử)")
    args = parser.parse_args()

    payload = json.loads(args.eval_set.read_text(encoding="utf-8"))
    questions = payload["questions"]
    expected = int(payload.get("n", 1500))
    if len(questions) != expected or len({int(q["uniq_id"]) for q in questions}) != expected:
        raise SystemExit(f"Locked eval set expected {expected} unique questions; found {len(questions)}")
    if args.limit:
        questions = questions[: args.limit]

    frozen = {level: load_frozen_roles(level) for level in LEVELS}
    grants = {principal_for_role(role_id): ids
              for _, allowed in frozen.values() for role_id, ids in allowed.items()}
    index = KGEventIndex.from_file(args.kg_dir / "full.txt")

    paths = {"A0": args.out_dir / "a0" / "outputs_A0.jsonl"}
    paths.update({f"A2-{level}": args.out_dir / "a2" / f"outputs_A2_{level}.jsonl" for level in LEVELS})
    # evaluate_outputs.py nhận đúng 5 trường của Output Contract, nên vết truy hồi của A2 ở file riêng.
    context_paths = {f"A2-{level}": args.out_dir / "a2" / f"context_A2_{level}.jsonl" for level in LEVELS}
    done: dict[str, set[int]] = {}
    for branch, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = _complete_rows(path)
        path.write_text("".join(rows), encoding="utf-8")  # cắt dòng dở dang trước khi ghi tiếp
        done[branch] = {int(json.loads(row)["question_id"]) for row in rows}
        if branch in context_paths:  # vết được ghi trước đầu ra: bỏ vết của câu chưa có đầu ra
            kept = [row for row in _complete_rows(context_paths[branch])
                    if int(json.loads(row)["question_id"]) in done[branch]]
            context_paths[branch].write_text("".join(kept), encoding="utf-8")
    remaining = sum(int(q["uniq_id"]) not in done[b] for q in questions for b in BRANCHES)
    print(f"Còn {remaining} lượt sinh ({len(questions)} câu × {len(BRANCHES)} nhánh, "
          f"đã xong {len(questions) * len(BRANCHES) - remaining}).", flush=True)

    latency_sum = dict.fromkeys(BRANCHES, 0.0)
    with embedded_postgres() as dsn:
        conn = connect_sync(dsn)
        try:
            started = time.perf_counter()
            tenant_id = create_tenant(conn, "a2-interleaved")
            report = load_events(conn, tenant_id=tenant_id, kg_dir=args.kg_dir, grants=grants)
            print("load:", json.dumps(asdict(report)),
                  f"({time.perf_counter() - started:.1f}s)", flush=True)
            retriever = GraphRetriever(conn, tenant_id)
            engine = GenerativeKGQA(index, args.model, args.max_new_tokens, args.threads, args.revision)

            streams = {branch: path.open("a", encoding="utf-8", newline="\n")
                       for branch, path in paths.items()}
            context_streams = {branch: path.open("a", encoding="utf-8", newline="\n")
                               for branch, path in context_paths.items()}
            try:
                run_started, generated = time.perf_counter(), 0
                for i, question in enumerate(questions):
                    qid = int(question["uniq_id"])
                    order = BRANCHES[i % len(BRANCHES):] + BRANCHES[: i % len(BRANCHES)]
                    for branch in order:
                        if qid in done[branch]:
                            continue
                        row_extra = {}
                        if branch == "A0":
                            engine.event_index = index
                        else:
                            level = branch.removeprefix("A2-")
                            role_id = frozen[level][0][qid]
                            scope = Scope(principal_for_role(role_id), question_as_of(question))
                            engine.event_index = retriever.scoped(scope)
                            row_extra = {"role_id": role_id,
                                         "as_of_year": None if scope.as_of is None else scope.as_of.year}
                        started = time.perf_counter()
                        prediction = engine.predict(question, args.retrieval_budget)
                        latency_ms = (time.perf_counter() - started) * 1000
                        latency_sum[branch] += latency_ms
                        row = {
                            "question_id": qid,
                            "answer": prediction["answer"],
                            "reasoning": prediction["reasoning"],
                            "latency_ms": round(latency_ms, 3),
                            "tokens": prediction["tokens"],
                        }
                        if branch in context_streams:
                            context_streams[branch].write(json.dumps(
                                {"question_id": qid, **row_extra,
                                 "retrieved_event_ids": prediction["retrieved_event_ids"]},
                                ensure_ascii=False) + "\n")
                            context_streams[branch].flush()
                        streams[branch].write(json.dumps(row, ensure_ascii=False) + "\n")
                        streams[branch].flush()
                        generated += 1
                    elapsed = time.perf_counter() - run_started
                    eta = (remaining - generated) * elapsed / generated if generated else 0.0
                    print(f"\r{i + 1}/{len(questions)} câu, {generated}/{remaining} lượt sinh, "
                          f"{elapsed / 60:.1f} phút, còn ~{eta / 60:.0f} phút", end="", flush=True)
                print("", flush=True)
            finally:
                for stream in (*streams.values(), *context_streams.values()):
                    stream.close()
        finally:
            conn.close()

    import transformers  # đã được GenerativeKGQA nạp; ở đây chỉ để ghi phiên bản

    common = {
        "model": args.model, "model_revision": engine.resolved_revision,
        "requested_revision": args.revision, "device": "cpu", "threads": args.threads,
        "retrieval_budget_events": args.retrieval_budget, "max_new_tokens": args.max_new_tokens,
        "runner": "scripts/run_interleaved.py: A0 và A2 ba mức xen kẽ từng câu, xoay vòng thứ tự",
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "torch": engine.torch.__version__, "transformers": transformers.__version__,
        },
    }
    (args.out_dir / "a0" / "run_metadata.json").write_text(json.dumps(
        {**common, "branch": "A0", "permission_filter": False, "asof_filter": False},
        ensure_ascii=False, indent=2), encoding="utf-8")
    for level in LEVELS:
        (args.out_dir / "a2" / f"run_metadata_A2_{level}.json").write_text(json.dumps({
            **common, "branch": "A2", "tightness": level,
            "implementation": "A2 bản đối chiếu: agent_graph.a2, truy vấn đồ thị trên Postgres nhúng",
            "permission_filter": True, "asof_filter": True,
            "permission_granularity": "sự kiện: edge_acl, edges.id = event id (số dòng full.txt)",
            "load_report": asdict(report),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Xong:", ", ".join(str(path) for path in paths.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
