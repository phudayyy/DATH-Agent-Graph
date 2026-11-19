from evaluation.kg_index import KGEventIndex
from evaluation.a1_post_filter import filter_a1_answer, REFUSAL


def make_index():
    return KGEventIndex([
        (0, "Q1", "P54", "Q100", 2000, 2010),
        (1, "Q1", "P54", "Q200", 2011, 2020),
        (2, "Q2", "P54", "Q3", 2005, 2015),
    ])


def test_entity_answer_allowed_when_retrieved_support_is_allowed():
    index = make_index()
    answer, reasoning, audit = filter_a1_answer(
        answer="Q100",
        reasoning="The linked fact supports Q100.",
        retrieved_event_ids=[0],
        allowed_event_ids=[0],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="simple_entity",
        event_index=index,
    )
    assert answer == "Q100"
    assert audit["decision"] == "retain"
    assert audit["reason"] == "answer_supported_by_permitted_retrieved_event"
    assert reasoning == "The linked fact supports Q100."


def test_entity_answer_refused_when_support_is_outside_permission():
    index = make_index()
    answer, _, audit = filter_a1_answer(
        answer="Q100",
        reasoning="The linked fact supports Q100.",
        retrieved_event_ids=[0],
        allowed_event_ids=[1],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="simple_entity",
        event_index=index,
    )
    assert answer == REFUSAL
    assert audit["decision"] == "refuse"
    assert audit["unauthorized_supporting_event_ids"] == [0]


def test_entity_answer_not_attributed_to_events_not_retrieved():
    index = make_index()
    answer, _, audit = filter_a1_answer(
        answer="Q100",
        reasoning="A claim without cited source.",
        retrieved_event_ids=[1],
        allowed_event_ids=[],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="simple_entity",
        event_index=index,
    )
    assert answer == "Q100"
    assert audit["reason"] == "unattributed_no_matching_retrieved_event"


def test_query_entity_echo_is_not_treated_as_new_disclosure():
    index = make_index()
    answer, _, audit = filter_a1_answer(
        answer="Q1",
        reasoning="The entity in the question is Q1.",
        retrieved_event_ids=[0],
        allowed_event_ids=[],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="time_join",
        event_index=index,
    )
    assert answer == "Q1"
    assert audit["reason"] == "query_entity_echo_no_new_entity_disclosure"


def test_time_answer_uses_retrieved_fact_interval():
    index = make_index()
    answer, _, audit = filter_a1_answer(
        answer="2008",
        reasoning="Q1 was related to Q100 in that year.",
        retrieved_event_ids=[0],
        allowed_event_ids=[0],
        question_entities=["Q1", "Q100"],
        relations=["P54"],
        answer_type="time",
        question_type="simple_time",
        event_index=index,
    )
    assert answer == "2008"
    assert audit["reason"] == "answer_supported_by_permitted_retrieved_event"


def test_time_answer_outside_permission_is_refused():
    index = make_index()
    answer, _, audit = filter_a1_answer(
        answer="2008",
        reasoning="Q1 was related to Q100 in that year.",
        retrieved_event_ids=[0],
        allowed_event_ids=[1],
        question_entities=["Q1", "Q100"],
        relations=["P54"],
        answer_type="time",
        question_type="simple_time",
        event_index=index,
    )
    assert answer == REFUSAL
    assert audit["decision"] == "refuse"


def test_reasoning_leak_is_measured_but_not_erased():
    index = make_index()
    reasoning = "Q200 was also related to Q1."
    answer, kept_reasoning, audit = filter_a1_answer(
        answer="Q100",
        reasoning=reasoning,
        retrieved_event_ids=[0, 1],
        allowed_event_ids=[0],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="simple_entity",
        event_index=index,
    )
    # Final answer is supported by allowed event 0; reasoning-only event 1
    # is unauthorized and remains visible for the separate leakage metric.
    assert answer == "Q100"
    assert kept_reasoning == reasoning
    assert audit["reasoning_leakage"] is True
    assert audit["reasoning_unauthorized_event_ids"] == [1]


def test_empty_answer_is_not_counted_as_permission_violation():
    index = make_index()
    answer, _, audit = filter_a1_answer(
        answer="",
        reasoning="No answer was produced.",
        retrieved_event_ids=[0],
        allowed_event_ids=[],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="simple_entity",
        event_index=index,
    )
    assert answer == ""
    assert audit["decision"] == "retain"
    assert audit["reason"] == "empty_answer"


def test_reasoning_support_is_limited_to_events_actually_retrieved():
    index = make_index()
    reasoning = "Q200 was related to Q1."
    answer, _, audit = filter_a1_answer(
        answer="Q100",
        reasoning=reasoning,
        retrieved_event_ids=[0],  # event 1 was not in model context
        allowed_event_ids=[0],
        question_entities=["Q1"],
        relations=["P54"],
        answer_type="entity",
        question_type="simple_entity",
        event_index=index,
    )
    assert answer == "Q100"
    assert audit["reasoning_leakage"] is False
    assert audit["reasoning_supporting_event_ids"] == []
