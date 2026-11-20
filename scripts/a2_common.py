"""Phần dùng chung của run_a2.py và check_a2_retrieval.py: đọc nhãn quyền đã đóng băng."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval_set" / "frozen_manifest.json"
LEVELS = ("loose", "medium", "strict")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_frozen_roles(level: str, permission_dir: Path = ROOT / "permission" / "output"):
    """Trả về (vai trò của từng câu, sự kiện mỗi vai trò được thấy) của một mức độ chặt.

    `roles_*.json` không commit (84 MB) nên trước khi dùng phải khớp hash đã ghim lúc đóng băng:
    một tệp vai trò lệch một byte là một thí nghiệm khác.
    """
    permissions = permission_dir / f"permissions_{level}.jsonl"
    roles_file = permission_dir / f"roles_{level}.json"
    pinned = json.loads(MANIFEST.read_text(encoding="utf-8"))["regenerated"]
    key = roles_file.resolve().relative_to(ROOT).as_posix()
    if key not in pinned or _sha256(roles_file) != pinned[key]["sha256"]:
        raise SystemExit(f"{key} không khớp hash trong {MANIFEST.name}: sinh lại bằng "
                         "scripts/generate_permissions.py rồi kiểm bằng scripts/freeze_dataset.py")

    role_of = {}
    with permissions.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("allowed_event_ids"):
                raise SystemExit(f"{permissions.name} ghi thẳng sự kiện cho từng câu; "
                                 "A2 chỉ đọc nhãn theo vai trò")
            role_of[int(row["question_id"])] = str(row["role_id"])
    roles = json.loads(roles_file.read_text(encoding="utf-8"))
    allowed = {role_id: [int(e) for e in role["allowed_event_ids"]] for role_id, role in roles.items()}
    missing = set(role_of.values()) - set(allowed)
    if missing:
        raise SystemExit(f"vai trò không có trong {roles_file.name}: {sorted(missing)[:5]}")
    return role_of, allowed
