"""
Aggregated metrics module.
Combines Hits@1, Leakage, Temporal Error, Latency, Token usage.
"""

import json
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

from .hits_at_1 import HitsAt1Result, compute_hits_at_1, compute_hits_at_1_batch
from .leakage import (LeakageLocation, LeakageResult, check_leakage,
                      compute_leakage_rate)
from .temporal_error import TemporalErrorResult, check_temporal_error, compute_temporal_error_rate


@dataclass
class EvaluationOutput:
    """Standard output contract for all branches (A0, A1, A2)."""
    question_id: int
    answer: str
    reasoning: str
    latency_ms: int
    tokens: int
    # Permission/time context (filled by evaluator)
    allowed_event_ids: Optional[List[int]] = None
    as_of_year: Optional[int] = None
    gold_answers: Optional[List[str]] = None
    gold_answers_at_asof: Optional[List[str]] = None
    gold_answers_latest: Optional[List[str]] = None
    question_entities: Optional[List[str]] = None
    relations: Optional[List[str]] = None


@dataclass
class AggregatedMetrics:
    """All metrics aggregated for a branch run."""
    branch: str  # 'A0', 'A1', 'A2-public', 'A2-private'
    tightness: Optional[str]  # 'loose', 'medium', 'strict', or None for no-permission runs
    permission_enabled: bool
    asof_enabled: bool

    # Hits@1
    hits_at_1: float
    hits_at_1_total: int
    hits_at_1_correct: int

    # Leakage
    overall_leakage_rate: Optional[float]
    final_answer_leakage_rate: Optional[float]
    reasoning_leakage_rate: Optional[float]
    both_leakage_rate: Optional[float]
    leakage_scored_questions: int

    # Temporal error
    temporal_error_rate: Optional[float]
    temporal_correct_rate: Optional[float]
    temporal_other_error_rate: Optional[float]
    temporal_with_asof: int

    # Latency & tokens
    avg_latency_ms: float
    avg_tokens: float
    total_latency_ms: int
    total_tokens: int
    reasoning_available: bool

    # Counts
    n_questions: int


