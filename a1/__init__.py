"""A1: shared generative model with post-generation event permission filter."""

from pathlib import Path
from typing import Optional

from a0.generative_qa import DEFAULT_MODEL, DEFAULT_RETRIEVAL_BUDGET, GenerativeKGQA
from evaluation.kg_index import KGEventIndex
from evaluation.metrics import EvaluationOutput
from evaluation.runner import post_generation_filter

ROOT = Path(__file__).resolve().parents[1]


def get_a1_branch(*, engine: Optional[GenerativeKGQA] = None,
                  model_id: str = DEFAULT_MODEL,
                  revision: str = "main",
                  retrieval_budget: int = DEFAULT_RETRIEVAL_BUDGET,
                  kg_path: Path = ROOT / "data" / "wikidata_big" / "kg" / "full.txt",
                  threads: int = 4):
    """Build the actual A1 branch; caller supplies allowed event IDs per query."""
    index = KGEventIndex.from_file(kg_path) if engine is None else engine.event_index
    engine = engine or GenerativeKGQA(index, model_id=model_id, threads=threads, revision=revision)

    def a1_branch(question, allowed_event_ids: set, as_of_year: int) -> EvaluationOutput:
        row = {
            "uniq_id": question.uniq_id,
            "question": question.question,
            "entities": sorted(question.entities),
            "relations": sorted(question.relations),
            "times": sorted(question.times),
        }
        prediction = engine.predict(row, retrieval_budget)
        answer, reasoning = post_generation_filter(
            prediction["answer"], prediction["reasoning"], allowed_event_ids,
            question_entities=question.entities,
            relations=question.relations,
            event_index=index,
        )
        return EvaluationOutput(
            question_id=question.uniq_id, answer=answer, reasoning=reasoning,
            latency_ms=0, tokens=prediction["tokens"],
        )

    return a1_branch
