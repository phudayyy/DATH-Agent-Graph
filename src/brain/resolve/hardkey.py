"""Entity resolution bằng KHOÁ CỨNG. P0 không có fuzzy matching, và đó là chủ đích.

Mọi entity ở P0 đều đến từ phong bì API, nên đều có định danh chính xác: email cho
người, file id cho tài liệu, thread id cho hội thoại. Fuzzy chỉ cần khi có tên người
hoặc tên dự án nằm trong văn xuôi — tức là khi có LLM extraction, tức là P1.

Nói cách khác: **blocking + matching xác suất là bài toán của P1, không phải P0.**
Dựng nó bây giờ là giải một bài toán chưa tồn tại.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Final

from psycopg import AsyncConnection

from brain.db import fetch_one

log = logging.getLogger(__name__)

# Nhà cung cấp coi dấu chấm trong phần tên là vô nghĩa và cắt bỏ phần sau dấu `+`.
# Chuẩn hoá theo để `a.b+tag@gmail.com` và `ab@gmail.com` không thành hai người.
_DOT_INSENSITIVE_DOMAINS = frozenset({"gmail.com", "googlemail.com"})

# Miền CÓ hỗ trợ plus-addressing. Exchange/Outlook doanh nghiệp TẮT nó theo mặc
# định, nên `a+b@` ở đó là một hộp thư riêng chứ không phải bí danh của `a@`.
_PLUS_ADDRESSING_DOMAINS = _DOT_INSENSITIVE_DOMAINS | frozenset(
    {"fastmail.com", "protonmail.com", "proton.me", "icloud.com"}
)

_WHITESPACE = re.compile(r"\s+")


def normalize_email(raw: str) -> str:
    """Chuẩn hoá email thành khoá chính của một `person`.

    Chỉ áp luật bỏ-dấu-chấm cho các miền THẬT SỰ có luật đó. Áp đại trà sẽ gộp
    nhầm hai người khác nhau ở những miền coi dấu chấm là có nghĩa — và gộp nhầm
    người là lỗi không sửa được bằng cách chạy lại.
    """
    email = unicodedata.normalize("NFKC", raw).strip().casefold()
    if "@" not in email:
        return email
    local, _, domain = email.rpartition("@")
    # ⛔ `+tag` cũng phải theo MIỀN, y như luật dấu chấm ngay dưới.
    #
    # Bản trước cắt `+tag` ở mọi miền trong khi luật dấu chấm đã được gác đúng —
    # một sự bất đối xứng không có lý do. Exchange **tắt plus-addressing theo mặc
    # định**, nên `bob+ext@acme.com` ở đó là một HỘP THƯ KHÁC, thường là tài khoản
    # nhà thầu. Gộp nó vào `bob@acme.com` là gộp hai người.
    #
    # Giá trị này vừa là `entities.primary_key` vừa là principal của ACL, nên gộp
    # nhầm người nghĩa là quyền đọc Drive của hộp thư này thành quyền của hộp thư
    # kia. Gộp nhầm người là lỗi không sửa được bằng cách chạy lại.
    if domain in _PLUS_ADDRESSING_DOMAINS:
        local = local.partition("+")[0]
    if domain in _DOT_INSENSITIVE_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{domain}"


def normalize_alias(raw: str) -> str:
    """Chuẩn hoá nhãn để tra cứu: NFKC + casefold + gộp khoảng trắng."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", raw).strip().casefold())


