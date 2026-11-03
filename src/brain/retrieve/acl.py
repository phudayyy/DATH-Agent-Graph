"""MỘT vị từ phân quyền, dùng chung cho mọi đường đọc.

Trước file này, vị từ nằm nguyên văn trong `pack.py`. Thêm đường đọc thứ hai
(chọn hạt giống theo khối văn bản) mà chép nó sang là tạo bản sao thứ hai của một
luật bảo mật — và hai bản sao của một luật bảo mật **sẽ** trôi khỏi nhau. Khi
chúng trôi, thứ trôi mất không phải là tính đúng, mà là tính kín.

Đã có tiền lệ trong chính dự án này: cổng phân quyền chặn `kind='file'` nhưng
không chặn `kind='thread'`, nên mọi thread Gmail đọc được bởi bất kỳ ai. Nó sống
sót qua 99 test và một lượt review, và chỉ lộ ra khi có người NHÌN vào đồ thị.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from brain import platforms
from brain.resolve.hardkey import normalize_email

# Loại entity MANG ACL riêng, và hằng vị từ đi cùng — cả hai ĐỌC từ
# `brain.platforms`, không khai lại ở đây.
#
# Bản trước khai `ACL_BEARING_KINDS` ở file này rồi chép cùng danh sách ấy vào
# `VISIBLE_ENTITY_SQL` bên dưới, và canh hai bản bằng một `assert` lúc import. Cái
# assert ấy làm đúng việc của nó — nhưng nó chỉ nhìn được HAI chỗ, trong khi một
# loại entity mới phải xuất hiện ở BA: hai chỗ này và `graph/apply.py::_ENTITY_KIND`.
# Một loại khai ở `apply` mà quên ở đây thì assert im lặng, và loại ấy lọt mọi cổng
# phân quyền. Đó đúng là lỗi `thread` của Gmail.
#
# Nay câu hỏi "loại này có mang ACL không" được trả lời cạnh chính loại đó trong
# `platforms.KINDS`, và cả ba chỗ đọc ra từ đấy — nên không còn hai bản để mà lệch.
#
# ⚠️ Thêm một loại entity mới có nội dung riêng tư thì thêm nó vào `platforms.KINDS`
# với `acl_bearing=True`. Đây không còn là chỗ để sửa.
ACL_BEARING_KINDS: Final = platforms.ACL_BEARING_KINDS

# Tham số bắt buộc phải có mặt khi dùng các mảnh dưới đây.
REQUIRED_PARAMS: Final = ("tenant_id", "principals", "as_of")


# Vị từ, dưới dạng HẰNG SỐ chứ không phải hàm nhận alias.
#
# Bản đầu nhận `alias` và kiểm theo danh sách trắng. Bỏ tham số đi thì bề mặt chèn
# SQL không còn tồn tại để mà phải kiểm — mọi truy vấn dùng vị từ này đều đặt bí
# danh bảng `entities` là `e`, nên tham số đó chưa từng mang giá trị nào khác. Ít
# linh hoạt hơn, và đó chính là điều mong muốn ở một cổng phân quyền.
#
# ⚠️ Truy vấn dùng vị từ này BẮT BUỘC:
#   - đặt bí danh cho `entities` là `e`
#   - truyền đủ `tenant_id`, `principals`, `as_of`
VISIBLE_ENTITY_SQL = platforms.VISIBLE_ENTITY_SQL


# ── Nở danh tính ───────────────────────────────────────────────────────────

# Ai cũng khớp nhãn này: `anyone` ở Drive nghĩa là "bất kỳ ai có liên kết", và
# người đang hỏi là một trong số đó. ĐỌC từ `platforms`, không khai lại — đường
# GHI cũng phát nhãn này, và hai bản chép của một nhãn sẽ lệch.
PUBLIC_PRINCIPAL: Final = platforms.PUBLIC_PRINCIPAL

_MEMBER_OF_AS_OF = """
SELECT g.primary_key
  FROM entities p
  JOIN edges  ed ON ed.src_entity_id = p.id AND ed.relation = 'member_of'
  JOIN entities g ON g.id = ed.dst_entity_id AND g.kind = 'group'
 WHERE p.tenant_id   = %(tenant_id)s
   AND p.kind        = 'person'
   AND p.primary_key = ANY(%(emails)s)
   AND ed.tenant_id  = %(tenant_id)s
   AND ed.valid_from <= %(as_of)s
   AND (ed.valid_to   IS NULL OR ed.valid_to   > %(as_of)s)
   AND (ed.expired_at IS NULL OR ed.expired_at > %(as_of)s)
