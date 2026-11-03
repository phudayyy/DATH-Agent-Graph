"""
Evaluation package for CO3101 G1 - Permission and As-of-time Filtering Evaluation.
"""

from .hits_at_1 import HitsAt1Result, compute_hits_at_1, compute_hits_at_1_batch, normalize_answer
from .leakage import (
    LeakageResult, LeakageLocation, check_leakage,
    compute_leakage_rate, compute_leakage_rate_separate, extract_event_ids,
    extract_explicit_event_refs
)
from .temporal_error import (
    TemporalErrorResult, check_temporal_error, compute_temporal_error_rate,
    left_closed_boundary_check, parse_year_from_question
)
from .metrics import (
    EvaluationOutput, AggregatedMetrics, MetricsAggregator, compute_delta_metrics
)
from .runner import (
    EvaluationRunner, Question, create_a0_branch, create_a1_branch, create_a2_branch,
    post_generation_filter, dummy_model_fn, dummy_a2_model_fn
)
from .kg_index import KGEventIndex, event_is_valid

__all__ = [
    # hits_at_1
    'HitsAt1Result', 'compute_hits_at_1', 'compute_hits_at_1_batch', 'normalize_answer',
    # leakage
    'LeakageResult', 'LeakageLocation', 'check_leakage',
    'compute_leakage_rate', 'compute_leakage_rate_separate', 'extract_event_ids',
    'extract_explicit_event_refs',
    # temporal_error
    'TemporalErrorResult', 'check_temporal_error', 'compute_temporal_error_rate',
    'left_closed_boundary_check', 'parse_year_from_question',
    # metrics
    'EvaluationOutput', 'AggregatedMetrics', 'MetricsAggregator', 'compute_delta_metrics',
    # runner
    'EvaluationRunner', 'Question', 'create_a0_branch', 'create_a1_branch', 'create_a2_branch',
    'post_generation_filter', 'dummy_model_fn', 'dummy_a2_model_fn',
    'KGEventIndex', 'event_is_valid',
]