# Trần độ dài một alias, tính bằng BYTE của `alias_norm` sau khi mã hoá UTF-8.
#
# ⛔ Đây là một cổng CHỐNG SẬP, không phải một tuỳ chọn. Ba index của bảng
# `aliases` là btree, và btree từ chối mọi hàng index quá 1/3 trang đệm (~2.704
# byte với block_size 8kB). `upsert_entity` ghi `canonical_name` — tức
# `Envelope.title`, tức giá trị nền tảng trả về — vào đó. Trong 17 parser, ĐÚNG
# MỘT cái cắt tiêu đề (`parse_slack_message` dùng `[:120]`); Drive lấy thẳng
# `name`, Gmail lấy thẳng `Subject`, Confluence/Notion/Jira lấy thẳng trường của
# nền tảng. Google Drive cho phép tên tệp tới 32.767 ký tự.
#
# Đo 2026-08-30 khi nạp EnterpriseRAG-Bench: một tiêu đề 10.693 ký tự làm ingest
# gãy với `index row size 5232 exceeds btree version 4 maximum 2704`.
#
# ⚠️ BYTE, không phải ký tự. Trần của Postgres là byte và `alias_norm` lưu UTF-8:
# 900 ký tự CJK hay tiếng Việt có dấu đã vượt trần trong khi phép đếm ký tự vẫn
# báo "ngắn". Cùng lớp lỗi với việc cắt tiếng Việt theo âm tiết ở đường chunk.
#
# BỎ chứ không CẮT. Alias tồn tại để `parse_intent` quét thấy một cái tên trong
# CÂU HỎI; một chuỗi 600 byte không bao giờ xuất hiện trong câu hỏi ai gõ, nên cắt
# nó chỉ tạo ra một cái tên giả — cũng không ai gõ — cộng thêm nguy cơ hai tiêu đề
# khác nhau đụng nhau ở phần đầu. Tên ĐẦY ĐỦ vẫn nằm nguyên ở `entities.
# canonical_name` (cột đó không có index nào), nên phần hiển thị không mất gì.
#
# 512 byte: rộng hơn mọi tên thật (Jira/Confluence chặn tiêu đề ở 255 ký tự), hẹp
# hơn trần btree nhiều lần.
MAX_ALIAS_BYTES: Final = 512


# ── và trần NGỮ NGHĨA, thứ trần kỹ thuật ở trên không thay được ──────────────
#
# ⛔ `MAX_ALIAS_BYTES` chặn Postgres ném; nó KHÔNG chặn một câu văn vào từ điển.
# Chú thích trên đã nói đúng luật — *"alias tồn tại để `parse_intent` quét thấy một
# cái tên trong CÂU HỎI"* — rồi chọn ngưỡng theo TIÊU ĐỀ ("Jira/Confluence chặn
# tiêu đề ở 255 ký tự"). Tiêu đề và tên là hai thứ khác nhau, và khoảng cách giữa
# chúng là toàn bộ lỗi này.
#
# Đo trên corpus RAGBench 511.958 object (2026-09-06), tenant `ragbench`:
#
#     alias                 511.432   trie 11.979.966 state   dựng 34,4s   RSS 3.471 MB
#     ≤4 từ và ≤40 ký tự    295.698   trie    294.468 state   dựng  1,0s   RSS ~85 MB
#                            (57,8%)        nhỏ hơn 40,7×      nhanh 34×
#
# `retrieve/intent.py` đặt trần cache 268 MB; automaton MỘT tenant chiếm 3,5 GB, và
# `_remember` giữ nó lại (guard `len > 1`) vì đuổi đi cũng không cứu được gì. Trần
# ấy không sai — đầu vào sai.
#
# Cái bị bỏ là TIÊU ĐỀ, không phải tên. Đo trên cùng corpus:
#
#     kind bị cắt:  thread 128.317 · issue 49.399 · file 22.843 · event 10.073
#                   · page 5.090 · organization 12 · **person 0**
#     alias 5–6 từ: "billing dispute and chargeback resolution playbook"
#                   "tenant finops cutover & governance playbook"
#     alias giữ:    eng-oncall · docs · eng-runtime · marketing · product
#
# Không một danh tính nào bị cắt, và 60/60 alias dài lấy mẫu vẫn tìm được ĐÚNG
# object của chúng qua BM25 (`chunks.tsv`). Hai kênh không thay nhau tuỳ tiện:
# Aho-Corasick trả lời *"câu hỏi có nhắc tên entity nào"* — cần khớp TOÀN CỤM, thứ
# BM25 làm kém với tên ngắn. Với một câu 13 từ thì khớp toàn cụm gần như không bao
# giờ xảy ra, nên alias ở đó không đóng góp gì mà vẫn tính đủ chi phí.
#
# ⛔ Chặn ở đường GHI, không ở đường đọc. Lọc lúc đọc vẫn phải dựng trie rồi vứt,
# và vẫn giữ 250 MB alias chết trong bảng.
MAX_ALIAS_WORDS: Final = 4
MAX_ALIAS_CHARS: Final = 40


