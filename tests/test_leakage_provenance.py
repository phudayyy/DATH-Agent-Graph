from evaluation.kg_index import KGEventIndex
from evaluation.leakage import check_leakage, LeakageLocation


def test_leakage_is_limited_to_events_actually_retrieved():
    index = KGEventIndex([
        (0, "Q1", "P31", "Q111", 2000, 2010),
        (1, "Q1", "P31", "Q222", 2000, 2010),
    ])

    # Q222 is connected in the full KG, but event 1 was not in this question's
    # prompt. It must not be attributed as a leak from retrieved context.
    result = check_leakage(
        "Q222",
        "The model mentioned Q222.",
        {0},
        question_entities={"Q1"},
        relations={"P31"},
        event_index=index,
        candidate_event_ids={0},
    )
    assert result.has_leakage is False
    assert result.leakage_location == LeakageLocation.NONE


def test_retrieved_unauthorized_event_is_counted_as_leakage():
    index = KGEventIndex([
        (0, "Q1", "P31", "Q111", 2000, 2010),
        (1, "Q1", "P31", "Q222", 2000, 2010),
    ])

    result = check_leakage(
        "Q222",
        "The model mentioned Q222.",
        {0},
        question_entities={"Q1"},
        relations={"P31"},
        event_index=index,
        candidate_event_ids={0, 1},
    )
    assert result.has_leakage is True
    assert result.leakage_location == LeakageLocation.BOTH
    assert result.leaked_events == ["1"]
