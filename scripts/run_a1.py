"""Run A1: CRONKGQA prediction followed by event-level permission filtering."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a0.generative_qa import (DEFAULT_MODEL, DEFAULT_MODEL_REVISION,
                              DEFAULT_RETRIEVAL_BUDGET, GenerativeKGQA)  # noqa: E402
from evaluation.kg_index import KGEventIndex  # noqa: E402
from evaluation.runner import post_generation_filter  # noqa: E402


def load_permissions(path: Path):
    roles_path = path.parent / path.name.replace("permissions_", "roles_").replace(".jsonl", ".json")
    roles = json.loads(roles_path.read_text(encoding="utf-8"))
    role_events = {role_id: set(map(str, data["allowed_event_ids"]))
                   for role_id, data in roles.items()}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        qid = int(row["question_id"])
        if row.get("allowed_event_ids"):
            result[qid] = set(map(str, row["allowed_event_ids"]))
        elif row.get("role_id") in role_events:
            result[qid] = role_events[row["role_id"]]
        else:
            raise ValueError(f"Cannot resolve role for question {qid}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tightness", choices=("loose", "medium", "strict"), required=True)
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--permission-file", type=Path)
    parser.add_argument("--kg", type=Path, default=ROOT / "data" / "wikidata_big" / "kg" / "full.txt")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Must match the A0 model and Phu's A2 model")
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION,
                        help="HF model commit; resolved commit is recorded in metadata")
    parser.add_argument("--retrieval-budget", type=int, default=DEFAULT_RETRIEVAL_BUDGET)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "a1")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive")

    if args.retrieval_budget < 1 or args.max_new_tokens < 1:
        parser.error("--retrieval-budget and --max-new-tokens must be positive")
    eval_questions = json.loads(args.eval_set.read_text(encoding="utf-8"))["questions"]
    permissions_path = args.permission_file or ROOT / "permission" / "output" / f"permissions_{args.tightness}.jsonl"
    permissions = load_permissions(permissions_path)
    selected_ids = {int(q["uniq_id"]) for q in eval_questions}
    if set(permissions) != selected_ids:
        raise ValueError("Permission labels do not exactly match locked eval set")

    print(f"[1/3] Building KG event index from {args.kg}...", flush=True)
    index_started = time.perf_counter()
    kg_index = KGEventIndex.from_file(args.kg)
    print(f"KG index ready ({time.perf_counter() - index_started:.1f}s).", flush=True)
    print("[2/3] Loading CPU model...", flush=True)
    engine = GenerativeKGQA(kg_index, args.model, args.max_new_tokens, args.threads, args.revision)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"outputs_A1_{args.tightness}.jsonl"
    print(f"[3/3] Generating predictions for {len(eval_questions)} questions...", flush=True)
    print(f"\r[{'-' * 30}] 0/{len(eval_questions)} (0.0%) waiting for first answer...",
          end="", flush=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        run_started = time.perf_counter()
        for offset in range(0, len(eval_questions), 1):
            question = eval_questions[offset]
            start = time.perf_counter()
            prediction = engine.predict(question, args.retrieval_budget)
            answer, reasoning = post_generation_filter(
                prediction["answer"], prediction["reasoning"], permissions[int(question["uniq_id"])],
                question_entities=set(question.get("entities", [])),
                relations=set(question.get("relations", [])), event_index=kg_index,
            )
            row = {
                "question_id": int(question["uniq_id"]),
                "answer": answer,
                "reasoning": reasoning,
                "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                "tokens": prediction["tokens"],
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            completed = offset + 1
            elapsed = time.perf_counter() - run_started
            rate = completed / elapsed if elapsed else 0.0
            eta = (len(eval_questions) - completed) / rate if rate else 0.0
            width = 30
            filled = int(width * completed / len(eval_questions))
            bar = "#" * filled + "-" * (width - filled)
            print(f"\r[{bar}] {completed}/{len(eval_questions)} "
                  f"({completed / len(eval_questions):.1%}) elapsed {elapsed / 60:.1f}m, "
                  f"ETA {eta / 60:.1f}m", end="", flush=True)
    print("", flush=True)
    print(f"Saved A1 outputs: {out_path}")
    (args.out_dir / f"run_metadata_A1_{args.tightness}.json").write_text(json.dumps({
        "model": args.model, "model_revision": engine.resolved_revision,
        "requested_revision": args.revision, "device": "cpu", "retrieval_budget_events": args.retrieval_budget,
        "max_new_tokens": args.max_new_tokens, "threads": args.threads,
        "permission_filter": True, "asof_filter": False,
        "tightness": args.tightness,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
