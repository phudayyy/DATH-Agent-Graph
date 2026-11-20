"""Run A2 (reference implementation): the same generative model, prompt and retrieval budget as
A0, but retrieval is a graph query that carries the permission and as-of predicates
(agent_graph.a2) on an embedded Postgres. Out-of-scope events never reach the model.

The outputs file holds exactly the five Output Contract fields, which is all evaluate_outputs.py
accepts; the role, the as-of year and the retrieved event ids go to a context_*.jsonl beside it.

    uv run --extra gen python scripts/run_a2.py --tightness strict
    uv run --extra gen python scripts/run_a2.py --tightness strict --no-asof   # ablation cell
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
from evaluation.hits_at_1 import compute_hits_at_1  # noqa: E402


def branch_label(permission: bool, asof: bool) -> str:
    if permission and asof:
        return "A2"
    if permission:
        return "A2-perm-only"
    return "A2-asof-only" if asof else "A2-none"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tightness", choices=LEVELS, required=True)
    parser.add_argument("--no-permission", action="store_true", help="turn the permission predicate off")
    parser.add_argument("--no-asof", action="store_true", help="turn the as-of predicate off")
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg-dir", type=Path, default=ROOT / "data" / "wikidata_big" / "kg")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "a2")
    parser.add_argument("--retrieval-budget", type=int, default=DEFAULT_RETRIEVAL_BUDGET)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions (smoke run)")
    args = parser.parse_args()
    if args.retrieval_budget < 1 or args.max_new_tokens < 1 or args.threads < 1:
        parser.error("retrieval budget, max new tokens, and threads must be positive")

    permission, asof = not args.no_permission, not args.no_asof
    label = branch_label(permission, asof)

    payload = json.loads(args.eval_set.read_text(encoding="utf-8"))
    questions = payload["questions"]
    expected = int(payload.get("n", 1500))
    if len(questions) != expected or len({int(q["uniq_id"]) for q in questions}) != expected:
        raise SystemExit(f"Locked eval set expected {expected} unique questions; found {len(questions)}")
    if args.limit:
        questions = questions[: args.limit]

    role_of, allowed_by_role = load_frozen_roles(args.tightness)
    grants = ({principal_for_role(role_id): ids for role_id, ids in allowed_by_role.items()}
              if permission else None)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs_path = args.out_dir / f"outputs_{label}_{args.tightness}.jsonl"
    context_path = args.out_dir / f"context_{label}_{args.tightness}.jsonl"
    total = correct = total_tokens = 0
    latency_sum = 0.0
    with embedded_postgres() as dsn:
        conn = connect_sync(dsn)
        try:
            print(f"[1/3] Loading the graph into embedded Postgres ({args.kg_dir})...", flush=True)
            started = time.perf_counter()
            tenant_id = create_tenant(conn, f"a2-{args.tightness}")
            report = load_events(conn, tenant_id=tenant_id, kg_dir=args.kg_dir, grants=grants)
            print("load:", json.dumps(asdict(report)),
                  f"({time.perf_counter() - started:.1f}s)", flush=True)
            retriever = GraphRetriever(conn, tenant_id)

            print("[2/3] Loading CPU model...", flush=True)
            engine = GenerativeKGQA(retriever.scoped(Scope(None, None)), args.model,
                                    args.max_new_tokens, args.threads, args.revision)
            print(f"[3/3] {label} ({args.tightness}): generating for {len(questions)} questions...",
                  flush=True)
            run_started = time.perf_counter()
            with (outputs_path.open("w", encoding="utf-8", newline="\n") as out,
                  context_path.open("w", encoding="utf-8", newline="\n") as context):
                for i, question in enumerate(questions, 1):
                    qid = int(question["uniq_id"])
                    role_id = role_of[qid]
                    scope = Scope(
                        principal=principal_for_role(role_id) if permission else None,
                        as_of=question_as_of(question) if asof else None,
                    )
                    engine.event_index = retriever.scoped(scope)
                    started = time.perf_counter()
                    prediction = engine.predict(question, args.retrieval_budget)
                    latency_ms = (time.perf_counter() - started) * 1000
                    gold = set(map(str, question.get("answers", [])))
                    correct += int(compute_hits_at_1(prediction["answer"], gold).hits_at_1 == 1.0)
                    total += 1
                    total_tokens += prediction["tokens"]
                    latency_sum += latency_ms
                    out.write(json.dumps({
                        "question_id": qid,
                        "answer": prediction["answer"],
                        "reasoning": prediction["reasoning"],
                        "latency_ms": round(latency_ms, 3),
                        "tokens": prediction["tokens"],
                    }, ensure_ascii=False) + "\n")
                    out.flush()
                    context.write(json.dumps({
                        "question_id": qid,
                        "role_id": role_id,
                        "as_of_year": None if scope.as_of is None else scope.as_of.year,
                        "retrieved_event_ids": prediction["retrieved_event_ids"],
                    }, ensure_ascii=False) + "\n")
                    context.flush()
                    elapsed = time.perf_counter() - run_started
                    eta = (len(questions) - i) * elapsed / i
                    print(f"\r{i}/{len(questions)} ({i / len(questions):.1%}) "
                          f"elapsed {elapsed / 60:.1f}m, ETA {eta / 60:.1f}m", end="", flush=True)
            print("", flush=True)
        finally:
            conn.close()

    import transformers  # already loaded by GenerativeKGQA; imported here only for its version

    metadata = {
        "branch": label,
        "implementation": "A2 reference: agent_graph.a2 graph query on embedded Postgres (pgserver)",
        "tightness": args.tightness,
        "permission_filter": permission,
        "asof_filter": asof,
        "permission_granularity": "event: edge_acl, edges.id = event id (line of full.txt)",
        "model": args.model,
        "model_revision": engine.resolved_revision,
        "requested_revision": args.revision,
        "device": "cpu",
        "threads": args.threads,
        "retrieval_budget_events": args.retrieval_budget,
        "max_new_tokens": args.max_new_tokens,
        "n_questions": total,
        "hits_at_1": correct / total if total else 0.0,
        "avg_latency_ms": latency_sum / total if total else 0.0,
        "avg_tokens": total_tokens / total if total else 0.0,
        "load_report": asdict(report),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "torch": engine.torch.__version__,
            "transformers": transformers.__version__,
        },
    }
    metadata_path = args.out_dir / f"run_metadata_{label}_{args.tightness}.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{label} ({args.tightness}) Hits@1: {metadata['hits_at_1']:.4f} ({correct}/{total})")
    print(f"Saved outputs: {outputs_path} (retrieval context: {context_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
