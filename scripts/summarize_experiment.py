"""Summarize the required eight 2x2 runs with paired bootstrap CIs."""

import argparse
import json
import random
from pathlib import Path

LEVELS = ("loose", "medium", "strict")


def read_scores(path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for row in rows:
        qid = int(row["question_id"])
        raw_score = float(row["hits_at_1"])
        if raw_score not in (0.0, 1.0):
            raise ValueError(f"Hits@1 must be binary for question {qid} in {path}")
        score = int(raw_score)
        if qid in result:
            raise ValueError(f"Duplicate question_id {qid} in {path}")
        result[qid] = score
    if not result:
        raise ValueError(f"No rows in {path}")
    return result


def paired_ci(baseline, comparison, n_bootstrap, seed):
    if baseline.keys() != comparison.keys():
        raise ValueError("Runs must contain exactly the same question IDs")
    ids = sorted(baseline)
    deltas = [baseline[qid] - comparison[qid] for qid in ids]
    mean = sum(deltas) / len(deltas)
    rng = random.Random(seed)
    draws = []
    for _ in range(n_bootstrap):
        draws.append(sum(deltas[rng.randrange(len(deltas))] for _ in ids) / len(ids))
    draws.sort()
    low = draws[int(0.025 * n_bootstrap)]
    high = draws[min(n_bootstrap - 1, int(0.975 * n_bootstrap))]
    return {"delta": mean, "ci95": [low, high], "n": len(ids)}


def paired_additivity_ci(a0, permission_only, asof_only, both, n_bootstrap, seed):
    """Paired CI for ΔPT - ΔP - ΔT, resampling the same question IDs."""
    key_sets = (a0.keys(), permission_only.keys(), asof_only.keys(), both.keys())
    if any(keys != key_sets[0] for keys in key_sets[1:]):
        raise ValueError("All four experiment cells must contain exactly the same question IDs")
    ids = sorted(a0)
    contrasts = [
        permission_only[qid] + asof_only[qid] - both[qid] - a0[qid]
        for qid in ids
    ]
    mean = sum(contrasts) / len(contrasts)
    rng = random.Random(seed)
    draws = [
        sum(contrasts[rng.randrange(len(contrasts))] for _ in ids) / len(ids)
        for _ in range(n_bootstrap)
    ]
    draws.sort()
    low = draws[int(0.025 * n_bootstrap)]
    high = draws[min(n_bootstrap - 1, int(0.975 * n_bootstrap))]
    return {"delta": mean, "ci95": [low, high], "n": len(ids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a0", type=Path, required=True, help="OFF/OFF detailed_*.json")
    parser.add_argument("--asof-only", type=Path, required=True, help="OFF/ON shared run")
    for level in LEVELS:
        parser.add_argument(f"--permission-{level}", type=Path, required=True)
        parser.add_argument(f"--both-{level}", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("results/experiment_summary.json"))
    args = parser.parse_args()
    if args.bootstrap < 100:
        parser.error("--bootstrap must be at least 100")

    a0 = read_scores(args.a0)
    asof = read_scores(args.asof_only)
    shared_delta_t = paired_ci(a0, asof, args.bootstrap, args.seed + 100)
    out = {
        "design": "8 distinct model runs; OFF/OFF and OFF/ON are shared across permission tightness levels",
        "bootstrap": {"method": "paired question-level percentile bootstrap", "replicates": args.bootstrap,
                      "seed": args.seed, "confidence": 0.95},
        "shared_cells": {"permission_off_asof_off": str(args.a0), "permission_off_asof_on": str(args.asof_only)},
        "by_tightness": {},
    }
    for index, level in enumerate(LEVELS):
        perm = read_scores(getattr(args, f"permission_{level}"))
        both = read_scores(getattr(args, f"both_{level}"))
        delta_p = paired_ci(a0, perm, args.bootstrap, args.seed + index)
        delta_pt = paired_ci(a0, both, args.bootstrap, args.seed + 200 + index)
        additivity = paired_additivity_ci(
            a0, perm, asof, both, args.bootstrap, args.seed + 300 + index
        )
        out["by_tightness"][level] = {
            "delta_P": delta_p, "delta_T": shared_delta_t, "delta_PT": delta_pt,
            "additivity_difference": additivity,
            "additivity_ci95_contains_zero": additivity["ci95"][0] <= 0 <= additivity["ci95"][1],
            "accuracy": {
                "A0_shared": sum(a0.values()) / len(a0),
                "permission_only": sum(perm.values()) / len(perm),
                "asof_only_shared": sum(asof.values()) / len(asof),
                "both": sum(both.values()) / len(both),
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved experiment summary: {args.output}")


if __name__ == "__main__":
    main()
