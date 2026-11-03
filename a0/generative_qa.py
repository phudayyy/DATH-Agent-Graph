"""Shared local generative KGQA model used unchanged by A0 and A1."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
DEFAULT_RETRIEVAL_BUDGET = 64


class GenerativeKGQA:
    """CPU-only causal LM plus fixed-budget, permission-blind KG retrieval."""

    def __init__(self, event_index, model_id: str = DEFAULT_MODEL,
                 max_new_tokens: int = 128, threads: int = 4,
                 revision: str = DEFAULT_MODEL_REVISION):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Generative A0/A1 requires CPU PyTorch and transformers; "
                "install them in the active Python environment."
            ) from exc
        if threads < 1 or max_new_tokens < 1:
            raise ValueError("threads and max_new_tokens must be positive")

        self.torch = torch
        self.event_index = event_index
        self.model_id = model_id
        self.requested_revision = revision
        self.max_new_tokens = max_new_tokens
        torch.set_num_threads(threads)
        print(f"Loading tokenizer: {model_id}", flush=True)
        started = time.perf_counter()
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        print(f"Tokenizer ready ({time.perf_counter() - started:.1f}s). Loading model weights on CPU...",
              flush=True)
        started = time.perf_counter()
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, revision=revision, torch_dtype=torch.float32, low_cpu_mem_usage=True
        ).to("cpu").eval()
        self.resolved_revision = getattr(self.model.config, "_commit_hash", None)
        print(f"Model ready ({time.perf_counter() - started:.1f}s). Starting inference.", flush=True)

    @staticmethod
    def _format_fact(event):
        event_id, head, relation, tail, start, end = event
        end_text = "open" if end == 0 else str(end)
        return (f"event_id={event_id}: ({head}, {relation}, {tail}), "
                f"valid_years=[{start},{end_text})")

    def _prompt(self, question: Mapping[str, Any], facts: Sequence[tuple],
                retrieval_budget: int) -> str:
        fact_text = "\n".join(self._format_fact(event) for event in facts)
        messages = [
            {"role": "system", "content": (
                "Answer the question using only the supplied Wikidata facts. "
                "Return one valid JSON object with exactly two string fields: "
                "answer and reasoning. For entity answers, answer with one Q-ID; "
                "for year answers, answer with the year. The reasoning must be "
                "a brief evidence summary and include the event_id values used. "
                "Do not invent event IDs or facts. If the evidence is insufficient, "
                "return an empty answer and explain that briefly. Do not reveal hidden chain-of-thought."
            )},
            {"role": "user", "content": (
                f"Question: {question.get('question', '')}\n"
                f"Linked entities: {', '.join(map(str, question.get('entities', [])))}\n"
                f"Relations: {', '.join(map(str, question.get('relations', [])))}\n"
                f"Question years: {', '.join(map(str, question.get('times', [])))}\n"
                f"KG facts (retrieval budget {retrieval_budget}):\n{fact_text}"
            )},
        ]
        return self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

    @staticmethod
    def _parse_response(text: str):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                value = json.loads(match.group(0))
                answer = value.get("answer", "")
                reasoning = value.get("reasoning", "")
                if isinstance(answer, (str, int, float)) and isinstance(reasoning, str):
                    return str(answer).strip(), reasoning.strip()
            except (json.JSONDecodeError, AttributeError):
                pass
        qid = re.search(r"\bQ\d+\b", text, re.IGNORECASE)
        return (qid.group(0).upper() if qid else ""), text.strip()

    def predict(self, question: Mapping[str, Any], retrieval_budget: int = DEFAULT_RETRIEVAL_BUDGET):
        facts = self.event_index.retrieve_question(question, limit=retrieval_budget)
        prompt = self._prompt(question, facts, retrieval_budget)
        encoded = self.tokenizer(prompt, return_tensors="pt", truncation=True,
                                 max_length=4096)
        with self.torch.inference_mode():
            generated = self.model.generate(
                **encoded,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        input_length = encoded["input_ids"].shape[1]
        completion = self.tokenizer.decode(generated[0][input_length:], skip_special_tokens=True)
        answer, reasoning = self._parse_response(completion)
        return {
            "answer": answer,
            "reasoning": reasoning,
            # generate() returns the full sequence: prompt plus completion.
            "tokens": int(generated.shape[1]),
            "retrieved_event_ids": [event[0] for event in facts],
        }