def _is_name_shaped(alias_norm: str) -> bool:
    """Chuỗi này có hình dạng một cái TÊN người ta gõ ra không.

    Cố ý KHÔNG log mỗi lần bỏ: trên corpus thật nó đúng với 42% alias, và một dòng
    log cho mỗi cái là hàng trăm nghìn dòng nói cùng một điều — tức im lặng bằng một
    cách khác. Số bị bỏ đếm được bất cứ lúc nào bằng một truy vấn trên `aliases`.
    """
    words = alias_norm.split()
    # ⛔ `if not alias_norm` một mình KHÔNG đủ: `"   "` là truthy, dài 3 ký tự và
    # `split()` cho 0 từ — nên nó lọt qua CẢ HAI trần và vào từ điển như một alias
    # rỗng. Đếm TỪ, không đếm ký tự, để quyết định "có nội dung hay không".
    if not words:
        return False
    return len(alias_norm) <= MAX_ALIAS_CHARS and len(words) <= MAX_ALIAS_WORDS


# Trần cho một khoá ĐỊNH DANH — `objects.external_id`, `entities.primary_key`,
# `acl.principal_ref`. Cùng giới hạn btree với `MAX_ALIAS_BYTES`, nhưng KHÁC cách
# xử lý, và khác biệt ấy không tuỳ tiện:
#
#   alias      là một trợ giúp tra cứu ⇒ quá dài thì BỎ, không mất gì.
#   khoá định danh  LÀ đối tượng ⇒ không bỏ được, không cắt được (cắt là đụng độ),
#                   không băm được (chặng nội dung phải gọi API bằng `external_id`
#                   THẬT — xem `content/stage.py`, nó lấy id ấy để mở tệp).
#
# Nên ở đây chỉ còn một việc đúng: **ném một lỗi ĐỌC ĐƯỢC**. Sự kiện vẫn chết như
# trước — điều đổi là nó chết có lý do. Trước bản này thông điệp là
# `index row size 4040 exceeds btree version 4 maximum 2704 for index
# "objects_unique"`, một câu nói về INDEX chứ không nói đối tượng nào, nền tảng
# nào, hay vì sao; nó nằm trong `event_state.last_error` và không ai suy ngược
# được. Đo 2026-08-30: ba biên ghi khác nhau cùng chết kiểu đó, và bản vá alias
# chỉ phủ MỘT trong bốn họ index.
#
# 2000 byte: dưới trần btree (~2.704 trừ chi phí hàng index) và trên mọi khoá thật
# — đường dẫn Dropbox sâu nhất, `owner/repo#12345` của GitHub, id Graph dài nhất.
MAX_KEY_BYTES: Final = 2000


class KeyTooLong(ValueError):
    """Khoá định danh vượt trần index. Đọc `MAX_KEY_BYTES` để biết vì sao."""


def checked_key(value: str, *, column: str, context: str) -> str:
    """Trả `value`, hoặc ném `KeyTooLong` kèm đủ thứ để lần ra đối tượng."""
    size = len(value.encode())
    if size > MAX_KEY_BYTES:
        raise KeyTooLong(
            f"{column} dài {size} byte, vượt trần {MAX_KEY_BYTES} — {context}. "
            f"Đầu khoá: {value[:80]!r}"
        )
    return value


def platform_key(platform: str, external_id: str) -> str:
    """Khoá chính cho entity gắn với một đối tượng nền tảng."""
    return f"{platform}:{external_id}"


