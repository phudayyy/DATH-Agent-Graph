"""Kiểm thử bắt buộc số 1 (phiếu đề tài, mục 6): bộ sinh mạng lưới quyền.

> Bộ sinh mạng lưới quyền với cùng một hạt giống sinh lại đúng cùng một phân quyền, và với
> một vai trò giả đã biết đáp án thì tập sự kiện nhìn thấy được đúng bằng tập tính tay.

Hai nửa của yêu cầu là hai kiểm thử đầu tệp:

1. `test_the_same_seed_regenerates_the_same_labels_byte_for_byte` chạy đúng lệnh đã sinh nhãn
   quyền đóng băng (`scripts/generate_permissions.py`) ở HAI tiến trình có PYTHONHASHSEED khác
   nhau, so từng byte mọi tệp ghi ra, rồi so với dấu vân tay ghim lúc đóng băng. Lặp trong
   cùng một tiến trình là không đủ: thứ tự duyệt một set chuỗi đổi theo tiến trình, và một bộ
   sinh vô tình dựa vào thứ tự đó vẫn qua được phép thử lặp tại chỗ.
2. `test_a_known_role_sees_exactly_the_hand_computed_events` dựng một vai trò có đáp án tính
   tay, và lấy tập sự kiện nhìn thấy được theo đúng đường của thí nghiệm: bộ sinh → tệp trên
   đĩa → `EvaluationRunner` phân giải role_id thành tập sự kiện.
"""

import hashlib
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.runner import EvaluationRunner
from permission.generator import PermissionGenerator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from select_eval_set import select_eval_set

REPO = Path(__file__).resolve().parent.parent
LEVELS = ("loose", "medium", "strict")
OUTPUTS = sorted(
    [f"roles_{level}.json" for level in LEVELS]
    + [f"permissions_{level}.jsonl" for level in LEVELS]
    + ["generation_summary.json"]
)


