"""Run A1-v2 as a true post-generation filter over A0-v2 outputs.

No model is loaded. The filter uses the exact retrieved_event_ids sidecar
written by run_a0_v2.py. This avoids requiring the model to cite event IDs in
its free-form reasoning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a0.generative_qa import A0_FORMAT_VERSION  # noqa: E402
from evaluation.a1_post_filter import filter_a1_answer  # noqa: E402
from evaluation.kg_index import KGEventIndex  # noqa: E402

OUTPUT_FIELDS = {"question_id", "answer", "reasoning", "latency_ms", "tokens"}
PROVENANCE_FIELDS = {
    "question_id", "retrieved_event_ids", "answer_type",
    "question_type", "answer_format_status", "raw_completion",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path, valid_ids: set[int], expected_fields: set[str]) -> dict[int, dict]:
    rows: dict[int, dict] = {}
    if not path.is_file():
        return rows
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not raw.endswith(("\n", "\r")):
                print(f"WARNING: ignoring torn final line in {path}")
                continue
            raise
        if set(row) != expected_fields:
            raise ValueError(
                f"{path}:{index+1}: expected fields {sorted(expected_fields)}, got {sorted(row)}"
            )
        qid = int(row["question_id"])
        if qid not in valid_ids:
            raise ValueError(f"Unexpected question ID {qid} in {path}")
        if qid in rows:
            raise ValueError(f"Duplicate question ID {qid} in {path}")
        rows[qid] = row
    return rows


def write_jsonl(path: Path, rows: dict[int, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for qid in sorted(rows):
            stream.write(json.dumps(rows[qid], ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_permissions(permission_path: Path, expected_ids: set[int]) -> tuple[dict[int, frozenset[int]], dict[int, str]]:
    role_path = permission_path.parent / permission_path.name.replace("permissions_", "roles_").replace(".jsonl", ".json")
    if not role_path.is_file():
        raise FileNotFoundError(role_path)
    roles = json.loads(role_path.read_text(encoding="utf-8"))
    role_sets = {
        str(role_id): frozenset(int(eid) for eid in data["allowed_event_ids"])
        for role_id, data in roles.items()
    }
    permissions: dict[int, frozenset[int]] = {}
    qid_roles: dict[int, str] = {}
    with permission_path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            qid = int(row["question_id"])
            if qid in permissions:
                raise ValueError(f"Duplicate permission label {qid}")
            role_id = str(row.get("role_id", ""))
            qid_roles[qid] = role_id
            inline = row.get("allowed_event_ids")
            if inline:
                permissions[qid] = frozenset(int(eid) for eid in inline)
            elif role_id in role_sets:
                permissions[qid] = role_sets[role_id]
            else:
                raise ValueError(f"Cannot resolve role {role_id!r} at {permission_path}:{line_no}")
    if set(permissions) != expected_ids:
        raise ValueError(
            f"Permission coverage mismatch: expected {len(expected_ids)}, got {len(permissions)}"
        )
    return permissions, qid_roles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tightness", choices=("loose", "medium", "strict"), required=True)
    parser.add_argument("--a0-output", type=Path, default=ROOT / "results" / "a0_v2" / "outputs_A0.jsonl")
    parser.add_argument("--a0-provenance", type=Path, default=ROOT / "results" / "a0_v2" / "provenance_A0.jsonl")
    parser.add_argument("--a0-metadata", type=Path, default=ROOT / "results" / "a0_v2" / "run_metadata.json")
    parser.add_argument("--eval-set", type=Path, default=ROOT / "eval_set" / "eval_set.json")
    parser.add_argument("--kg", type=Path, default=ROOT / "data" / "wikidata_big" / "kg" / "full.txt")
    parser.add_argument("--permission-file", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "a1_v2")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    for path in (args.a0_output, args.a0_provenance, args.a0_metadata,
                 args.eval_set, args.kg, args.permission_file):
        if not path.is_file():
            raise FileNotFoundError(path)

    payload = json.loads(args.eval_set.read_text(encoding="utf-8"))
    questions = payload["questions"]
    by_id = {int(q["uniq_id"]): q for q in questions}
    expected_ids = set(by_id)
    if len(by_id) != int(payload.get("n", len(questions))):
        raise ValueError("Locked evaluation set has duplicate/missing IDs")

    outputs = read_jsonl(args.a0_output, expected_ids, OUTPUT_FIELDS)
    provenance = read_jsonl(args.a0_provenance, expected_ids, PROVENANCE_FIELDS)
    if set(outputs) != expected_ids or set(provenance) != expected_ids:
        raise ValueError(
            f"A0-v2 requires complete output and provenance: "
            f"outputs={len(outputs)}/{len(expected_ids)}, "
            f"provenance={len(provenance)}/{len(expected_ids)}"
        )

    for qid in expected_ids:
        prov = provenance[qid]
        if prov["answer_type"] != by_id[qid].get("answer_type", ""):
            raise ValueError(f"A0 provenance answer_type mismatch for {qid}")
        if prov["question_type"] != by_id[qid].get("type", ""):
            raise ValueError(f"A0 provenance question_type mismatch for {qid}")
        ids = [int(x) for x in prov["retrieved_event_ids"]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate retrieved event ID for {qid}")
        prov["retrieved_event_ids"] = ids

    a0_meta = json.loads(args.a0_metadata.read_text(encoding="utf-8"))
    if a0_meta.get("permission_filter") is not False or a0_meta.get("asof_filter") is not False:
        raise ValueError("A0-v2 metadata must record permission_filter=false and asof_filter=false")
    if a0_meta.get("a0_format_version") != A0_FORMAT_VERSION:
        raise ValueError("A0-v2 format version mismatch")

    permissions, qid_roles = load_permissions(args.permission_file, expected_ids)
    print(f"Permission {args.tightness}: {len(permissions)}/{len(expected_ids)}")

    print("Building KG event index...")
    event_index = KGEventIndex.from_file(args.kg)
    all_event_ids = set(event_index.events)
    for qid, row in provenance.items():
        bad = set(row["retrieved_event_ids"]) - all_event_ids
        if bad:
            raise ValueError(f"Unknown retrieved event IDs for {qid}: {sorted(bad)[:5]}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    level = args.tightness
    output_path = args.out_dir / f"outputs_A1_{level}.jsonl"
    audit_path = args.out_dir / f"filter_audit_A1_{level}.jsonl"
    summary_path = args.out_dir / f"filter_summary_A1_{level}.json"
    metadata_path = args.out_dir / f"run_metadata_A1_{level}.json"

    run_meta = {
        "branch": "A1",
        "tightness": level,
        "filter_version": "a1_v2_exact_a0_retrieval_provenance",
        "source": "reuse_a0_v2",
        "a0_output_sha256": sha256(args.a0_output),
        "a0_provenance_sha256": sha256(args.a0_provenance),
        "model": a0_meta.get("model"),
        "requested_revision": a0_meta.get("requested_revision"),
        "resolved_model_revision": a0_meta.get("model_revision"),
        "resolved_model_revision_known": bool(a0_meta.get("model_revision")),
        "permission_filter": True,
        "asof_filter": False,
        "llm_inference_rerun": False,
        "reasoning_retained_unchanged": True,
    }

    if args.restart:
        for path in (output_path, audit_path, summary_path, metadata_path):
            if path.exists():
                path.unlink()

    # Resume only matching output+audit pairs; refuse to merge different runs.
    if metadata_path.exists():
        old_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        mismatches = [k for k, v in run_meta.items() if old_meta.get(k) != v]
        if mismatches:
            raise ValueError(
                f"A1 output belongs to a different run ({mismatches}); use --restart"
            )

    existing_outputs = read_jsonl(output_path, expected_ids, OUTPUT_FIELDS) if output_path.exists() else {}
    audit_fields = {
        "question_id", "role_id", "decision", "reason", "answer_type",
        "question_type", "answer_value", "retrieved_event_count",
        "supporting_event_ids", "authorized_supporting_event_ids",
        "unauthorized_supporting_event_ids", "reasoning_supporting_event_ids",
        "reasoning_unauthorized_event_ids", "reasoning_leakage",
    }
    existing_audits = read_jsonl(audit_path, expected_ids, audit_fields) if audit_path.exists() else {}
    done = set(existing_outputs) & set(existing_audits)
    existing_outputs = {qid: existing_outputs[qid] for qid in done}
    existing_audits = {qid: existing_audits[qid] for qid in done}
    write_jsonl(output_path, existing_outputs)
    write_jsonl(audit_path, existing_audits)
    metadata_path.write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    started = time.perf_counter()
    with output_path.open("a", encoding="utf-8", newline="\n") as out_stream, \
         audit_path.open("a", encoding="utf-8", newline="\n") as audit_stream:
        for index, qid in enumerate(sorted(expected_ids), 1):
            if qid in done:
                continue
            question = by_id[qid]
            source = outputs[qid]
            prov = provenance[qid]
            start = time.perf_counter()
            filtered_answer, preserved_reasoning, audit = filter_a1_answer(
                answer=source["answer"],
                reasoning=source["reasoning"],
                retrieved_event_ids=prov["retrieved_event_ids"],
                allowed_event_ids=permissions[qid],
                question_entities=question.get("entities", []),
                relations=question.get("relations", []),
                answer_type=question.get("answer_type", ""),
                question_type=question.get("type", ""),
                event_index=event_index,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            row = {
                "question_id": qid,
                "answer": filtered_answer,
                "reasoning": preserved_reasoning,
                "latency_ms": round(float(source["latency_ms"]) + elapsed_ms, 3),
                "tokens": int(source["tokens"]),
            }
            audit_row = {
                "question_id": qid,
                "role_id": qid_roles.get(qid),
                **audit,
            }
            out_stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_stream.flush()
            audit_stream.write(json.dumps(audit_row, ensure_ascii=False) + "\n")
            audit_stream.flush()
            done.add(qid)
            if index % 100 == 0 or index == len(expected_ids):
                print(
                    f"\rA1 {level}: {len(done)}/{len(expected_ids)} "
                    f"elapsed={(time.perf_counter()-started)/60:.1f}m",
                    end="",
                    flush=True,
                )
    print("", flush=True)

    final_outputs = read_jsonl(output_path, expected_ids, OUTPUT_FIELDS)
    final_audits = read_jsonl(output_path.with_name(f"filter_audit_A1_{level}.jsonl"), expected_ids, audit_fields)
    if set(final_outputs) != expected_ids or set(final_audits) != expected_ids:
        raise RuntimeError("A1 output/audit incomplete")

    reasons = Counter(row["reason"] for row in final_audits.values())
    decisions = Counter(row["decision"] for row in final_audits.values())
    reasoning_leaks = sum(bool(row["reasoning_leakage"]) for row in final_audits.values())
    summary = {
        "branch": "A1",
        "tightness": level,
        "filter_version": run_meta["filter_version"],
        "n_questions": len(final_outputs),
        "retained": decisions.get("retain", 0),
        "refused": decisions.get("refuse", 0),
        "refused_due_to_permission": reasons.get("answer_supported_by_out_of_scope_event", 0),
        "unattributed_retained": reasons.get("unattributed_no_matching_retrieved_event", 0),
        "query_entity_echo_retained": reasons.get("query_entity_echo_no_new_entity_disclosure", 0),
        "reasoning_leakage_questions": reasoning_leaks,
        "reason_counts": dict(reasons),
        "decision_counts": dict(decisions),
        "a0_reused": True,
        "llm_inference_rerun": False,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"A1 {level}: {len(final_outputs)}/{len(expected_ids)}; "
        f"retained={summary['retained']}; refused={summary['refused']}; "
        f"unattributed={summary['unattributed_retained']}; "
        f"reasoning_leakage={reasoning_leaks}"
    )
    print(f"Saved: {output_path}")
    print(f"Audit: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
