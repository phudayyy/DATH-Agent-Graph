"""Rank-1 answer scoring aligned with CronKGQA's top-k membership metric.

CronKGQA evaluates an ordered candidate list. This project stores one answer
string, so a string naming multiple distinct QIDs has no recoverable rank-1
candidate and is scored as ambiguous rather than searching for any gold QID.
"""

from typing import Set, List
from dataclasses import dataclass


@dataclass
class HitsAt1Result:
    """Result of Hits@1 evaluation for a single question."""
    question_id: int
    hits_at_1: float  # 1.0 if correct, 0.0 otherwise
    predicted_answer: str
    gold_answers: Set[str]
    match_type: str  # 'exact', 'single_qid', 'ambiguous', 'none'


def normalize_answer(answer: str) -> str:
    """
    Normalize answer for comparison.
    Removes whitespace, lowercases, handles Wikidata Q-ids.
    """
    if answer is None or answer == "":
        return ""
    answer = str(answer)
    # Handle Wikidata IDs
    answer = answer.strip()
    if answer.startswith('Q') and answer[1:].isdigit():
        return answer.upper()
    return answer.lower().strip()


def compute_hits_at_1(predicted: str, gold_answers: Set[str]) -> HitsAt1Result:
    """
    Score one rank-1 prediction against a possibly multi-answer gold set.

    Args:
        predicted: Model's single answer, optionally prose containing one QID
        gold_answers: Set of gold answer strings

    Returns:
        HitsAt1Result with score and metadata
    """
    pred_norm = normalize_answer(predicted)
    gold_norm = {normalize_answer(a) for a in gold_answers}

    # Exact match after normalization
    if pred_norm in gold_norm:
        return HitsAt1Result(
            question_id=-1,  # to be filled by caller
            hits_at_1=1.0,
            predicted_answer=predicted,
            gold_answers=gold_answers,
            match_type='exact'
        )

    # A prose answer may contain one unambiguous entity ID (e.g. "Answer: Q123").
    # Do not scan for any matching gold ID: that can award Hits@1 when the model
    # lists several candidates and only a lower-ranked one happens to be gold.
    import re
    qid_matches = re.findall(r'\b(Q\d+)\b', str(predicted or ""), re.IGNORECASE)
    unique_qids = {normalize_answer(qid) for qid in qid_matches}
    if len(unique_qids) == 1:
        only_qid = next(iter(unique_qids))
        if only_qid in gold_norm:
            return HitsAt1Result(
                question_id=-1,
                hits_at_1=1.0,
                predicted_answer=predicted,
                gold_answers=gold_answers,
                match_type='single_qid'
            )
    elif len(unique_qids) > 1:
        return HitsAt1Result(
            question_id=-1,
            hits_at_1=0.0,
            predicted_answer=predicted,
            gold_answers=gold_answers,
            match_type='ambiguous'
        )

    return HitsAt1Result(
        question_id=-1,
        hits_at_1=0.0,
        predicted_answer=predicted,
        gold_answers=gold_answers,
        match_type='none'
    )


def compute_hits_at_1_batch(results: List[HitsAt1Result]) -> dict:
    """
    Compute aggregate Hits@1 over a batch of results.

    Returns:
        Dict with 'hits_at_1', 'total', 'correct', 'by_match_type'
    """
    if not results:
        return {'hits_at_1': 0.0, 'total': 0, 'correct': 0, 'by_match_type': {}}

    total = len(results)
    correct = sum(1 for r in results if r.hits_at_1 == 1.0)

    by_type = {}
    for r in results:
        by_type[r.match_type] = by_type.get(r.match_type, 0) + 1

    return {
        'hits_at_1': correct / total,
        'total': total,
        'correct': correct,
        'by_match_type': by_type
    }
