"""Hình dạng của một nền tảng, khai ĐÚNG MỘT LẦN.

Trước file này, thêm một nền tảng phải sửa **tám chỗ**, và năm trong số đó nằm
ngoài `connectors/`:

    connectors/<p>.py          backfill · poll_changes · POLL_CURSOR_KEY
    connectors/transports.py   quy ước gọi + phân loại lỗi xác thực
    connectors/registry.py     ConnectorSpec
    extract/envelope.py        parse_* + PARSERS
    ingest/events.py           ObjectType (Literal)
    graph/apply.py             _ENTITY_KIND
    retrieve/acl.py            ACL_BEARING_KINDS  **và**  VISIBLE_ENTITY_SQL
    migrations/               ba CHECK: entities.kind · edges.relation · sources.platform

Migration 0004 tự ghi lại cái giá: *"danh sách trắng THỨ BA … trong một phiên đã
dính đủ cả ba lần lượt"*. Ba trong tám chỗ ấy chỉ ồn khi CHẠY, và một trong ba —
`ACL_BEARING_KINDS` — không ồn chút nào: quên nó thì loại entity mới lọt mọi cổng
phân quyền. Đó đúng là lỗi `thread` của Gmail, sống sót qua 99 test và một lượt
review.

Module này là chỗ **đọc** cho năm chỗ ngoài `connectors/`. Nó không import gì từ
`brain` — cố ý, vì `graph/apply.py` và `retrieve/acl.py` đọc nó, còn `connectors/`
thì import ngược lên chúng; một cạnh nữa là một vòng.

⛔ **`acl_bearing` là câu hỏi bảo mật, và nó được trả lời cạnh chính loại entity
đó.** Bản trước để câu trả lời ở `retrieve/acl.py` dưới dạng một tuple riêng, kèm
một `assert` lúc import canh cho nó khớp với hằng SQL bên cạnh. Cái assert ấy chỉ
canh được HAI chỗ đã biết; nó không canh được chỗ thứ ba là `_ENTITY_KIND`, nơi
một loại mới có thể xuất hiện mà không ai nghĩ tới quyền.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

# Loại entity và tên quan hệ đi thẳng vào SQL (hằng vị từ ACL, và các CHECK trong
# migration). Chúng là hằng của chính repo này chứ không phải dữ liệu người dùng,
# nhưng cổng vẫn nằm ở đây: một hằng đi vào SQL mà không ai kiểm hình dạng là một
# thói quen, và thói quen đó sẽ được chép sang chỗ có dữ liệu người dùng.
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
    Không ⇒ nút hiện ra theo tài liệu dẫn tới nó, và chặn nó là chặn nhầm — người,
    nhóm, thư mục, nhãn thuộc nhóm này.

    ⚠️ `space` (Notion database / Jira project) MANG ACL trong khi `folder` của
    Drive thì không. Sự KHÔNG nhất quán ấy có chủ đích và migration 0004 giải
    thích: tên một Notion database thường chính là điều nhạy cảm
    (*"Kế hoạch cắt giảm 2026"*), còn tên thư mục Drive hiếm khi là bí mật.
    """

    kind: str
    acl_bearing: bool

    def __post_init__(self) -> None:
        _check_name("kind", self.kind)


# ⛔ Danh sách ĐÓNG. Thêm một dòng ở đây là một quyết định về quyền, nên nó phải
# đi kèm một migration mở rộng `entities_kind_ck` — và
# `test_manifest_matches_migration` đỏ cho tới khi migration ấy tồn tại.
KINDS: Final[tuple[EntityKind, ...]] = (
    EntityKind("person", acl_bearing=False),
    EntityKind("group", acl_bearing=False),
    EntityKind("file", acl_bearing=True),      # Drive
    EntityKind("thread", acl_bearing=True),    # Gmail, Slack
    EntityKind("folder", acl_bearing=False),   # Drive
    EntityKind("label", acl_bearing=False),    # Gmail
    EntityKind("channel", acl_bearing=True),   # Slack
    EntityKind("page", acl_bearing=True),      # Notion
    EntityKind("issue", acl_bearing=True),     # Jira
    EntityKind("space", acl_bearing=True),     # Notion database · Jira project
    # ⛔ Một cuộc họp MANG ACL: `attendees[]` vừa là quan hệ vừa là quyền đọc,
    # và tiêu đề thường chính là điều nhạy cảm — "1:1 về việc chuyển bộ phận"
    # nói ra đúng thứ mà nội dung nói.
    EntityKind("event", acl_bearing=True),     # Google Calendar
    # …còn LỊCH thì không. Tên một lịch gần như luôn là tên người hoặc tên
    # phòng họp, và nó đã lộ ra qua chính các sự kiện dẫn tới nó. Khác `space`
    # của Notion, nơi tên database do người dùng đặt và thường mô tả nội dung.
    EntityKind("calendar", acl_bearing=False),
    # ⛔ MANG ACL. Tên một công ty trong CRM *chính là* điều nhạy cảm — "ta có đang
    # nói chuyện với ACME không" được trả lời bằng đúng cái tên, không cần nội dung
    # nào. Cùng lập luận 0017 dùng cho `event` (tiêu đề cuộc họp), và ngược với
    # `calendar` (tên lịch gần như luôn là tên người, đã lộ qua chính sự kiện).
    EntityKind("organization", acl_bearing=True),
)

