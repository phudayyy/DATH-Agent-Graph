from a0.generative_qa import GenerativeKGQA


def test_entity_answer_normalization_extracts_single_qid():
    assert GenerativeKGQA._normalize_answer("Answer: Q123", "entity") == (
        "Q123", "valid_single_qid"
    )


def test_entity_answer_normalization_rejects_multiple_qids():
    assert GenerativeKGQA._normalize_answer("Q123 or Q456", "entity") == (
        "", "ambiguous_multiple_qids"
    )


def test_time_answer_normalization_selects_first_ranked_year():
    assert GenerativeKGQA._normalize_answer("2009, 2012", "time") == (
        "2009", "multiple_years_rank1_selected"
    )


def test_time_answer_normalization_extracts_year_from_date():
    assert GenerativeKGQA._normalize_answer("December 31, 2011", "time") == (
        "2011", "valid_single_year"
    )


def test_parser_does_not_take_question_anchor_from_reasoning_as_answer():
    answer, reasoning, status = GenerativeKGQA._parse_response(
        "The answer may be found by checking Q123 and Q456.", "entity"
    )
    assert answer == ""
    assert status == "invalid_missing_json_answer"


def test_parser_reads_json_answer_and_reasoning():
    answer, reasoning, status = GenerativeKGQA._parse_response(
        '{"answer":"Q123","reasoning":"Supported by the supplied fact."}',
        "entity",
    )
    assert answer == "Q123"
    assert reasoning == "Supported by the supplied fact."
    assert status == "valid_single_qid"
