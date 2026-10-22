"""
Audit CRONQUESTIONS data_v2 (wikidata_big) before locking the eval set.

Checks:
  1. KG files (full.txt, train, valid, test): format, counts, split overlap,
     malformed lines, time-range sanity.
  2. Question pickles (test/train/valid): record counts, uniq_id duplicates,
     paraphrase structure, empty fields, type distribution.
  3. Cross-checks: question entities/relations exist in KG; times in KG range.
  4. Entity/relation text maps coverage.
  5. tkbc_processed_data peek.

Usage:
  python scripts/audit_data_v2.py [--out data/AUDIT_REPORT.md]

Writes JSON report to data/audit_report.json + markdown to --out.
Read-only: never modifies data files.
"""
import argparse
import json
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
KG_DIR = REPO_DIR / "data" / "wikidata_big" / "kg"
Q_DIR = REPO_DIR / "data" / "wikidata_big" / "questions"


def parse_kg_file(path: Path):
    """Parse 'head rel tail start end' TSV. Returns (events, malformed, stats)."""
    events = []
    malformed = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.rstrip("\n")
            if not line.strip():
                malformed.append((i, "blank line"))
                continue
            parts = line.split("\t")
            if len(parts) != 5:
                malformed.append((i, f"{len(parts)} fields: {line[:100]}"))
                continue
            head, rel, tail, start, end = parts
            try:
                start_y, end_y = int(start), int(end)
            except ValueError:
                malformed.append((i, f"non-int year: {line[:100]}"))
                continue
            events.append((head, rel, tail, start_y, end_y))
    return events, malformed


def audit_kg():
    report = {}
    splits = {}
    for name in ["full.txt", "train", "valid", "test"]:
        events, malformed = parse_kg_file(KG_DIR / name)
        splits[name] = events
        starts = [e[3] for e in events]
        ends = [e[4] for e in events]
        inverted = sum(1 for e in events if e[3] > e[4])
        report[name] = {
            "n_events": len(events),
            "n_malformed": len(malformed),
            "malformed_sample": malformed[:5],
            "year_start_min": min(starts) if starts else None,
            "year_start_max": max(starts) if starts else None,
            "year_end_max": max(ends) if ends else None,
            "start_gt_end": inverted,
            "n_unique_heads": len({e[0] for e in events}),
            "n_unique_tails": len({e[2] for e in events}),
            "n_unique_entities": len({e[0] for e in events} | {e[2] for e in events}),
            "n_unique_relations": len({e[1] for e in events}),
            "top_relations": Counter(e[1] for e in events).most_common(10),
        }
    # split consistency
    set_full = set(splits["full.txt"])
    set_train = set(splits["train"])
    set_valid = set(splits["valid"])
    set_test = set(splits["test"])
    report["split_consistency"] = {
        "train_valid_overlap": len(set_train & set_valid),
        "train_test_overlap": len(set_train & set_test),
        "valid_test_overlap": len(set_valid & set_test),
        "train_in_full": len(set_train - set_full),
        "valid_in_full": len(set_valid - set_full),
        "test_in_full": len(set_test - set_full),
        "train_plus_valid_plus_test_eq_full": (
            len(set_train | set_valid | set_test) == len(set_full)
            and len(set_train) + len(set_valid) + len(set_test)
            == len(set_train | set_valid | set_test)
        ),
    }
    all_entities = {e[0] for e in splits["full.txt"]} | {e[2] for e in splits["full.txt"]}
    all_relations = {e[1] for e in splits["full.txt"]}
    return report, all_entities, all_relations