_UPSERT_ENTITY = """
INSERT INTO entities (tenant_id, kind, primary_key, canonical_name, object_id)
VALUES (%(tenant_id)s, %(kind)s, %(primary_key)s, %(canonical_name)s, %(object_id)s)
ON CONFLICT (tenant_id, kind, primary_key) DO UPDATE
    -- Chỉ nâng cấp tên khi có tên TỐT HƠN. Nhiều phong bì chỉ trả về email, và
    -- ghi đè "Nguyễn Văn A" bằng "a@acme.com" là mất thông tin.
    --
    -- ⛔ `%(placeholder)s` là vế quan trọng nhất, và thiếu nó thì phép bảo vệ trên
    -- KHÔNG hoạt động cho đường `referenced`. Chỗ đó truyền `canonical_name` =
    -- `external_id` TRẦN (`1DnLp…`) trong khi `primary_key` mang tiền tố nền tảng
    -- (`google_drive:1DnLp…`) — hai chuỗi KHÁC nhau, nên vế `<> primary_key` đúng
    -- và một lần được-nhắc-tới sẽ ghi đè tên thật bằng ID. Thứ tự xử lý sự kiện
    -- quyết định tên cuối cùng, tức cùng một corpus cho hai kết quả.
    SET canonical_name = CASE
            WHEN NOT %(placeholder)s
             AND excluded.canonical_name <> excluded.primary_key
             AND excluded.canonical_name <> ''
            THEN excluded.canonical_name
            ELSE entities.canonical_name
        END,
        object_id  = coalesce(excluded.object_id, entities.object_id),
        updated_at = now()
RETURNING id
"""

_UPSERT_ALIAS = """
INSERT INTO aliases (tenant_id, entity_id, alias, alias_norm, source)
VALUES (%(tenant_id)s, %(entity_id)s, %(alias)s, %(alias_norm)s, %(source)s)
ON CONFLICT (tenant_id, entity_id, alias_norm) DO NOTHING
"""

async def upsert_entity(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    kind: str,
    primary_key: str,
    canonical_name: str,
    placeholder: bool = False,
    object_id: str | None = None,
    aliases: list[str] | None = None,
) -> str:
    """Tạo hoặc lấy entity theo khoá cứng. Trả `entity_id`.

    Idempotent: gọi lại với cùng `primary_key` luôn trả về cùng một id, nên
    đường ingest chạy lại (giao ước "ít nhất một lần") không sinh entity trùng.
    """
    checked_key(
        primary_key, column="entities.primary_key",
        context=f"entity kind={kind!r} tên={(canonical_name or '')[:40]!r}",
    )
    row = await fetch_one(
        await conn.execute(
            _UPSERT_ENTITY,
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "primary_key": primary_key,
                "canonical_name": canonical_name or primary_key,
                # `True` ⇒ tên này chỉ là chỗ giữ chỗ (một ID lấy từ phong bì của
                # đối tượng KHÁC). Được phép TẠO entity, không được phép đổi tên nó.
                "placeholder": placeholder,
                "object_id": object_id,
            },
        )
    )
    entity_id = str(row["id"])

    # Mỗi nhãn gặp được đều vào từ điển. Đây là cách bảng `aliases` tự lớn lên mà
    # không cần LLM, và nó là đầu vào cho bước tách intent ở §8.1.
    # ⛔ KHÔNG bump bộ đếm phiên bản nào ở đây, và đó là một quyết định có giá.
    #
    # Bản trước làm vậy — `UPDATE tenants SET alias_version = alias_version + 1` —
    # và vì hàm này chạy MỘT LẦN CHO MỖI ENTITY, lệnh bump nằm xen giữa các lock
    # hàng `entities`, tạo ra một đảo thứ tự khoá làm deadlock hai worker ingest
    # chạy song song. `retrieve/intent.py` giờ SUY RA phiên bản từ
    # `max(aliases.id)` và `max(identity_links.id)`, nên đường ghi không phải nhớ
    # gì cả. Chi tiết ở `migrations/versions/0006_alias_version.py`.
    for alias in {canonical_name, *(aliases or [])}:
        if not alias or alias == primary_key:
            continue
        alias_norm = normalize_alias(alias)
        if not _is_name_shaped(alias_norm):
            # Một tiêu đề, không phải một cái tên. `entities.canonical_name` vẫn giữ
            # nguyên bản đầy đủ cho phần hiển thị, và `chunks.tsv` vẫn tìm ra nó.
            continue
        if len(alias_norm.encode()) > MAX_ALIAS_BYTES:
            # ⛔ Không phải chuyện thẩm mỹ: quá trần thì Postgres NÉM
            # `ProgramLimitExceeded`, transaction ghi phong bì bị cuốn theo, và
            # đối tượng không bao giờ hạ cánh. Xem `MAX_ALIAS_BYTES`.
            log.info(
                "bỏ alias %d byte của entity %s — quá dài để làm tên ai đó gõ ra",
                len(alias_norm.encode()), entity_id,
            )
            continue
        await conn.execute(
            _UPSERT_ALIAS,
            {
                "tenant_id": tenant_id,
                "entity_id": entity_id,
                "alias": alias,
                "alias_norm": alias_norm,
                "source": "envelope",
            },
        )
    return entity_id


