"""Context Pack — hình dạng của lời hứa "một truy vấn".

Thứ tự các bước không đổi được, và lý do nằm ở bước 2:

    1. lan từ hạt giống
    2. CẮT QUYỀN            ← phải TRƯỚC bước 3
    3. cắt cho vừa gói

Cắt quyền SAU khi cắt cho vừa nghĩa là người dùng nhận về một gói đã bị cắt cụt
**đầy thứ họ không được xem** — vừa thiếu vừa rò, cùng lúc. Đo trên hệ multi-tenant
thật: retrieval không có cổng phân quyền rò dữ liệu chéo trong 98–100% số lần thử.

`gaps` là cơ chế chống ảo giác chính của cả hệ, quan trọng hơn đồ thị. Agent lặp tool
vì nó **không biết mình thiếu gì**, nên nó dò dẫm. Khi gói nói thẳng "tôi không có X",
agent gọi MỘT lần có chủ đích thay vì sáu lần thăm dò.

Rút gọn cho CRONQUESTIONS từ bản ở mốc: đồ thị Wikidata của đề chỉ có quan hệ có thời
hạn — không văn bản, không thuộc tính — nên bỏ đoạn trích, `FACTS`, `STALE` và ngân
sách token theo tỉ lệ. Ngân sách token thay bằng trần SỐ DÒNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from psycopg import AsyncConnection

from brain.clock import require_utc
from brain.retrieve.acl import with_acl

# Trần số dòng mỗi mục, thay cho ngân sách token của bản gốc. Một thực thể trung tâm
# của Wikidata (một quốc gia, một chức vụ) nối tới hàng trăm thứ trong cùng một năm;
# không có trần thì gói của nó dài bằng cả đồ thị con.
MAX_ENTITIES: Final = 50
MAX_RELATIONS: Final = 100


# ⛔ Gấp xuống dòng cho MỌI mục — và CHỈ xuống dòng.
#
# Gói định dạng theo DÒNG: mọi thứ định danh một mục nằm ở ĐẦU dòng. Một giá trị có
# `\n` vì thế tách ra và mọi dòng sau dòng đầu thành khẳng định vô chủ.
def _one_line(value: str) -> str:
    return value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


@dataclass(slots=True)
class Gap:
    """Thứ brain KHÔNG biết, kèm gợi ý lấy ở đâu."""

    about: str
    why: str
    suggest_tool: str | None = None


def _entity_line(e: dict[str, Any]) -> str:
    return (
        f"- {e['kind']}:{_one_line(str(e['name']))} "
        f"id={e['id'][:8]} score={e['score']:.2f}"
    )


def _relation_line(r: dict[str, Any]) -> str:
    # `require_utc` trước khi định dạng: psycopg trả `timestamptz` theo múi giờ CỦA
    # PHIÊN, và mọi mốc của CRONQUESTIONS nằm đúng nửa đêm 1/1 UTC — ở phiên phía tây
    # UTC chúng in ra 31/12 năm TRƯỚC, tức lệch nguyên một năm.
    return (
        f"- {_one_line(str(r['src_name']))} --{r['relation']}--> "
        f"{_one_line(str(r['dst_name']))} "
        f"(since {require_utc(r['valid_from']):%Y-%m-%d})"
    )


@dataclass(slots=True)
class ContextPack:
    as_of: datetime
    principal: str
    entities: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    truncated: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        """Định dạng theo DÒNG, không phải JSON lồng.

        JSON tốn ~30–40% token cho dấu ngoặc và khoá lặp lại — và trong một gói mà
        ngân sách token là ràng buộc chính, đó là 30–40% nội dung bị đánh đổi lấy
        dấu câu.
        """
        lines: list[str] = [f"# CONTEXT as_of={self.as_of.isoformat()}"]
        if self.entities:
            lines.append("## ENTITIES")
            lines += [_entity_line(e) for e in self.entities]
        if self.relations:
            lines.append("## RELATIONS")
            lines += [_relation_line(r) for r in self.relations]
        if self.gaps:
            lines.append("## GAPS — the brain does NOT have these")
            lines += [
                f"- {_one_line(g.about)}: {_one_line(g.why)}"
                + (f" → use {g.suggest_tool}" if g.suggest_tool else "")
                for g in self.gaps
            ]
        if self.truncated:
            lines.append(
                "## TRUNCATED " + ", ".join(f"{k}={v}" for k, v in self.truncated.items())
            )
        return "\n".join(lines)


# Cắt quyền. Vị từ này chạy TRƯỚC bước cắt cho vừa — xem docstring đầu file.
# Bản thân vị từ nằm ở `retrieve/acl.py` vì đường chọn hạt giống cũng dùng nó, và
# hai bản sao của một luật bảo mật sẽ trôi khỏi nhau.
_VISIBLE = with_acl("""
SELECT e.id, e.kind, e.canonical_name, e.primary_key
  FROM entities e
 WHERE e.tenant_id = %(tenant_id)s
   AND e.id = ANY(%(entity_ids)s::uuid[])
   AND {acl}
