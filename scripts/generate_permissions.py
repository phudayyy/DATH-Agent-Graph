"""
Generate permission labels for the locked 1500-question eval set.

W4 deliverable (dot 1): bo sinh mang luoi quyen, 3 muc do chat,
deterministic (cung seed -> cung labels).

Reads eval_set/selected_question_ids.json, writes:
  permission/output/roles_{loose,medium,strict}.json
  permission/output/permissions_{loose,medium,strict}.jsonl  (qid + role_id per line)
  permission/output/generation_summary.json

Usage:
  python scripts/generate_permissions.py [--seed 42]
"""
import argparse
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from permission.generator import PermissionGenerator  # noqa: E402
import json  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Generate permission labels for eval set.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-set", type=str, default=str(REPO_DIR / "eval_set" / "selected_question_ids.json"))
    parser.add_argument("--kg", type=str, default=str(REPO_DIR / "data" / "wikidata_big" / "kg" / "full.txt"))
    parser.add_argument("--questions", type=str, default=str(REPO_DIR / "data" / "wikidata_big" / "questions" / "test.pickle"))
    parser.add_argument("--out", type=str, default=str(REPO_DIR / "permission" / "output"))
    args = parser.parse_args()

    with open(args.eval_set, "r", encoding="utf-8") as f:
        eval_meta = json.load(f)
    question_ids = eval_meta["question_ids"]
    print(f"eval set: {len(question_ids)} questions, seed={eval_meta.get('seed')}")

    generator = PermissionGenerator(seed=args.seed)
    summary = generator.generate_all(Path(args.kg), Path(args.questions), question_ids, Path(args.out))

    print("=== Generation Complete ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