# Số ký tự tối thiểu của một token tên mới được vào từ điển. Giữ bằng
# `intent.min_alias_len` là chủ đích: ghi vào một alias mà tầng đọc lọc bỏ thì chỉ
# tốn hàng, không giúp ai. ⚠️ Ngưỡng này MÙ NGÔN NGỮ — tên hai ký tự ("Hà", và
# phần lớn tên CJK) rơi hết. Đó là khiếm khuyết đã biết của `min_alias_len`, ghi
# lại ở đây để nó không bị phát hiện lần thứ ba như một điều bất ngờ.
_MIN_NAME_TOKEN = 3


def name_tokens(display_name: str) -> list[str]:
    """Token tên gọi rút từ họ tên đầy đủ — thứ người ta THẬT SỰ dùng khi hỏi.

    ⛔ Bảng `aliases` chỉ chứa họ tên ĐẦY ĐỦ, và khớp alias là tìm cả cụm trong
    câu hỏi. Nên "Alice sở hữu những tài liệu nào?" khớp **0** alias, dù entity
    "Nguyễn Thị Alice" tồn tại — và khi BM25 không tình cờ cứu được thì gói về
    RỖNG HOÀN TOÀN: 0 hạt giống, 0 entity, 0 quan hệ.

    Đo trên bộ vàng (`q11-owner-of-many`):

        "Alice sở hữu những tài liệu nào?"           alias 0 · entity 0 · TRƯỢT
        "Nguyễn Thị Alice sở hữu những tài liệu nào?" alias 1 · entity 12 · ĐÚNG

    Lấy CẢ token đầu lẫn token cuối, vì thứ tự tên khác nhau theo văn hoá: tiếng
    Việt đặt tên gọi ở CUỐI ("Nguyễn Thị Alice" → "Alice"), phần lớn tên phương
    Tây đặt ở ĐẦU ("Alice Nguyen" → "Alice"). Đoán một chiều là đoán sai cho nửa
    số người dùng.

    ⚠️ Việc này LÀM TĂNG nhập nhằng — nhiều người cùng tên gọi. Đó là đánh đổi có
    chủ đích: một alias nhập nhằng đưa vài entity vào hạt giống rồi để RRF và
    phân quyền lọc, còn KHÔNG có alias thì gói rỗng và không gì lọc được. Hệ vốn
    đã sống với nhập nhằng — đo được 1.116 `alias_norm` trỏ tới hơn một entity
    trên corpus 10k.
    """
    parts = [t for t in display_name.replace(",", " ").split() if t]
    if len(parts) < 2:
        return []
    ends = {parts[0], parts[-1]}
    return [t for t in ends if len(t) >= _MIN_NAME_TOKEN and t != display_name]


async def upsert_person(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    email: str,
    display_name: str | None = None,
) -> str:
    """Người — khoá cứng là email đã chuẩn hoá."""
    key = normalize_email(email)
    return await upsert_entity(
        conn,
        tenant_id=tenant_id,
        kind="person",
        primary_key=key,
        canonical_name=display_name or key,
        # Họ tên đầy đủ vào qua `canonical_name`; email và TOKEN TÊN GỌI vào đây.
        aliases=(
            ([email] if email != key else [])
            + (name_tokens(display_name) if display_name else [])
        )
        or None,
    )