_KIND_BY_NAME: Final[dict[str, EntityKind]] = {k.kind: k for k in KINDS}

# Thứ `retrieve/acl.py` từng khai bằng tay cạnh một hằng SQL chép tay.
ACL_BEARING_KINDS: Final[tuple[str, ...]] = tuple(
    k.kind for k in KINDS if k.acl_bearing
)

ALL_KINDS: Final[tuple[str, ...]] = tuple(k.kind for k in KINDS)

# Nhãn "ai cũng đọc được". `expand_principals` luôn thêm nó vào tập của mọi người
# hỏi, và `_drive_principal` phát nó cho `type: "anyone"` của Drive.
#
# ⛔ Ở ĐÂY chứ không ở `retrieve/acl.py`, vì đường GHI (`extract/envelope.py`) cũng
# cần nó và `extract` không được import `retrieve` — đó là lộn tầng. Một bản chép ở
# đường ghi là đúng lớp lỗi mà cả nhánh này dành để sửa: nhãn hai đầu lệch nhau thì
# cả hai đều là chuỗi hợp lệ và không có gì đỏ.
PUBLIC_PRINCIPAL: Final = "anyone"


@dataclass(frozen=True, slots=True)
class PlatformManifest:
    """Một nền tảng khai ra thứ nó sinh vào đồ thị.

    `objects` là **loại đối tượng ở nền tảng → loại entity trong đồ thị**. Hai
    không gian khác nhau: `message` của Gmail và của Slack đều thành `thread`, vì
    đơn vị hội thoại là chuỗi chứ không phải từng tin.

    `referenced_kinds` là các nút mà phong bì NHẮC TỚI nhưng không phải chủ thể của
    nó — thư mục cha, nhãn, thread, space. Chúng đi qua `Envelope.referenced` và
    được tạo dưới dạng placeholder.

    `relations` phải liệt kê MỌI quan hệ parser của nền tảng này phát ra, kể cả
    quan hệ phát ra qua một biến. Cổng cũ quét mã nguồn bằng regex
    `env\\.link\\("([a-z_]+)"` nên nó **mù với vòng lặp**: ba quan hệ `sent`,
    `received`, `cc` của Gmail đi qua `env.link(relation, key)` và chưa từng được
    cổng ấy nhìn thấy. Chúng tình cờ đã có trong whitelist, nên không có gì hỏng —
    và đó chính là lý do nó sống sót.
    """

    platform: str
    objects: Mapping[str, str]
    relations: tuple[str, ...] = ()
    referenced_kinds: tuple[str, ...] = ()
    # Nền tảng có connector native (làn A) hay không. `sources.platform` là một
    # CHECK, nên một nền tảng khai ở đây mà chưa có connector vẫn phải nằm trong
    # whitelist — `google_calendar` đã ở đúng trạng thái đó từ migration 0004.
    has_connector: bool = True

    def __post_init__(self) -> None:
        _check_name("platform", self.platform)
        for object_type, kind in self.objects.items():
            _check_name("object_type", object_type)
            if kind not in _KIND_BY_NAME:
                raise ValueError(
                    f"{self.platform}: object_type {object_type!r} ánh xạ tới kind "
                    f"{kind!r} chưa khai trong KINDS"
                )
        for relation in self.relations:
            _check_name("relation", relation)
        for kind in self.referenced_kinds:
            if kind not in _KIND_BY_NAME:
                raise ValueError(
                    f"{self.platform}: referenced kind {kind!r} chưa khai trong KINDS"
                )


