"""Một mã, hai nơi chạy: phần đề tài hỏi đồ thị qua đúng giao diện này.

`LocalBackend` chạy trên lõi tối thiểu của kho này — ai cũng dựng lại được. Một backend thứ
hai trỏ sang finch-brain (hệ riêng, không công khai) sẽ cài CÙNG giao diện và chỉ chạy trên
máy tác giả; bộ chạy và bộ chấm không đổi dòng nào khi đổi nơi chạy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from psycopg import AsyncConnection

from agent_graph import graph
from agent_graph.cronquestions import Question
from brain.clock import require_utc


@dataclass(frozen=True, slots=True)
class Reply:
    text: str  # gói, đúng như agent đọc
    keys: frozenset[str]  # Wikidata id của mọi thực thể gói nêu tên: dòng ENTITIES và hai đầu mỗi dòng RELATIONS
    years: frozenset[int]  # năm bắt đầu của mọi dòng RELATIONS — thứ duy nhất về thời gian mà gói in ra


class Backend(Protocol):
    async def ask(self, question: Question, *, identity: str, as_of: datetime) -> Reply: ...


@dataclass(slots=True)
class LocalBackend:
    conn: AsyncConnection[dict[str, Any]]
    tenant_id: str

    async def ask(self, question: Question, *, identity: str, as_of: datetime) -> Reply:
        pack = await graph.ask(
            self.conn, tenant_id=self.tenant_id, identities=[identity],
            seed_keys=list(question.entities), as_of=as_of, question=question.text,
        )
        keys = {e["key"] for e in pack.entities}
        keys |= {r["src_key"] for r in pack.relations} | {r["dst_key"] for r in pack.relations}
        years = {require_utc(r["valid_from"]).year for r in pack.relations}
        return Reply(pack.render(), frozenset(keys), frozenset(years))


def answer_in(reply: Reply, question: Question) -> bool:
    """Đáp án có nằm trong gói không — trần trên của Hits@1 cho MỌI cách trả lời chỉ đọc gói."""
    if question.answer_type == "entity":
        return any(a in reply.keys for a in question.answers)
    return any(a.lstrip("-").isdigit() and int(a) in reply.years for a in question.answers)
