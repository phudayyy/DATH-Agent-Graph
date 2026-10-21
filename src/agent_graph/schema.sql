-- Lược đồ tối thiểu cho CRONQUESTIONS, rút từ `migrations/versions/0001_initial_schema.py` ở mốc:
-- bốn bảng tenants · entities · edges · acl. Giữ nguyên bốn cột thời gian bi-temporal
-- (valid_from/valid_to = ngoài đời đúng khi nào; observed_at/expired_at = ta biết khi nào),
-- bỏ những gì chỉ nền tảng thật mới cần (objects, events, chunks, cộng đồng, pagerank).

CREATE TABLE tenants (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entities (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind           text        NOT NULL,   -- person | group | item
    primary_key    text        NOT NULL,   -- email | khoá nhóm | Wikidata id (Q…)
    canonical_name text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entities_unique  UNIQUE (tenant_id, kind, primary_key),
    -- ⛔ Danh sách ĐÓNG, khớp `brain.platforms.KINDS` — `tests/test_schema.py` canh.
    CONSTRAINT entities_kind_ck CHECK (kind IN ('person', 'group', 'item'))
);

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
    attrs         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT edges_no_self_ck CHECK (src_entity_id <> dst_entity_id),
    CONSTRAINT edges_range_ck   CHECK (valid_to IS NULL OR valid_to > valid_from)
);
-- Bản ở mốc còn có `EXCLUDE USING gist` cấm hai khoảng chồng nhau trên cùng
-- (src, dst, relation). Nó cần `btree_gist`, mà Postgres nhúng của `pgserver` không có,
-- nên ở đây bộ nạp GỘP các khoảng chồng nhau trước khi ghi (`cronquestions.merge_intervals`).
CREATE INDEX edges_src_idx ON edges (tenant_id, src_entity_id);
CREATE INDEX edges_dst_idx ON edges (tenant_id, dst_entity_id);

CREATE TABLE acl (
    id               bigserial PRIMARY KEY,
    tenant_id        uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    object_entity_id uuid        NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    principal_ref    text        NOT NULL,   -- email | group:x | domain:x | anyone
    role             text        NOT NULL,
    valid_from       timestamptz NOT NULL,
    valid_to         timestamptz,
    observed_at      timestamptz NOT NULL,
    expired_at       timestamptz,
    CONSTRAINT acl_role_ck  CHECK (role IN ('reader', 'commenter', 'writer', 'owner')),
    CONSTRAINT acl_range_ck CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX acl_object_idx ON acl (tenant_id, object_entity_id, principal_ref);