MANIFESTS: Final[dict[str, PlatformManifest]] = {
    m.platform: m
    for m in (
        PlatformManifest(
            platform="google_drive",
            # ⛔ `folder` ở đây KHÔNG thừa, dù `PARSERS` chỉ khai khoá
            # `("google_drive", "file")`. `parse_drive_file` quyết định
            # `object_type` theo `mimeType` và trả `"folder"` cho thư mục — Drive
            # trả thư mục qua chính `files.list`, chỉ khác một trường. Thiếu dòng
            # này thì MỌI thư mục ném `ValueError` ở `apply_envelope` và vào hàng
            # đợi thử lại, trên dữ liệu thật.
            objects={"file": "file", "folder": "folder"},
            # `member_of` không do parser phát ra mà do LÀN DANH BẠ
            # (`connectors/directory.py`), vốn chạy trên một nguồn Drive. Manifest
            # khai theo *nền tảng ghi gì vào `edges.relation`*, không theo *parser
            # nào phát ra* — nếu không thì mọi quan hệ của một làn khác sẽ vắng.
            relations=("owns", "edited", "located_in", "member_of"),
            referenced_kinds=("folder",),
        ),
        PlatformManifest(
            platform="google_gmail",
            objects={"message": "thread", "thread": "thread"},
            # `sent`/`received`/`cc` đi qua một vòng lặp trong `parse_gmail_message`.
            # Đây là chỗ đầu tiên trong repo mà chúng được KHAI, chứ không chỉ
            # được cho phép.
            relations=("sent", "received", "cc", "in_thread", "labeled"),
            referenced_kinds=("thread", "label"),
        ),
        PlatformManifest(
            platform="google_calendar",
            objects={"event": "event"},
            # `created` và `located_in` đã có sẵn từ Notion/Jira và Drive; chỉ
            # `organized` và `invited` là mới — xem migration 0017.
            relations=("organized", "invited", "created", "located_in", "child_of"),
            referenced_kinds=("calendar", "event"),
        ),
        PlatformManifest(
            platform="microsoft_outlook",
            objects={"message": "thread", "thread": "thread"},
            relations=("sent", "received", "cc", "in_thread"),
            referenced_kinds=("thread",),
        ),
        PlatformManifest(
            platform="microsoft_onedrive",
            objects={"file": "file", "folder": "folder"},
            relations=("owns", "edited", "located_in", "member_of"),
            referenced_kinds=("folder",),
        ),
        PlatformManifest(
            platform="servicenow",
            objects={"issue": "issue"},
            relations=("reported", "assigned_to", "member_of"),
            referenced_kinds=("group",),
        ),
        PlatformManifest(
            platform="zoom",
            objects={"event": "event"},
            relations=("organized", "invited"),
        ),
        # Fireflies là bản chép lời cuộc họp, cùng hình dạng Zoom: một `event`, ACL
        # LÀ danh sách người dự. Khác một điểm quyết định làn nội dung — chữ nằm SẴN
        # trong phong bì (`sentences[]`), không sau một lượt tải như VTT của Zoom.
        PlatformManifest(
            platform="fireflies",
            objects={"event": "event"},
            relations=("organized", "invited"),
        ),
        PlatformManifest(
            platform="salesforce",
            objects={"contact": "person", "company": "organization", "issue": "issue"},
            relations=("owns", "associated_with", "member_of"),
            referenced_kinds=("person", "organization", "issue"),
        ),
        PlatformManifest(
            platform="hubspot",
            objects={"contact": "person", "company": "organization", "issue": "issue"},
            relations=("owns", "located_in", "associated_with", "member_of"),
            referenced_kinds=("space", "person", "organization", "issue"),
        ),
        PlatformManifest(
            platform="dropbox",
            objects={"file": "file", "folder": "folder"},
            relations=("owns", "located_in"),
            referenced_kinds=("folder",),
        ),
        PlatformManifest(
            platform="box",
            objects={"file": "file", "folder": "folder"},
            relations=("owns", "edited", "located_in", "member_of"),
            referenced_kinds=("folder", "group"),
        ),
        PlatformManifest(
            platform="zendesk",
            objects={"issue": "issue"},
            relations=("reported", "assigned_to", "located_in", "labeled", "member_of"),
            referenced_kinds=("space", "label"),
        ),
        PlatformManifest(
            platform="slack",
            objects={"message": "thread", "channel": "channel"},
            relations=(
                "sent", "mentioned", "reacted", "in_channel", "replies_to", "member_of",
            ),
            referenced_kinds=("channel", "thread"),
        ),
        PlatformManifest(
            # ⛔ Merge request VÀ issue cùng vào `issue`. `ObjectType` là tập đóng,
            # nhưng lý do thật không phải chi phí: câu hỏi người ta hỏi là "ai đã
            # bàn gì về thay đổi này", và với GitLab nó được trả lời bằng cả hai
            # như nhau. Hai loại entity sẽ chia đôi câu trả lời ấy.
            platform="gitlab",
            objects={"issue": "issue"},
            relations=("reported", "assigned_to", "edited", "located_in", "labeled"),
            referenced_kinds=("space", "label"),
        ),
        PlatformManifest(
            # Hội thoại: đơn vị truy hồi là CẢ luồng, như Slack và Gmail.
            platform="microsoft_teams",
            objects={"message": "thread", "channel": "channel"},
            relations=("sent", "mentioned", "in_channel", "replies_to"),
            referenced_kinds=("channel", "thread", "person"),
        ),
        PlatformManifest(
            # ⛔ CÙNG hình dạng OneDrive vì cùng tài nguyên `driveItem` — và nó
            # dùng lại ĐÚNG parser ấy. Khác đúng một thứ: ổ chung thay vì ổ cá nhân.
            platform="sharepoint",
            objects={"file": "file", "folder": "folder"},
            # ⛔ Danh sách này phải khớp thứ `parse_onedrive_item` THẬT SỰ phát ra,
            # không phải thứ tôi đoán khi viết manifest. `owns` bị bỏ sót ở bản
            # nháp và cổng quan-sát-hành-vi bắt được — nó parse fixture thật rồi so,
            # nên một quan hệ phát qua biến cũng không trốn được.
            relations=("created", "edited", "owns", "child_of", "located_in"),
            referenced_kinds=("folder", "group", "person"),
        ),
        PlatformManifest(
            platform="intercom",
            objects={"message": "thread"},
            relations=("assigned_to", "associated_with"),
            referenced_kinds=("person", "group"),
        ),
        PlatformManifest(
            # Sự cố vận hành — kiểu tri thức đồ thị này chưa có trước đó.
            platform="pagerduty",
            objects={"issue": "issue"},
            relations=("assigned_to", "located_in"),
            referenced_kinds=("space", "person"),
        ),
        PlatformManifest(
            # Quyết định thiết kế: file là vật chứa, COMMENT mới là thứ đáng trích.
            platform="figma",
            objects={"file": "file", "message": "thread"},
            relations=("created", "located_in", "replies_to"),
            referenced_kinds=("space", "file", "thread", "person"),
        ),
        PlatformManifest(
            platform="linear",
            objects={"issue": "issue"},
            relations=("reported", "assigned_to", "located_in", "labeled",
                       "child_of", "member_of"),
            referenced_kinds=("space", "label", "issue"),
        ),
        PlatformManifest(
            platform="notion",
            objects={"page": "page"},
            relations=("created", "edited", "located_in", "child_of"),
            referenced_kinds=("space", "page"),
        ),
        PlatformManifest(
            platform="asana",
            objects={"issue": "issue"},
            relations=("reported", "assigned_to", "located_in", "labeled",
                       "child_of", "member_of"),
            referenced_kinds=("space", "label", "issue"),
        ),
        PlatformManifest(
            platform="confluence",
            objects={"page": "page"},
            relations=("created", "edited", "located_in", "child_of"),
            referenced_kinds=("space", "page"),
        ),
        PlatformManifest(
            platform="github",
            # Issue VÀ pull request cùng một loại: GitHub trả chúng qua cùng
            # endpoint, và tách ra làm câu "việc này bàn ở đâu" phải hỏi hai chỗ.
            # Kho dùng lại `space` của Notion/Jira — cùng hình dạng vật chứa.
            objects={"issue": "issue"},
            relations=("reported", "assigned_to", "located_in", "labeled", "member_of"),
            referenced_kinds=("space", "label"),
        ),
        PlatformManifest(
            platform="jira",
            objects={"issue": "issue"},
            relations=(
                "reported", "assigned_to", "created", "watching", "located_in", "child_of",
            ),
            referenced_kinds=("space", "issue"),
        ),
    )
}