""")

_RELATIONS = """
SELECT s.canonical_name AS src_name, d.canonical_name AS dst_name,
       ed.relation, ed.valid_from,
       -- Không in ra: thứ hạng đọc hai id, bộ chấm đọc hai khoá (`agent_graph.backend`).
       ed.src_entity_id, ed.dst_entity_id,
       s.primary_key AS src_key, d.primary_key AS dst_key
  FROM edges ed
  JOIN entities s ON s.id = ed.src_entity_id
  JOIN entities d ON d.id = ed.dst_entity_id
 WHERE ed.tenant_id = %(tenant_id)s
   AND ed.src_entity_id = ANY(%(entity_ids)s::uuid[])
   AND ed.dst_entity_id = ANY(%(entity_ids)s::uuid[])
   AND ed.valid_from <= %(as_of)s
   AND (ed.valid_to   IS NULL OR ed.valid_to   > %(as_of)s)
   AND (ed.expired_at IS NULL OR ed.expired_at > %(as_of)s)
-- Thứ tự TOÀN PHẦN: thiếu nó thì quan hệ nào qua được trần do thứ tự dòng của
-- Postgres quyết — hai lần chạy giống hệt nhau cho hai gói khác nhau.
 ORDER BY ed.valid_from DESC, ed.src_entity_id, ed.dst_entity_id, ed.relation
"""


async def build_pack(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    principals: list[str],
    reached: list[Any],
    as_of: datetime,
    question: str = "",
) -> ContextPack:
    """Dựng gói từ kết quả lan.

    `principals` rỗng ⇒ gói RỖNG, không phải gói đầy. Kiểu hỏng nguy hiểm nhất của
    một cổng phân quyền không phải chặn nhầm — mà là một nhánh nào đó coi "không có
    danh tính" là "không cần lọc".
    """
    as_of = require_utc(as_of)
    pack = ContextPack(as_of=as_of, principal=principals[0] if principals else "")

    if not principals:
        pack.gaps.append(
            Gap(about="caller identity", why="no principal — returning empty, fail-closed")
        )
        return pack

    if not reached:
        pack.gaps.append(
            Gap(about=question or "this question", why="no entity in the graph matched it")
        )
        return pack

    score_by_id = {r.entity_id: r.score for r in reached}
    # ⛔ Tên `candidate_args` là một phần của thiết kế: dict này mang danh sách entity
    # CHƯA cắt quyền — đúng thứ `_RELATIONS` không bao giờ được nhận, vì `_RELATIONS`
    # không mang `{acl}` và chỉ an toàn nhờ người gọi truyền đúng danh sách.
    candidate_args = {
        "tenant_id": tenant_id,
        "entity_ids": list(score_by_id),
        "principals": principals,
        "as_of": as_of,
    }

    # ── Bước 2: CẮT QUYỀN, trước mọi thứ khác ──────────────────────────────
    cur = await conn.execute(_VISIBLE, candidate_args)
    visible = {str(r["id"]): dict(r) for r in await cur.fetchall()}

    hidden = len(score_by_id) - len(visible)
    if hidden:
        # Nói rằng CÓ thứ bị ẩn, nhưng không nói thứ đó là gì. Đếm được là hữu ích
        # cho người dùng; nội dung thì đúng là cái phải giấu.
        pack.gaps.append(Gap(about=f"{hidden} related objects", why="outside your permissions"))

    if not visible:
        return pack

    # ⛔ Khoá phụ `i` không phải để cho đẹp — thiếu nó, xếp hạng này KHÔNG TOÀN PHẦN,
    # và phần bị trần cắt đi là ngẫu nhiên.
    ranked = sorted(visible, key=lambda i: (-score_by_id[i], i))
    entities = [
        {
            "id": eid,
            "kind": visible[eid]["kind"],
            "name": visible[eid]["canonical_name"],
            "key": visible[eid]["primary_key"],
            "score": score_by_id[eid],
        }
        for eid in ranked
    ]
    rank_pos = {eid: n for n, eid in enumerate(ranked)}

    # Dựng MỚI thay vì `{**candidate_args, …}`: trải rồi ghi đè vẫn để cái sai làm
    # mặc định, và một khoá bị gõ nhầm sẽ lặng lẽ giữ danh sách chưa lọc.
    visible_args = {
        "tenant_id": tenant_id,
        "entity_ids": ranked,
        "principals": principals,
        "as_of": as_of,
    }
    cur = await conn.execute(_RELATIONS, visible_args)
    rel_rows = [dict(r) for r in await cur.fetchall()]
    # Hạng của một quan hệ = hạng của đầu mút CAO NHẤT: một cạnh chạm tới entity
    # hạng nhất đáng giữ, dù đầu kia là gì.
    rel_rows.sort(key=lambda r: min(rank_pos[str(r["src_entity_id"])], rank_pos[str(r["dst_entity_id"])]))

    # ── Bước 3: cắt cho vừa gói ────────────────────────────────────────────
    pack.entities = entities[:MAX_ENTITIES]
    pack.relations = rel_rows[:MAX_RELATIONS]
    if len(entities) > MAX_ENTITIES:
        pack.truncated["entities"] = len(entities) - MAX_ENTITIES
    if len(rel_rows) > MAX_RELATIONS:
        pack.truncated["relations"] = len(rel_rows) - MAX_RELATIONS
    return pack
