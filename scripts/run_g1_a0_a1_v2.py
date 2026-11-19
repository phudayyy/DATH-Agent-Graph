"""Orchestrate A0-v2 full generation, A1-v2 three tightness levels, evaluation, and ZIP."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEVELS = ("loose", "medium", "strict")


def run(command: list[str]) -> None:
    print("\n$", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart-a0", action="store_true",
                        help="delete only results/a0_v2 before starting a fresh 1500-question A0")
    parser.add_argument("--restart-a1", action="store_true",
                        help="restart each A1 condition from its saved A0-v2 outputs")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--retrieval-budget", type=int, default=64)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    args = parser.parse_args()

    results = ROOT / "results"
    a0_dir = results / "a0_v2"
    a1_dir = results / "a1_v2"
    eval_dir = results / "evaluated_v2"
    zip_path = Path("/kaggle/working/g1_a0_a1_v2_complete.zip")

    if args.restart_a0 and a0_dir.exists():
        import shutil
        shutil.rmtree(a0_dir)

    # A0-v2 is a distinct, fresh baseline. It stores both the five-field
    # prediction contract and a provenance sidecar with exact retrieved IDs.
    run([
        sys.executable, "scripts/run_a0_v2.py",
        "--out-dir", str(a0_dir),
        "--threads", str(args.threads),
        "--retrieval-budget", str(args.retrieval_budget),
        "--max-new-tokens", str(args.max_new_tokens),
    ])

    a0_output = a0_dir / "outputs_A0.jsonl"
    a0_provenance = a0_dir / "provenance_A0.jsonl"
    a0_metadata = a0_dir / "run_metadata.json"
    for path in (a0_output, a0_provenance, a0_metadata):
        if not path.is_file():
            raise FileNotFoundError(path)

    with (ROOT / "eval_set" / "eval_set.json").open(encoding="utf-8") as stream:
        expected_n = len(json.load(stream)["questions"])

    def count_jsonl(path: Path) -> int:
        return sum(1 for line in path.open(encoding="utf-8") if line.strip())

    if count_jsonl(a0_output) != expected_n or count_jsonl(a0_provenance) != expected_n:
        raise RuntimeError("A0-v2 outputs/provenance are incomplete; A1 was not started.")

    # Three A1 permission networks, all reuse the exact same A0-v2 output.
    for level in LEVELS:
        permission_file = ROOT / "permission" / "output" / f"permissions_{level}.jsonl"
        run([
            sys.executable, "scripts/run_a1_v2.py",
            "--tightness", level,
            "--a0-output", str(a0_output),
            "--a0-provenance", str(a0_provenance),
            "--a0-metadata", str(a0_metadata),
            "--permission-file", str(permission_file),
            "--out-dir", str(a1_dir),
            *( ["--restart"] if args.restart_a1 else [] ),
        ])

    # Evaluate A0 and A1 against the same question-specific permissions.
    for level in LEVELS:
        permission_file = ROOT / "permission" / "output" / f"permissions_{level}.jsonl"

        # A0 remains permission-OFF; its leakage is scored under the current
        # synthetic permission network. This does not filter A0.
        run([
            sys.executable, "scripts/evaluate_outputs_provenance.py",
            "--outputs", str(a0_output),
            "--provenance", str(a0_provenance),
            "--branch", "A0",
            "--tightness", level,
            "--permission-file", str(permission_file),
            "--metadata", str(a0_metadata),
            "--eval-set", str(ROOT / "eval_set" / "eval_set.json"),
            "--kg", str(ROOT / "data" / "wikidata_big" / "kg" / "full.txt"),
            "--out-dir", str(eval_dir / f"a0_{level}"),
        ])

        a1_prov = a0_provenance
        a1_meta = a1_dir / f"run_metadata_A1_{level}.json"
        run([
            sys.executable, "scripts/evaluate_outputs_provenance.py",
            "--outputs", str(a1_dir / f"outputs_A1_{level}.jsonl"),
            "--provenance", str(a1_prov),
            "--branch", "A1",
            "--tightness", level,
            "--permission",
            "--permission-file", str(permission_file),
            "--metadata", str(a1_meta),
            "--eval-set", str(ROOT / "eval_set" / "eval_set.json"),
            "--kg", str(ROOT / "data" / "wikidata_big" / "kg" / "full.txt"),
            "--out-dir", str(eval_dir / f"a1_{level}"),
        ])

    # Summarize A0/A1 outputs and preserve all provenance.
    manifest = {
        "project": "G1",
        "dataset": "CRONQUESTIONS data_v2",
        "eval_questions": expected_n,
        "A0": {
            "path": "results/a0_v2/outputs_A0.jsonl",
            "provenance": "results/a0_v2/provenance_A0.jsonl",
            "metadata": "results/a0_v2/run_metadata.json",
            "settings": json.loads(a0_metadata.read_text(encoding="utf-8")),
        },
        "A1": {
            "levels": list(LEVELS),
            "path": "results/a1_v2",
            "same_A0_generation_reused": True,
            "LLM_inference_repeated": False,
            "permission_filter": True,
            "asof_filter": False,
        },
        "evaluation": "results/evaluated_v2",
        "answer_format_version": "a0_v2_answer_type_and_retrieval_provenance",
        "permission_attribution": "exact_retrieved_event_ids_sidecar",
    }
    (ROOT / "UPDATE_MANIFEST_A0_A1_V2.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if zip_path.exists():
        zip_path.unlink()
    excluded = {"__pycache__", ".pytest_cache", ".ipynb_checkpoints", ".git"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if path.suffix == ".pyc" or any(part in excluded for part in rel.parts):
                continue
            archive.write(path, (Path("g1") / rel).as_posix())

    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity failed: {bad}")

    print("\nSUCCESS — A0-v2 + A1-v2 (3 levels) + metrics + ZIP")
    print(f"ZIP: {zip_path}")
    print(f"SIZE: {zip_path.stat().st_size / (1024 * 1024):.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
