"""A0: shared local generative baseline with permission/as-of filters off."""

from pathlib import Path
from typing import Callable, Optional

from evaluation.metrics import EvaluationOutput
from evaluation.runner import create_a0_branch

from .generative_qa import DEFAULT_MODEL, DEFAULT_MODEL_REVISION, DEFAULT_RETRIEVAL_BUDGET, GenerativeKGQA
from evaluation.kg_index import KGEventIndex

REPO_DIR = Path(__file__).resolve().parent.parent


def get_a0_branch(
    model_fn: Optional[Callable] = None,
    *,
    engine: Optional[GenerativeKGQA] = None,
    model_id: str = DEFAULT_MODEL,
    revision: str = DEFAULT_MODEL_REVISION,
    retrieval_budget: int = DEFAULT_RETRIEVAL_BUDGET,
    kg_path: Path = REPO_DIR / "data" / "wikidata_big" / "kg" / "full.txt",
    threads: int = 4,
):
    """Create A0; injection remains available for deterministic unit fixtures."""
    if model_fn is not None:
        return create_a0_branch(model_fn)
    index = KGEventIndex.from_file(kg_path) if engine is None else engine.event_index
    engine = engine or GenerativeKGQA(index, model_id=model_id, threads=threads, revision=revision)

    def a0_branch(question, allowed_event_ids: set, as_of_year: int) -> EvaluationOutput:
        row = {
            "uniq_id": question.uniq_id,
            "question": question.question,
            "entities": sorted(question.entities),
            "relations": sorted(question.relations),
            "times": sorted(question.times),
        }
        prediction = engine.predict(row, retrieval_budget)
        return EvaluationOutput(
            question_id=question.uniq_id,
            answer=prediction["answer"],
            reasoning=prediction["reasoning"],
            latency_ms=0,
            tokens=prediction["tokens"],
        )

    return a0_branch
