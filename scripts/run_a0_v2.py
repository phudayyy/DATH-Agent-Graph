"""Run A0-v2 with normalized answers and a retrieval-provenance sidecar."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a0.generative_qa import (  # noqa: E402
    A0_FORMAT_VERSION,
    DEFAULT_MODEL,
    DEFAULT_MODEL_REVISION,
    DEFAULT_RETRIEVAL_BUDGET,
    GenerativeKGQA,
)
from evaluation.hits_at_1 import compute_hits_at_1  # noqa: E402
from evaluation.kg_index import KGEventIndex  # noqa: E402

OUTPUT_FIELDS = {
    "question_id", "answer", "reasoning", "latency_ms", "tokens"
}
PROVENANCE_FIELDS = {
    "question_id", "retrieved_event_ids", "answer_type",
    "question_type", "answer_format_status", "raw_completion",
}


def _read_jsonl(path: Path, valid_ids: set[int]) -> dict[int, dict]:
    rows: dict[int, dict] = {}
    if not path.exists():
        return rows
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not raw.endswith(("\n", "\r")):
                print(f"WARNING: ignoring torn final line: {path}")
                continue
            raise
        qid = int(row["question_id"])
        if qid not in valid_ids:
            raise ValueError(f"Unexpected question_id {qid} in {path}")
        if qid in rows:
            raise ValueError(f"Duplicate question_id {qid} in {path}")
        rows[qid] = row
    return rows


def _write_jsonl(path: Path, rows: dict[int, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for qid in sorted(rows):
            stream.write(json.dumps(rows[qid], ensure_ascii=False) + "\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg", type=Path, default=ROOT / "data" / "wikidata_big" / "kg" / "full.txt")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "a0_v2")
    parser.add_argument("--retrieval-budget", type=int, default=DEFAULT_RETRIEVAL_BUDGET)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    if min(args.retrieval_budget, args.max_new_tokens, args.threads) < 1:
        parser.error("retrieval budget, max new tokens, and threads must be positive")

    payload = json.loads(args.eval_set.read_text(encoding="utf-8"))
    questions = payload["questions"]
    expected = int(payload.get("n", len(questions)))
    all_ids = {int(q["uniq_id"]) for q in questions}
    if len(questions) != expected or len(all_ids) != expected:
        raise SystemExit(f"Expected {expected} unique questions; found {len(all_ids)}")
    if args.limit:
        questions = questions[:args.limit]
    selected_ids = {int(q["uniq_id"]) for q in questions}
    by_id = {int(q["uniq_id"]): q for q in questions}

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs_path = args.out_dir / "outputs_A0.jsonl"
    provenance_path = args.out_dir / "provenance_A0.jsonl"
    errors_path = args.out_dir / "errors_A0.jsonl"
    metadata_path = args.out_dir / "run_metadata.json"
    metrics_path = args.out_dir / "metrics_A0.json"

    if args.restart:
        for path in (outputs_path, provenance_path, errors_path, metadata_path, metrics_path):
            if path.exists():
                path.unlink()

    # Resume only complete output/provenance pairs. Rows without provenance
    # are regenerated so A0-v2 never silently reuses legacy incomplete rows.
    output_rows = _read_jsonl(outputs_path, selected_ids)
    provenance_rows = _read_jsonl(provenance_path, selected_ids)
    done_ids = set(output_rows) & set(provenance_rows)
    output_rows = {qid: output_rows[qid] for qid in done_ids}
    provenance_rows = {qid: provenance_rows[qid] for qid in done_ids}
    _write_jsonl(outputs_path, output_rows)
    _write_jsonl(provenance_path, provenance_rows)

    settings = {
        "model": args.model,
        "requested_revision": args.revision,
        "device": "cpu",
        "threads": args.threads,
        "retrieval_budget_events": args.retrieval_budget,
        "max_new_tokens": args.max_new_tokens,
        "permission_filter": False,
        "asof_filter": False,
        "a0_format_version": A0_FORMAT_VERSION,
        "provenance_file": provenance_path.name,
    }

    if metadata_path.exists():
        previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        mismatches = [k for k, v in settings.items() if previous.get(k) != v]
        if mismatches:
            raise SystemExit(
                "Cannot resume A0-v2 with different settings: " + ", ".join(mismatches)
            )

    print(f"[1/3] Building KG event index from {args.kg}...", flush=True)
    started = time.perf_counter()
    event_index = KGEventIndex.from_file(args.kg)
    print(f"KG index ready ({time.perf_counter() - started:.1f}s).", flush=True)

    print(
        f"[2/3] A0-v2 generation: {len(questions)} questions; "
        f"{len(done_ids)} complete output/provenance pairs already saved.",
        flush=True,
    )

    engine = None
    if len(done_ids) < len(questions):
        print("Loading CPU model...", flush=True)
        engine = GenerativeKGQA(
            event_index,
            args.model,
            args.max_new_tokens,
            args.threads,
            args.revision,
        )
        metadata = {
            **settings,
            # The current model/tokenizer load is the only source of a
            # resolved revision. Never read from `metadata` before it exists.
            "model_revision": engine.resolved_revision,
            "resolved_revision_status": (
                "verified" if engine.resolved_revision else "unknown_from_runtime"
            ),
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        print("A0-v2 is already complete; no model will be loaded.", flush=True)

    total = correct = total_tokens = 0
    latency_sum = 0.0
    by_type = defaultdict(lambda: {"correct": 0, "total": 0})
    format_counts = defaultdict(int)
    errors = []

    started_all = time.perf_counter()
    with outputs_path.open("a", encoding="utf-8", newline="\n") as output_stream, \
         provenance_path.open("a", encoding="utf-8", newline="\n") as provenance_stream:
        for index, question in enumerate(questions, 1):
            qid = int(question["uniq_id"])
            if qid in done_ids:
                row = output_rows[qid]
                hit = compute_hits_at_1(
                    row["answer"], set(map(str, question.get("answers", [])))
                )
                correct += int(hit.hits_at_1 == 1.0)
                total += 1
                total_tokens += int(row["tokens"])
                latency_sum += float(row["latency_ms"])
                qtype = str(question.get("type", "unknown"))
                by_type[qtype]["total"] += 1
                by_type[qtype]["correct"] += int(hit.hits_at_1 == 1.0)
                format_counts[provenance_rows[qid]["answer_format_status"]] += 1
                continue

            started = time.perf_counter()
            try:
                prediction = engine.predict(question, args.retrieval_budget)
            except Exception as exc:
                error = {"question_id": qid, "error": repr(exc)}
                errors.append(error)
                with errors_path.open("a", encoding="utf-8") as error_stream:
                    error_stream.write(json.dumps(error, ensure_ascii=False) + "\n")
                print(f"\nERROR on {qid}: {exc!r}", flush=True)
                continue

            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            row = {
                "question_id": qid,
                "answer": prediction["answer"],
                "reasoning": prediction["reasoning"],
                "latency_ms": latency_ms,
                "tokens": int(prediction["tokens"]),
            }
            provenance_row = {
                "question_id": qid,
                "retrieved_event_ids": prediction["retrieved_event_ids"],
                "answer_type": question.get("answer_type", ""),
                "question_type": question.get("type", ""),
                "answer_format_status": prediction["answer_format_status"],
                "raw_completion": prediction["raw_completion"],
            }

            output_stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            output_stream.flush()
            provenance_stream.write(json.dumps(provenance_row, ensure_ascii=False) + "\n")
            provenance_stream.flush()

            output_rows[qid] = row
            provenance_rows[qid] = provenance_row
            done_ids.add(qid)

            hit = compute_hits_at_1(
                row["answer"], set(map(str, question.get("answers", [])))
            )
            correct += int(hit.hits_at_1 == 1.0)
            total += 1
            total_tokens += row["tokens"]
            latency_sum += latency_ms
            qtype = str(question.get("type", "unknown"))
            by_type[qtype]["total"] += 1
            by_type[qtype]["correct"] += int(hit.hits_at_1 == 1.0)
            format_counts[prediction["answer_format_status"]] += 1

            if index % 25 == 0 or index == len(questions):
                elapsed = time.perf_counter() - started_all
                print(
                    f"A0-v2 {len(done_ids)}/{len(questions)} "
                    f"elapsed={elapsed / 60:.1f}m "
                    f"Hits@1_so_far={correct / max(total, 1):.3f}",
                    flush=True,
                )

    print("", flush=True)
    completed = len(set(output_rows) & set(provenance_rows))
    all_complete = completed == len(questions)

    metrics = {
        "branch": "A0-v2",
        "format_version": A0_FORMAT_VERSION,
        "model": args.model,
        "model_revision": metadata.get("model_revision"),
        "requested_revision": args.revision,
        "device": "cpu",
        "permission_filter": False,
        "asof_filter": False,
        "retrieval_budget_events": args.retrieval_budget,
        "max_new_tokens": args.max_new_tokens,
        "threads": args.threads,
        "n_questions": completed,
        "hits_at_1_correct": correct,
        "hits_at_1": correct / total if total else 0.0,
        "avg_latency_ms": latency_sum / total if total else 0.0,
        "avg_tokens": total_tokens / total if total else 0.0,
        "answer_format_status_counts": dict(format_counts),
        "by_question_type": {
            kind: {
                "correct": v["correct"],
                "total": v["total"],
                "hits_at_1": v["correct"] / v["total"] if v["total"] else 0.0,
            }
            for kind, v in sorted(by_type.items())
        },
        "provenance_rows": len(provenance_rows),
        "complete": all_complete,
    }
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"A0-v2 completed: {completed}/{len(questions)}")
    print(f"A0-v2 Hits@1: {metrics['hits_at_1']:.4f}")
    print("Answer format statuses:", dict(format_counts))
    print(f"Saved outputs: {outputs_path}")
    print(f"Saved provenance: {provenance_path}")

    return 0 if all_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
