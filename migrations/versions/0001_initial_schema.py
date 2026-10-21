"""Schema khởi tạo — ARCHITECTURE.md §4.

Revision ID: 0001
Create Date: 2026-08-12

SQL viết thẳng, không dùng ORM: schema này dựa vào bảng phân mảnh, ràng buộc
`EXCLUDE`, `tstzrange` và cột sinh — đều là thứ ORM che mất hoặc chống lại.

BA SAI LỆCH SO VỚI §4 ĐÃ DUYỆT, đều tìm ra lúc dựng và đều đã kiểm bằng chạy thật:

1. `events` KHÔNG mang ràng buộc UNIQUE chống trùng nữa; việc đó chuyển sang bảng
   `event_dedupe` không phân mảnh. Lý do: Postgres bắt "unique constraint on
   partitioned table must include all partitioning columns", mà khoá phân mảnh là
   `received_at DEFAULT now()` — nên mỗi lần webhook gửi LẠI sẽ có `received_at`
   khác nhau và ràng buộc **không bao giờ nổ**. Idempotency trong bản duyệt là
   idempotency giả.

2. Các cột truy vết nguồn (`event_id`, `last_event_id`) là `bigint` TRẦN, không
   `REFERENCES events(id)`. Lý do: khoá chính của bảng phân mảnh phải chứa khoá
   phân mảnh, nên `events` không có khoá duy nhất chỉ trên `id` để mà tham chiếu.
   Toàn vẹn tham chiếu tới log được kiểm bằng job reconcile, không bằng ràng buộc.

3. `query_access_log` KHÔNG phân mảnh ở P0. Ở quy mô P0 nó nhỏ; phân mảnh sẽ kéo
   theo phải nhét cột thời gian vào khoá chính. Thêm khi đo thấy cần.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = r"""
-- ═══════════════════════════════════════════════════════════════════════════
-- Extension. Không dùng Apache AGE (không có trên RDS/Cloud SQL/Supabase —
-- chọn nó là tự khoá nơi chạy, giết mức triển khai D3/D4 ở §11.4).
-- ═══════════════════════════════════════════════════════════════════════════
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: kNN cho seed truy hồi
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- cần cho EXCLUDE trên (uuid, tstzrange)
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- khớp alias mờ, dùng từ P1


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.1  Danh tính & nguồn
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE tenants (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform            text NOT NULL,          -- google_drive | google_gmail
    account_email       text NOT NULL,
    status              text NOT NULL DEFAULT 'active',
    cursor              jsonb NOT NULL DEFAULT '{}'::jsonb,
    channel_id          text,
    channel_resource_id text,
    channel_expires_at  timestamptz,
    last_sync_at        timestamptz,
    last_error          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sources_unique      UNIQUE (tenant_id, platform, account_email),
    CONSTRAINT sources_status_ck   CHECK (status IN ('active', 'paused', 'error')),
    CONSTRAINT sources_platform_ck CHECK (platform IN ('google_drive', 'google_gmail'))
);
COMMENT ON COLUMN sources.cursor IS
    'Con trỏ đồng bộ: {pageToken} cho Drive, {historyId} cho Gmail. '
    'Credential KHÔNG nằm ở đây — chỉ tham chiếu, xem §11.4 ràng buộc 3.';


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.2  Event log — NGUỒN SỰ THẬT. Chỉ INSERT. Không UPDATE, không DELETE.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE events (
    id            bigserial   NOT NULL,
    tenant_id     uuid        NOT NULL,
    source_id     uuid        NOT NULL,
    platform      text        NOT NULL,
    object_type   text        NOT NULL,   -- file | message | thread | permission
    object_id     text        NOT NULL,
    kind          text        NOT NULL,   -- created | updated | deleted | acl_changed | poke
    version       text,                   -- etag | historyId
    dedupe_key    text        NOT NULL,
    occurred_at   timestamptz NOT NULL,   -- thời gian ở NỀN TẢNG   (valid time)
    received_at   timestamptz NOT NULL DEFAULT now(),  -- ta biết lúc nào (transaction time)
    actor_email   text,
    payload       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, received_at),
    CONSTRAINT events_kind_ck CHECK (
        kind IN ('created', 'updated', 'deleted', 'acl_changed', 'poke')
    )
) PARTITION BY RANGE (received_at);

