"""A2 bản đối chiếu: vị từ quyền và vị từ as-of nằm ngay trong truy vấn đồ thị.

Phiếu đề tài (mục 4) bắt ba nhánh dùng cùng một mô hình sinh và cùng một ngân sách truy hồi. A0
và A1 lấy sự kiện bằng `KGEventIndex.retrieve_question`, không xét quyền, không xét thời gian. Ở
đây đúng thứ hạng đó chạy bằng SQL trên đồ thị Postgres, nhưng trên khung nhìn `visible`: phần đồ
thị người hỏi được thấy tại mốc hỏi. Hai vị từ nằm trong khung nhìn đó, nên cả bước lấy ứng viên
lẫn bước lan một bước đều chỉ chạm tới cạnh được thấy:

- quyền: cạnh phải có hàng trong `edge_acl` cho principal của người hỏi; không có thì không thấy;
- as-of: `valid_from <= as_of < valid_to`, và bản ghi chưa hết hạn tại `as_of`.

Kết quả bằng đúng `retrieve_question` chạy trên phần đồ thị đó — `scripts/check_a2_retrieval.py`
so từng câu của tập đóng băng. Sự kiện ngoài quyền không ra khỏi database, nên mô hình sinh không
có cách nào nhắc tới chúng.

Nhãn quyền của khung đo cấp theo sự kiện, với event id là số dòng (từ 0) của `full.txt`. Vì vậy
mỗi dòng nạp thành đúng một cạnh có `edges.id` = event id, và KHÔNG gộp khoảng như bộ nạp của
`cronquestions`: hai dòng cùng (chủ thể, quan hệ, đối tượng) có thể khác quyền. Lược đồ không chứa
được dòng có khoảng rỗng (năm bắt đầu sau năm kết thúc) hay dòng tự trỏ, nên các dòng đó không bao
giờ được truy hồi; bộ nạp đếm từng loại.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agent_graph.cronquestions import MAX_YEAR, MIN_YEAR, read_labels, year_start
from agent_graph.db import SCHEMA_SQL
from brain.clock import utc_now

Event = tuple[int, str, str, str, int, int]
"""(event_id, head, pid, tail, năm bắt đầu, năm kết thúc) — cùng dạng `Event` của KGEventIndex."""

Conn = psycopg.Connection[dict[str, Any]]


@dataclass(slots=True)
class EventLoadReport:
    """Đếm mọi dòng bị bỏ — một bộ nạp lặng lẽ bỏ dữ liệu là một bộ nạp nói dối."""

    lines: int = 0
    malformed: int = 0  # không đủ năm cột, hoặc năm không phải số
    empty_interval: int = 0  # năm bắt đầu sau năm kết thúc: không đúng ở năm nào
    out_of_range: int = 0  # năm ngoài 1–9998
    self_loop: int = 0  # chủ thể trùng đối tượng: lược đồ cấm
    edges: int = 0
    entities: int = 0
    grants: int = 0
    grants_to_unloaded_events: int = 0  # nhãn cấp cho một dòng không nạp được


@dataclass(frozen=True, slots=True)
class Scope:
    """Ai hỏi và hỏi tại mốc nào. Trường nào là None thì vị từ đó tắt — dùng cho các ô ablation."""

    principal: str | None
    as_of: datetime | None


def principal_for_role(role_id: str) -> str:
    return f"group:{role_id}"


def question_as_of(question: Mapping[str, Any]) -> datetime | None:
    """Mốc hỏi như khung đo (`EvaluationRunner._get_as_of_year`): năm lớn nhất câu hỏi nêu, tại
    1/1 UTC. Câu không nêu năm thì không có mốc, và vị từ as-of không áp cho câu đó."""
    years = [int(year) for year in question.get("times", [])]
    return year_start(max(years)) if years else None


def connect_sync(dsn: str) -> Conn:
    # prepare_threshold=None: không chuẩn bị sẵn câu lệnh, để mỗi lần hỏi Postgres lập kế hoạch
    # theo đúng tham số (vị từ nào đang tắt) thay vì dùng một kế hoạch chung.
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None)
    # Mọi mốc của CRONQUESTIONS là nửa đêm 1/1 UTC; xem `agent_graph.db.connect`.
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def create_tenant(conn: Conn, name: str) -> str:
    """Dựng lược đồ trong một database TRỐNG rồi tạo tenant."""
    row = conn.execute("SELECT to_regclass('entities') IS NOT NULL AS present").fetchone()
    if row and row["present"]:
        raise RuntimeError("database đã có bảng `entities`; A2 chỉ dựng trên một Postgres trống")
    conn.execute(SCHEMA_SQL)
    row = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id::text AS id", (name,)
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT … RETURNING không trả về dòng nào")
    return str(row["id"])


def read_events(path: Path, report: EventLoadReport) -> list[Event]:
    """Đọc `full.txt`, giữ event id = số dòng (từ 0) đúng như `KGEventIndex.from_file`."""
    events: list[Event] = []
    with path.open(encoding="utf-8") as stream:
        for event_id, line in enumerate(stream):
            report.lines += 1
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 5:
                report.malformed += 1
                continue
            head, pid, tail, raw_start, raw_end = fields
            try:
                start, end = int(raw_start), int(raw_end)
            except ValueError:
                report.malformed += 1
                continue
            if start > end:
                report.empty_interval += 1
            elif start < MIN_YEAR or end > MAX_YEAR:
                report.out_of_range += 1
            elif head == tail:
                report.self_loop += 1
            else:
                events.append((event_id, head, pid, tail, start, end))
    return events


def load_events(
    conn: Conn,
    *,
    tenant_id: str,
    kg_dir: Path,
    grants: Mapping[str, Iterable[int]] | None = None,
) -> EventLoadReport:
    """Nạp mỗi dòng của `full.txt` thành một cạnh, rồi nạp quyền theo cạnh.

    `grants`: principal → các event id được thấy. None ⇒ không nạp hàng quyền nào, nên khi vị từ
    quyền bật thì không ai thấy gì (fail-closed); chỉ dùng None cho các ô tắt vị từ quyền.
    """
    report = EventLoadReport()
    events = read_events(kg_dir / "full.txt", report)
    names = read_labels(kg_dir / "wd_id2entity_text.txt")
    relation_names = read_labels(kg_dir / "wd_id2relation_text.txt")
    items = sorted({event[1] for event in events} | {event[3] for event in events})

    with conn.cursor() as cur:
        with cur.copy(
            "COPY entities (tenant_id, kind, primary_key, canonical_name) FROM STDIN"
        ) as copy:
            for key in items:
                copy.write_row((tenant_id, "item", key, names.get(key) or key))
        cur.execute(
            "SELECT id::text AS id, primary_key FROM entities WHERE tenant_id = %s AND kind = 'item'",
            (tenant_id,),
        )
        id_of = {row["primary_key"]: row["id"] for row in cur.fetchall()}
        report.entities = len(id_of)

        observed = utc_now()
        with cur.copy(
            "COPY edges (id, tenant_id, src_entity_id, dst_entity_id, relation,"
            " valid_from, valid_to, observed_at, attrs) FROM STDIN"
        ) as copy:
            for event_id, head, pid, tail, start, end in events:
                copy.write_row((
                    event_id, tenant_id, id_of[head], id_of[tail],
                    relation_names.get(pid) or pid,
                    year_start(start), year_start(end + 1), observed,
                    Jsonb({"pid": pid, "start": start, "end": end}),
                ))
        report.edges = len(events)
        # `edges.id` được ghi thẳng bằng event id, nên đẩy chuỗi tự tăng qua id lớn nhất.
        cur.execute("SELECT setval(pg_get_serial_sequence('edges', 'id'), max(id)) FROM edges")

        if grants:
            loaded = {event[0] for event in events}
            with cur.copy("COPY edge_acl (tenant_id, edge_id, principal_ref) FROM STDIN") as copy:
                for principal, event_ids in grants.items():
                    for event_id in sorted(set(event_ids)):
                        if event_id in loaded:
                            copy.write_row((tenant_id, event_id, principal))
                            report.grants += 1
                        else:
                            report.grants_to_unloaded_events += 1
        cur.execute("ANALYZE entities")
        cur.execute("ANALYZE edges")
        cur.execute("ANALYZE edge_acl")
    return report


# Thứ hạng của `KGEventIndex.retrieve_question`: (khớp quan hệ, số đầu mút là thực thể của câu
# hỏi, event id). Khớp quan hệ luôn bằng 1 vì cạnh đã được lọc theo quan hệ, nên chỉ còn hai khoá.
_RETRIEVE = """
WITH visible AS NOT MATERIALIZED (
    SELECT ed.id, ed.src_entity_id, ed.dst_entity_id
    FROM edges ed
    WHERE ed.tenant_id = %(tenant_id)s
      AND (cardinality(%(relations)s::text[]) = 0
           OR ed.attrs->>'pid' = ANY(%(relations)s::text[]))
      AND (%(principal)s::text IS NULL OR EXISTS (
            SELECT 1 FROM edge_acl g
            WHERE g.tenant_id = ed.tenant_id
              AND g.principal_ref = %(principal)s::text
              AND g.edge_id = ed.id))
      AND (%(as_of)s::timestamptz IS NULL OR (
            ed.valid_from <= %(as_of)s::timestamptz
            AND (ed.valid_to   IS NULL OR ed.valid_to   > %(as_of)s::timestamptz)
            AND (ed.expired_at IS NULL OR ed.expired_at > %(as_of)s::timestamptz)))
), anchor AS (
    SELECT id FROM entities
    WHERE tenant_id = %(tenant_id)s AND kind = 'item' AND primary_key = ANY(%(anchors)s::text[])
), cand AS (
    -- Hai nhánh JOIN thay cho `src IN … OR dst IN …`, để mỗi nhánh đi đúng một index
    -- (edges_src_idx, edges_dst_idx) thay vì quét cả bảng cạnh.
    SELECT v.id, v.src_entity_id, v.dst_entity_id FROM visible v JOIN anchor a ON a.id = v.src_entity_id
    UNION
    SELECT v.id, v.src_entity_id, v.dst_entity_id FROM visible v JOIN anchor a ON a.id = v.dst_entity_id
), bridge AS (
    SELECT dst_entity_id AS id FROM cand WHERE src_entity_id IN (SELECT id FROM anchor)
    UNION
    SELECT src_entity_id FROM cand WHERE dst_entity_id IN (SELECT id FROM anchor)
), picked AS (
    SELECT id, src_entity_id, dst_entity_id FROM cand
    UNION
    SELECT v.id, v.src_entity_id, v.dst_entity_id FROM visible v JOIN bridge b ON b.id = v.src_entity_id
    UNION
    SELECT v.id, v.src_entity_id, v.dst_entity_id FROM visible v JOIN bridge b ON b.id = v.dst_entity_id
)
SELECT p.id AS event_id, s.primary_key AS head, ed.attrs->>'pid' AS pid, d.primary_key AS tail,
       (ed.attrs->>'start')::int AS start_year, (ed.attrs->>'end')::int AS end_year
