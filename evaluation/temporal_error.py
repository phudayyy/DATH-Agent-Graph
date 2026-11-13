"""
Temporal error evaluation metric.
Measures errors where answer is correct for latest state but wrong for as-of time.
"""

from typing import Set, List, Dict, Optional
from dataclasses import dataclass

from .hits_at_1 import compute_hits_at_1
from .kg_index import event_is_valid


@dataclass
class TemporalErrorResult:
    """Temporal error evaluation result for a single question."""
    question_id: int
    has_temporal_error: bool
    as_of_year: int
    predicted_answer: str
    gold_answer_at_asof: str
    gold_answer_latest: str
    error_type: str  # 'correct', 'temporal_error', 'other_error'


def parse_year_from_question(question: dict) -> Optional[int]:
    """Extract the as-of year from a question."""
    times = question.get('times', set())
    if times:
        # CRONQUESTIONS uses year-level granularity
        return max(times)  # Use the latest time mentioned as the as-of point
    return None


def check_temporal_error(
    predicted: str,
    gold_at_asof: Set[str],
    gold_latest: Set[str],
    as_of_year: int
) -> TemporalErrorResult:
    """
    Check if there's a temporal error.

    Temporal error = answer is correct for latest state but wrong for as-of time.

    Args:
        predicted: Model's predicted answer
        gold_at_asof: Gold answers valid at the as-of time
        gold_latest: Gold answers valid at latest time
        as_of_year: The as-of year being queried

    Returns:
        TemporalErrorResult
    """
    # Use the same strict rank-1 decision as Hits@1. In particular, an
    # answer mentioning multiple distinct QIDs is ambiguous, not a hit merely
    # because one of the mentioned entities belongs to a gold set.
    hits_asof = compute_hits_at_1(predicted, gold_at_asof).hits_at_1 == 1.0
    hits_latest = compute_hits_at_1(predicted, gold_latest).hits_at_1 == 1.0
    if hits_asof:
        return TemporalErrorResult(
            question_id=-1,
            has_temporal_error=False,
            as_of_year=as_of_year,
            predicted_answer=predicted,
            gold_answer_at_asof=str(gold_at_asof),
            gold_answer_latest=str(gold_latest),
            error_type='correct'
        )

    # Case 2: Temporal error - correct at latest but wrong at as-of.
    if hits_latest and not hits_asof:
        return TemporalErrorResult(
            question_id=-1,
            has_temporal_error=True,
            as_of_year=as_of_year,
            predicted_answer=predicted,
            gold_answer_at_asof=str(gold_at_asof),
            gold_answer_latest=str(gold_latest),
            error_type='temporal_error'
        )

    # Case 3: Other error (wrong at both)
    return TemporalErrorResult(
        question_id=-1,
        has_temporal_error=False,
        as_of_year=as_of_year,
        predicted_answer=predicted,
        gold_answer_at_asof=str(gold_at_asof),
        gold_answer_latest=str(gold_latest),
        error_type='other_error'
    )


def compute_temporal_error_rate(results: List[TemporalErrorResult]) -> Dict:
    """
    Compute aggregate temporal error rate.

    Temporal error rate = fraction of questions where answer is correct for
    latest state but wrong for as-of time, among questions that have a valid as-of time.
    """
    if not results:
        return {
            'temporal_error_rate': 0.0,
            'correct_rate': 0.0,
            'other_error_rate': 0.0,
            'total': 0,
            'with_asof': 0
        }

    # Only consider questions with valid as-of time
    with_asof = [r for r in results if r.as_of_year is not None and r.as_of_year > 0]

    if not with_asof:
        return {
            'temporal_error_rate': 0.0,
            'correct_rate': 0.0,
            'other_error_rate': 0.0,
            'total': len(results),
            'with_asof': 0
        }

    total = len(with_asof)
    temporal_errors = sum(1 for r in with_asof if r.error_type == 'temporal_error')
    correct = sum(1 for r in with_asof if r.error_type == 'correct')
    other_errors = sum(1 for r in with_asof if r.error_type == 'other_error')

    return {
        'temporal_error_rate': temporal_errors / total,
        'correct_rate': correct / total,
        'other_error_rate': other_errors / total,
        'total': len(results),
        'with_asof': total,
        'temporal_error_count': temporal_errors,
        'correct_count': correct,
        'other_error_count': other_errors
    }


def left_closed_boundary_check(event_start: int, event_end: Optional[int], as_of_year: int) -> bool:
    """
    Check if an event is valid at as_of_year using the README's left-closed convention.

    Left-closed (nửa khoảng đóng bên trái): a fact [start_year, end_year] maps to
    [1/1/start_year, 1/1/(end_year + 1)), so at year granularity it is valid iff
    start_year <= as_of_year <= end_year. Same predicate as `kg_index.event_is_valid`.

    For events with no end year recorded (None), treat end_year as infinity.
    """
    if event_end is None:
        return event_start <= as_of_year
    return event_is_valid(event_start, event_end, as_of_year)