class MetricsAggregator:
    """Aggregates all evaluation metrics for a branch run."""

    def __init__(self, branch: str, tightness: Optional[str] = None,
                 permission_enabled: bool = False, asof_enabled: bool = False,
                 event_index=None):
        self.branch = branch
        self.tightness = tightness
        self.permission_enabled = permission_enabled
        self.asof_enabled = asof_enabled
        self.event_index = event_index

        self.hits_results: List[HitsAt1Result] = []
        self.leakage_results: List[LeakageResult] = []
        # Keep one slot per question so detailed output stays aligned even
        # when a query form has no defensible temporal gold labels.
        self.temporal_results: List[Optional[TemporalErrorResult]] = []
        self.latencies: List[int] = []
        self.tokens: List[int] = []
        self.answers: List[str] = []
        self.reasonings: List[str] = []
        self.leakage_scored: List[bool] = []
        self.question_ids: List[int] = []

    def add_result(self, output: EvaluationOutput):
        """Add a single question's output and compute all metrics."""
        qid = output.question_id
        self.question_ids.append(qid)
        self.latencies.append(output.latency_ms)
        self.tokens.append(output.tokens)
        self.answers.append(output.answer or "")
        self.reasonings.append(output.reasoning or "")

        # Hits@1
        gold = set(output.gold_answers) if output.gold_answers else set()
        hits_result = compute_hits_at_1(output.answer, gold)
        hits_result.question_id = qid
        self.hits_results.append(hits_result)

        # Leakage
        scored = output.allowed_event_ids is not None and self.event_index is not None
        allowed = set(output.allowed_event_ids) if scored else set()
        self.leakage_scored.append(scored)
        if self.event_index is not None:
            leakage_result = check_leakage(
                output.answer, output.reasoning, allowed,
                question_entities=set(output.question_entities or []),
                relations=set(output.relations or []),
                event_index=self.event_index,
            )
        else:
            leakage_result = LeakageResult(
                question_id=qid, has_leakage=False,
                leakage_location=LeakageLocation.NONE, leaked_events=[],
                final_answer=output.answer or "", reasoning=output.reasoning or "",
                allowed_event_ids=allowed,
            )
        leakage_result.question_id = qid
        self.leakage_results.append(leakage_result)

        # Temporal error
        # Measure stale-answer behavior for every branch on as-of questions,
        # including A0/A1 where temporal filtering is intentionally OFF.
        if (output.as_of_year and
                output.gold_answers_at_asof is not None and
                output.gold_answers_latest is not None):
            gold_asof = set(output.gold_answers_at_asof) if output.gold_answers_at_asof else set()
            gold_latest = set(output.gold_answers_latest) if output.gold_answers_latest else set()
            temporal_result = check_temporal_error(
                output.answer, gold_asof, gold_latest, output.as_of_year
            )
            temporal_result.question_id = qid
            self.temporal_results.append(temporal_result)
        else:
            self.temporal_results.append(None)

    def compute(self) -> AggregatedMetrics:
        """Compute all aggregated metrics."""
        # Hits@1
        hits_batch = compute_hits_at_1_batch(self.hits_results)

        # Leakage
        scored_leakage = [r for r, scored in zip(self.leakage_results, self.leakage_scored) if scored]
        leakage = compute_leakage_rate(scored_leakage)

        # Temporal error
        temporal = compute_temporal_error_rate(
            [result for result in self.temporal_results if result is not None]
        )

        # Latency & tokens
        n = len(self.latencies)
        avg_latency = sum(self.latencies) / n if n > 0 else 0
        avg_tokens = sum(self.tokens) / n if n > 0 else 0
        total_latency = sum(self.latencies)
        total_tokens = sum(self.tokens)

        return AggregatedMetrics(
            branch=self.branch,
            tightness=self.tightness,
            permission_enabled=self.permission_enabled,
            asof_enabled=self.asof_enabled,
            hits_at_1=hits_batch.get('hits_at_1', 0.0),
            hits_at_1_total=hits_batch.get('total', 0),
            hits_at_1_correct=hits_batch.get('correct', 0),
            overall_leakage_rate=leakage.get('overall_leakage_rate') if scored_leakage else None,
            final_answer_leakage_rate=leakage.get('final_answer_leakage_rate') if scored_leakage else None,
            reasoning_leakage_rate=leakage.get('reasoning_leakage_rate') if scored_leakage else None,
            both_leakage_rate=leakage.get('both_leakage_rate') if scored_leakage else None,
            leakage_scored_questions=len(scored_leakage),
            temporal_error_rate=temporal.get('temporal_error_rate') if temporal.get('with_asof') else None,
            temporal_correct_rate=temporal.get('correct_rate') if temporal.get('with_asof') else None,
            temporal_other_error_rate=temporal.get('other_error_rate') if temporal.get('with_asof') else None,
            temporal_with_asof=temporal.get('with_asof', 0),
            avg_latency_ms=avg_latency,
            avg_tokens=avg_tokens,
            total_latency_ms=total_latency,
            total_tokens=total_tokens,
            reasoning_available=any(bool(text.strip()) for text in self.reasonings),
            n_questions=n
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        metrics = self.compute()
        return asdict(metrics)

    def save(self, output_path: Path):
        """Save metrics to JSON file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


def compute_delta_metrics(results: Dict[str, AggregatedMetrics]) -> Dict[str, Any]:
    """
    Compute ΔP, ΔT, ΔPT from the 2x2 experiment results.

    Expected keys in results:
    - 'A0': permission=OFF, asof=OFF
    - 'permission_only': permission=ON, asof=OFF (averaged over 3 tightness)
    - 'asof_only': permission=OFF, asof=ON
    - 'A2': permission=ON, asof=ON (averaged over 3 tightness)

    Returns point estimates only. Use scripts/summarize_experiment.py for the
    paired question-level bootstrap CIs required to assess additivity.
    """
    a0 = results.get('A0')
    perm_only = results.get('permission_only')
    asof_only = results.get('asof_only')
    a2 = results.get('A2')

    if not all([a0, perm_only, asof_only, a2]):
        return {'error': 'Missing required experiment cells'}

    # Accuracy drops (Hits@1 difference)
    baseline_acc = a0.hits_at_1
    perm_acc = perm_only.hits_at_1
    asof_acc = asof_only.hits_at_1
    both_acc = a2.hits_at_1

    delta_P = baseline_acc - perm_acc
    delta_T = baseline_acc - asof_acc
    delta_PT = baseline_acc - both_acc

    # This point estimate is descriptive, not a hypothesis test.
    additivity_diff = delta_PT - (delta_P + delta_T)

    return {
        'delta_P': round(delta_P, 4),
        'delta_T': round(delta_T, 4),
        'delta_PT': round(delta_PT, 4),
        'delta_P_plus_delta_T': round(delta_P + delta_T, 4),
        'additivity_difference': round(additivity_diff, 4),
        'additivity_interpretation': 'point estimate only; use paired bootstrap CI to assess the hypothesis',
        'baseline_accuracy': round(baseline_acc, 4),
        'permission_only_accuracy': round(perm_acc, 4),
        'asof_only_accuracy': round(asof_acc, 4),
        'both_accuracy': round(both_acc, 4)
    }
