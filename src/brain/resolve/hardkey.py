"""Chuẩn hoá email thành khoá của một người — thứ duy nhất đề tài cần từ module này.

Bản ở mốc làm entity resolution bằng KHOÁ CỨNG cho mọi nền tảng; ở đây chỉ còn
`normalize_email`, vì `retrieve/acl.py::expand_principals` dùng nó để nở danh tính
người hỏi thành tập nhãn ACL.
"""

from __future__ import annotations

import unicodedata

# Nhà cung cấp coi dấu chấm trong phần tên là vô nghĩa và cắt bỏ phần sau dấu `+`.
# Chuẩn hoá theo để `a.b+tag@gmail.com` và `ab@gmail.com` không thành hai người.
_DOT_INSENSITIVE_DOMAINS = frozenset({"gmail.com", "googlemail.com"})

# Miền CÓ hỗ trợ plus-addressing. Exchange/Outlook doanh nghiệp TẮT nó theo mặc
# định, nên `a+b@` ở đó là một hộp thư riêng chứ không phải bí danh của `a@`.
_PLUS_ADDRESSING_DOMAINS = _DOT_INSENSITIVE_DOMAINS | frozenset(
    {"fastmail.com", "protonmail.com", "proton.me", "icloud.com"}
)


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
    # Exchange **tắt plus-addressing theo mặc định**, nên `bob+ext@acme.com` ở đó là
    # một HỘP THƯ KHÁC, thường là tài khoản nhà thầu. Gộp nó vào `bob@acme.com` là
    # gộp hai người — và giá trị này vừa là `entities.primary_key` vừa là principal
    # của ACL, nên gộp nhầm người là trao quyền đọc của người này cho người kia.
    if domain in _PLUS_ADDRESSING_DOMAINS:
        local = local.partition("+")[0]
    if domain in _DOT_INSENSITIVE_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{domain}"
