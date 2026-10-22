"""
Select and lock the 1500-question evaluation set from CRONQUESTIONS data_v2.

W4 deliverable (dot 1):
  1. Tai + chuan hoa data_v2 (already present under data/wikidata_big).
  2. Chon 1500 cau bang random seed co dinh de tai lap.
  3. Khoa seed + cong bo tap/seed trong repo va README.
  4. Luu metadata can thiet de chay lai.

Usage:
  python scripts/select_eval_set.py [--seed 42] [--n 1500]
"""
import argparse
import json
import pickle
import random
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_QUESTION_PATH = REPO_DIR / "data" / "wikidata_big" / "questions" / "test.pickle"
DEFAULT_OUTPUT_DIR = REPO_DIR / "eval_set"


def to_jsonable(obj):
    if isinstance(obj, set):
        return sorted(obj)
    return obj


def select_eval_set(question_path: Path, seed: int, n: int):
    with open(question_path, "rb") as f:
        questions = pickle.load(f)

    # Deduplicate by uniq_id first: the test pool has 30K records but only
    # ~18K unique uniq_ids (same question repeated, e.g. per paraphrase).
    seen = set()
    unique_qids = []
    for q in questions:
        qid = q["uniq_id"]
        if qid not in seen:
            seen.add(qid)
            unique_qids.append(qid)

    rng = random.Random(seed)
    rng.shuffle(unique_qids)
    selected_ids = sorted(unique_qids[:n])

    # Group records by uniq_id: first record is canonical, the rest are
    # paraphrases of the same question (same answers/entities/relations).
    grouped = {}
    order = []
    for q in questions:
        qid = q["uniq_id"]
        if qid not in grouped:
            grouped[qid] = []
            order.append(qid)
        grouped[qid].append(q)
    by_id = {qid: grouped[qid][0] for qid in order}
    selected = []
    for qid in selected_ids:
        q = by_id[qid]
        # All paraphrase texts for this uniq_id: canonical question + other
        # records' questions + the dataset's own paraphrases list.
        all_texts = [q.get("question", "")]
        for other in grouped[qid][1:]:
            if other.get("question") and other["question"] not in all_texts:
                all_texts.append(other["question"])
        for p in q.get("paraphrases", []) or []:
            if p and p not in all_texts:
                all_texts.append(p)
        selected.append({
            "uniq_id": qid,
            "question": q.get("question", ""),
            "paraphrases": all_texts[1:],
            "answers": to_jsonable(q.get("answers", set())),
            "entities": to_jsonable(q.get("entities", set())),
            "relations": to_jsonable(q.get("relations", set())),
            "times": to_jsonable(q.get("times", set())),
            "type": q.get("type", ""),
            "answer_type": q.get("answer_type", ""),
            "template": q.get("template", ""),
            "annotation": to_jsonable(q.get("annotation", {})),
        })
    return selected_ids, selected, len(unique_qids), len(questions)


def main():
    parser = argparse.ArgumentParser(description="Lock the 1500-question eval set.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n", type=int, default=1500)
    parser.add_argument("--questions", type=str, default=str(DEFAULT_QUESTION_PATH))
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_ids, selected, pool_size, pool_records = select_eval_set(Path(args.questions), args.seed, args.n)

    with open(out_dir / "selected_question_ids.json", "w", encoding="utf-8") as f:
        json.dump({"seed": args.seed, "n": args.n, "pool_size": pool_size,
                   "source": "data/wikidata_big/questions/test.pickle",
                   "question_ids": selected_ids}, f, ensure_ascii=False, indent=2)

    with open(out_dir / "eval_set.json", "w", encoding="utf-8") as f:
        json.dump({"seed": args.seed, "n": len(selected), "questions": selected},
                  f, ensure_ascii=False, indent=2)

    with open(out_dir / "eval_metadata.json", "w", encoding="utf-8") as f:
        json.dump({
            "seed": args.seed,
            "n": args.n,
            "pool_records": pool_records,
            "pool_unique_ids": pool_size,
            "source": "data/wikidata_big/questions/test.pickle",
            "dedup_rule": "group by uniq_id; first record is canonical; "
                          "other records merged into paraphrases",
            "data_version": "data_v2",
            "known_issues": [
                "652 KG events have start_year > end_year (treated as empty "
                "intervals downstream; see data/AUDIT_REPORT.md)",
                "KG valid split is a subset of train (overlap 5000/5000); "
                "only full.txt + test.pickle are used, so no impact",
            ],
        }, f, ensure_ascii=False, indent=2)

    print(f"pool={pool_size} selected={len(selected)} seed={args.seed}")
    print(f"first10={selected_ids[:10]}")
    print(f"wrote {out_dir / 'selected_question_ids.json'}")
    print(f"wrote {out_dir / 'eval_set.json'}")


if __name__ == "__main__":
    sys.exit(main())