def _entity_kind_index() -> dict[str, str]:
    """`object_type` → `kind`, gộp từ mọi manifest. Mâu thuẫn thì NÉM lúc import.

    Ánh xạ này là TOÀN CỤC chứ không theo nền tảng, vì `graph/apply.py` tra nó
    bằng `event.object_type` một mình. Hai nền tảng khai cùng một `object_type`
    sang hai `kind` khác nhau sẽ làm nền tảng nạp sau ghi đè nền tảng nạp trước —
    im lặng, và chỉ lộ ra khi NHÌN vào đồ thị đã xuất.
    """
    index: dict[str, str] = {}
    owner: dict[str, str] = {}
    for manifest in MANIFESTS.values():
        for object_type, kind in manifest.objects.items():
            previous = index.get(object_type)
            if previous is not None and previous != kind:
                raise ValueError(
                    f"object_type {object_type!r} ánh xạ mâu thuẫn: "
                    f"{owner[object_type]} nói {previous!r}, "
                    f"{manifest.platform} nói {kind!r}"
                )
            index[object_type] = kind
            owner.setdefault(object_type, manifest.platform)
    return index


ENTITY_KIND_BY_OBJECT_TYPE: Final[Mapping[str, str]] = _entity_kind_index()

ALL_PLATFORMS: Final[tuple[str, ...]] = tuple(sorted(MANIFESTS))

