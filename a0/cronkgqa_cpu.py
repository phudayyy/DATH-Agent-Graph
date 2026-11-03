"""CPU inference adapter for the released CRONKGQA EmbedKGQA checkpoint.

The released QA checkpoint contains the DistilBERT encoder and trained QA
layers. The corresponding tokenizer vocabulary is still required separately.
This module intentionally keeps filtering out of the inference path: A0 reads
the question, linked entity/time annotations, and the full unfiltered model
candidate space.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class A0Prediction:
    answer: str
    tokens: int
    score: float


def _make_model(torch: Any, DistilBertConfig: Any, DistilBertModel: Any,
                state: Mapping[str, Any]):
    from torch import nn

    entity_weight = state["tkbc_model.embeddings.0.weight"]
    relation_weight = state["tkbc_model.embeddings.1.weight"]
    time_weight = state["tkbc_model.embeddings.2.weight"]
    dim = int(entity_weight.shape[1])

    class CronKGQA(nn.Module):
        def __init__(self):
            super().__init__()
            self.tkbc_model = nn.Module()
            self.tkbc_model.embeddings = nn.ModuleList([
                nn.Embedding(entity_weight.shape[0], dim),
                nn.Embedding(relation_weight.shape[0], dim),
                nn.Embedding(time_weight.shape[0], dim),
            ])
            self.tkbc_model.rank = dim // 2

            config = DistilBertConfig(
                vocab_size=30522,
                max_position_embeddings=512,
                n_layers=6,
                n_heads=12,
                dim=768,
                hidden_dim=3072,
                dropout=0.1,
                attention_dropout=0.1,
            )
            self.roberta_model = DistilBertModel(config)
            self.entity_time_embedding = nn.Embedding(
                entity_weight.shape[0] + time_weight.shape[0], dim
            )
            self.answer_type_embedding = nn.Embedding(
                2, entity_weight.shape[0] + time_weight.shape[0]
            )
            self.combine_all_entities_func_forReal = nn.Linear(dim, self.tkbc_model.rank)
            self.combine_all_entities_func_forCmplx = nn.Linear(dim, self.tkbc_model.rank)
            self.linear = nn.Linear(768, dim)
            self.linear1 = nn.Linear(dim, dim)
            self.linear2 = nn.Linear(dim, dim)
            self.dropout = nn.Dropout(0.3)
            self.bn1 = nn.BatchNorm1d(dim)
            self.bn2 = nn.BatchNorm1d(dim)

        def forward(self, input_ids, attention_mask, head_ids, tail_ids, time_ids):
            head_embedding = self.entity_time_embedding(head_ids)
            tail_embedding = self.entity_time_embedding(tail_ids)
            time_embedding = self.entity_time_embedding(time_ids)
            q_embedding = self.roberta_model(
                input_ids=input_ids, attention_mask=attention_mask
            ).last_hidden_state[:, 0, :]
            relation_embedding = self.linear(q_embedding)
            relation_time = self.dropout(self.bn1(self.linear1(relation_embedding)))
            relation_entity = self.dropout(self.bn2(self.linear2(relation_embedding)))

            rank = self.tkbc_model.rank
            lhs = head_embedding[:, :rank], head_embedding[:, rank:]
            rhs = tail_embedding[:, :rank], tail_embedding[:, rank:]
            rel = relation_time[:, :rank], relation_time[:, rank:]
            times = self.tkbc_model.embeddings[2].weight
            time_parts = times[:, :rank], times[:, rank:]
            score_times = (
                (lhs[0] * rel[0] * rhs[0] - lhs[1] * rel[1] * rhs[0]
                 - lhs[1] * rel[0] * rhs[1] + lhs[0] * rel[1] * rhs[1]) @ time_parts[0].t()
                + (lhs[1] * rel[0] * rhs[0] - lhs[0] * rel[1] * rhs[0]
                   + lhs[0] * rel[0] * rhs[1] - lhs[1] * rel[1] * rhs[1]) @ time_parts[1].t()
            )

            entity_rel = relation_entity[:, :rank], relation_entity[:, rank:]
            query_time = time_embedding[:, :rank], time_embedding[:, rank:]
            all_entities = self.tkbc_model.embeddings[0].weight
            entity_parts = all_entities[:, :rank], all_entities[:, rank:]
            combined_rel = (
                entity_rel[0] * query_time[0] - entity_rel[1] * query_time[1],
                entity_rel[1] * query_time[0] + entity_rel[0] * query_time[1],
            )
            score_entities = (
                (lhs[0] * combined_rel[0] - lhs[1] * combined_rel[1]) @ entity_parts[0].t()
                + (lhs[1] * combined_rel[0] + lhs[0] * combined_rel[1]) @ entity_parts[1].t()
            )
            return torch.cat((score_entities, score_times), dim=1)

    model = CronKGQA()
    model.load_state_dict(state, strict=True)
    model.eval()
    return model


class CronKGQACPU:
    """Load and run the official CRONKGQA checkpoint on CPU."""

    def __init__(self, checkpoint: Path, tokenizer_path: Path, data_dir: Path):
        try:
            import torch
            from transformers import DistilBertConfig, DistilBertModel, DistilBertTokenizerFast
        except ImportError as exc:
            raise RuntimeError(
                "A0 requires CPU PyTorch and transformers. Install them with "
                "`pip install torch transformers`."
            ) from exc

        for path, label in ((checkpoint, "CRONKGQA checkpoint"),
                            (tokenizer_path, "DistilBERT tokenizer"),
                            (data_dir, "processed CRONQUESTIONS mapping directory")):
            if not path.exists():
                raise FileNotFoundError(f"Missing {label}: {path}")

        self.torch = torch
        self.tokenizer = DistilBertTokenizerFast.from_pretrained(
            str(tokenizer_path), local_files_only=True
        )
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        self.model = _make_model(torch, DistilBertConfig, DistilBertModel, state)
        self.model.to(torch.device("cpu"))

        import pickle
        with (data_dir / "ent_id").open("rb") as f:
            self.ent_to_id = pickle.load(f)
        with (data_dir / "ts_id").open("rb") as f:
            self.time_to_id = pickle.load(f)
        self.id_to_time = {idx: key[0] for key, idx in self.time_to_id.items()}
        self.id_to_entity = {idx: entity for entity, idx in self.ent_to_id.items()}
        self.num_entities = int(state["tkbc_model.embeddings.0.weight"].shape[0])

    def _feature_ids(self, question: Mapping[str, Any]) -> Tuple[int, int, int]:
        text = str(question["question"])
        located = []
        for entity in question.get("entities", []):
            entity = str(entity)
            if entity not in self.ent_to_id:
                raise ValueError(f"Question references entity missing from model map: {entity}")
            located.append((text.find(entity), self.ent_to_id[entity]))
        located.sort()
        if not located:
            raise ValueError(f"Question {question.get('uniq_id')} has no linked entities")
        head_id = int(located[0][1])
        tail_id = int(located[1][1]) if len(located) > 1 else head_id

        times = sorted(int(year) for year in question.get("times", []))
        time_index = self.time_to_id.get((times[0], 0, 0), 0) if times else 0
        return head_id, tail_id, self.num_entities + int(time_index)

    def predict(self, questions: Sequence[Mapping[str, Any]]) -> List[A0Prediction]:
        torch = self.torch
        if not questions:
            return []

        texts = [str(q["question"]) for q in questions]
        encoded = self.tokenizer(
            texts, padding=True, truncation=True, return_tensors="pt"
        )
        features = [self._feature_ids(q) for q in questions]
        heads, tails, times = zip(*features)
        with torch.inference_mode():
            scores = self.model(
                encoded["input_ids"], encoded["attention_mask"],
                torch.tensor(heads, dtype=torch.long),
                torch.tensor(tails, dtype=torch.long),
                torch.tensor(times, dtype=torch.long),
            )
            values, indices = torch.max(scores, dim=1)

        predictions = []
        for row, (value, index) in enumerate(zip(values.tolist(), indices.tolist())):
            if index < self.num_entities:
                answer = self.id_to_entity.get(index, "")
            else:
                answer = str(self.id_to_time.get(index - self.num_entities, ""))
            predictions.append(A0Prediction(
                answer=answer,
                tokens=int(encoded["attention_mask"][row].sum().item()),
                score=float(value),
            ))
        return predictions