CREATE INDEX events_object_idx ON events (tenant_id, platform, object_id, occurred_at DESC);
CREATE INDEX events_time_idx   ON events (tenant_id, occurred_at DESC);

COMMENT ON TABLE events IS
    'Log bất biến. Xoá theo hạn lưu trữ bằng DETACH + DROP PARTITION (tức thì, '
    'không sinh dead tuple), không bao giờ bằng DELETE.';

-- Tạo phân mảnh tháng. Idempotent nên scheduler gọi lại được vô hại.
CREATE OR REPLACE FUNCTION ensure_events_partition(month_start date)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    part_name text := 'events_' || to_char(month_start, 'YYYY_MM');
    lo date := date_trunc('month', month_start)::date;
    hi date := (date_trunc('month', month_start) + interval '1 month')::date;
BEGIN
    IF to_regclass(part_name) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
            part_name, lo, hi
        );
    END IF;
    RETURN part_name;
END;
$$;

-- Phân mảnh mặc định: hứng mọi thứ rơi ngoài dải đã tạo, để một sự kiện tới trễ
-- bất thường KHÔNG làm hỏng lệnh INSERT. Có hàng ở đây là dấu hiệu scheduler
-- tạo phân mảnh đã ngừng chạy — brain_health phải đếm nó.
CREATE TABLE events_default PARTITION OF events DEFAULT;


-- Chống trùng THẬT SỰ. Bảng riêng vì `events` đã phân mảnh theo `received_at`,
-- mà UNIQUE trên bảng phân mảnh bắt buộc phải chứa khoá phân mảnh — trong khi
-- `received_at` lại khác nhau ở mỗi lần webhook gửi lại. Xem docstring ở đầu file.
-- Bảng chỉ hai cột nên nhỏ, và chịu được hạn lưu trữ riêng.
CREATE TABLE event_dedupe (
    tenant_id     uuid        NOT NULL,
    dedupe_key    text        NOT NULL,
    event_id      bigint      NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dedupe_key)
);
CREATE INDEX event_dedupe_age_idx ON event_dedupe (first_seen_at);


-- Trạng thái xử lý: HẸP, churn cao, nhưng nhỏ và tự dọn (xử lý xong là XOÁ hàng).
-- Tách khỏi log để dead tuple không rơi vào bảng lớn nhất hệ thống (§4.2).
CREATE TABLE event_state (
    event_id     bigint      PRIMARY KEY,
    tenant_id    uuid        NOT NULL,
    shard        smallint    NOT NULL,   -- hash(object_id) — tuần tự hoá theo entity (§7.2b)
    claimed_at   timestamptz,
    processed_at timestamptz,
    attempts     smallint    NOT NULL DEFAULT 0,
    last_error   text
) WITH (
    fillfactor = 70,                          -- chừa chỗ cho HOT update
    autovacuum_vacuum_scale_factor = 0.02,    -- vacuum sớm, đừng đợi 20%
    autovacuum_analyze_scale_factor = 0.02
);

CREATE INDEX event_state_queue_idx
    ON event_state (tenant_id, shard, event_id)
    WHERE processed_at IS NULL;


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.3  Đối tượng nền tảng (bản hydrate mới nhất)
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE objects (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform           text        NOT NULL,
    object_type        text        NOT NULL,
    external_id        text        NOT NULL,
    title              text,
    url                text,
    mime_type          text,
    created_at_source  timestamptz,
    modified_at_source timestamptz,
    observed_at        timestamptz NOT NULL,
    content_hash       text,
    deleted_at         timestamptz,
    last_event_id      bigint,      -- không FK: xem sai lệch (2) ở docstring
    raw                jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT objects_unique UNIQUE (tenant_id, platform, external_id)
);
COMMENT ON COLUMN objects.observed_at IS
    'Lần cuối ta ĐỌC từ nền tảng. Cột chống ảo giác: mọi fact trả ra phải mang '
    'được mốc này tới tận câu trả lời (§3.1).';


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.4  Thực thể chuẩn hoá & bí danh
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE entities (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind           text        NOT NULL,   -- person|file|thread|folder|label|group
    primary_key    text        NOT NULL,   -- email | platform:external_id
    canonical_name text        NOT NULL,
    object_id      uuid        REFERENCES objects(id) ON DELETE SET NULL,
    community_id   int,                    -- job Leiden ghi (§7.6)
    pagerank       real,
    degree         int         NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entities_unique  UNIQUE (tenant_id, kind, primary_key),
    CONSTRAINT entities_kind_ck CHECK (
        kind IN ('person', 'file', 'thread', 'folder', 'label', 'group')
    )
);
CREATE INDEX entities_community_idx ON entities (tenant_id, community_id);

