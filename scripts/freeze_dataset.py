"""Đóng băng tập đánh giá: ghim từng byte, và kiểm lại được bất cứ lúc nào.

Tập đánh giá có quyền và có mốc thời gian gồm 1.500 câu đã khoá (`eval_set/`) và nhãn quyền ba
mức (`permission/output/`). `eval_set/frozen_manifest.json` ghim SHA-256 của:

- các tệp ĐÃ COMMIT của tập — `tests/test_frozen_dataset.py` canh chúng trong CI;
- các tệp SINH LẠI ĐƯỢC nhưng không commit vì quá lớn: `roles_{loose,medium,strict}.json` (84 MB);
- các tệp gốc của data_v2 mà tập được rút ra — tức đúng bản `data_v2.zip`, không phải `data.zip`.

    python scripts/freeze_dataset.py                                    # tệp đã commit còn nguyên?
    python scripts/freeze_dataset.py --data data/wikidata_big           # + sinh lại từ data_v2 và so
    python scripts/freeze_dataset.py --data data/wikidata_big --write   # đóng băng (lại)

Có `--data`, script chạy lại ĐÚNG mã của kho — `scripts/select_eval_set.py` rồi
`permission.generator` — với seed của manifest vào một thư mục tạm, và so từng byte. Pickle câu
hỏi của đề được nạp bằng một Unpickler từ chối mọi global: tệp chính thức chỉ chứa
dict/list/set/str/int, nên không dòng mã nào nằm trong tệp có thể chạy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "scripts")]

MANIFEST = REPO / "eval_set" / "frozen_manifest.json"
SEED = 42
N_QUESTIONS = 1500
COMMITTED = [
    "eval_set/selected_question_ids.json",
    "eval_set/eval_set.json",
    "eval_set/eval_metadata.json",
    "permission/output/permissions_loose.jsonl",
    "permission/output/permissions_medium.jsonl",
    "permission/output/permissions_strict.jsonl",
    "permission/output/generation_summary.json",
]
REGENERATED = [f"permission/output/roles_{level}.json" for level in ("loose", "medium", "strict")]
UPSTREAM = [
    "kg/full.txt",
    "questions/test.pickle",
    "kg/wd_id2entity_text.txt",
    "kg/wd_id2relation_text.txt",
]
DATA_V2 = {
    "dataset": "CRONQUESTIONS data_v2 (Saxena, Chakrabarti, Talukdar, ACL-IJCNLP 2021; CronKGQA, MIT)",
    "download": "gdown https://drive.google.com/uc?id=1fe7-x7ChszqzczKncoZcpwmWc1PBq1_0",
    "unpack": "unzip -q data_v2.zip  # tạo data/wikidata_big/ ngay tại gốc kho",
}


def pin(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def check(label: str, path: Path, want: dict[str, Any]) -> bool:
    if not path.exists():
        print(f"THIẾU  {label}")
        return False
    got = pin(path)
    if got == want:
        print(f"KHỚP   {label}")
        return True
    print(f"KHÁC   {label}  (bytes={got['bytes']} sha256={got['sha256']})")
    return False


class _NoGlobals(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        raise pickle.UnpicklingError(
            f"tệp câu hỏi đòi global {module}.{name}: không phải tệp chính thức của data_v2"
        )


class _SafePickle:
    """Thay cho module `pickle` bên trong hai mã sinh dữ liệu — chúng chỉ gọi `pickle.load`."""

    @staticmethod
    def load(stream: BinaryIO) -> Any:
        return _NoGlobals(stream).load()


def rederive(data: Path, seed: int, out: Path) -> None:
    """Chạy lại đúng mã của kho, ghi `eval_set/` và `permission/output/` vào `out`."""
    import select_eval_set

    import permission.generator as generator

    select_eval_set.pickle = _SafePickle  # type: ignore[assignment]
    generator.pickle = _SafePickle  # type: ignore[assignment]
    questions = data / "questions" / "test.pickle"
    argv = sys.argv
    try:
        sys.argv = ["select_eval_set.py", "--seed", str(seed), "--n", str(N_QUESTIONS),
                    "--questions", str(questions), "--out", str(out / "eval_set")]
        select_eval_set.main()
    finally:
        sys.argv = argv
    selected = json.loads((out / "eval_set" / "selected_question_ids.json").read_text(encoding="utf-8"))
    generator.PermissionGenerator(seed=seed).generate_all(
        data / "kg" / "full.txt", questions, selected["question_ids"], out / "permission" / "output"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, help="thư mục data/wikidata_big của data_v2 đã giải nén")
    parser.add_argument("--zip", type=Path, help="data_v2.zip, nếu còn giữ: kiểm (hoặc ghim) cả tệp nén")
    parser.add_argument("--write", action="store_true",
                        help="đóng băng (lại): ghi manifest từ một lần sinh lại đầy đủ")
    args = parser.parse_args()
    if args.write and not args.data:
        parser.error("--write cần --data: manifest chỉ được ghi từ một lần sinh lại đầy đủ")

    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    if not manifest and not args.write:
        print(f"chưa có {MANIFEST.relative_to(REPO)}: chạy với --data … --write để đóng băng")
        return 1
    seed = manifest.get("seed", SEED)
    results: list[bool] = []

    if not args.write:
        print("== tệp đã commit")
        results += [check(rel, REPO / rel, manifest["committed"][rel]) for rel in COMMITTED]
        local_roles = [rel for rel in REGENERATED if (REPO / rel).exists()]
        if local_roles:
            print("== roles_*.json đang có trong kho (không commit)")
            results += [check(rel, REPO / rel, manifest["regenerated"][rel]) for rel in local_roles]
        if args.zip:
            print("== tệp nén")
            results.append(check(args.zip.name, args.zip, manifest["upstream"]["archive"]))
        if args.data:
            print("== tệp gốc của data_v2")
            results += [check(f"data/wikidata_big/{rel}", args.data / rel, manifest["upstream"]["files"][rel])
                        for rel in UPSTREAM]

    if args.data:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            print(f"== sinh lại từ data_v2 với seed {seed}", flush=True)
            rederive(args.data, seed, out)
            if args.write:
                differ = [rel for rel in COMMITTED if (out / rel).read_bytes() != (REPO / rel).read_bytes()]
                if differ:
                    print("tệp trong kho KHÁC bản vừa sinh lại — sinh lại và commit chúng trước khi đóng băng:")
                    print("\n".join(f"  {rel}" for rel in differ))
                    return 1
                archive = pin(args.zip) if args.zip else manifest.get("upstream", {}).get("archive")
                MANIFEST.write_text(json.dumps({
                    "frozen_on": time.strftime("%Y-%m-%d"),
                    "seed": seed,
                    "n_questions": N_QUESTIONS,
                    "derivation": [
                        f"python scripts/select_eval_set.py --seed {seed} --n {N_QUESTIONS}",
                        f"python scripts/generate_permissions.py --seed {seed}",
                    ],
                    "verify": "python scripts/freeze_dataset.py --data data/wikidata_big",
                    "upstream": {**DATA_V2, "archive": archive,
                                 "files": {rel: pin(args.data / rel) for rel in UPSTREAM}},
                    "committed": {rel: pin(REPO / rel) for rel in COMMITTED},
                    "regenerated": {rel: pin(out / rel) for rel in REGENERATED},
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"đã đóng băng → {MANIFEST.relative_to(REPO)}")
                return 0
            results += [check(f"{rel}  (sinh lại)", out / rel, manifest["committed"][rel]) for rel in COMMITTED]
            results += [check(f"{rel}  (sinh lại)", out / rel, manifest["regenerated"][rel])
                        for rel in REGENERATED]

    ok = all(results)
    print("FROZEN CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
