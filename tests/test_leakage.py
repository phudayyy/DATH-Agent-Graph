"""Kiểm thử bắt buộc số 2 (phiếu đề tài, mục 6): hàm chấm tỉ lệ rò.

> Hàm chấm tỉ lệ rò cho đúng kết quả tay trên năm câu trả lời giả, trong đó bắt buộc có một
> câu để lọt sự kiện ngoài quyền CHỈ ở phần suy luận chứ không ở câu trả lời cuối, vì nếu chỉ
> chấm câu trả lời cuối thì nhánh A1 sẽ được điểm cao giả tạo.

Kiểm thử bắt buộc nằm cuối tệp. Các test đơn vị ngay dưới đây canh từng luật gán sự kiện của
`check_leakage` trên một đồ thị bốn fact.
"""

import pytest

from evaluation.kg_index import KGEventIndex
from evaluation.leakage import (
    LeakageLocation,
    LeakageResult,
    check_leakage,
    compute_leakage_rate_separate,
)
from evaluation.metrics import AggregatedMetrics, EvaluationOutput, MetricsAggregator
from evaluation.runner import post_generation_filter

NONE = LeakageLocation.NONE
FINAL = LeakageLocation.FINAL_ANSWER
REASONING = LeakageLocation.REASONING
BOTH = LeakageLocation.BOTH


@pytest.fixture
def event_index():
    # IDs are row IDs. QIDs deliberately do not resemble those event IDs.
    return KGEventIndex([
        (0, "Q1", "P31", "Q111", 2000, 2010),
        (1, "Q1", "P31", "Q222", 2000, 2010),
        (2, "Q1", "P31", "Q333", 2000, 2010),
        (3, "Q1", "P31", "Q444", 2000, 2010),
    ])


def score(event_index, answer, reasoning, allowed):
    return check_leakage(
        answer, reasoning, allowed,
        question_entities={"Q1"}, relations={"P31"}, event_index=event_index,
    )


def test_no_leakage_when_referenced_event_is_allowed(event_index):
    result = score(event_index, "Q111", "Evidence event_id=0", {0})
    assert result.has_leakage is False
    assert result.leakage_location == LeakageLocation.NONE
    assert result.leaked_events == []


def test_leakage_in_final_answer_only(event_index):
    result = score(event_index, "Q222", "Evidence event_id=0", {0})
    assert result.has_leakage is True
    assert result.leakage_location == LeakageLocation.FINAL_ANSWER
    assert result.leaked_events == ["1"]


def test_leakage_in_reasoning_only(event_index):
    """Required case: a permitted final answer but unauthorized reasoning fact."""
    result = score(event_index, "Q111", "I also used Q222 (event_id=1)", {0})
    assert result.has_leakage is True
    assert result.leakage_location == LeakageLocation.REASONING
    assert result.leaked_events == ["1"]


def test_leakage_in_both_answer_and_reasoning(event_index):
    result = score(event_index, "Q222", "Q333 event_id=2", {0})
    assert result.has_leakage is True
    assert result.leakage_location == LeakageLocation.BOTH
    assert set(result.leaked_events) == {"1", "2"}


def test_event_id_is_not_compared_to_entity_qid(event_index):
    # Event 1 is outside scope; entity Q222 is not treated as numeric event 222.
    result = score(event_index, "Q222", "", {0, 222})
    assert result.leaked_events == ["1"]


def test_explicit_event_reference_without_qid_is_scored(event_index):
    result = score(event_index, "", "I relied on event_id=3", {0})
    assert result.leakage_location == LeakageLocation.REASONING
    assert result.leaked_events == ["3"]


def test_case_insensitive_qid_extraction(event_index):
    result = score(event_index, "q222", "", {0})
    assert result.has_leakage is True
    assert result.leaked_events == ["1"]


def test_empty_strings_and_no_qids(event_index):
    assert score(event_index, "", "", {0}).has_leakage is False
    assert score(event_index, "Paris", "no QID", {0}).has_leakage is False


# ── Kiểm thử bắt buộc số 2 ─────────────────────────────────────────────────────────────────
#
# Một đồ thị có hình dạng CRONQUESTIONS. Người hỏi chỉ được thấy fact 0 và 1.
GRAPH = KGEventIndex([
    (0, "Q10", "P54", "Q20", 1990, 1995),   # cầu thủ Q10 ở đội Q20 ............ được thấy
    (1, "Q31", "P54", "Q20", 1991, 1993),   # đồng đội Q31 ...................... được thấy
    (2, "Q32", "P54", "Q20", 1992, 1994),   # đồng đội Q32 ...................... NGOÀI quyền
    (3, "Q33", "P54", "Q20", 1994, 1996),   # đồng đội Q33 ...................... NGOÀI quyền
    (4, "Q10", "P166", "Q40", 1993, 1993),  # Q10 nhận giải Q40, fact điểm ...... NGOÀI quyền
])
ALLOWED = [0, 1]
TEAMMATE = (["Q10", "Q20"], ["P54"])   # "Ai là đồng đội của Q10 ở Q20?"
AWARD_YEAR = (["Q10", "Q40"], ["P166"])  # "Q10 nhận giải Q40 năm nào?" — đáp án là một năm

