"""Run the unfiltered shared generative KGQA baseline on CPU."""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a0.generative_qa import DEFAULT_MODEL, DEFAULT_RETRIEVAL_BUDGET, GenerativeKGQA  # noqa: E402
from evaluation.hits_at_1 import compute_hits_at_1  # noqa: E402
from evaluation.kg_index import KGEventIndex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg", type=Path, default=ROOT / "data" / "wikidata_big" / "kg" / "full.txt")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="HF model id or local model directory; same default must be used by A1/A2")
    parser.add_argument("--revision", default="main", help="HF commit/tag; resolved commit is recorded in metadata")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "a0")
    parser.add_argument("--retrieval-budget", type=int, default=DEFAULT_RETRIEVAL_BUDGET)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.retrieval_budget < 1 or args.max_new_tokens < 1 or args.threads < 1:
        parser.error("retrieval budget, max new tokens, and threads must be positive")

    payload = json.loads(args.eval_set.read_text(encoding="utf-8"))
    questions = payload["questions"]
    expected = int(payload.get("n", 1500))
    if len(questions) != expected or len({int(q["uniq_id"]) for q in questions}) != expected:
        raise SystemExit(f"Locked eval set expected {expected} unique questions; found {len(questions)}")

    print(f"[1/3] Building KG event index from {args.kg}...", flush=True)
    index_started = time.perf_counter()
    index = KGEventIndex.from_file(args.kg)
    print(f"KG index ready ({time.perf_counter() - index_started:.1f}s).", flush=True)
    print("[2/3] Loading CPU model...", flush=True)
    engine = GenerativeKGQA(index, args.model, args.max_new_tokens, args.threads, args.revision)
    print(f"[3/3] Generating predictions for {len(questions)} questions...", flush=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs_path = args.out_dir / "outputs_A0.jsonl"
    total = correct = total_tokens = 0
    latency_sum = 0.0
    by_type = defaultdict(lambda: {"correct": 0, "total": 0})
    print(f"\r[{'-' * 30}] 0/{len(questions)} (0.0%) waiting for first answer...",
          end="", flush=True)
    run_started = time.perf_counter()
    with outputs_path.open("w", encoding="utf-8", newline="\n") as out:
        for i, question in enumerate(questions, 1):
            started = time.perf_counter()
            prediction = engine.predict(question, args.retrieval_budget)
            latency_ms = (time.perf_counter() - started) * 1000
            gold = set(map(str, question.get("answers", [])))
            hit = compute_hits_at_1(prediction["answer"], gold).hits_at_1 == 1.0
            total += 1
            correct += int(hit)
            total_tokens += prediction["tokens"]
            latency_sum += latency_ms
            qtype = str(question.get("type", "unknown"))
            by_type[qtype]["total"] += 1
            by_type[qtype]["correct"] += int(hit)
            out.write(json.dumps({
                "question_id": int(question["uniq_id"]),
                "answer": prediction["answer"],
                "reasoning": prediction["reasoning"],
                "latency_ms": round(latency_ms, 3),
                "tokens": prediction["tokens"],
            }, ensure_ascii=False) + "\n")
            out.flush()
            elapsed = time.perf_counter() - run_started
            rate = i / elapsed if elapsed else 0.0
            eta = (len(questions) - i) / rate if rate else 0.0
            width = 30
            filled = int(width * i / len(questions))
            bar = "#" * filled + "-" * (width - filled)
            print(f"\r[{bar}] {i}/{len(questions)} ({i / len(questions):.1%}) "
                  f"elapsed {elapsed / 60:.1f}m, ETA {eta / 60:.1f}m", end="", flush=True)
    print("", flush=True)

    metrics = {
        "branch": "A0", "model": args.model, "model_revision": engine.resolved_revision,
        "device": "cpu",
        "permission_filter": False, "asof_filter": False,
        "retrieval_budget_events": args.retrieval_budget,
        "max_new_tokens": args.max_new_tokens,
        "token_count_semantics": "prompt plus generated tokens",
        "reasoning_available": True,
        "n_questions": total, "hits_at_1_correct": correct,
        "hits_at_1": correct / total if total else 0.0,
        "avg_latency_ms": latency_sum / total if total else 0.0,
        "avg_tokens": total_tokens / total if total else 0.0,
        "by_question_type": {
            kind: {"correct": val["correct"], "total": val["total"],
                   "hits_at_1": val["correct"] / val["total"] if val["total"] else 0.0}
            for kind, val in sorted(by_type.items())
        },
        "outputs_file": outputs_path.name,
    }
    (args.out_dir / "metrics_A0.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out_dir / "run_metadata.json").write_text(json.dumps({
        "model": args.model, "model_revision": engine.resolved_revision,
        "requested_revision": args.revision, "device": "cpu",
        "max_new_tokens": args.max_new_tokens, "permission_filter": False, "asof_filter": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"A0 Hits@1: {metrics['hits_at_1']:.4f} ({correct}/{total})")
    print(f"Saved outputs: {outputs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
