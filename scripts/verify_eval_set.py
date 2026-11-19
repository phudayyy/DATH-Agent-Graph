"""Verify that the locked 1500-question eval set is intact and reproducible.

  python scripts/verify_eval_set.py                      # integrity only (offline)
  python scripts/verify_eval_set.py --questions PATH     # + regenerate from test.pickle, seed 42
  python scripts/verify_eval_set.py --questions PATH --record   # also store the source hash in metadata

Selection rule (scripts/select_eval_set.py): unique uniq_id in file order ->
random.Random(42).shuffle -> first 1500 -> sorted. Line endings are ignored when hashing.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path, strip_cr: bool = False) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data.replace(b"\r\n", b"\n") if strip_cr else data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, default=ROOT / "data" / "wikidata_big" / "questions" / "test.pickle")
    ap.add_argument("--eval-dir", type=Path, default=ROOT / "eval_set")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--record", action="store_true", help="write source_sha256 into eval_metadata.json after a successful regeneration")
    args = ap.parse_args()

    failures = []

    def check(ok: bool, msg: str):
        print(("PASS  " if ok else "FAIL  ") + msg)
        if not ok:
            failures.append(msg)

    eval_path = args.eval_dir / "eval_set.json"
    meta_path = args.eval_dir / "eval_metadata.json"
    ids_path = args.eval_dir / "selected_question_ids.json"
    data = json.loads(eval_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    qids = [q["uniq_id"] for q in data["questions"]]

    print("== Integrity (offline) ==")
    check(data["seed"] == args.seed and meta["seed"] == args.seed, f"seed = {args.seed}")
    check(len(qids) == args.n and meta["n"] == args.n, f"n = {args.n}")
    check(len(set(qids)) == len(qids), "uniq_id are unique")
    check(qids == sorted(qids), "ids sorted ascending")
    check(ids["question_ids"] == qids, "selected_question_ids.json matches eval_set.json")
    if "eval_set_sha256" in meta:
        check(meta["eval_set_sha256"] == sha256(eval_path, True), "eval_set.json sha256 matches metadata")
    else:
        print("INFO  metadata has no eval_set_sha256; current value:", sha256(eval_path, True))

    print("== Regeneration from the source pickle ==")
    if not args.questions.exists():
        print(f"SKIP  {args.questions} not found. Download data_v2 (see README) and rerun with --questions.")
    else:
        src = sha256(args.questions)
        if meta.get("source_sha256"):
            check(meta["source_sha256"] == src, "source pickle sha256 matches metadata")
        else:
            print(f"INFO  source pickle sha256 = {src} (not recorded yet; use --record)")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "select_eval_set.py"),
                            "--questions", str(args.questions), "--out", tmp,
                            "--seed", str(args.seed), "--n", str(args.n)],
                           check=True, stdout=subprocess.DEVNULL)
            same_set = sha256(Path(tmp) / "eval_set.json", True) == sha256(eval_path, True)
            check(same_set, "regenerated eval_set.json is identical (ignoring line endings)")
            regen_meta = json.loads((Path(tmp) / "eval_metadata.json").read_text(encoding="utf-8"))
            check(regen_meta["pool_unique_ids"] == meta["pool_unique_ids"]
                  and regen_meta["pool_records"] == meta["pool_records"], "pool size matches metadata")
        if args.record and not failures:
            meta["source_sha256"] = src
            meta["eval_set_sha256"] = sha256(eval_path, True)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Recorded source_sha256 and eval_set_sha256 in", meta_path)

    print("RESULT:", "OK" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
