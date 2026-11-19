"""Evaluate A0-v2/A1-v2 outputs while restricting leakage attribution to retrieved facts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.metrics import EvaluationOutput  # noqa: E402
from evaluation.runner import EvaluationRunner  # noqa: E402

OUTPUT_FIELDS = {"question_id", "answer", "reasoning", "latency_ms", "tokens"}
PROVENANCE_FIELDS = {
    "question_id", "retrieved_event_ids", "answer_type",
    "question_type", "answer_format_status", "raw_completion",
}


def read(path: Path, fields: set[str]) -> dict[int, dict]:
    rows = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if set(row) != fields:
                raise ValueError(f"{path}:{line_no}: invalid fields")
            qid = int(row["question_id"])
            if qid in rows:
                raise ValueError(f"Duplicate ID {qid} in {path}")
            rows[qid] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--branch", choices=("A0", "A1"), required=True)
    parser.add_argument("--tightness", choices=("loose", "medium", "strict"), required=True)
    parser.add_argument("--permission-file", type=Path, required=True)
    parser.add_argument("--permission", action="store_true")
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg", type=Path, default=ROOT / "data" / "wikidata_big" / "kg" / "full.txt")
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    outputs = read(args.outputs, OUTPUT_FIELDS)
    provenance = read(args.provenance, PROVENANCE_FIELDS)
    if set(outputs) != set(provenance):
        raise ValueError("Output/provenance question IDs do not match")

    with args.eval_set.open("r", encoding="utf-8") as stream:
        selected = json.load(stream)["questions"]
    selected_ids = {int(q["uniq_id"]) for q in selected}
    if set(outputs) != selected_ids:
        raise ValueError("Output IDs do not match locked eval set")

    def cached_branch(question, allowed_event_ids, as_of_year):
        qid = int(question.uniq_id)
        row = outputs[qid]
        prov = provenance[qid]
        return EvaluationOutput(
            question_id=qid,
            answer=row["answer"],
            reasoning=row["reasoning"],
            latency_ms=float(row["latency_ms"]),
            tokens=int(row["tokens"]),
            retrieved_event_ids=[int(x) for x in prov["retrieved_event_ids"]],
        )

    runner = EvaluationRunner(
        branch_name=args.branch,
        branch_fn=cached_branch,
        question_ids=sorted(selected_ids),
        question_path=args.eval_set,
        permission_path=args.permission_file,
        tightness=args.tightness,
        enable_permission=args.permission,
        enable_asof=False,
        kg_path=args.kg,
        measure_latency=False,
    )
    metrics = runner.run(args.out_dir)
    metrics_dict = metrics.__dict__
    metrics_dict["run_metadata"] = json.loads(args.metadata.read_text(encoding="utf-8"))
    (args.out_dir / f"metrics_{args.branch}.json").write_text(
        json.dumps(metrics_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics_dict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
