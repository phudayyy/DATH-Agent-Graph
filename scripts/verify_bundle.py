"""Verify the complete Huy CPU bundle before upload or experiment runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path, normalize_newlines: bool = False) -> str:
    if normalize_newlines:
        data = path.read_bytes().replace(b"\r\n", b"\n")
        return hashlib.sha256(data).hexdigest()
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> int:
    required = [
        ROOT / "data/wikidata_big/kg/full.txt",
        ROOT / "data/wikidata_big/questions/test.pickle",
        ROOT / "eval_set/eval_set.json",
        ROOT / "eval_set/selected_question_ids.json",
        ROOT / "eval_set/eval_metadata.json",
        ROOT / "permission/generator.py",
        ROOT / "a0/generative_qa.py",
        ROOT / "scripts/run_a0.py",
        ROOT / "scripts/run_a1.py",
        ROOT / "scripts/evaluate_outputs.py",
    ]
    for path in required:
        if not path.is_file():
            fail(f"required file missing: {path.relative_to(ROOT)}")

    eval_payload = json.loads((ROOT / "eval_set/eval_set.json").read_text(encoding="utf-8"))
    questions = eval_payload["questions"]
    qids = [int(q["uniq_id"]) for q in questions]
    if len(questions) != 1500 or len(set(qids)) != 1500 or qids != sorted(qids):
        fail("locked eval set must contain 1,500 unique IDs sorted ascending")

    selected = json.loads((ROOT / "eval_set/selected_question_ids.json").read_text(encoding="utf-8"))
    if selected["question_ids"] != qids or selected["seed"] != 42 or selected["n"] != 1500:
        fail("selected_question_ids.json does not match seed-42 eval set")

    meta = json.loads((ROOT / "eval_set/eval_metadata.json").read_text(encoding="utf-8"))
    if meta.get("source_sha256") != sha256(ROOT / "data/wikidata_big/questions/test.pickle"):
        fail("source question pickle SHA-256 mismatch")
    if meta.get("eval_set_sha256") != sha256(ROOT / "eval_set/eval_set.json", normalize_newlines=True):
        # The selection script hashes normalized line endings; this file is LF in the bundle.
        fail("eval_set SHA-256 mismatch")

    kg_path = ROOT / "data/wikidata_big/kg/full.txt"
    n_events = sum(1 for _ in kg_path.open("r", encoding="utf-8"))
    print(f"PASS: KG full.txt has {n_events:,} rows")

    for level, expected_roles in (("loose", 5), ("medium", 12), ("strict", 25)):
        permissions_path = ROOT / f"permission/output/permissions_{level}.jsonl"
        roles_path = ROOT / f"permission/output/roles_{level}.json"
        if not permissions_path.is_file() or not roles_path.is_file():
            fail(f"missing {level} permission outputs")

        roles = json.loads(roles_path.read_text(encoding="utf-8"))
        if "public" not in roles or len(roles) != expected_roles + 1:
            fail(f"{level}: expected {expected_roles} synthetic roles plus public")

        role_event_sets = {}
        for role_id, role in roles.items():
            event_ids = [int(eid) for eid in role.get("allowed_event_ids", [])]
            if len(event_ids) != len(set(event_ids)):
                fail(f"{level}/{role_id}: duplicate event IDs")
            if any(eid < 0 or eid >= n_events for eid in event_ids):
                fail(f"{level}/{role_id}: event ID outside full.txt row range")
            role_event_sets[role_id] = set(event_ids)

        if len(role_event_sets["public"]) != n_events:
            fail(f"{level}: public role does not see every KG event")

        rows = []
        with permissions_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                row = json.loads(line)
                qid = int(row["question_id"])
                role_id = row.get("role_id")
                if role_id not in role_event_sets:
                    fail(f"{level}: unknown role_id at line {line_no}")
                rows.append(qid)
        if len(rows) != 1500 or len(set(rows)) != 1500 or sorted(rows) != qids:
            fail(f"{level}: permission labels must cover exactly the locked 1,500 IDs")
        print(f"PASS: {level}: {expected_roles} roles + public; 1,500 labels; event IDs valid")

    print("PASS: source hashes, locked eval set, and all permission files verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
