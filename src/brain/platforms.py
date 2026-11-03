"""Loại nút của đồ thị, và vị từ phân quyền DỰNG từ chúng.

Rút gọn từ bản ở mốc, nơi file này khai 26 nền tảng và 14 loại nút. Đề tài chỉ nạp
CRONQUESTIONS, nên chỉ còn ba loại. Phần giữ nguyên là phần bảo mật:

⛔ **`acl_bearing` là câu hỏi bảo mật, và nó được trả lời cạnh chính loại nút đó.**
Vị từ ACL được dựng TỪ danh sách ấy chứ không chép tay — một loại mới khai ở đây mà
quên quyền thì nó lọt mọi cổng phân quyền, im lặng.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# Loại nút đi thẳng vào SQL (hằng vị từ ACL, và CHECK trong `schema.sql`). Chúng là
# hằng của chính repo này chứ không phải dữ liệu người dùng, nhưng cổng vẫn nằm ở đây:
# một hằng đi vào SQL mà không ai kiểm hình dạng là một thói quen, và thói quen đó sẽ
# được chép sang chỗ có dữ liệu người dùng.
_SAFE_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_name(what: str, value: str) -> str:
    if not _SAFE_NAME.match(value):
        raise ValueError(f"{what} không hợp lệ: {value!r} — chờ dạng [a-z][a-z0-9_]*")
    return value


@dataclass(frozen=True, slots=True)
class EntityKind:
    """Một loại nút trong đồ thị.

    `acl_bearing` = *"nút này có mang nội dung riêng tư của chính nó không?"*

    Có ⇒ vị từ ở `retrieve/acl.py` đòi một hàng `acl` khớp principal mới cho thấy.
    Không ⇒ nút luôn hiện ra; người và nhóm thuộc nhóm này.
    """

    kind: str
    acl_bearing: bool

    def __post_init__(self) -> None:
        _check_name("kind", self.kind)


# ⛔ Danh sách ĐÓNG, và `schema.sql` khai đúng danh sách này trong `entities_kind_ck`:
# vị từ chỉ chặn những loại nó BIẾT, nên một loại lạ lọt vào bảng là lọt qua cổng.
# `tests/test_schema.py` đỏ khi hai danh sách lệch nhau.
KINDS: Final[tuple[EntityKind, ...]] = (
    EntityKind("person", acl_bearing=False),
    EntityKind("group", acl_bearing=False),
    # Một thực thể Wikidata của CRONQUESTIONS. MANG ACL: mạng lưới quyền của đề tài
    # quyết ai thấy nó, và không có hàng `acl` nào thì không ai thấy — fail-closed.
    EntityKind("item", acl_bearing=True),
)

ACL_BEARING_KINDS: Final[tuple[str, ...]] = tuple(k.kind for k in KINDS if k.acl_bearing)

ALL_KINDS: Final[tuple[str, ...]] = tuple(k.kind for k in KINDS)

# Nhãn "ai cũng đọc được". `expand_principals` luôn thêm nó vào tập của mọi người hỏi.
PUBLIC_PRINCIPAL: Final = "anyone"


# Vị từ phân quyền, DỰNG từ `KINDS` chứ không chép tay.
#
# ⚠️ Truy vấn dùng vị từ này BẮT BUỘC đặt bí danh cho `entities` là `e` và truyền
# đủ `tenant_id`, `principals`, `as_of`.
#
# ⛔ Dựng bằng `.replace()` trên một mẫu HẰNG, không bằng f-string: S608 cấm f-string
# dựng SQL, và `retrieve/acl.py::with_acl` tồn tại chính vì thế. Danh sách chèn vào là
# hằng của chính repo, đã qua `_check_name` lúc dựng `KINDS`; phép kiểm được LẶP LẠI
# ngay trước khi chèn — một hằng đi vào SQL phải được kiểm ở chỗ nó đi vào.
_VISIBLE_ENTITY_TEMPLATE = """(
    e.kind NOT IN ({gated})
    OR EXISTS (
        SELECT 1 FROM acl a
         WHERE a.tenant_id        = %(tenant_id)s
           AND a.object_entity_id = e.id
           AND a.principal_ref    = ANY(%(principals)s)
           AND a.valid_from      <= %(as_of)s
           AND (a.valid_to   IS NULL OR a.valid_to   > %(as_of)s)
           AND (a.expired_at IS NULL OR a.expired_at > %(as_of)s)
    )
)"""


def _visible_entity_sql() -> str:
    gated = ", ".join(
        "'" + _check_name("kind chèn vào vị từ ACL", k) + "'"
        for k in ACL_BEARING_KINDS
    )
    return _VISIBLE_ENTITY_TEMPLATE.replace("{gated}", gated)


VISIBLE_ENTITY_SQL: Final[str] = _visible_entity_sql()