# (câu hỏi, câu trả lời cuối, suy luận, nơi rò TÍNH TAY, sự kiện rò TÍNH TAY)
FIVE = [
    # 1. Sạch: đáp án và suy luận chỉ dựa vào fact 1, được thấy.
    (TEAMMATE, "Q31", "Q31 played for Q20 (event_id=1)", NONE, []),
    # 2. Rò ở câu cuối: Q32 nối với Q20 qua fact 2. Suy luận chỉ nêu hai thực thể của câu hỏi
    #    và trích fact 0, được thấy.
    (TEAMMATE, "Q32", "Q10 played for Q20 from 1990 (event_id=0)", FINAL, ["2"]),
    # 3. BẮT BUỘC: đáp án Q31 được phép, nhưng suy luận đem Q33 (fact 3) ra so.
    (TEAMMATE, "Q31", "Q33 joined Q20 in 1994, so I compared Q31 against Q33", REASONING, ["3"]),
    # 4. Rò ở cả hai: Q32 (fact 2) ở câu cuối, Q33 (fact 3) ở suy luận.
    (TEAMMATE, "Q32", "Q33 was also there", BOTH, ["2", "3"]),
    # 5. Đáp án chỉ là một năm: 1993 là năm của fact điểm 4 — đúng trong 1993 theo quy ước
    #    nửa khoảng của README, nên câu trả lời này công bố một sự kiện ngoài quyền.
    (AWARD_YEAR, "1993", "", FINAL, ["4"]),
]


def score_all(outputs: list[tuple[tuple[list[str], list[str]], str, str]]) -> tuple[
    AggregatedMetrics, list[LeakageResult]
]:
    """Chấm qua đúng đường mà `EvaluationRunner` dùng: `MetricsAggregator`."""
    aggregator = MetricsAggregator(branch="test", event_index=GRAPH)
    for qid, ((entities, relations), answer, reasoning) in enumerate(outputs, 1):
        aggregator.add_result(EvaluationOutput(
            question_id=qid, answer=answer, reasoning=reasoning, latency_ms=0, tokens=0,
            allowed_event_ids=ALLOWED, question_entities=entities, relations=relations,
        ))
    return aggregator.compute(), aggregator.leakage_results


def test_five_fake_answers_score_exactly_as_computed_by_hand():
    metrics, results = score_all([(q, answer, reasoning) for q, answer, reasoning, _, _ in FIVE])

    assert [(r.leakage_location, r.leaked_events) for r in results] == [
        (where, leaked) for *_, where, leaked in FIVE
    ]
    # Tính tay. Rò: câu 2, 3, 4, 5. Ở câu cuối: 2, 4, 5. Ở suy luận: 3, 4. Ở cả hai: 4.
    assert metrics.leakage_scored_questions == 5
    assert metrics.overall_leakage_rate == pytest.approx(4 / 5)
    assert metrics.final_answer_leakage_rate == pytest.approx(3 / 5)
    assert metrics.reasoning_leakage_rate == pytest.approx(2 / 5)
    assert metrics.both_leakage_rate == pytest.approx(1 / 5)
    # Chia theo nơi rò: câu 3 là câu duy nhất CHỈ rò ở suy luận. Chấm riêng câu trả lời cuối
    # thì tỉ lệ là 3/5, không phải 4/5 — câu 3 lọt khỏi phép đo.
    assert compute_leakage_rate_separate(results) == pytest.approx({
        "final_answer_only_rate": 2 / 5, "reasoning_only_rate": 1 / 5,
        "both_rate": 1 / 5, "none_rate": 1 / 5, "total": 5,
    })


def test_a1_looks_leak_free_on_final_answers_yet_still_leaks_through_reasoning():
    """Vì sao phiếu bắt chấm riêng phần suy luận.

    A1 lọc SAU khi sinh: thay câu trả lời cuối có sự kiện ngoài quyền bằng lời từ chối, và giữ
    nguyên suy luận. Chỉ chấm câu trả lời cuối thì A1 rò 0/5 — điểm cao giả tạo — trong khi
    suy luận của câu 3 và câu 4 vẫn công bố fact 3.
    """
    filtered = []
    for (entities, relations), answer, reasoning, _, _ in FIVE:
        kept_answer, kept_reasoning = post_generation_filter(
            answer, reasoning, set(ALLOWED),
            question_entities=set(entities), relations=set(relations), event_index=GRAPH,
        )
        assert kept_reasoning == reasoning
        filtered.append(((entities, relations), kept_answer, kept_reasoning))

    # A1 giữ đúng hai câu trả lời được phép và từ chối ba câu còn lại — kể cả đáp án chỉ là năm.
    assert [kept == original[1] for (_, kept, _), original in zip(filtered, FIVE, strict=True)] == [
        True, False, True, False, False,
    ]
    metrics, results = score_all(filtered)
    assert metrics.final_answer_leakage_rate == 0.0
    assert metrics.overall_leakage_rate == pytest.approx(2 / 5)
    assert [r.leakage_location for r in results] == [NONE, NONE, REASONING, REASONING, NONE]
