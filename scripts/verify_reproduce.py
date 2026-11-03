"""
W4 reproduce check: run the permission generation twice and assert identical outputs.

Verifies the spec requirement: same input + same seed -> same permission labels.
Compares roles_*.json and permissions_*.jsonl per tightness level.

Usage:
  python scripts/verify_reproduce.py [--seed 42]
"""
import argparse
import filecmp
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def run_generation(seed: int, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(REPO_DIR / "scripts" / "generate_permissions.py"),
           "--seed", str(seed), "--out", str(out_dir)]
    subprocess.run(cmd, check=True, cwd=str(REPO_DIR),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    run_a = REPO_DIR / "permission" / "output_repro_a"
    run_b = REPO_DIR / "permission" / "output_repro_b"
    for d in (run_a, run_b):
        if d.exists():
            shutil.rmtree(d)

    print("run A...", flush=True)
    run_generation(args.seed, run_a)
    print("run B...", flush=True)
    run_generation(args.seed, run_b)

    ok = True
    for tight in ["loose", "medium", "strict"]:
        for name in [f"roles_{tight}.json", f"permissions_{tight}.jsonl"]:
            same = filecmp.cmp(run_a / name, run_b / name, shallow=False)
            print(f"{name}: {'IDENTICAL' if same else 'DIFFERENT'}")
            ok = ok and same
        # also check line counts == 1500
        n = sum(1 for _ in open(run_a / f"permissions_{tight}.jsonl", encoding="utf-8"))
        print(f"permissions_{tight}.jsonl lines={n} (expect 1500)")
        ok = ok and (n == 1500)

    print("REPRODUCE CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