def write_kg(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def write_questions(path: Path, questions: list[dict]) -> Path:
    # Đúng hình dạng `questions/*.pickle` của đề: entities/relations/times/answers là SET.
    path.write_bytes(pickle.dumps(questions))
    return path


# ── 1a. Cùng hạt giống ⇒ cùng phân quyền, từng byte ──────────────────────────────────────────
#
# 20 fact, đủ để mỗi nhánh của bộ sinh đều chạy ở ít nhất một mức: lõi ngữ nghĩa vượt 2× chỉ
# tiêu nên bị lấy mẫu xuống (P54 ở 'strict'), lõi thiếu nên được bù ngẫu nhiên (P54 ở
# 'medium'), lõi vừa khít (P31 ở 'strict'), và nhóm không có quan hệ nào trong đồ thị.
DETERMINISM_KG = [
    "Q1\tP54\tQ100\t1990\t1995", "Q2\tP54\tQ100\t1991\t1993", "Q3\tP54\tQ101\t1992\t1992",
    "Q4\tP54\tQ101\t1993\t1999", "Q5\tP54\tQ102\t1994\t1994", "Q1\tP54\tQ102\t1996\t2000",
    "Q6\tP54\tQ100\t1997\t1998", "Q7\tP54\tQ103\t1998\t2002", "Q8\tP54\tQ103\t1999\t1999",
    "Q1\tP31\tQ5\t1980\t2020", "Q9\tP31\tQ5\t1985\t1985", "Q10\tP31\tQ6\t1990\t1991",
    "Q11\tP31\tQ6\t2000\t2000", "Q12\tP39\tQ200\t1990\t1994", "Q13\tP39\tQ200\t1995\t1999",
    "Q1\tP39\tQ201\t2000\t2004", "Q2\tP166\tQ300\t1995\t1995", "Q3\tP166\tQ300\t1996\t1996",
    "Q4\tP166\tQ301\t1997\t1997", "Q5\tP166\tQ301\t1998\t1998",
]
# Nhiều thực thể mỗi câu (set chuỗi), một bản diễn đạt lại trùng uniq_id, một quan hệ không
# thuộc nhóm nào, và một câu không khớp gì (mọi vai trò hoà điểm).
DETERMINISM_QUESTIONS = [
    {"uniq_id": 7, "entities": {"Q1", "Q100"}, "relations": {"P54"}},
    {"uniq_id": 3, "entities": {"Q2", "Q3", "Q4", "Q5"}, "relations": {"P166"}},
    {"uniq_id": 7, "entities": {"Q1", "Q100"}, "relations": {"P54"}},
    {"uniq_id": 11, "entities": {"Q12", "Q13", "Q200"}, "relations": {"P39"}},
    {"uniq_id": 5, "entities": {"Q9", "Q10", "Q11", "Q6"}, "relations": {"P31"}},
    {"uniq_id": 2, "entities": {"Q999"}, "relations": {"P999"}},
]
# Ghim lúc đóng băng tập dữ liệu (2026-11-04), cùng lúc với eval_set/frozen_manifest.json.
# Đổi số này nghĩa là bộ sinh không còn sinh lại được nhãn quyền đã đóng băng: chạy
# `python scripts/freeze_dataset.py --data data/wikidata_big` để xem nhãn thật có đổi không,
# và nếu đổi có chủ đích thì đóng băng lại trong CÙNG một commit.
FROZEN_FIXTURE_DIGEST = "5f9f968c6a621327f2c89a20f16ca63dd1f159bdcd0ff6ec8e8bdb861751f839"


def generate_in_fresh_process(tmp: Path, hash_seed: str) -> Path:
    inputs = tmp / "inputs"
    inputs.mkdir(exist_ok=True)
    kg = write_kg(inputs / "full.txt", DETERMINISM_KG)
    questions = write_questions(inputs / "test.pickle", DETERMINISM_QUESTIONS)
    eval_set = inputs / "selected_question_ids.json"
    eval_set.write_text(json.dumps({"seed": 42, "question_ids": [2, 3, 5, 7, 11]}), encoding="utf-8")
    out = tmp / f"out_hashseed_{hash_seed}"
    done = subprocess.run(  # noqa: S603 — chính script của kho, đối số do test dựng
        [sys.executable, str(REPO / "scripts" / "generate_permissions.py"), "--seed", "42",
         "--eval-set", str(eval_set), "--kg", str(kg), "--questions", str(questions),
         "--out", str(out)],
        check=False, capture_output=True, text=True,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    assert done.returncode == 0, done.stderr
    return out


def fingerprint(out: Path) -> str:
    h = hashlib.sha256()
    for name in OUTPUTS:
        h.update(name.encode() + b"\0" + (out / name).read_bytes() + b"\0")
    return h.hexdigest()


def test_the_same_seed_regenerates_the_same_labels_byte_for_byte(tmp_path: Path) -> None:
    run_a = generate_in_fresh_process(tmp_path, hash_seed="1")
    run_b = generate_in_fresh_process(tmp_path, hash_seed="2")
    for name in OUTPUTS:
        assert (run_a / name).read_bytes() == (run_b / name).read_bytes(), (
            f"{name}: cùng seed 42 mà hai tiến trình sinh ra hai nội dung khác nhau"
        )
    labels = [json.loads(line) for line in (run_a / "permissions_strict.jsonl").read_text().splitlines()]
    assert [row["question_id"] for row in labels] == [2, 3, 5, 7, 11], "mỗi uniq_id đúng một nhãn, theo thứ tự"
    assert fingerprint(run_a) == FROZEN_FIXTURE_DIGEST, (
        f"bộ sinh vẫn tất định nhưng KHÁC lúc đóng băng (vân tay mới {fingerprint(run_a)})"
    )


# ── 1b. Vai trò giả đã biết đáp án ───────────────────────────────────────────────────────────
#
# Tính tay. Đồ thị 8 fact; mức 'medium' phủ 50% ⇒ chỉ tiêu mỗi vai trò = int(8 × 0.5) = 4.
# Nhóm 'classification' = {P31, P279, P361, P364}; trong đồ thị chỉ P31 xuất hiện, ở các dòng
# 0, 2, 4, 6 ⇒ lõi ngữ nghĩa = {0, 2, 4, 6}: đúng bằng chỉ tiêu, nên không bị lấy mẫu xuống
# (lõi ≤ 2 × 4) và không được bù ngẫu nhiên. 'medium' có 12 vai trò trên 8 nhóm nên nhóm nào
# cũng có vai trò. Câu hỏi về Q10, Q11 với P31: vai trò classification được 1 + 1 + 10 = 12
# điểm; mọi vai trò khác nhiều nhất 3 (chỉ trùng thực thể, vì P31 không thuộc nhóm nào khác).
# ⇒ Câu được gán vào vai trò classification, và tập nhìn thấy được là {0, 2, 4, 6} — với MỌI
# hạt giống, vì không bước nào ở trên đụng tới bộ sinh ngẫu nhiên.
KNOWN_ROLE_KG = [
    "Q10\tP31\tQ5\t2000\t2004",    # 0 ← P31
    "Q10\tP166\tQ900\t2001\t2001",  # 1
    "Q11\tP31\tQ5\t1995\t1999",    # 2 ← P31
    "Q12\tP54\tQ700\t2002\t2003",  # 3
    "Q13\tP31\tQ6\t2010\t2010",    # 4 ← P31
    "Q14\tP166\tQ901\t2003\t2003",  # 5
    "Q15\tP31\tQ6\t1980\t1990",    # 6 ← P31
    "Q16\tP54\tQ701\t1999\t2001",  # 7
]
HAND_COMPUTED = {0, 2, 4, 6}


@pytest.mark.parametrize("seed", [42, 2026])
def test_a_known_role_sees_exactly_the_hand_computed_events(tmp_path: Path, seed: int) -> None:
    kg = write_kg(tmp_path / "full.txt", KNOWN_ROLE_KG)
    questions = write_questions(tmp_path / "test.pickle", [{
        "uniq_id": 100, "question": "Which class are Q10 and Q11 instances of?",
        "entities": {"Q10", "Q11"}, "relations": {"P31"}, "times": set(),
        "type": "simple_entity", "answers": {"Q5"},
    }])
    out = tmp_path / "permission_output"
    PermissionGenerator(seed=seed).generate_all(kg, questions, [100], out)

    roles = json.loads((out / "roles_medium.json").read_text(encoding="utf-8"))
    (label,) = [json.loads(line) for line in (out / "permissions_medium.jsonl").read_text().splitlines()]
    assert roles[label["role_id"]]["name"].startswith("classification_")

    runner = EvaluationRunner(
        branch_name="A1", branch_fn=lambda **_: None, question_ids=[100],
        question_path=questions, permission_path=out / "permissions_medium.jsonl",
        tightness="medium", enable_permission=True, kg_path=kg,
    )
    assert set(runner.permissions[100]) == HAND_COMPUTED
    # Vai trò public (nhánh không chặn quyền) thấy cả 8 fact.
    assert set(roles["public"]["allowed_event_ids"]) == set(range(8))


# ── Các tính chất khác của bộ sinh ───────────────────────────────────────────────────────────


@pytest.fixture
def sample_kg(tmp_path: Path) -> Path:
    return write_kg(tmp_path / "test_kg.txt", [
        "Q1\tP31\tQ100\t2000\t2010", "Q2\tP31\tQ100\t2005\t2015",
        "Q3\tP54\tQ200\t2010\t2020", "Q4\tP54\tQ200\t2012\t2022",
        "Q5\tP108\tQ300\t1990\t2000", "Q6\tP108\tQ300\t1995\t2005",
        "Q7\tP276\tQ400\t2000\t2020", "Q8\tP276\tQ400\t2005\t2025",
    ])


@pytest.fixture
def sample_questions(tmp_path: Path) -> Path:
    return write_questions(tmp_path / "test_questions.pickle", [
        {"uniq_id": 1, "entities": {"Q1", "Q2"}, "relations": {"P31"}, "times": {2005}},
        {"uniq_id": 2, "entities": {"Q3", "Q4"}, "relations": {"P54"}, "times": {2015}},
        {"uniq_id": 3, "entities": {"Q5", "Q6"}, "relations": {"P108"}, "times": {1998}},
    ])


def test_a_different_seed_gives_a_different_network(sample_kg: Path, sample_questions: Path) -> None:
    def network(seed: int) -> list[tuple[str, list[int]]]:
        roles, _ = PermissionGenerator(seed=seed).generate_for_tightness(
            "medium", sample_kg, sample_questions, [1, 2, 3]
        )
        return [(rid, role.allowed_event_ids) for rid, role in sorted(roles.items())]

    assert network(42) != network(999)


def test_tighter_levels_have_more_and_narrower_roles(sample_kg: Path, sample_questions: Path) -> None:
    generator = PermissionGenerator(seed=42)
    shape = {}
    for tightness in LEVELS:
        roles, _ = generator.generate_for_tightness(tightness, sample_kg, sample_questions, [1, 2, 3])
        narrow = [role for rid, role in roles.items() if rid != "public"]
        shape[tightness] = (len(narrow), sum(len(r.allowed_event_ids) for r in narrow) / len(narrow))
    assert shape["loose"][0] < shape["medium"][0] < shape["strict"][0]
    assert shape["loose"][1] > shape["medium"][1] > shape["strict"][1]


def test_every_question_gets_a_role_with_events_and_public_sees_all(
    sample_kg: Path, sample_questions: Path
) -> None:
    generator = PermissionGenerator(seed=42)
    for tightness in LEVELS:
        roles, labels = generator.generate_for_tightness(tightness, sample_kg, sample_questions, [1, 2, 3])
        assert [label.question_id for label in labels] == [1, 2, 3]
        for label in labels:
            assert label.role_id in roles and roles[label.role_id].allowed_event_ids
        assert sorted(roles["public"].allowed_event_ids) == list(range(8))


def test_selecting_the_eval_set_with_the_same_seed_returns_the_same_ids(tmp_path: Path) -> None:
    pool = write_questions(tmp_path / "pool.pickle", [
        {"uniq_id": i, "question": f"Question {i}", "entities": {f"Q{i}"}, "relations": {"P31"},
         "times": {2000 + i % 20}, "type": "time_join", "answers": {f"Q{i + 100}"}}
        for i in range(5000)
    ])
    ids_a, _, _, _ = select_eval_set(pool, seed=42, n=1500)
    ids_b, _, _, _ = select_eval_set(pool, seed=42, n=1500)
    assert ids_a == ids_b == sorted(set(ids_a))
    assert len(ids_a) == 1500