FROM picked p
JOIN edges ed ON ed.id = p.id
JOIN entities s ON s.id = p.src_entity_id
JOIN entities d ON d.id = p.dst_entity_id
ORDER BY ((p.src_entity_id IN (SELECT id FROM anchor))::int
          + (p.dst_entity_id IN (SELECT id FROM anchor))::int) DESC,
         p.id
LIMIT %(limit)s
"""


class GraphRetriever:
    """Truy hồi của A2 trên một tenant đã nạp bằng `load_events`."""

    def __init__(self, conn: Conn, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = tenant_id

    def retrieve(self, question: Mapping[str, Any], scope: Scope, limit: int = 64) -> list[Event]:
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        rows = self._conn.execute(_RETRIEVE, {
            "tenant_id": self._tenant_id,
            "anchors": sorted({str(entity) for entity in question.get("entities", [])}),
            "relations": sorted({str(relation) for relation in question.get("relations", [])}),
            "principal": scope.principal,
            "as_of": scope.as_of,
            "limit": limit,
        }).fetchall()
        return [
            (int(row["event_id"]), str(row["head"]), str(row["pid"]), str(row["tail"]),
             int(row["start_year"]), int(row["end_year"]))
            for row in rows
        ]

    def scoped(self, scope: Scope) -> ScopedRetriever:
        return ScopedRetriever(self, scope)


class ScopedRetriever:
    """Cùng chữ ký với `KGEventIndex.retrieve_question`, để `GenerativeKGQA` của A0 dùng thẳng:
    A2 chỉ thay bước truy hồi, còn mô hình, prompt và ngân sách giữ nguyên."""

    def __init__(self, retriever: GraphRetriever, scope: Scope) -> None:
        self._retriever = retriever
        self.scope = scope

    def retrieve_question(self, question: Mapping[str, Any], limit: int = 64) -> list[Event]:
        return self._retriever.retrieve(question, self.scope, limit)