def audit_questions(all_entities, all_relations):
    report = {}
    per_split = {}
    for name in ["test", "train", "valid"]:
        with open(Q_DIR / f"{name}.pickle", "rb") as f:
            records = pickle.load(f)
        per_split[name] = records
        n = len(records)
        ids = [r.get("uniq_id") for r in records]
        n_unique = len(set(ids))
        dup_ids = [k for k, v in Counter(ids).items() if v > 1]
        empty_answers = sum(1 for r in records if not r.get("answers"))
        empty_entities = sum(1 for r in records if not r.get("entities"))
        empty_relations = sum(1 for r in records if not r.get("relations"))
        missing_keys = Counter()
        for r in records:
            for k in ["question", "answers", "entities", "relations", "times",
                      "type", "uniq_id", "paraphrases", "annotation"]:
                if k not in r:
                    missing_keys[k] += 1
        types = Counter(r.get("type") for r in records)
        all_times = set()
        for r in records:
            all_times.update(r.get("times") or set())
        q_entities = set()
        q_relations = set()
        for r in records:
            q_entities.update(r.get("entities") or set())
            q_relations.update(r.get("relations") or set())
        report[name] = {
            "n_records": n,
            "n_unique_ids": n_unique,
            "n_dup_ids": len(dup_ids),
            "dup_id_sample": dup_ids[:10],
            "empty_answers": empty_answers,
            "empty_entities": empty_entities,
            "empty_relations": empty_relations,
            "missing_keys": dict(missing_keys),
            "type_distribution": dict(types),
            "time_min": min(all_times) if all_times else None,
            "time_max": max(all_times) if all_times else None,
            "n_distinct_times": len(all_times),
            "entities_not_in_kg": len(q_entities - all_entities),
            "relations_not_in_kg": sorted(q_relations - all_relations),
        }
    # dup structure: same text vs paraphrase?
    dup_analysis = {}
    for name in ["test", "train", "valid"]:
        records = per_split[name]
        by_id = defaultdict(list)
        for r in records:
            by_id[r.get("uniq_id")].append(r)
        multi = {k: v for k, v in by_id.items() if len(v) > 1}
        same_text = 0
        diff_text_same_rest = 0
        diff_answers = 0
        for k, recs in list(multi.items())[:2000]:
            texts = {r.get("question") for r in recs}
            answers = {tuple(sorted(r.get("answers") or [])) for r in recs}
            if len(texts) == 1:
                same_text += 1
            else:
                diff_text_same_rest += 1
            if len(answers) > 1:
                diff_answers += 1
        dup_analysis[name] = {
            "n_multi_ids_sampled": min(len(multi), 2000),
            "n_multi_ids_total": len(multi),
            "same_text": same_text,
            "diff_text_paraphrase": diff_text_same_rest,
            "diff_answers_among_dups": diff_answers,
        }
    report["dup_analysis"] = dup_analysis
    return report