"""


async def expand_principals(
    conn: Any, *, tenant_id: str, identities: list[str], as_of: datetime
) -> list[str]:
    """Danh tính → TẬP nhãn mà một hàng `acl` có thể mang.

    `envelope._drive_principal` sinh ra bốn hình dạng — `alice@acme.com`,
    `group:eng@acme.com`, `domain:acme.com`, `anyone` — và cho tới hàm này đường
    đọc chỉ biết hình dạng thứ nhất. Trong một tổ chức Workspace phần lớn chia
    sẻ đi qua hai hình dạng giữa, nên thiếu bước nở thì người dùng **không thấy
    gì** dù họ mở được tài liệu đó bằng mắt.

    Ba nguồn, và không nguồn nào cần gọi mạng ở đây:

        email       chính nó, đã chuẩn hoá
        domain:     suy từ phần sau `@` — thành viên một domain thì thấy thứ
                    chia cho cả domain đó, theo định nghĩa
        group:      từ cạnh `member_of` ĐANG ĐÚNG tại `as_of` — nên một truy vấn
                    kiểm toán về quá khứ dùng thành viên nhóm CỦA LÚC ĐÓ, đúng
                    như §4.7 đòi hỏi cho ACL

    ⚠️ **Fail-closed.** Danh tính rỗng trả về rỗng, và một pack rỗng là hành vi
    đúng — `select_seeds` đã dựa vào chính điều đó. Không có cạnh `member_of`
    nào (chưa chạy directory sync, hoặc tài khoản không phải admin Workspace)
    thì kết quả HỤT chứ không THỪA: người dùng thấy ít hơn quyền thật của họ,
    không bao giờ nhiều hơn.
    """
    if not identities:
        return []

    # ⛔ Phân loại theo TIỀN TỐ, không theo "có dấu @ hay không".
    #
    # `group:eng@acme.com` có dấu `@`. Bản đầu phân loại bằng `"@" in i`, nên nó
    # rơi vào nhánh email và bị suy ra `domain:acme.com`. Với `group:eng@acme.com`
    # thì domain đó tình cờ đúng; với một nhãn nhóm ở miền khác —
    # `group:vendor@partner.com` — nó cấp cho người hỏi MỌI thứ chia cho cả
    # domain `partner.com`. Đó là leo thang quyền từ một phép phân loại chuỗi,
    # đúng loại lỗi mà cổng phân quyền không được phép có.
    # ⚠️ `.strip()` TRƯỚC khi thử tiền tố, không chỉ khi lưu. Bản trước thử tiền
    # tố trên chuỗi thô: `" group:vendor@partner.com"` (thừa một khoảng trắng) hụt
    # phép thử, rơi xuống nhánh email vì có dấu `@`, và sinh lại đúng
    # `domain:partner.com` mà dòng trên nói là đã vá.
    # ⚠️ CASEFOLD trước khi thử tiền tố, cùng lý do với `.strip()` ở trên.
    # `BRAIN_PRINCIPALS` là biến môi trường người vận hành gõ tay, nên
    # `"Group:vendor@partner.com"` — một lỗi gõ hoàn toàn hợp lý — hụt phép thử
    # tiền tố, rơi xuống nhánh email vì có dấu `@`, và dòng `domain:` bên dưới
    # cấp cho người gọi quyền đọc MỌI thứ chia sẻ cho cả miền `partner.com`.
    # Cùng một lỗ, lần thứ ba, chỉ đổi cách gõ.
    cleaned = [i.strip().casefold() for i in identities if i and i.strip()]
    literal = {
        i for i in cleaned if i.startswith(("group:", "domain:")) or i == PUBLIC_PRINCIPAL
    }
    emails = sorted({normalize_email(i) for i in cleaned if i not in literal and "@" in i})

    out: set[str] = set(emails) | literal | {PUBLIC_PRINCIPAL}
    # Domain suy ra CHỈ từ email của chính người hỏi.
    out |= {f"domain:{email.split('@', 1)[1]}" for email in emails}

    if emails:
        cur = await conn.execute(
            _MEMBER_OF_AS_OF, {"tenant_id": tenant_id, "emails": emails, "as_of": as_of}
        )
        out |= {f"group:{row['primary_key']}" for row in await cur.fetchall()}

    return sorted(out)


def with_acl(sql: str) -> str:
    """Chèn vị từ phân quyền vào chỗ `{acl}` của một mẫu truy vấn.

    Là HÀM chứ không phải f-string ở từng chỗ dùng, vì hai lý do lồng nhau. Thứ
    nhất: phép chèn vị từ bảo mật xảy ra ở đúng một chỗ, nên đọc `git blame` một
    dòng là thấy hết mọi truy vấn chịu ảnh hưởng. Thứ hai: ghép chuỗi rải rác
    khiến linter phải đoán, và ở đây nó đoán mâu thuẫn — S608 cấm f-string dựng
    SQL, UP032 lại đòi đổi `.format()` thành f-string. Một hàm thì không bên nào
    phải đoán.
    """
    if "{acl}" not in sql:
        raise ValueError("mẫu truy vấn không có chỗ chèn `{acl}`")
    return sql.replace("{acl}", VISIBLE_ENTITY_SQL)
