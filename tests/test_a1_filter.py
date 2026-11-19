"""A1 post-generation filter behavior and reasoning-retention tests."""

from evaluation.kg_index import KGEventIndex
from evaluation.runner import post_generation_filter


def make_index():
    return KGEventIndex([
        (0, "Q1", "P31", "Q111", 2000, 2010),
        (1, "Q1", "P31", "Q222", 2000, 2010),
    ])


def test_a1_refuses_unauthorized_final_answer_but_preserves_reasoning():
    index = make_index()
    reasoning = "Intermediate evidence included Q222 event_id=1."
    answer, kept_reasoning = post_generation_filter(
        "Q222", reasoning, {0},
        question_entities={"Q1"}, relations={"P31"}, event_index=index,
    )
    assert answer.startswith("I cannot answer")
    assert kept_reasoning == reasoning


def test_a1_allows_answer_supported_by_permitted_event():
    index = make_index()
    answer, reasoning = post_generation_filter(
        "Q111", "Evidence event_id=0", {0},
        question_entities={"Q1"}, relations={"P31"}, event_index=index,
    )
    assert answer == "Q111"
    assert reasoning == "Evidence event_id=0"


def test_a1_fails_closed_on_ungrounded_prose():
    index = make_index()
    answer, _ = post_generation_filter(
        "Some unsupported claim", "", {0},
        question_entities={"Q1"}, relations={"P31"}, event_index=index,
    )
    assert answer.startswith("I cannot answer")


def test_a1_fails_closed_without_event_provenance():
    answer, reasoning = post_generation_filter("Q111", "reasoning", {0})
    assert answer.startswith("I cannot answer")
    assert reasoning == "reasoning"


def test_a1_does_not_erase_reasoning_only_leak():
    index = make_index()
    reasoning = "The model also saw Q222 event_id=1."
    answer, kept_reasoning = post_generation_filter(
        "Q111", reasoning, {0},
        question_entities={"Q1"}, relations={"P31"}, event_index=index,
    )
    assert answer == "Q111"
    assert kept_reasoning == reasoning