CREATE TABLE aliases (
    id         bigserial PRIMARY KEY,
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_id  uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias      text NOT NULL,
    alias_norm text NOT NULL,   -- NFKC + casefold + gộp khoảng trắng
    source     text NOT NULL,   -- directory | envelope | manual
    CONSTRAINT aliases_unique UNIQUE (tenant_id, entity_id, alias_norm)
);
CREATE INDEX aliases_norm_idx ON aliases (tenant_id, alias_norm);
CREATE INDEX aliases_trgm_idx ON aliases USING gin (alias_norm gin_trgm_ops);
COMMENT ON TABLE aliases IS
    'Từ điển cho bước tách intent (§8.1): quét câu hỏi bằng Aho-Corasick trên '
    'bảng này để nhận ra entity mà KHÔNG cần LLM.';


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.5  Cạnh BI-TEMPORAL — bốn trường thời gian, không phải ba
--
--   valid_from / valid_to    thời gian THỰC   — ngoài đời đúng từ lúc nào tới lúc nào
--   observed_at / expired_at thời gian HỆ THỐNG — ta tin điều này từ lúc nào tới lúc nào
--
-- Thiếu `expired_at` thì "ngoài đời hết đúng" và "ta mới biết mình sai" ghi vào
-- cùng một ô, và truy vấn kiểm toán `as_of` trả lời SAI mà không báo. Spike Ngày 2.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE edges (
    id            bigserial PRIMARY KEY,
    tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    src_entity_id uuid        NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst_entity_id uuid        NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation      text        NOT NULL,
    valid_from    timestamptz NOT NULL,
    valid_to      timestamptz,               -- NULL = ngoài đời vẫn đang đúng
    observed_at   timestamptz NOT NULL,
    expired_at    timestamptz,               -- NULL = đây vẫn là điều ta đang tin
    confidence    real        NOT NULL DEFAULT 1.0,
    origin        text        NOT NULL DEFAULT 'envelope',
    event_id      bigint,                    -- không FK: xem sai lệch (2)
    attrs         jsonb       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT edges_relation_ck CHECK (relation IN (
        'owns', 'edited', 'shared_with', 'located_in',
        'sent', 'received', 'cc', 'in_thread', 'attached', 'labeled', 'member_of'
    )),
    CONSTRAINT edges_origin_ck   CHECK (origin IN ('envelope', 'llm', 'manual')),
    CONSTRAINT edges_no_self_ck  CHECK (src_entity_id <> dst_entity_id),
    CONSTRAINT edges_range_ck    CHECK (valid_to IS NULL OR valid_to > valid_from),

    -- Lưới an toàn tầng hai: dù writer có sai sharding, database vẫn từ chối hai
    -- cạnh cùng (src,dst,relation) chồng khoảng thời gian. `tstzrange` mặc định
    -- nửa mở [from, to) — khớp đúng quy tắc biên `<=` ở §7.5 quy tắc 2.
    -- Chỉ áp cho các bản ghi ta ĐANG TIN (expired_at IS NULL): bản đã bị thay thế
    -- là lịch sử, được phép chồng lấn.
    CONSTRAINT edges_no_overlap EXCLUDE USING gist (
        tenant_id     WITH =,
        src_entity_id WITH =,
        dst_entity_id WITH =,
        relation      WITH =,
        tstzrange(valid_from, valid_to) WITH &&
    ) WHERE (expired_at IS NULL)
);

CREATE INDEX edges_out_idx  ON edges (tenant_id, src_entity_id, relation)
    WHERE expired_at IS NULL;
CREATE INDEX edges_in_idx   ON edges (tenant_id, dst_entity_id, relation)
    WHERE expired_at IS NULL;