def audit_text_maps(all_entities, all_relations):
    report = {}
    ent_map = {}
    with open(KG_DIR / "wd_id2entity_text.txt", "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                ent_map[parts[0]] = parts[1]
    rel_map = {}
    with open(KG_DIR / "wd_id2relation_text.txt", "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                rel_map[parts[0]] = parts[1]
    report["entity_text_map"] = {
        "n_entries": len(ent_map),
        "kg_entities_missing_text": len(all_entities - set(ent_map)),
    }
    report["relation_text_map"] = {
        "n_entries": len(rel_map),
        "kg_relations_missing_text": sorted(all_relations - set(rel_map)),
    }
    return report


def audit_tkbc():
    report = {}
    tkbc = KG_DIR / "tkbc_processed_data" / "wikidata_big"
    if not tkbc.exists():
        return {"exists": False}
    report["exists"] = True
    report["files"] = {}
    for name in ["ent_id", "rel_id", "ts_id"]:
        p = tkbc / name
        if p.exists():
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            report["files"][name] = {"n_lines": len(lines), "head": lines[:3]}
    for name in ["train.pickle", "valid.pickle", "test.pickle", "event_list_all.pickle", "ts_diffs.pickle"]:
        p = tkbc / name
        if p.exists():
            try:
                with open(p, "rb") as f:
                    obj = pickle.load(f)
                if isinstance(obj, (list, tuple)):
                    report["files"][name] = {"type": type(obj).__name__, "len": len(obj),
                                             "sample_keys": list(obj[0].keys()) if obj and isinstance(obj[0], dict) else str(obj[0])[:120]}
                elif isinstance(obj, dict):
                    report["files"][name] = {"type": "dict", "keys": list(obj.keys())[:10]}
                else:
                    report["files"][name] = {"type": type(obj).__name__, "repr": str(obj)[:200]}
            except Exception as e:
                report["files"][name] = {"error": str(e)[:200]}
    return report


def to_md(report):
    lines = ["# DATA_V2 AUDIT REPORT", ""]
    lines.append(f"Source: CRONQUESTIONS data_v2 (wikidata_big). Read-only audit; no data files modified.")
    lines.append("")
    lines.append("## 1. KG splits")
    for name in ["full.txt", "train", "valid", "test"]:
        r = report["kg"][name]
        lines.append(f"### {name}: {r['n_events']} events, "
                     f"{r['n_unique_entities']} entities, {r['n_unique_relations']} relations, "
                     f"{r['n_malformed']} malformed, start>end: {r['start_gt_end']}, "
                     f"years {r['year_start_min']}..{r['year_end_max']}")
    sc = report["kg"]["split_consistency"]
    lines.append("")
    lines.append(f"Overlap train|valid={sc['train_valid_overlap']}, train|test={sc['train_test_overlap']}, "
                 f"valid|test={sc['valid_test_overlap']}. "
                 f"Not-in-full: train={sc['train_in_full']}, valid={sc['valid_in_full']}, test={sc['test_in_full']}. "
                 f"Partition==full: {sc['train_plus_valid_plus_test_eq_full']}")
    lines.append("")
    lines.append("## 2. Questions")
    for name in ["test", "train", "valid"]:
        r = report["questions"][name]
        lines.append(f"### {name}.pickle: {r['n_records']} records, {r['n_unique_ids']} unique ids "
                     f"({r['n_dup_ids']} dup ids), empty answers={r['empty_answers']}, "
                     f"entities not in KG={r['entities_not_in_kg']}, "
                     f"relations not in KG={r['relations_not_in_kg']}, "
                     f"times {r['time_min']}..{r['time_max']} ({r['n_distinct_times']} distinct)")
        lines.append(f"types: {r['type_distribution']}")
    lines.append("")
    lines.append("## 3. Duplicate-id structure")
    for name, d in report["questions"]["dup_analysis"].items():
        lines.append(f"{name}: {d['n_multi_ids_total']} multi-record ids "
                     f"(sampled {d['n_multi_ids_sampled']}): identical text={d['same_text']}, "
                     f"paraphrase text={d['diff_text_paraphrase']}, differing answers={d['diff_answers_among_dups']}")
    lines.append("")
    lines.append("## 4. Text maps")
    lines.append(f"entities: {report['maps']['entity_text_map']}, relations: {report['maps']['relation_text_map']}")
    lines.append("")
    lines.append("## 5. tkbc_processed_data")
    lines.append(json.dumps(report["tkbc"], ensure_ascii=False, indent=2))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO_DIR / "data" / "AUDIT_REPORT.md"))
    args = parser.parse_args()

    print("Auditing KG...", flush=True)
    kg_report, all_entities, all_relations = audit_kg()
    print("Auditing questions (this loads ~250MB train pickle)...", flush=True)
    q_report = audit_questions(all_entities, all_relations)
    print("Auditing text maps...", flush=True)
    maps_report = audit_text_maps(all_entities, all_relations)
    print("Peeking tkbc_processed_data...", flush=True)
    tkbc_report = audit_tkbc()

    report = {"kg": kg_report, "questions": q_report, "maps": maps_report, "tkbc": tkbc_report}
    with open(REPO_DIR / "data" / "audit_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    md = to_md(report)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    sys.exit(main())