ALL_RELATIONS: Final[tuple[str, ...]] = tuple(
    sorted({r for m in MANIFESTS.values() for r in m.relations})
)

# Nền tảng có `poll`/`backfill` thật. `connectors/registry.py` phải khớp đúng tập
# này — `test_registry_covers_every_connected_platform` canh.
CONNECTED_PLATFORMS: Final[tuple[str, ...]] = tuple(
    sorted(p for p, m in MANIFESTS.items() if m.has_connector)
)


def entity_kind(object_type: str) -> str:
    """Loại entity cho một loại đối tượng nền tảng. Loại lạ thì NÉM, không đoán.

    Mặc định im lặng về `"file"` là thứ đã gây ra: mọi kênh Slack, trang Notion và
    issue Jira thành entity `file`, trong khi đường `referenced` tạo THÊM một
    entity thứ hai cùng khoá nhưng đúng loại. Không có gì đỏ — `file` cũng mang
    ACL nên nó sai NGHĨA chứ không rò.
    """
    try:
        return ENTITY_KIND_BY_OBJECT_TYPE[object_type]
    except KeyError:
        raise ValueError(
            f"chưa ánh xạ object_type {object_type!r} sang loại entity — "
            f"thêm nó vào `PlatformManifest.objects` của nền tảng tương ứng "
            f"trong `brain/platforms.py`"
        ) from None


def acl_bearing(kind: str) -> bool:
    """Loại này có mang ACL riêng không. Loại lạ ⇒ True (fail-closed).

    Hướng mặc định là chỗ đắt nhất trong file này. Một loại chưa khai mà mặc định
    `False` thì nó lọt cổng phân quyền im lặng; mặc định `True` thì nó vô hình cho
    tới khi có ai khai — thấy được, đếm được, và không rò.
    """
    spec = _KIND_BY_NAME.get(kind)
    return True if spec is None else spec.acl_bearing


# Vị từ phân quyền, DỰNG từ `KINDS` chứ không chép tay.
#
# ⚠️ Truy vấn dùng vị từ này BẮT BUỘC đặt bí danh cho `entities` là `e` và truyền
# đủ `tenant_id`, `principals`, `as_of`.
#
# ⛔ Dựng bằng `.replace()` trên một mẫu HẰNG, không bằng f-string — và đó không
# phải để làm vừa lòng linter. `retrieve/acl.py::with_acl` tồn tại chính vì f-string
# dựng SQL khiến linter phải đoán, và ở đây nó đoán mâu thuẫn: S608 cấm f-string
# dựng SQL, UP032 lại đòi đổi `.format()` thành f-string. Bản đầu của hàm này ghép
# bằng f-string, tức dựng lại đúng chỗ mà `with_acl` được viết ra để loại bỏ — một
# tầng cao hơn, nơi không ai nghĩ tới nó nữa.
#
# Danh sách chèn vào là hằng của chính repo, đã qua `_check_name` lúc dựng `KINDS`.
# Phép kiểm được LẶP LẠI ngay trước khi chèn: một hằng đi vào SQL phải được kiểm ở
# chỗ nó đi vào, không phải ở chỗ nó ra đời — hai chỗ ấy có thể xa nhau về sau.
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