CREATE INDEX edges_asof_idx ON edges (tenant_id, valid_from, valid_to);
CREATE INDEX edges_audit_idx ON edges (tenant_id, observed_at, expired_at);


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.6  Fact có giá trị — cùng mô hình bốn trường
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE facts (
    id              bigserial PRIMARY KEY,
    tenant_id       uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_id       uuid        NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate       text        NOT NULL,
    value_text      text,
    value_num       numeric,
    value_ts        timestamptz,
    freshness_class text        NOT NULL DEFAULT 'slow',
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz,
    observed_at     timestamptz NOT NULL,
    expired_at      timestamptz,
    confidence      real        NOT NULL DEFAULT 1.0,
    origin          text        NOT NULL DEFAULT 'envelope',
    event_id        bigint,

    CONSTRAINT facts_freshness_ck CHECK (
        freshness_class IN ('structural', 'slow', 'volatile')
    ),
    CONSTRAINT facts_origin_ck CHECK (origin IN ('envelope', 'llm', 'manual')),
    CONSTRAINT facts_range_ck  CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT facts_no_overlap EXCLUDE USING gist (
        tenant_id WITH =,
        entity_id WITH =,
        predicate WITH =,
        tstzrange(valid_from, valid_to) WITH &&
    ) WHERE (expired_at IS NULL)
);

CREATE INDEX facts_live_idx ON facts (tenant_id, entity_id, predicate)
    WHERE expired_at IS NULL;


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.7  ACL — quyền cũng đổi theo thời gian nên cũng bi-temporal.
-- Truy vấn `as_of` quá khứ phải dùng ACL CỦA THỜI ĐIỂM ĐÓ, không phải hiện tại.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE acl (
    id               bigserial PRIMARY KEY,
    tenant_id        uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    object_entity_id uuid        NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    principal_ref    text        NOT NULL,   -- email | group:x | domain:acme.com | anyone
    role             text        NOT NULL,
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz,
    observed_at      timestamptz NOT NULL,
    expired_at       timestamptz,
    event_id         bigint,
    CONSTRAINT acl_role_ck  CHECK (role IN ('reader', 'commenter', 'writer', 'owner')),
    CONSTRAINT acl_range_ck CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX acl_lookup_idx ON acl (tenant_id, principal_ref, object_entity_id)
    WHERE expired_at IS NULL;
CREATE INDEX acl_object_idx ON acl (tenant_id, object_entity_id)
    WHERE expired_at IS NULL;


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.8  Chunk + embedding
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE chunks (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    object_id   uuid        NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    entity_id   uuid        REFERENCES entities(id) ON DELETE SET NULL,
    ordinal     int         NOT NULL,
    text        text        NOT NULL,
    lang        text,
    -- 'simple' chứ KHÔNG 'english': corpus VI+EN trộn, stemmer tiếng Anh làm
    -- hỏng token tiếng Việt. Dạng hai tham số là IMMUTABLE nên dùng được cho
    -- cột sinh; dạng một tham số chỉ STABLE và sẽ bị từ chối.
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text)) STORED,
    embedding   vector(1024),           -- bge-m3
    observed_at timestamptz NOT NULL,
    CONSTRAINT chunks_unique UNIQUE (object_id, ordinal)
);

CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_vec_idx ON chunks USING hnsw (embedding vector_cosine_ops);


-- ═══════════════════════════════════════════════════════════════════════════
-- §4.9  Đo lường + nhật ký truy cập
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE query_log (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    principal   text        NOT NULL,
    question    text        NOT NULL,
    mode        text,
    latency_ms  int         NOT NULL,
    pack_tokens int         NOT NULL,
    n_entities  int, n_facts int, n_edges int, n_gaps int, n_stale int,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Trả lời được câu doanh nghiệp thật sự hỏi: "chứng minh không ai từng thấy X".
-- Lưu ID chứ không lưu nội dung — nhật ký không được trở thành bản sao thứ hai
-- của dữ liệu khách.
CREATE TABLE query_access_log (
    query_id  bigint NOT NULL REFERENCES query_log(id) ON DELETE CASCADE,
    tenant_id uuid   NOT NULL,
    entity_id uuid   NOT NULL,
    object_id uuid,
    PRIMARY KEY (query_id, entity_id)
);
CREATE INDEX qal_object_idx ON query_access_log (tenant_id, object_id);
CREATE INDEX qal_entity_idx ON query_access_log (tenant_id, entity_id);
"""


DROP = r"""
DROP TABLE IF EXISTS query_access_log, query_log, chunks, acl, facts, edges,
                     aliases, entities, objects, event_state, event_dedupe,
                     events, sources, tenants CASCADE;
DROP FUNCTION IF EXISTS ensure_events_partition(date);
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(DROP)
