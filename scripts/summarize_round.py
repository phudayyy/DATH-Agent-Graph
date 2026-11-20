"""So A0, A1 và A2 bản đối chiếu trên cùng 1.500 câu ở ba mức độ chặt.

Đọc `detailed_{A0,A1,A2}.json` mà `evaluate_outputs.py` ghi cho từng mức, rồi tính tỉ lệ của từng
nhánh và khoảng tin cậy 95% cho hiệu giữa hai nhánh bằng bootstrap theo cặp câu — dùng lại đúng
`paired_ci` của `summarize_experiment.py` (cùng câu ở hai nhánh được rút cùng nhau).

    uv run python scripts/summarize_round.py --evaluated results/round2/evaluated
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from summarize_experiment import LEVELS, paired_ci  # noqa: E402

BRANCHES = ("A0", "A1", "A2")
PAIRS = (("A0", "A1"), ("A0", "A2"), ("A1", "A2"))
METRICS = {
    "hits_at_1": lambda row: int(float(row["hits_at_1"]) == 1.0),
    "leakage": lambda row: int(bool(row["leakage"])),
    "leakage_final_answer": lambda row: int(row["leakage_location"] in ("final_answer", "both")),
    "leakage_reasoning": lambda row: int(row["leakage_location"] in ("reasoning", "both")),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evaluated", type=Path, required=True,
                        help="thư mục có {loose,medium,strict}/detailed_{A0,A1,A2}.json")
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = {"bootstrap": {"method": "paired question-level percentile bootstrap",
                             "replicates": args.bootstrap, "seed": args.seed, "confidence": 0.95},
               "levels": {}}
    for level_index, level in enumerate(LEVELS):
        rows = {}
        for branch in BRANCHES:
            detailed = json.loads((args.evaluated / level / f"detailed_{branch}.json").read_text(encoding="utf-8"))
            rows[branch] = {int(row["question_id"]): row for row in detailed}
        if not rows["A0"].keys() == rows["A1"].keys() == rows["A2"].keys():
            raise SystemExit(f"{level}: ba nhánh không chấm cùng một tập câu")

        level_summary = {"rates": {}, "latency_ms": {}, "tokens": {}, "deltas": {}}
        for branch in BRANCHES:
            values = list(rows[branch].values())
            level_summary["rates"][branch] = {
                name: sum(score(row) for row in values) / len(values) for name, score in METRICS.items()
            }
            latencies = [float(row["latency_ms"]) for row in values]
            level_summary["latency_ms"][branch] = {"mean": statistics.fmean(latencies),
                                                   "median": statistics.median(latencies)}
            level_summary["tokens"][branch] = statistics.fmean(float(row["tokens"]) for row in values)
        for metric_index, (name, score) in enumerate(METRICS.items()):
            for pair_index, (first, second) in enumerate(PAIRS):
                seed = args.seed + 1000 * level_index + 10 * metric_index + pair_index
                level_summary["deltas"][f"{name}: {first} - {second}"] = paired_ci(
                    {qid: score(row) for qid, row in rows[first].items()},
                    {qid: score(row) for qid, row in rows[second].items()},
                    args.bootstrap, seed,
                )
        summary["levels"][level] = level_summary

        print(f"\n== {level}")
        print(f"{'':>22}" + "".join(f"{b:>10}" for b in BRANCHES))
        for name in METRICS:
            print(f"{name:>22}" + "".join(f"{level_summary['rates'][b][name]:>10.2%}" for b in BRANCHES))
        print(f"{'latency mean (s)':>22}"
              + "".join(f"{level_summary['latency_ms'][b]['mean'] / 1000:>10.2f}" for b in BRANCHES))
        for key, value in level_summary["deltas"].items():
            low, high = value["ci95"]
            print(f"   {key:<34} {value['delta']:+.2%}  [{low:+.2%}, {high:+.2%}]")

    if args.output:
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
