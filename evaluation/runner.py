"""
Evaluation runner.
Orchestrates evaluation of A0, A1, A2 branches using the common evaluator.
"""

import json
import time
import pickle
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass

from .metrics import (
    MetricsAggregator, EvaluationOutput, AggregatedMetrics,
    compute_delta_metrics
)
from .hits_at_1 import normalize_answer
from .kg_index import KGEventIndex


@dataclass
class Question:
    """Question data structure."""
    uniq_id: int
    question: str
    answers: set
    entities: set
    relations: set
    times: set
    question_type: str
    annotation: dict


class EvaluationRunner:
    """
    Runs evaluation for a branch (A0, A1, A2) on the 1500 questions.

    Branch function signature:
        branch_fn(question: Question, allowed_event_ids: set, as_of_year: int) -> EvaluationOutput
    """

    def __init__(
        self,
        branch_name: str,
        branch_fn: Callable,
        question_ids: List[int],
        question_path: Path,
        permission_path: Optional[Path] = None,
        tightness: Optional[str] = None,
        enable_permission: bool = False,
        enable_asof: bool = False,
        kg_path: Optional[Path] = None,
        measure_latency: bool = True,
    ):
        self.branch_name = branch_name
        self.branch_fn = branch_fn
        self.question_ids = set(question_ids)
        self.question_path = question_path
        self.permission_path = permission_path
        self.tightness = tightness
        self.enable_permission = enable_permission
        self.enable_asof = enable_asof
        self.measure_latency = measure_latency
        self.kg_path = kg_path or (question_path.parent.parent / 'data' / 'wikidata_big' / 'kg' / 'full.txt')
        self.event_index = KGEventIndex.from_file(self.kg_path) if self.kg_path.exists() else None
        # Branch closures that need KG provenance (A1) receive the same index
        # used by the evaluator for scoring.
        try:
            self.branch_fn.event_index = self.event_index
        except (AttributeError, TypeError):
            pass

        # Load questions
        self.questions = self._load_questions()

        # Load permission labels if needed
        self.permissions = {}
        if permission_path:
            self.permissions = self._load_permissions()

    def _load_questions(self) -> Dict[int, Question]:
        """Load and index questions by uniq_id."""
        if self.question_path.suffix.lower() == '.json':
            with open(self.question_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            all_questions = payload.get('questions', payload) if isinstance(payload, dict) else payload
        else:
            with open(self.question_path, 'rb') as f:
                all_questions = pickle.load(f)

        questions = {}
        for q in all_questions:
            qid = q.get('uniq_id')
            if qid in self.question_ids:
                questions[qid] = Question(
                    uniq_id=qid,
                    question=q.get('question', ''),
                    answers=set(q.get('answers', [])),
                    entities=set(q.get('entities', [])),
                    relations=set(q.get('relations', [])),
                    times=set(q.get('times', [])),
                    question_type=q.get('type', ''),
                    annotation=q.get('annotation', {})
                )
        return questions

    def _load_permissions(self) -> Dict[int, List[int]]:
        """Load permission labels for each question.

        Compact format: permissions_*.jsonl stores only
        {question_id, role_id} per line; the allowed event list lives in
        roles_*.json under that role_id. Resolve role_id -> events via the
        sibling roles file (permissions_X.jsonl -> roles_X.json).
        Falls back to inline allowed_event_ids when present.
        """
        perms = {}
        if not self.permission_path or not self.permission_path.exists():
            return perms

        # Try to load sibling roles file for role_id resolution
        roles_map: Dict[str, List[int]] = {}
        role_file = self.permission_path.parent / self.permission_path.name.replace('permissions_', 'roles_').replace('.jsonl', '.json')
        if role_file.exists():
            with open(role_file, 'r', encoding='utf-8') as f:
                roles_data = json.load(f)
            roles_map = {rid: r.get('allowed_event_ids', []) for rid, r in roles_data.items()}

        with open(self.permission_path, 'r', encoding='utf-8') as f:
            for line in f:
                data = json.loads(line.strip())
                qid = int(data['question_id'])
                if data.get('allowed_event_ids'):
                    perms[qid] = data['allowed_event_ids']
                elif data.get('role_id') in roles_map:
                    perms[qid] = roles_map[data['role_id']]
                else:
                    raise ValueError(f"No role permissions resolved for question {qid}")
        missing = self.question_ids - set(perms)
        if missing:
            raise ValueError(f"Permission file misses {len(missing)} selected question IDs")
        return perms

    def _get_as_of_year(self, question: Question) -> int:
        """Extract as-of year from question times."""
        if question.times:
            return max(question.times)
        return 0

    def _gold_relation_query(self, question: Question):
        """Return (head, relation) only for query forms with unambiguous KG semantics."""
        if question.question_type != 'simple_entity' or not question.times or len(question.relations) != 1:
            return None
        head = question.annotation.get('head') if isinstance(question.annotation, dict) else None
        relation = next(iter(question.relations))
        return (str(head), str(relation)) if head else None

    def _get_gold_answers_at_asof(self, question: Question):
        query = self._gold_relation_query(question)
        if not self.event_index or not query:
            return None
        # CRONQUESTIONS supplies the gold answer for the explicit year in the
        # natural-language query. Use that annotation as the as-of truth; do
        # not reconstruct labels from KG intervals, which can be lossy for
        # one-year facts and noisy source intervals.
        return set(question.answers)

    def _get_gold_answers_latest(self, question: Question) -> set:
        """Derive latest-state gold when the question has a supported query form."""
        query = self._gold_relation_query(question)
        if not self.event_index or not query:
            return None
        return self.event_index.latest_answers(*query)

    def run(self, output_dir: Path) -> AggregatedMetrics:
        """Run evaluation on all questions."""
        output_dir.mkdir(parents=True, exist_ok=True)

        aggregator = MetricsAggregator(
            branch=self.branch_name,
            tightness=self.tightness,
            permission_enabled=self.enable_permission,
            asof_enabled=self.enable_asof,
            event_index=self.event_index,
        )

        missing_questions = self.question_ids - set(self.questions)
        if missing_questions:
            raise ValueError(f"Evaluation set misses {len(missing_questions)} selected question IDs")
        if self.enable_permission and not self.permissions:
            raise ValueError("Permission filtering is enabled but no usable permission labels were loaded")

        # Process each question
        for i, qid in enumerate(sorted(self.question_ids)):
            question = self.questions[qid]

            # Get permission context
            allowed_events = set()
            scoped_events = set(self.permissions.get(qid, [])) if self.permissions else None
            if self.enable_permission:
                allowed_events = scoped_events or set()

            # Get as-of year
            as_of_year = self._get_as_of_year(question) if self.enable_asof else 0

            # Get gold answers
            gold_answers = question.answers
            gold_at_asof = self._get_gold_answers_at_asof(question)
            gold_latest = self._get_gold_answers_latest(question)

            # Run branch
            start_time = time.perf_counter()
            try:
                output = self.branch_fn(
                    question=question,
                    allowed_event_ids=allowed_events,
                    as_of_year=as_of_year
                )
            except Exception as e:
                print(f"Error on question {qid}: {e}")
                output = EvaluationOutput(
                    question_id=qid,
                    answer="",
                    reasoning=f"ERROR: {e}",
                    latency_ms=0,
                    tokens=0
                )

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            if self.measure_latency:
                output.latency_ms = elapsed_ms
            output.question_id = qid
            # Keep the scope for leakage scoring even when the branch itself
            # runs permission-OFF (A0); the prediction is still unfiltered.
            output.allowed_event_ids = (
                sorted(scoped_events)
                if scoped_events is not None and self.event_index is not None
                else None
            )
            output.as_of_year = as_of_year if as_of_year > 0 else None
            output.gold_answers = list(gold_answers)
            output.gold_answers_at_asof = list(gold_at_asof) if gold_at_asof is not None else None
            output.gold_answers_latest = list(gold_latest) if gold_latest is not None else None
            output.question_entities = sorted(question.entities)
            output.relations = sorted(question.relations)

            # Add to aggregator
            aggregator.add_result(output)

            # Progress
            if (i + 1) % 100 == 0:
                print(f"  Processed {i + 1}/{len(self.question_ids)} questions...")

        # Save detailed results
        self._save_detailed_results(aggregator, output_dir)

        # Compute and save aggregated metrics
        metrics = aggregator.compute()
        aggregator.save(output_dir / f'metrics_{self.branch_name}.json')

        return metrics

    def _save_detailed_results(self, aggregator: MetricsAggregator, output_dir: Path):
        """Save per-question detailed results."""
        detailed = []
        for i, qid in enumerate(aggregator.question_ids):
            detailed.append({
                'question_id': qid,
                'hits_at_1': aggregator.hits_results[i].hits_at_1,
                'hits_match_type': aggregator.hits_results[i].match_type,
                'leakage': aggregator.leakage_results[i].has_leakage,
                'leakage_scored': aggregator.leakage_scored[i],
                'leakage_location': aggregator.leakage_results[i].leakage_location.value,
                'leaked_events': aggregator.leakage_results[i].leaked_events,
                'temporal_error': aggregator.temporal_results[i].has_temporal_error if i < len(aggregator.temporal_results) else False,
                'temporal_error_type': aggregator.temporal_results[i].error_type if i < len(aggregator.temporal_results) else 'n/a',
                'latency_ms': aggregator.latencies[i],
                'tokens': aggregator.tokens[i]
            })

        with open(output_dir / f'detailed_{self.branch_name}.json', 'w', encoding='utf-8') as f:
            json.dump(detailed, f, ensure_ascii=False, indent=2)

        # Raw branch outputs satisfy the shared five-field Output Contract.
        with open(output_dir / f'outputs_{self.branch_name}.jsonl', 'w', encoding='utf-8', newline='\n') as f:
            for qid, answer, reasoning, latency, tokens in zip(
                    aggregator.question_ids, aggregator.answers, aggregator.reasonings,
                    aggregator.latencies, aggregator.tokens):
                f.write(json.dumps({"question_id": qid, "answer": answer,
                                    "reasoning": reasoning, "latency_ms": latency,
                                    "tokens": tokens}, ensure_ascii=False) + '\n')


# ============================================================
# BRANCH IMPLEMENTATIONS
# ============================================================

def create_a0_branch(model_fn: Callable[[str], tuple]) -> Callable:
    """
    Create A0 branch function (no filtering).

    Args:
        model_fn: Function that takes question text and returns (answer, reasoning, tokens)

    Returns:
        Branch function compatible with EvaluationRunner
    """
    def a0_branch(question: Question, allowed_event_ids: set, as_of_year: int) -> EvaluationOutput:
        answer, reasoning, tokens = model_fn(question.question)
        return EvaluationOutput(
            question_id=question.uniq_id,
            answer=answer,
            reasoning=reasoning,
            latency_ms=0,  # filled by runner
            tokens=tokens
        )
    return a0_branch


def create_a1_branch(model_fn: Callable[[str], tuple], filter_fn: Callable) -> Callable:
    """
    Create A1 branch function (post-generation filtering).

    Args:
        model_fn: Function that takes question text and returns (answer, reasoning, tokens)
        filter_fn: Function that takes (answer, reasoning, allowed_event_ids)
                   and returns (filtered_answer, filtered_reasoning)

    Returns:
        Branch function compatible with EvaluationRunner
    """
    def a1_branch(question: Question, allowed_event_ids: set, as_of_year: int) -> EvaluationOutput:
        # Generate answer
        answer, reasoning, tokens = model_fn(question.question)

        # Post-generation filter
        filtered_answer, filtered_reasoning = filter_fn(
            answer, reasoning, allowed_event_ids,
            question_entities=question.entities,
            relations=question.relations,
            event_index=getattr(a1_branch, 'event_index', None),
        )

        return EvaluationOutput(
            question_id=question.uniq_id,
            answer=filtered_answer,
            reasoning=filtered_reasoning,
            latency_ms=0,
            tokens=tokens
        )
    return a1_branch


def create_a2_branch(model_fn: Callable[[str, set, int], tuple]) -> Callable:
    """
    Create A2 branch function (permission + as-of pushed into query).

    Args:
        model_fn: Function that takes (question, allowed_event_ids, as_of_year)
                  and returns (answer, reasoning, tokens)

    Returns:
        Branch function compatible with EvaluationRunner
    """
    def a2_branch(question: Question, allowed_event_ids: set, as_of_year: int) -> EvaluationOutput:
        answer, reasoning, tokens = model_fn(question.question, allowed_event_ids, as_of_year)
        return EvaluationOutput(
            question_id=question.uniq_id,
            answer=answer,
            reasoning=reasoning,
            latency_ms=0,
            tokens=tokens
        )
    return a2_branch


def post_generation_filter(answer: str, reasoning: str, allowed_event_ids: set,
                           *, question_entities=None, relations=None,
                           event_index=None) -> tuple:
    """
    A1 post-generation filter.
    Refuses the answer or removes reasoning when query-grounded evidence
    includes an event outside the caller's allowed event set.
    """
    import re

    from .leakage import extract_event_ids, extract_explicit_event_refs
    if not answer and not reasoning:
        return answer, reasoning
    if event_index is None:
        # Fail closed: without KG provenance, entity QIDs cannot be compared
        # to event permission IDs safely.
        return ("I cannot answer because event-level authorization could not be verified.", "")

    anchors = {str(e).upper() for e in (question_entities or set())}
    rels = {str(r).upper() for r in (relations or set())}
    allowed = {str(eid) for eid in allowed_event_ids}

    def has_unauthorized_evidence(text):
        mentioned = {qid.upper() for qid in extract_event_ids(text)}
        evidence = event_index.evidence_for_mentions(mentioned, anchors, rels)
        evidence.update(extract_explicit_event_refs(text))
        if not mentioned:
            evidence.update(event_index.evidence_for_year_mentions(text, anchors, rels))
        if mentioned and not evidence:
            return True  # fail closed when the QID cannot be grounded to KG evidence
        if text and not mentioned and not evidence and re.search(r"[A-Za-z]", text):
            return True  # ungrounded prose cannot be authorized safely
        return any(str(eid) not in allowed for eid in evidence)

    answer_leaks = has_unauthorized_evidence(answer)
    reasoning_leaks = has_unauthorized_evidence(reasoning)
    return (
        "I cannot answer because the available evidence is outside your permissions." if answer_leaks else answer,
        "" if reasoning_leaks else reasoning,
    )


def dummy_model_fn(question: str) -> tuple:
    """Dummy model function for testing - returns empty answer."""
    return "", "No model implemented", 0


def dummy_a2_model_fn(question: str, allowed_event_ids: set, as_of_year: int) -> tuple:
    """Dummy A2 model function for testing."""
    return "", "No A2 model implemented", 0
