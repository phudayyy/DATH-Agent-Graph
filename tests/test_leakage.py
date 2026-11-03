"""Synthetic event-level leakage tests, including reasoning-only leakage."""

import pytest

from evaluation.kg_index import KGEventIndex
from evaluation.leakage import (
    LeakageLocation,
    LeakageResult,
    check_leakage,
    compute_leakage_rate,
    compute_leakage_rate_separate,
)


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


def test_aggregate_leakage_rates():
    results = [
        LeakageResult(1, False, LeakageLocation.NONE, [], "A", "R", {0}),
        LeakageResult(2, True, LeakageLocation.FINAL_ANSWER, ["1"], "A", "R", {0}),
        LeakageResult(3, True, LeakageLocation.REASONING, ["2"], "A", "R", {0}),
        LeakageResult(4, True, LeakageLocation.BOTH, ["3"], "A", "R", {0}),
        LeakageResult(5, False, LeakageLocation.NONE, [], "A", "R", {0}),
    ]
    rates = compute_leakage_rate(results)
    assert rates["total"] == 5
    assert rates["leaked_count"] == 3
    assert rates["overall_leakage_rate"] == 0.6
    assert rates["final_answer_leakage_rate"] == 0.4
    assert rates["reasoning_leakage_rate"] == 0.4
    separate = compute_leakage_rate_separate(results)
    assert separate["final_answer_only_rate"] == 0.2
    assert separate["reasoning_only_rate"] == 0.2
    assert separate["both_rate"] == 0.2
    assert separate["none_rate"] == 0.4
