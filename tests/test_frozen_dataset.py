"""Tập đánh giá đã đóng băng: không byte nào được đổi lặng lẽ.

`eval_set/frozen_manifest.json` ghim SHA-256 của mọi tệp đã commit của tập — 1.500 câu khoá
bằng seed 42 và nhãn quyền ba mức — cùng hash của `roles_*.json` (sinh lại được, không commit)
và của các tệp gốc data_v2. Test này canh phần chạy được trong CI: các tệp đã commit còn nguyên
từng byte, và nhất quán với nhau và với README. Phần cần dữ liệu gốc — sinh lại từ data_v2
rồi so từng byte — là `python scripts/freeze_dataset.py --data data/wikidata_big`.

Đổi tập dữ liệu là được, nhưng phải là một lần đóng băng lại CÓ CHỦ ĐÍCH: sinh lại từ data_v2,
chạy `scripts/freeze_dataset.py --data … --write`, và commit manifest mới cùng dữ liệu mới.
"""

import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((REPO / "eval_set" / "frozen_manifest.json").read_text(encoding="utf-8"))
ROLES_PER_LEVEL = {"loose": 5, "medium": 12, "strict": 25}
FIRST10_IN_README = [14, 27, 40, 45, 103, 112, 136, 185, 191, 224]


def read_json(rel: str) -> dict:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def frozen_ids() -> list[int]:
    return read_json("eval_set/selected_question_ids.json")["question_ids"]


@pytest.mark.parametrize("rel", sorted(MANIFEST["committed"]))
def test_a_frozen_file_is_unchanged_byte_for_byte(rel: str) -> None:
    data = (REPO / rel).read_bytes()
    assert {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} == MANIFEST["committed"][rel], (
        f"{rel} đã đổi sau khi đóng băng ngày {MANIFEST['frozen_on']} — xem docstring của tests/test_frozen_dataset.py"
    )


def test_the_frozen_questions_are_the_1500_ids_drawn_with_the_seed() -> None:
    selection = read_json("eval_set/selected_question_ids.json")
    ids = selection["question_ids"]
    assert (selection["seed"], selection["n"], selection["pool_size"]) == (MANIFEST["seed"], 1500, 18050)
    assert len(ids) == MANIFEST["n_questions"] == 1500
    assert ids == sorted(set(ids)), "tăng dần, không trùng"
    assert ids[:10] == FIRST10_IN_README

    full = read_json("eval_set/eval_set.json")
    assert (full["seed"], full["n"]) == (MANIFEST["seed"], 1500)
    assert [q["uniq_id"] for q in full["questions"]] == ids
    assert all(q["answers"] and q["entities"] and q["relations"] for q in full["questions"])

    metadata = read_json("eval_set/eval_metadata.json")
    assert (metadata["seed"], metadata["n"], metadata["pool_unique_ids"], metadata["data_version"]) == (
        MANIFEST["seed"], 1500, 18050, "data_v2",
    )


@pytest.mark.parametrize("level", ROLES_PER_LEVEL)
def test_each_level_labels_every_frozen_question_exactly_once(level: str) -> None:
    lines = (REPO / f"permission/output/permissions_{level}.jsonl").read_text(encoding="utf-8").splitlines()
    labels = [json.loads(line) for line in lines]
    assert [label["question_id"] for label in labels] == frozen_ids()
    # Định dạng gọn: tập sự kiện của mỗi câu nằm ở roles_{level}.json, phân giải qua role_id.
    roles = {f"role_{level}_{i}" for i in range(ROLES_PER_LEVEL[level])}
    assert {label["role_id"] for label in labels} <= roles
    assert all(label["allowed_event_ids"] == [] for label in labels)

    summary = read_json("permission/output/generation_summary.json")
    assert (summary["seed"], summary["n_questions"]) == (MANIFEST["seed"], 1500)
    assert summary["tightness_levels"][level]["n_roles"] == ROLES_PER_LEVEL[level]
    assert summary["tightness_levels"][level]["total_events_in_kg"] == 328635
    assert f"permission/output/roles_{level}.json" in MANIFEST["regenerated"]
