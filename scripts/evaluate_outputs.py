"""Score an existing A0/A1/A2 JSONL file with the shared evaluator."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.metrics import EvaluationOutput  # noqa: E402
from evaluation.runner import EvaluationRunner  # noqa: E402

CONTRACT = {"question_id", "answer", "reasoning", "latency_ms", "tokens"}


def load_outputs(path: Path):
    outputs = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if set(row) != CONTRACT:
                raise ValueError(f"Line {line_no} must contain exactly {sorted(CONTRACT)}")
            qid = int(row["question_id"])
            if qid in outputs:
                raise ValueError(f"Duplicate question_id {qid} in output")
            if not isinstance(row["answer"], str) or not isinstance(row["reasoning"], str):
                raise ValueError(f"Line {line_no}: answer/reasoning must be strings")
            if int(row["latency_ms"]) < 0 or int(row["tokens"]) < 0:
                raise ValueError(f"Line {line_no}: latency_ms and tokens must be nonnegative")
            outputs[qid] = row
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True, help="Branch outputs JSONL")
    parser.add_argument("--branch", required=True, help="e.g. A0, A1, A2-public")
    parser.add_argument("--tightness", choices=("loose", "medium", "strict"))
    parser.add_argument("--permission", action="store_true", help="Enable permission cell")
    parser.add_argument("--asof", action="store_true", help="Enable as-of cell")
    parser.add_argument("--permission-file", type=Path,
                        help="permissions_{tightness}.jsonl; needed to score leakage")
    parser.add_argument("--run-metadata", type=Path,
                        help="Optional sidecar recording model ID and generation/retrieval settings")
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg", type=Path, default=ROOT / "data" / "wikidata_big" / "kg" / "full.txt")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "evaluated")
    args = parser.parse_args()

    rows = load_outputs(args.outputs)
    with args.eval_set.open("r", encoding="utf-8") as f:
        selected = json.load(f)["questions"]
    selected_ids = {int(q["uniq_id"]) for q in selected}
    if set(rows) != selected_ids:
        missing, extra = selected_ids - set(rows), set(rows) - selected_ids
        raise ValueError(f"Output IDs do not match locked eval set: missing={len(missing)}, extra={len(extra)}")

    def cached_branch(question, allowed_event_ids, as_of_year):
        row = rows[question.uniq_id]
        return EvaluationOutput(
            question_id=question.uniq_id, answer=row["answer"], reasoning=row["reasoning"],
            latency_ms=int(row["latency_ms"]), tokens=int(row["tokens"]),
        )

    runner = EvaluationRunner(
        branch_name=args.branch,
        branch_fn=cached_branch,
        question_ids=sorted(selected_ids),
        question_path=args.eval_set,
        permission_path=args.permission_file,
        tightness=args.tightness,
        enable_permission=args.permission,
        enable_asof=args.asof,
        kg_path=args.kg,
        measure_latency=False,
    )
    metrics = runner.run(args.out_dir)
    metrics_dict = metrics.__dict__
    if args.run_metadata:
        metadata = json.loads(args.run_metadata.read_text(encoding="utf-8"))
        metrics_dict["run_metadata"] = metadata
        (args.out_dir / f"metrics_{args.branch}.json").write_text(
            json.dumps(metrics_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(metrics_dict, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
