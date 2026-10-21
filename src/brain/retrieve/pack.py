"""Context Pack — hình dạng của lời hứa "một truy vấn".

Thứ tự các bước không đổi được, và lý do nằm ở bước 2:

    1. lan từ hạt giống
    2. CẮT QUYỀN            ← phải TRƯỚC bước 4
    3. gắn cờ độ tươi
    4. xếp vào ngân sách token

Cắt quyền SAU khi xếp ngân sách nghĩa là người dùng nhận về một gói đã bị cắt cụt
**đầy thứ họ không được xem** — vừa thiếu vừa rò, cùng lúc. Đo trên hệ multi-tenant
thật: retrieval không có cổng phân quyền rò dữ liệu chéo trong 98–100% số lần thử.

`gaps` và `stale` là cơ chế chống ảo giác chính của cả hệ, quan trọng hơn đồ thị.
Agent lặp tool vì nó **không biết mình thiếu gì**, nên nó dò dẫm. Khi gói nói thẳng
"tôi không có X, gọi tool Y", agent gọi MỘT lần có chủ đích thay vì sáu lần thăm dò.
Đó mới là chỗ token thật sự giảm — không phải ở chỗ nén dữ liệu.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Final

from psycopg import AsyncConnection

from brain.clock import require_utc
from brain.embed.encoder import Encoder
from brain.retrieve.acl import with_acl
from brain.retrieve.rerank import (
    Reranker,
    RerankPolicy,
    fetch_window,
    rerank_passages,
)
from brain.retrieve.seed import (
    Passage,
    PassageFate,
    QuestionLexicon,
    fetch_passages,
    question_grounded,
    unseen_terms,
)
from brain.tokens import estimate_tokens

log = logging.getLogger(__name__)

# Hạn tươi theo lớp. `volatile` không có ở P0 (Drive/Gmail không mang số liệu biến
# động) nhưng cột và trường đã sẵn, nên P2 cắm read-through probe vào mà không phải
# đổi hợp đồng với agent.
FRESHNESS_TTL: dict[str, timedelta] = {
    # ⛔ `immutable` KHÔNG phải "rất lâu mới hết hạn" — nó là *không có gì để đọc
    # lại*. Tiêu đề một thư đã gửi cho cùng giá trị mãi mãi, nên một cảnh báo quá
    # hạn về nó không bao giờ đúng, và một cảnh báo không bao giờ đúng là nhiễu
    # vĩnh viễn chứ không phải thận trọng.
    #
    # `timedelta.max` chứ không phải `None`: bảng này chỉ được ĐEM SO SÁNH, nên một
    # giá trị lớn vô lý diễn đạt đúng ý mà không bắt mọi chỗ dùng phải xử lý `None`
    # — và một `None` bị quên xử lý sẽ ném đúng ở tầng chống ảo giác.
    "immutable": timedelta.max,
    "structural": timedelta(days=7),
    "slow": timedelta(hours=24),
    "volatile": timedelta(seconds=0),
}

# Ước lượng token nay nằm ở `brain.tokens` và tính theo CHỮ VIẾT. Bản cũ ở đây
# dùng một hằng số `3.0 ký tự/token` cho mọi ngôn ngữ, và nó sai theo hai hướng
# ngược nhau cùng lúc: tiếng Trung thực tế ~1,5 (ước THẤP, gói tràn) còn tiếng Anh
# ~4,0 (ước CAO, gói phí chỗ). Nhập về một chỗ vì `chunk.py` cần đúng phép đếm đó
# để cắt khối — hai bộ đếm khác nhau nghĩa là khối vừa ngân sách của chunker lại
# không vừa ngân sách của gói.

# Phần ngân sách dành cho đoạn trích nguyên văn. Chúng là thứ DUY NHẤT trả lời được
# câu hỏi khái niệm — quan hệ và thuộc tính chỉ trả lời được câu hỏi về cấu trúc —
# nhưng chúng cũng tốn nhất, nên chia phần cứng thay vì để chúng nuốt cả gói.
# ⛔ MỘT nguồn cho ngân sách mặc định. Nó đứng ở BA chữ ký — `build_pack`,
# `brain_query`, `tool_brain_query` — và ba bản sao của một con số thì hai trong
# ba sẽ lệch, lặng lẽ, ở lần chỉnh tiếp theo.
#
# 3.000 chứ không phải 2.000, và con số đến từ phép đo chứ không từ cảm giác. Quét
# trên corpus Gmail + Drive thật, sáu câu thăm dò, mã đã merge:
#
#     ngân sách  đoạn  khớp  entity  fact   câu có ĐỦ BỐN MỤC   p50
#         2.000    20    16      39     6           4/6        21ms
#         2.500    28    22      40     8           6/6        18ms
#         3.000    31    25      59     8           6/6        20ms
#         4.000    43    35      81    12           6/6        19ms
#
# 2.500 là chỗ cả sáu câu có đủ bốn mục. 3.000 là chỗ **khối mang đáp án tới nơi**
# ở ca `B1` của bộ 14 ca truy vấn — ở 2.000 nó có ba đoạn, cả ba đều KHỚP, và câu
# trả lời là đoạn thứ tư. Đó là ca sạch nhất chứng minh 2.000 quá nhỏ: không phải
# xếp hạng sai, không phải chia ngân sách sai, chỉ là không đủ chỗ ngồi.
#
# Độ trễ PHẲNG suốt dải (18–21ms), nên nâng mặc định tốn token chứ không tốn thời
# gian — và một gói thiếu câu trả lời đáng giá 0 dù rẻ tới đâu.
#
# ⚠️ Đây là con số cho corpus NÀY, nơi một đoạn trích tốn ~200 token. Corpus có
# đoạn ngắn hơn cần ít hơn; đừng đọc nó thành hằng số của hệ.
DEFAULT_BUDGET_TOKENS = 3000

_PASSAGE_BUDGET_SHARE = 0.45

# ⛔ BỐN MỤC, BỐN SÀN. Ba mục có phần chia và mục thứ tư lấy phần còn lại là công
# thức chắc chắn bỏ đói hai mục cuối.
#
# Trật tự cũ: đoạn trích lấy 55%, `entities` lấy **toàn bộ phần còn lại**, rồi
# `relations` và `facts` chia nhau cái không còn. Entity rẻ (59 token/mục trên
# corpus thật) và đông, nên nó vét sạch.
#
# Đo 20/08 trên corpus Gmail+Drive thật, bốn câu thăm dò, ngân sách 2.000:
#
#     đoạn 10 · entity 83 · quan hệ **0** · fact **0**
#
# Và KHÔNG phải vì ngân sách chật. Quét tới 32.000 token: 200 entity, 316 quan
# hệ, **0 fact** — vì `relations` và `facts` rút từ CHUNG một `remaining` và
# `relations` đi trước. DB có 13.365 fact và **chưa fact nào từng vào một gói**.
# Đó là lỗi THỨ TỰ, không phải lỗi thiếu chỗ, nên nới ngân sách không chữa được.
#
# Chặn riêng `entities` cũng không đủ: quét lại với trần 0,25 cho entity thì
# `facts` VẪN 0, vì `relations` ăn nốt phần dư. Mọi mục phải có sàn.
#
# Phần chưa dùng của mục trước TRÀN sang mục sau, nên không chỗ nào bị bỏ phí —
# một gói không có đoạn trích nào vẫn dùng hết ngân sách cho bộ khung.
_SECTION_SHARES: tuple[tuple[str, float], ...] = (
    ("passages", _PASSAGE_BUDGET_SHARE),
    ("entities", 0.25),
    ("relations", 0.18),
    ("facts", 0.12),
)

# Đoạn trích trung bình 312 token trên corpus thật, nên `limit=12` mặc định của
# `fetch_passages` là ~3.700 token — không chạm ở ngân sách 2.000. Nhưng nó là
# TRẦN CỨNG: đo ở ngân sách 32.000, phần đoạn trích được ~14.900 token mà vẫn
# đúng 12 đoạn, vì `build_pack` chưa từng truyền `limit`. Suy trần từ ngân sách,
# lấy cận dưới rộng rãi cho giá mỗi đoạn để chính NGÂN SÁCH quyết chứ không phải
# con số này.
_MIN_PASSAGE_TOKENS = 50

# ⛔ Dưới ngưỡng này gói thu về ĐÚNG MỘT đoạn, và một đoạn không phải một tập ứng viên.
#
# Đoạn trích đầy đủ tốn ~173 token trong khi ngân sách chênh nhau 6 lần, nên phần
# đoạn trích ở ngân sách 3.000 giữ 7–8 đoạn còn ở 500 giữ **1** — nó không suy giảm
# dần mà sập. Ở đó gói thôi là "mấy ứng viên để agent đọc" và thành một phỏng đoán
# duy nhất, trong khi ba đoạn liên quan khác vẫn tồn tại và vẫn có chỗ nếu chúng
# ngắn hơn.
#
# 📌 Mâu thuẫn này vốn đã nằm sẵn trong code: `limit` ngay dưới đây tính theo
# `_MIN_PASSAGE_TOKENS = 50`, tức nó GIẢ ĐỊNH mỗi đoạn tốn ~50 token, rồi tầng dưới
# sinh ra đoạn 173. Hai con số nói hai chuyện khác nhau về cùng một thứ.
#
# Đo trên VM 22/08, ngân sách 500 (phần đoạn trích = 225 token): 2×60 vừa 1 đoạn ·
# 1×40 vừa 3 · 1×30 vừa 4 nhưng CẮT MẤT ba kim của `B2` — xem `HEADLINE_NARROW`.
# ⛔ Dưới ngân sách này gói ƯU TIÊN BỀ RỘNG hơn chiều sâu: nhiều đoạn ngắn thay vì ít
# đoạn dài. Đây là một LỰA CHỌN, không phải một phép tính sức chứa.
#
# Bản trước viết điều kiện thành `budget × 0,45 < 8 × 173` — trông như đang tính "đầy
# đủ có nhét nổi 8 đoạn không". Nó sai ở cả hai vế và review chỉ đúng chỗ:
#
#   · `_fit_budget` cấp cho đoạn KHỚP tới `_MATCHED_PASSAGE_CEILING` = **65%** phần
#     `available`, không phải 45% — nên phép tính so với sai con số ngay từ đầu;
#   · và đo được là ở chế độ đầy đủ `A2`, `A3`, `B2` vẫn giữ 8–10 đoạn, tức đúng cái
#     ngưỡng kia vừa tuyên bố là không thể.
#
# Lợi ích của chế độ hẹp KHÔNG đến từ một vách sức chứa. Quét trên bộ 22 ca, corpus
# thật:
#
#     ngưỡng cũ N=3    14/22        N=8   17/22
#     N=12             17/22        N=100 17/22   ← "luôn hẹp", GIỐNG HỆT N=8
#
# Dữ liệu không phân biệt được "ngưỡng 8" với "luôn hẹp", nên mọi biên trong vùng phẳng
# ấy là tương đương ĐO ĐƯỢC. Chọn một con số tròn, đọc được, thay vì để biên rơi ra từ
# tích của bốn hằng số — biên cũ nằm ở 3.076, tức lệch 76 token so với ngân sách mặc
# định, một chỗ không ai chọn và không ai thấy.
#
# ⚠️ VÁCH VẪN CÒN, và giờ nó ở chỗ nhìn thấy được. Cỡ gói KHÔNG đơn điệu theo ngân
# sách: đo với 20 tài liệu, ngân sách 3.070 cho **20 đoạn**, ngân sách 3.080 cho **14**
# — thêm 10 token mất 6 đoạn. Đó là bản chất của một hàm bậc thang, không phải lỗi;
# thứ sửa được là để nó ở một con số người ta cố ý đặt. `test_budget_aware_excerpt`
# ghim ngân sách mặc định nằm ở phía nào của biên.
#
# ⚠️ Vì sao chế độ đầy đủ KHÔNG bị bỏ hẳn dù `N=100` đo ngang: bộ gold hỏi "kim có
# TRONG gói không", không hỏi "đoạn trích có đủ ngữ cảnh để SUY LUẬN không". Một mảnh
# 40 từ có thể chứa con số mà không giải thích nổi nó. Giữ chế độ đầy đủ ở ngân sách
# rộng là chỗ phòng cho rủi ro đó — phán đoán, không phải phép đo.
_NARROW_BELOW_BUDGET = 6000

# ⛔ ĐOẠN TRÍCH KHỚP CHỮ được cấp chỗ TRƯỚC, không chia phần cùng bối cảnh.
#
# Bảng phần chia ở trên cấp ghế cho `facts` và `relations` đúng như nó hứa — và
# nó lấy ghế đó TỪ đoạn trích. Đo trên corpus thật, câu "what is my AWS account
# number", ngân sách mặc định 2.000, với cả bản vá tầng hạt giống:
#
#     chỉ #205        đoạn 3 (khớp 3)   → kim CÓ
#     #204 + #205     đoạn 3 (khớp 2)   → kim MẤT
#     #204 + #205     đoạn 6 ở 4.000    → kim CÓ
#
# Khối mang đáp án xếp khoảng hạng năm, vì câu hỏi dùng chữ `number` mà khối đáp
# án KHÔNG chứa — nó chỉ có `aws` và `account`. Những khối phủ cả ba từ xếp trên
# nó một cách chính đáng. Đó là trần của truy hồi theo chữ, và cách duy nhất để
# không đánh rơi đáp án là **mang đủ đoạn trích khớp**.
#
# Vì sao ưu tiên chúng chứ không nới đều: đoạn trích khớp chữ là thứ TRẢ LỜI câu
# hỏi; fact và quan hệ là bối cảnh. Một gói có sáu quan hệ mà thiếu câu trả lời
# thì đủ bốn mục và vô dụng. Cùng hình dạng với việc bỏ `label` khỏi ghế bảo đảm
# của quota — ghế phải thuộc về thứ trả lời được.
#
# Trần theo PHẦN, không theo số lượng. Bản bó-theo-số-lượng (giữ 4 đoạn khớp) đã
# được đo và BỎ: nó cho thành phần sáu câu y hệt nhưng mất kim ở ngân sách 2.500,
# vì trần theo phần kéo về được sáu đoạn ở đó còn bó theo số chỉ cho bốn. Ưu tiên không giới hạn thì một
# tài liệu dài nuốt cả gói và ta quay lại đúng chỗ `_fit_budget` xuất phát.
#
# ⚠️ Và nói rõ giới hạn của bản vá này, vì nó KHÔNG cứu được ca đã dẫn ở trên: đo
# lại sau khi ưu tiên, kim quay về ở ngân sách 2.500 nhưng vẫn mất ở 2.000. Lý do
# không phải chia ngân sách — ở 2.000, `limit` của `fetch_passages` (suy từ ngân
# sách) là 18, còn khối mang đáp án xếp NGOÀI 18 đoạn đầu. Không chính sách ngân
# sách nào mang về một đoạn chưa từng được nạp; chỗ đó là XẾP HẠNG đoạn trích, và
# nó là việc của vòng sau.
_MATCHED_PASSAGE_CEILING = 0.65

# ⛔ Trần cho phần ngân sách mà CẢNH BÁO được phép chiếm.
#
# `gaps` và `stale` được trừ ra trước mọi thứ khác, và trước bản vá này chúng
# được trừ KHÔNG GIỚI HẠN. Trên corpus tổng hợp điều đó vô hại — một gói có một
# hai cảnh báo. Trên Gmail + Drive thật thì `stale` bám theo số fact trong gói,
# và đo 20/08/2026 nó ra **276–294 cảnh báo, 3.081–3.284 token, trên ngân sách
# 2.000**: `available` về 0, và gói tới tay agent RỖNG — không entity, không đoạn
# trích, không quan hệ, chỉ còn danh sách cảnh báo về những fact không có ở đó.
#
# Vì sao nhiều đến thế: `FRESHNESS_TTL["slow"]` là 24 giờ và **13.357/13.357
# fact** của corpus đó mang lớp `slow` (`subject`, `snippet`, `mime_type`,
# `title`, `size_bytes`), với `observed_at` là lúc NẠP. Nên đúng 24 giờ sau mỗi
# lần backfill, 99,87% fact quá hạn CÙNG LÚC.
#
# Bó nó lại chứ không bỏ nó đi: cảnh báo là cơ chế chống ảo giác, nhưng thứ agent
# cần là BIẾT dữ liệu đã cũ, không phải đọc 294 dòng nói cùng một điều. Phần bị
# cắt hiện ra ở `truncated["stale"]`, mà `render()` đã in sẵn.
_WARNING_BUDGET_SHARE = 0.15

# Số entity mỗi LOẠI được bảo đảm một chỗ trong ngân sách. Giữ bằng
# `traverse.MIN_PER_KIND` là chủ đích: hai nhát cắt nối tiếp nhau, và chừa chỗ
# ở một bên rồi cắt ở bên kia là công vô ích — `traverse` đưa `folder` vào 200,
# `build_pack` xếp lại theo điểm, rồi `_fit_budget` cắt đúng cái vừa cứu.
# ⛔ LOẠI KHÔNG PHẢI BẰNG CHỨNG không được ăn chỗ bảo đảm của quota.
#
# `label` của Gmail là `INBOX`, `SENT`, `SPAM`, `UNREAD`, `DRAFT`, `IMPORTANT`,
# `CATEGORY_*` — corpus thật có ĐÚNG mười cái. Không câu hỏi nào của người dùng
# có đáp án là "INBOX", nhưng quota theo loại bảo đảm cho `label` bốn chỗ trong
# MỌI gói, và đo được là chúng chiếm ~18% danh sách entity.
#
# Chúng KHÔNG bị xoá khỏi đồ thị và vẫn lan được — một câu hỏi về thư trong
# `SPAM` vẫn đi qua cạnh đó. Thứ bị lấy lại chỉ là chỗ ngồi trong gói.
_NON_EVIDENCE_KINDS: frozenset[str] = frozenset({"label"})

_MIN_PER_KIND = 4

# Dải bảo đảm theo loại chỉ nhận entity tới được trong MỘT bước từ hạt giống, trừ
# khi entity đó có đoạn trích trong gói. Đo 18/09 trên 11 ca EN, corpus thật, làn
# chữ: 75 dòng entity vào gói nhờ ghế bảo đảm mà không mang bằng chứng nào (đoạn,
# fact hay quan hệ) — 45 trong đó ở depth 2 (16 file + 29 folder), điểm 0,01–0,03,
# tức là hàng xóm-của-hàng-xóm được cấp ghế chỉ vì loại của nó hiếm trong gói.
#
# Ngưỡng là 1, không phải 0: ca 18/08 trong docstring `_kind_quota_order` — person
# mang câu trả lời tới ở depth 1 qua `owns` và KHÔNG có đoạn nào (câu trả lời là
# chính tên nó). Ngưỡng 0 cắt đúng ca mà dải bảo đảm sinh ra để cứu.
_PROMOTION_MAX_DEPTH = 1


# ⛔ Gấp xuống dòng cho MỌI mục, không riêng đoạn trích — và CHỈ xuống dòng.
#
# Gói định dạng theo DÒNG: mọi thứ định danh một mục (nguồn, ngày, nhãn, `src ev:`)
# nằm ở ĐẦU dòng. Một giá trị có `\n` vì thế tách ra và mọi dòng sau dòng đầu thành
# khẳng định vô chủ. `FACTS` còn tệ hơn đoạn trích: hậu tố `(observed …, src ev:N)`
# hàn vào NỬA SAU, tức gán nguồn cho một câu nó không đến từ đó. Giá trị fact có
# `\n` thật — thân Slack thô, mô tả Jira ghép bằng `"\n"` theo cấu tạo, topic kênh.
#
# ⛔ CHỈ `\n` và `\r`. Bản trước dùng `" ".join(x.split())`, và nó nuốt luôn TAB —
# `extract/text.py` cố ý giữ đúng một tab, kèm chú thích gọi tên chính lỗi này:
# *"gộp nó vào dấu cách là xoá ranh giới cột — `Q3\t2,4 tỷ` thành `Q3 2,4 tỷ`, và
# model đọc lại không còn biết đâu là ô"*. Bảng docx/xlsx phát ô ngăn bằng tab.
#
# `split()` còn chuẩn hoá NBSP, NBSP hẹp, dấu cách biểu ý và U+2028/29 về dấu cách
# ASCII trong khi bỏ qua ZWSP — một phép chuẩn hoá nửa vời, dưới một tiêu đề hứa
# "trích NGUYÊN VĂN". Trích dẫn agent phát ra khi đó không còn tồn tại trong nguồn.
def _one_line(value: str) -> str:
    return value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


@dataclass(slots=True)
class Gap:
    """Thứ brain KHÔNG biết, kèm gợi ý lấy ở đâu."""

    about: str
    why: str
    suggest_tool: str | None = None


@dataclass(slots=True)
class StaleFact:
    entity_id: str
    predicate: str
    observed_at: datetime
    age_hours: float
    freshness_class: str


# ⛔ MỘT hàm sinh dòng cho mỗi mục, và cả hai bên cùng gọi nó.
#
# Trước bản này `render()` phát ra một chuỗi còn `_fit_budget` định giá một chuỗi
# KHÁC: nó cân `estimate_tokens(str(e))` — tức `str()` của dict, kèm khoá, dấu nháy,
# ngoặc — trong khi dòng thật là `- kind:name id=xxxxxxxx score=0.12`. Với `stale`
# thì tệ hơn: định giá `predicate + entity_id` thuần ASCII, còn render phát ra văn
# tiếng Việt, vốn tốn gấp ~2,1 lần cùng số ký tự.
#
# 📌 Hậu quả đo 28/08 trên 22 câu hỏi thật: `used_tokens` khai THIẾU **trung vị 43%**,
# và **20/22 gói giao VƯỢT ngân sách** — `E2-en-budget` xin 500 nhận về 842. Cùng con
# số sai ấy đi vào `query_log.pack_tokens`, nên không chỗ nào hạ nguồn nhìn thấy, và
# một bộ đo token đọc nó (`bench/platform_floor.py`) đã báo −51,5% cho thứ thật ra là
# −39,8%.
#
# Ngân sách là HỢP ĐỒNG với người gọi. Định giá bằng đúng chuỗi sẽ phát ra là cách
# duy nhất giữ nó, và gộp vào một hàm là cách duy nhất để nó không lệch lại lần nữa.
# Nhãn gắn vào đoạn KHÔNG mang bằng chứng. Một hằng số vì nó xuất hiện ở ba chỗ —
# hai chỗ render và một chỗ định giá — và ba bản sao thì lệch được.
_UNMATCHED_TAG: Final = " (does not match the question; context only)"


def _entity_line(e: dict[str, Any]) -> str:
    return (
        f"- {e['kind']}:{_one_line(str(e['name']))} "
        f"id={e['id'][:8]} score={e['score']:.2f}"
    )


def _fact_line(f: dict[str, Any]) -> str:
    return (
        f"- {_one_line(str(f['entity_name']))} · {f['predicate']} = "
        f"{_one_line(str(f['value']))} "
        f"(observed {f['observed_at']:%Y-%m-%d}, src ev:{f['event_id']})"
    )


def _relation_line(r: dict[str, Any]) -> str:
    return (
        f"- {_one_line(str(r['src_name']))} --{r['relation']}--> "
        f"{_one_line(str(r['dst_name']))} "
        f"(since {r['valid_from']:%Y-%m-%d})"
    )


def _stale_line(s: StaleFact) -> str:
    return f"- {s.predicate} of {s.entity_id[:8]}: last read {s.age_hours:.0f}h ago"


@dataclass(slots=True)
class ContextPack:
    as_of: datetime
    principal: str
    budget_tokens: int
    used_tokens: int = 0
    entities: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    stale: list[StaleFact] = field(default_factory=list)
    truncated: dict[str, int] = field(default_factory=dict)
    # ⛔ MỌI entity mà gói ĐƯA RA — không render, đây là thứ `query_access_log` ghi.
    #
    # Không phải `entities`: dòng `ENTITIES` chỉ là một trong bốn chỗ một entity lộ ra.
    # Đoạn trích mang tên và chữ nguyên văn của tài liệu, dòng `FACTS` mang tên entity,
    # dòng `RELATIONS` mang tên hai đầu — và `_fit_budget` giữ đoạn trích TRƯỚC rồi mới cắt
    # danh sách entity, nên một tài liệu được trích vẫn có thể không có dòng `ENTITIES`.
    # Đo 27/09/2026 trên corpus thật, 22 ca, ngân sách mặc định: 10/21 gói, 42/170 tài
    # liệu được trích mà nhật ký không ghi. Tính SAU `_fit_budget`, từ đúng những gì còn lại.
    shown_entity_ids: list[str] = field(default_factory=list)

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
        if self.facts:
            lines.append("## FACTS")
            lines += [_fact_line(f) for f in self.facts]
        if self.relations:
            lines.append("## RELATIONS")
            lines += [_relation_line(r) for r in self.relations]
        if self.passages:
            # Đoạn trích đứng SAU quan hệ và thuộc tính vì chúng dài nhất, và một
            # người đọc lướt từ trên xuống sẽ thấy bộ khung trước rồi mới tới bằng
            # chứng. Mỗi đoạn mang tên tài liệu và số thứ tự khối, nên trích dẫn
            # nào cũng lần ngược về đúng chỗ được.
            lines.append("## PASSAGES — verbatim excerpts, each with a source")
            for p in self.passages:
                where = f"{p.entity_name}#{p.ordinal}"
                # Đoạn KHÔNG khớp chữ được đánh dấu tại chỗ. Trộn lẫn nó với đoạn
                # khớp là đưa ra ngữ cảnh nền dưới hình dạng bằng chứng.
                tag = "" if p.matched else _UNMATCHED_TAG
                # ⛔ Tuổi tài liệu đi cùng đoạn trích, cùng chỗ với `(url)`.
                #
                # `FACTS` in `(observed …)` và `RELATIONS` in `(since …)`; đoạn trích là
                # mục DUY NHẤT của gói không mang thời gian. Trên ca gold `h13-newest`
                # gói giao hai bản chính sách mâu thuẫn — 90 ngày và 30 ngày — cả hai
                # mang nhãn bằng chứng và KHÔNG gì phân biệt, nên model trích bản cũ.
                # Rò 1/1 ở cả năm lượt đo 23/08.
                #
                # NGÀY tuyệt đối, không phải "N ngày trước": dòng đầu gói đã có `as_of`
                # nên người đọc tự trừ được, và một mốc tuyệt đối không đổi nghĩa khi
                # gói được đọc lại ở thời điểm khác.
                # ⛔ ÉP UTC trước khi định dạng, và nhãn tiếng Anh TRUNG TÍNH.
                #
                # `modified_at_source` là `timestamptz`; psycopg trả nó theo `TimeZone`
                # của phiên, và không chỗ nào trong `db.py`, compose, conftest hay CI
                # ghim biến đó. Hai tài liệu cách nhau 6 giờ qua nửa đêm UTC vì thế in
                # ra CÙNG một ngày dưới `Asia/Ho_Chi_Minh` — đúng phép phân biệt mà
                # tính năng này sinh ra để cung cấp, biến mất không một lỗi nào. Dựng
                # lại được: `PGTZ=America/New_York pytest tests/test_passage_time.py`
                # cho 2 đỏ trước bản vá này. Và dòng đầu gói in `as_of=…+00:00`, nên
                # một nhãn theo giờ địa phương còn mâu thuẫn với chính tiêu đề.
                #
                # `dated`, không phải `sửa`/`edited`: chỉ Drive cho mốc SỬA
                # (`modifiedTime`). Gmail cho `internalDate` — lúc NHẬN — và Slack cho
                # `ts`/`created` — lúc ĐĂNG. Gọi tất cả là "đã sửa" là dán một cái tên
                # sai lên ~87% corpus. Tiếng Anh vì đây là chữ MODEL đọc, theo
                # `claude.md`; chú thích thì vẫn tiếng Việt.
                when = (
                    f" (dated {require_utc(p.modified_at):%Y-%m-%d})"
                    if p.modified_at
                    else ""
                )
                # ⛔ MỘT dòng cho một đoạn — gấp xuống-dòng lại, đừng để chúng nguyên.
                #
                # Docstring của hàm này nói gói định dạng "theo DÒNG", và cả ba thứ
                # định danh một đoạn — nguồn `[tên#ordinal]`, ngày sửa, nhãn "chỉ là
                # ngữ cảnh" — nằm ở ĐẦU dòng. Một đoạn trích có `\n` vì thế tách làm
                # nhiều dòng, và mọi dòng sau dòng đầu mất sạch ba thứ đó.
                #
                # Không phải ca hiếm: thư và tài liệu thật đầy xuống dòng. Đo 23/08
                # trên `h13-newest`, đúng chỗ ngày lẽ ra phải cứu:
                #
                #   - [Willow (bản cũ)#0] (sửa 2025-03-19) hợp bản cấp tác lược… biên.
                #   Chính sách Willow cho phép tối đa 30 ngày lưu trữ.
                #
                # Câu MANG ĐÁP ÁN đứng thành một dòng trần — không nguồn, không ngày,
                # không nhãn. Model đọc nó như một khẳng định vô chủ, nên in ngày ở
                # dòng trên không cứu được gì. Rò 1/1 vẫn nguyên sau khi thêm ngày.
                flat = _one_line(p.excerpt)
                lines.append(
                    f"- [{where}]{tag}{when} {flat}"
                    + (f" ({p.url})" if p.url else "")
                )
        if self.stale:
            lines.append("## STALE — past its freshness window; do NOT state it as current")
            lines += [_stale_line(s) for s in self.stale]
        if self.gaps:
            lines.append("## GAPS — the brain does NOT have these")
            lines += [
                # `about` có thể là chuỗi câu hỏi THÔ của người gọi MCP
                # (`about=question or …`), nên một câu hỏi nhiều dòng làm vỡ đúng
                # mục mà `_fit_budget` từ chối cắt.
                f"- {_one_line(g.about)}: {_one_line(g.why)}"
                + (f" → use {g.suggest_tool}" if g.suggest_tool else "")
                for g in self.gaps
            ]
        if self.truncated:
            lines.append(
                "## TRUNCATED " + ", ".join(f"{k}={v}" for k, v in self.truncated.items())
            )
        return "\n".join(lines)


# Cắt quyền. Vị từ này chạy TRƯỚC mọi bước xếp ngân sách — xem docstring đầu file.
# Bản thân vị từ nằm ở `retrieve/acl.py` vì đường chọn hạt giống cũng dùng nó, và
# hai bản sao của một luật bảo mật sẽ trôi khỏi nhau.
_VISIBLE = with_acl("""
SELECT e.id, e.kind, e.canonical_name, e.object_id,
       -- ⛔ `modified_at_source`, KHÔNG `observed_at`. Hai cột trả lời hai câu hỏi
       -- khác nhau: cột này là "nền tảng ghi nhận nó đổi lúc nào", còn
       -- `observed_at` là "ta ĐỌC nó lúc nào". Lấy nhầm cột thì một lượt backfill
       -- làm CẢ KHO trở thành "mới hôm nay" — câu trả lời sai mà nghe rất chắc
       -- chắn, đúng thứ `bench.gate` đo.
       --
       -- `LEFT JOIN`, nên entity KHÔNG mang object (`person`, `folder`, `label`)
       -- về với `at_source = NULL` và đi qua cửa sổ thời gian vô điều kiện. Một
       -- `JOIN` thường sẽ lặng lẽ vứt chúng, và chúng chính là thứ giữ cho câu
       -- "ai làm việc này" trả lời được.
       coalesce(o.modified_at_source, o.created_at_source) AS at_source
  FROM entities e
  LEFT JOIN objects o ON o.id = e.object_id AND o.tenant_id = e.tenant_id
 WHERE e.tenant_id = %(tenant_id)s
   AND e.id = ANY(%(entity_ids)s::uuid[])
   AND {acl}
""")

_FACTS = """
SELECT f.entity_id, e.canonical_name, f.predicate,
       coalesce(f.value_text, f.value_num::text, f.value_ts::text) AS value,
       f.observed_at, f.freshness_class, f.event_id
  FROM facts f
  JOIN entities e ON e.id = f.entity_id
 WHERE f.tenant_id = %(tenant_id)s
   AND f.entity_id = ANY(%(entity_ids)s::uuid[])
   AND f.valid_from <= %(as_of)s
   AND (f.valid_to   IS NULL OR f.valid_to   > %(as_of)s)
   AND (f.expired_at IS NULL OR f.expired_at > %(as_of)s)
-- Khoá phụ để thứ tự TOÀN PHẦN. Một lượt ingest theo lô cho mọi fact CÙNG một
-- `observed_at`, nên chỉ xếp theo nó thì các dòng hoà nhau về theo thứ tự tuỳ ý —
-- rồi bị `_fit_budget` cắt đuôi, tức phần agent được đọc là ngẫu nhiên.
 ORDER BY f.observed_at DESC, f.entity_id, f.predicate, f.event_id
"""

_RELATIONS = """
SELECT s.canonical_name AS src_name, d.canonical_name AS dst_name,
       ed.relation, ed.valid_from, ed.event_id,
       -- Dùng để xếp theo THỨ HẠNG entity, rồi đi theo dòng tới `shown_entity_ids`
       -- (nhật ký truy cập). `render()` không đọc chúng.
       ed.src_entity_id, ed.dst_entity_id
  FROM edges ed
  JOIN entities s ON s.id = ed.src_entity_id
  JOIN entities d ON d.id = ed.dst_entity_id
 WHERE ed.tenant_id = %(tenant_id)s
   AND ed.src_entity_id = ANY(%(entity_ids)s::uuid[])
   AND ed.dst_entity_id = ANY(%(entity_ids)s::uuid[])
   AND ed.valid_from <= %(as_of)s
   AND (ed.valid_to   IS NULL OR ed.valid_to   > %(as_of)s)
   AND (ed.expired_at IS NULL OR ed.expired_at > %(as_of)s)
-- Câu này TRƯỚC ĐÂY KHÔNG CÓ `ORDER BY` NÀO, trong khi `_fit_budget` cắt đuôi
-- `pack.relations` theo ngân sách token. Tức quan hệ nào tới được agent do thứ tự
-- dòng của Postgres quyết định — hai lần chạy giống hệt nhau cho hai tập khác nhau.
-- Thứ tự cuối cùng vẫn do `_rank_key` quyết (theo thứ hạng entity); phần này chỉ
-- bảo đảm đầu vào của nó tất định, vì `sorted` của Python ổn định nên nó thừa
-- hưởng thứ tự đầu vào ở mọi chỗ hoà.
 ORDER BY ed.valid_from DESC, ed.src_entity_id, ed.dst_entity_id, ed.relation
"""

def _doc_lexeme(w: str) -> str:
    """Lexeme để ĐỌC, không phải lexeme để so.

    `to_tsvector('simple', 'INV-8842190')` tách thành `inv` và `-8842190`, nên gap của
    ca gold `D4` gọi tên `-8842190` — một mảnh vỡ, không phải thứ người hỏi vừa gõ.
    Cắt dấu câu chỉ ở chỗ HIỂN THỊ; phép so vẫn dùng lexeme nguyên vẹn.
    """
    return w.strip("-_.,;:/\\") or w




def _rank_facts(
    rows: list[Any], *, question: str, rank_pos: dict[str, int], unranked: int
) -> list[Any]:
    """Xếp fact cho `_fit_budget` cắt từ đuôi. Ba khoá, và khoá đầu là mới.

    ⛔ **Khoá 1 — predicate mà CÂU HỎI gọi tên đi trước tất cả.** Đo 2026-09-06 trên
    corpus GH Archive thật, ca `gha-01` (*"What is holding up the post-layout
    verification re-run?"*, bộ vàng đòi `blocked_by`): fact ấy có trong đồ thị, entity
    của nó hạng 5/28 trong chính gói đó, đoạn văn của nó cũng trong gói — mà nó đứng
    **vị trí 13/59**, cộng dồn tới nó **911 token**, trong khi mục `facts` nhận về
    **259**. Nó bị bỏ cùng 56 fact khác.

    Hai cách sửa hiển nhiên đều KHÔNG cứu được, và cả hai đã đo chứ không đoán:

      · cấp thêm ngân sách — 12 fact đứng trước tốn 855 token, nên kể cả trọn phần
        chia 360 vẫn không với tới vị trí 13;
      · xếp theo `_question_overlap` — nó đếm từ chung, và `blocked_by`, `constraint`,
        `proposed` ở ca này **đều bằng 1**. Một tín hiệu không phân biệt được ba ứng
        viên thì không sắp xếp được chúng.

    Thứ phân biệt chúng là `predicate`, mà không khoá nào nhìn tới. Xem
    `retrieve/predicates.py`.

    ⛔ **Khoá 2 giữ NGUYÊN vai trò cũ.** Thứ tự theo hạng entity có lý do đã đo và ghi
    ngay dưới đây: fact về từ SQL theo `observed_at DESC`, nên không xếp lại thì fact
    của entity hạng nhất bị bỏ bất cứ khi nào nó cũ hơn fact của một entity hạng bét.
    Bản vá này không đụng vào đó — nó chỉ nói rằng một fact ĐÚNG LOẠI ĐƯỢC HỎI thì
    đứng trước, và mọi thứ khác xếp sau y như trước.

    ⚠️ Câu hỏi không gọi tên predicate nào là ca THƯỜNG GẶP, và khi ấy khoá 1 hoà cho
    mọi hàng nên thứ tự giống hệt bản cũ từng bit. Đó là điều làm bản vá này an toàn:
    nó không đổi hành vi ở nơi nó không hiểu gì.
    """
    from brain.retrieve.predicates import asked_predicates

    asked = asked_predicates(question)
    return sorted(
        rows,
        key=lambda r: (
            0 if str(r.get("predicate") or "") in asked else 1,
            rank_pos.get(str(r["entity_id"]), unranked),
            -_question_overlap(str(r.get("value") or ""), question),
        ),
    )


def _question_overlap(value: str, question: str) -> int:
    """Số từ mà giá trị fact chia sẻ với câu hỏi. CHỈ để phá hoà.

    Cắt theo khoảng trắng, nên với tiếng Việt đây là mức ÂM TIẾT chứ không phải mức
    từ — cùng giới hạn mà `retrieve/seed.py` đã ghi. Ở đây điều đó chấp nhận được vì
    kết quả không xếp hạng gì cả: nó chọn giữa những fact ĐÃ hoà nhau trên mọi khoá
    khác, và bất cứ tín hiệu nào cũng tốt hơn thứ tự bảng chữ cái của `predicate`.

    Bỏ từ ≤2 ký tự: "và", "là", "ở" có mặt khắp nơi và làm mọi fact hoà nhau lần nữa.
    """
    if not question:
        return 0
    want = {w for w in question.lower().split() if len(w) > 2}
    return sum(1 for w in set(value.lower().split()) if w in want)


def _within_window(
    visible: dict[str, dict[str, Any]], *, since: datetime, pack: ContextPack
) -> dict[str, dict[str, Any]]:
    """Cắt theo cửa sổ người hỏi nêu — và KHÔNG BAO GIỜ trả về rỗng vì nó.

    ⛔ **Cửa sổ cắt rỗng thì giữ lại và DÁN NHÃN, không trả gói rỗng.** Hai kiểu
    sai, và chúng không ngang giá nhau:

      · lọc rồi im   → người hỏi "tuần này" nhận tài liệu ba tháng trước và không
                       có cách nào biết. Đây là hành vi TRƯỚC bản này.
      · lọc rồi rỗng → người hỏi "tuần này" nhận *"không có gì"* trong khi kho có
                       tài liệu đúng chủ đề ở tuần thứ chín. Cũng sai, và còn tệ
                       hơn ở chỗ nó không để lại đường nào đi tiếp.

    Luật của hệ này là *brain được phép KHÔNG biết; nó không được phép nghe chắc
    chắn khi không biết* — nên câu trả lời đúng là **đưa cả hai và nói rõ cái nào
    nằm ngoài cửa sổ**. `Gap` là cơ chế có sẵn cho đúng việc ấy.

    ⚠️ `at_source IS NULL` ĐI QUA vô điều kiện. Đó là `person`, `folder`, `label` —
    entity không mang object, nên không có mốc nào để so. Cắt chúng theo thời gian
    là cắt đúng thứ trả lời "ai làm việc này".
    """
    fresh = {k: v for k, v in visible.items()
             if v.get("at_source") is None or v["at_source"] >= since}
    older = len(visible) - len(fresh)
    if not older:
        return visible

    has_object = any(v.get("at_source") is not None for v in fresh.values())
    if not has_object:
        # Cửa sổ quét sạch mọi tài liệu. Giữ nguyên tập, nói ra rằng nó nằm ngoài.
        pack.gaps.append(Gap(
            about=f"nothing modified since {since:%Y-%m-%d}",
            why=(f"the {older} objects below are older than the window you asked "
                 "about; answer from them only if that is acceptable"),
            suggest_tool="widen the time range, or search the platform directly",
        ))
        return visible

    pack.gaps.append(Gap(
        about=f"{older} related objects",
        why=f"not modified since {since:%Y-%m-%d}, outside the window you asked about",
    ))
    return fresh


async def build_pack(
    conn: AsyncConnection[dict[str, Any]],
    *,
    tenant_id: str,
    principals: list[str],
    reached: list[Any],
    as_of: datetime,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    question: str = "",
    # ⛔ **Cửa sổ thời gian người hỏi NÊU RA, và trước bản này nó bị vứt.**
    #
    # `intent.parse_intent` đọc "hôm nay / tuần này / tuần trước / tháng này" thành
    # `Intent.since` từ lâu — và đo 22/09 bằng một lượt grep cả `src/brain`, trường
    # ấy xuất hiện đúng HAI lần: dòng khai và dòng gán. Không ai đọc nó.
    #
    # Hệ quả đo được: hỏi *"có gì mới hôm nay"* được trả lời bằng khớp từ/ngữ nghĩa
    # trên TOÀN kho. Một tài liệu vào graph sau 0,35 giây và một tài liệu vào sau 16
    # giây cho CÙNG một câu trả lời — nên toàn bộ đường realtime (#109–#112) không
    # hiện ra ở đâu trong câu trả lời. Đó là "nghe chắc chắn về một câu hỏi khác",
    # đúng chỉ số `bench.gate` tồn tại để đo.
    #
    # `None` ⇒ câu hỏi không nêu thời gian ⇒ không lọc gì, y như trước.
    since: datetime | None = None,
    encoder: Encoder | None = None,
    # Vector câu hỏi mã hoá sẵn — chuyển thẳng xuống `fetch_passages(question_vector=)`.
    # `brain_query` mã hoá một lần cho cả tầng hạt lẫn tầng này; thiếu thì
    # `fetch_passages` tự mã hoá bằng `encoder` như trước.
    question_vector: str | None = None,
    # Từ vựng câu hỏi đã đếm sẵn — chuyển thẳng xuống `fetch_passages(lexicon=)`, và
    # `total` của nó xuống `unseen_terms(corpus_size=)`. Cùng hợp đồng với
    # `question_vector`: `brain_query` đếm một lần cho cả tầng hạt lẫn tầng này; thiếu
    # thì hai hàm kia tự đếm như trước.
    lexicon: QuestionLexicon | None = None,
    # ⛔ TRUYỀN VÀO, không tự đi lấy — cùng khuôn với `encoder` ngay trên.
    #
    # Bản đầu gọi `get_reranker()` ngay trong thân hàm, và `get_reranker()` gọi
    # `get_settings()`. `build_pack` được hàng chục test gọi THẲNG, không qua
    # `brain_query`, nên chúng không có `DATABASE_URL` hay `BRAIN_TENANT_ID` trong
    # môi trường — và `Settings()` là bắt buộc-có-đủ. Kết quả: **54 bài đỏ trên CI**
    # với `ValidationError`, trong khi máy tôi xanh vì đã có sẵn env.
    #
    # Đây không phải lỗi môi trường CI. Đó là `build_pack` bỗng đòi một thứ nó
    # chưa từng đòi, và cái đòi ấy chỉ để trả lời "lớp rerank có bật không" — câu
    # hỏi mà chỗ gọi biết trước.
    reranker: Reranker | None = None,
    # Lớp rerank chạm tới gói THẾ NÀO (sâu bao nhiêu, hoà điểm ra sao, lấy về bao
    # nhiêu). Cùng hợp đồng truyền-vào như `reranker`, và vì cùng lý do. `None` =
    # `RerankPolicy()` = cấu hình `bench/rerank_matrix` chọn ra 05/09; vô nghĩa khi
    # `reranker` là `None`.
    rerank_policy: RerankPolicy | None = None,
    # Chuyển thẳng xuống `fetch_passages(trace=)`; xem `PassageFate`. Gói trả về
    # không đổi giữa bật và tắt.
    fetch_trace: list[PassageFate] | None = None,
    # Hạt giống theo ĐÚNG thứ tự `select_seeds` trả về (hạng RRF), cùng danh sách đã
    # đưa cho `traverse(seed_ids=)`. Chỉ là KHOÁ HOÀ cho `ranked` bên dưới — xem chú
    # thích ở đó. Mặc định rỗng ⇒ mọi hạt hoà nhau như trước, không call site nào
    # phải đổi; `brain_query` và các bench mô phỏng pipeline truyền vào.
    seed_ids: Sequence[str] = (),
) -> ContextPack:
    """Dựng gói từ kết quả lan.

    `principals` rỗng ⇒ gói RỖNG, không phải gói đầy. Kiểu hỏng nguy hiểm nhất của
    một cổng phân quyền không phải chặn nhầm — mà là một nhánh nào đó coi "không có
    danh tính" là "không cần lọc".
    """
    as_of = require_utc(as_of)
    pack = ContextPack(as_of=as_of, principal=principals[0] if principals else "", budget_tokens=budget_tokens)

    if not principals:
        pack.gaps.append(
            Gap(about="caller identity", why="no principal — returning empty, fail-closed")
        )
        return pack

    if not reached:
        pack.gaps.append(
            Gap(
                about=question or "this question",
                why="no entity in the graph matched it",
                suggest_tool="search the platform directly",
            )
        )
        return pack

    score_by_id = {r.entity_id: r.score for r in reached}
    depth_by_id = {r.entity_id: r.depth for r in reached}
    # ⛔ Tên `candidate_args` là một phần của bản vá, không phải chuyện thẩm mỹ.
    #
    # Dict này mang danh sách entity CHƯA cắt quyền — đúng thứ `_FACTS` và
    # `_RELATIONS` không bao giờ được nhận. Cả hai truy vấn ấy KHÔNG mang `{acl}`;
    # chúng an toàn nhờ người gọi truyền đúng danh sách. Trước bản này cả hai dict
    # đều tên `…args` và mang cùng khoá `entity_ids`, nên đổi cái đúng thành cái sai
    # là sửa MỘT TỪ — và cả 1.521 test vẫn xanh khi đã đổi. Xem
    # `tests/test_pack_does_not_leak_hidden_facts.py`.
    #
    # Sau bước cắt quyền, `candidate_args` không được dùng lại. Nó vẫn tồn tại vì
    # `_VISIBLE` cần chính danh sách ứng viên ấy — đó là đầu vào của phép cắt.
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
        pack.gaps.append(
            Gap(
                about=f"{hidden} related objects",
                why="outside your permissions",
            )
        )

    if since is not None:
        visible = _within_window(visible, since=since, pack=pack)

    if not visible:
        return pack

    visible_ids = list(visible)
    # ⛔ Khoá phụ `i` không phải để cho đẹp — thiếu nó, xếp hạng này KHÔNG TOÀN PHẦN.
    #
    # Điểm bằng nhau là ca thường: mọi file trong cùng thư mục, cùng quan hệ, cùng
    # `valid_from` đều có ĐÚNG một điểm. `sorted` của Python ổn định, nên khi hoà
    # nó giữ nguyên thứ tự đầu vào — mà thứ tự đầu vào là thứ tự DÒNG của truy vấn
    # `_VISIBLE`, thứ Postgres không hứa hẹn gì. Kết quả: cùng một câu hỏi có thể
    # cho hai gói khác nhau, và phần bị ngân sách token cắt đi là ngẫu nhiên.
    #
    # `traverse` mang đúng khiếm khuyết này ở `ORDER BY` của nó và đã được vá cùng
    # lúc. Vá một chỗ thôi thì không đủ: chỗ này xếp lại từ đầu, nên nó ghi đè
    # thứ tự kia. Bắt được bởi `tests/test_traverse_cap.py`, khi trần chật và trần
    # rộng trả về hai tập lệch hẳn nhau thay vì tập con.
    # ⛔ Ba khoá, và khoá GIỮA là khoá hoà của `traverse`. Chỗ này xếp LẠI TỪ ĐẦU,
    # nên bỏ nó ở đây là xoá sạch bản vá kia — đúng cái bẫy `traverse.__doc__`
    # điều kiện (1) ghi: "Thiếu một trong hai là đủ hỏng, vì chỗ sau xếp lại từ
    # đầu." `False` sắp trước `True` trong Python, nên `not from_named` đưa entity
    # tới từ tài liệu người hỏi GỌI TÊN lên trước ở mọi chỗ hoà điểm.
    #
    # ⛔ Khoá thứ BA là HẠNG RRF của hạt giống, và nó chỉ lên tiếng ở chỗ khoá hai
    # còn hoà. `traverse` đưa mọi hạt vào cùng 1,0 (1,25 nếu được gọi tên), nên 25
    # hạt hoà điểm và trước đây UUID quyết hạt nào đứng đầu gói và hạt nào bị nhát
    # cắt ngân sách bỏ — đo 17/09 trên 11 ca EN: ca G2 có hai vàng là hạt hạng RRF 1
    # và 5 mà cả hai vắng khỏi 20 dòng in ra. Hạng RRF là thông tin `select_seeds`
    # đã tính và bị vứt ở đúng chỗ này. Không phải hạt (depth >= 1) xếp SAU mọi hạt
    # khi hoà — hiếm, vì điểm hop đã nhân trọng số quan hệ và suy giảm tuổi.
    #
    # 📌 Đây là bản đo LẠI của một đòn bẩy đã thử 19/08 (`traverse.py`, "dùng làm
    # khoá hoà": 31/35 → 30/35, h06 4→0). Lần đó khoá cài ở CẢ `ORDER BY` của
    # `traverse` — tức quyết trần 200 → tập lan → đoạn — lẫn ở đây; lần này
    # `traverse` không đổi, khoá chỉ xếp thứ tự IN và nhát cắt của danh sách entity,
    # và haystack bật/tắt cùng tiến trình là cổng cho đúng rủi ro ấy.
    #
    # ⛔ Và khoá này phải SỐNG QUA hai nhát xếp phía sau — `_kind_quota_order` và
    # `_fit_budget` — vốn xếp lại bằng `(-score, id)` và xoá cả khoá `named` lẫn khoá
    # này. Cả hai nay xếp theo VỊ TRÍ trong `ranked` thay vì tự dựng lại khoá.
    named = {r.entity_id for r in reached if getattr(r, "from_named", False)}
    seed_rank = {eid: n for n, eid in enumerate(seed_ids)}
    unseeded = len(seed_rank)
    ranked = sorted(
        visible_ids,
        key=lambda i: (-score_by_id[i], i not in named, seed_rank.get(i, unseeded), i),
    )

    # `depth` không được in (`_entity_line` chỉ đọc kind/name/id/score) và không ra
    # dây — MCP trả `render()`. Nó ở đây cho `_kind_quota_order`: dải bảo đảm theo
    # loại cần biết entity tới từ đâu, và `reached` không còn trong tay `_fit_budget`.
    pack.entities = [
        {
            "id": eid,
            "kind": visible[eid]["kind"],
            "name": visible[eid]["canonical_name"],
            "score": score_by_id[eid],
            "depth": depth_by_id[eid],
        }
        for eid in ranked
    ]

    # ⛔ Fact và quan hệ phải xếp theo THỨ HẠNG ENTITY, không theo độ mới.
    #
    # `_fit_budget` cắt đuôi và tự mô tả là "cắt từ đuôi danh sách đã xếp hạng" —
    # câu đó chỉ đúng với `pack.entities`. Fact về từ SQL theo `observed_at DESC`,
    # nên một fact của entity hạng nhất bị bỏ bất cứ khi nào nó cũ hơn fact của một
    # entity hạng bét. Xếp lại ở đây làm cho lời hứa kia thành thật.
    rank_pos = {eid: n for n, eid in enumerate(ranked)}
    unranked = len(rank_pos)  # entity không có trong `ranked` xuống cuối, không lên đầu

    # Dựng MỚI thay vì `{**candidate_args, …}`: trải rồi ghi đè vẫn để cái sai làm
    # mặc định, và một khoá bị gõ nhầm sẽ lặng lẽ giữ danh sách chưa lọc.
    visible_args = {
        "tenant_id": tenant_id,
        "entity_ids": visible_ids,
        "principals": principals,
        "as_of": as_of,
    }
    cur = await conn.execute(_FACTS, visible_args)
    rows = [dict(r) for r in await cur.fetchall()]
    # `sorted` ổn định, nên thứ tự SQL (đã toàn phần) quyết định mọi chỗ hoà — và
    # trong một entity, khoá cuối cùng của SQL là `f.predicate`, tức THỨ TỰ CHỮ CÁI.
    #
    # ⛔ Với fact phong bì điều đó gần như vô hại: `title`, `mime_type`, `size_bytes`
    # đều là metadata, và cái nào sống sót qua `_fit_budget` cũng gần như nhau. Với
    # fact SUY DIỄN thì không: một trong số chúng LÀ câu trả lời.
    #
    # Đo 28/08, chạy P1 thật trên corpus demo bằng model cục bộ. Câu hỏi *"nếu đợt
    # nâng cấp hạ tầng trượt tiếp thì điều gì xảy ra?"*, và trên cùng một entity
    # (`drive:plan`) có hai fact suy diễn cùng `observed_at`:
    #
    #     blocked_by      "đợt nâng cấp hạ tầng đang chậm hai tuần"
    #     consequence_if  "nếu nó trượt tiếp, mốc bàn giao phải lùi sang giữa tháng 10"
    #
    # `blocked_by` < `consequence_if` theo bảng chữ cái, nên nó vào gói và câu trả
    # lời bị cắt. Không gì đỏ: gói có 8 fact, trông đầy đặn, và thiếu đúng một cái.
    #
    # Nên trong mỗi entity, fact CHIA SẺ TỪ với câu hỏi đi trước. Đây là phá hoà,
    # không phải hệ xếp hạng: nó chỉ quyết định thứ tự giữa những fact vốn đã hoà
    # nhau, và mọi khoá cũ vẫn giữ nguyên vai trò của chúng phía sau.
    rows = _rank_facts(rows, question=question, rank_pos=rank_pos, unranked=unranked)

    # ── Bước 3: độ tươi ────────────────────────────────────────────────────
    for row in rows:
        ttl = FRESHNESS_TTL.get(row["freshness_class"], FRESHNESS_TTL["slow"])
        age = as_of - row["observed_at"]
        if age > ttl:
            pack.stale.append(
                StaleFact(
                    entity_id=str(row["entity_id"]),
                    predicate=row["predicate"],
                    observed_at=row["observed_at"],
                    age_hours=age.total_seconds() / 3600.0,
                    freshness_class=row["freshness_class"],
                )
            )
        pack.facts.append(
            {
                # Không render (`_fact_line` đọc theo tên khoá) — để `shown_entity_ids`
                # biết dòng này đã đưa TÊN của entity nào ra ngoài.
                "entity_id": str(row["entity_id"]),
                "entity_name": row["canonical_name"],
                "predicate": row["predicate"],
                "value": row["value"],
                "observed_at": row["observed_at"],
                "event_id": row["event_id"],
            }
        )

    cur = await conn.execute(_RELATIONS, visible_args)
    rel_rows = [dict(r) for r in await cur.fetchall()]
    # Hạng của một quan hệ = hạng của đầu mút CAO NHẤT: một cạnh chạm tới entity
    # hạng nhất đáng giữ, dù đầu kia là gì.
    rel_rows.sort(key=lambda r: min(
        rank_pos.get(str(r["src_entity_id"]), unranked),
        rank_pos.get(str(r["dst_entity_id"]), unranked),
    ))
    # Hai id ĐI THEO dòng qua `_fit_budget` (nhát cắt theo vị trí), dạng chuỗi: dòng
    # quan hệ in TÊN cả hai đầu, nên cả hai là entity gói đã đưa ra — `shown_entity_ids`
    # đọc chúng. `render()` đọc theo tên khoá nên không thấy hai khoá này.
    pack.relations = [
        {**r, "src_entity_id": str(r["src_entity_id"]), "dst_entity_id": str(r["dst_entity_id"])}
        for r in rel_rows
    ]

    # Đoạn trích nguyên văn. Chỉ lấy cho các entity ĐÃ qua cổng phân quyền, xếp
    # theo thứ hạng của chúng — `ranked` giữ đúng thứ tự đó.
    _passage_limit = max(
        12, int(budget_tokens * _PASSAGE_BUDGET_SHARE / _MIN_PASSAGE_TOKENS)
    )
    # Cửa sổ lấy về có thể RỘNG HƠN cửa sổ của gói khi lớp rerank bật và đòi thế
    # (`RerankPolicy.candidates`): đoạn nào có mặt trong gói là do thứ tự truy hồi
    # quyết ở tầng này, và một khối đúng nằm ở hạng 30–80 thì cross-encoder chưa
    # bao giờ được nhìn nó. `_fit_budget` vẫn cắt phần dôi như trước.
    _policy = rerank_policy or RerankPolicy()
    _fetch_limit = fetch_window(_passage_limit, reranker, _policy)
    _fetch_stats: dict[str, int] = {}
    pack.passages = await fetch_passages(
        conn,
        tenant_id=tenant_id,
        question=question,
        entity_ids=ranked,
        principals=principals,
        as_of=as_of,
        encoder=encoder,
        question_vector=question_vector,
        lexicon=lexicon,
        limit=_fetch_limit,
        narrow=budget_tokens < _NARROW_BELOW_BUDGET,
        stats=_fetch_stats,
        trace=fetch_trace,
    )

    # ⛔ Lớp xếp hạng lại — mặc định TẮT, xem `retrieve/rerank.py` cho phép đo.
    #
    # ⛔ Và nó đọc KHỐI ĐẦY ĐỦ, không đọc `p.excerpt`. Đo 02/09, cùng model cùng bộ ca:
    #
    #     không rerank                       nDCG@10 0,575
    #     rerank trên KHỐI đầy đủ                    0,632
    #     rerank trên ĐOẠN TRÍCH (~250 ký tự)        0,512   ⛔ TỆ HƠN không rerank
    #
    # `ts_headline` cắt quanh chỗ khớp và bỏ phần còn lại; cross-encoder chấm cặp
    # trên thứ nó ĐỌC ĐƯỢC, nên cho nó một mảnh là bắt nó phán trên chứng cứ đã bị
    # cắt. Một truy vấn nữa cho ≤`depth` hàng là cái giá của việc chấm đúng thứ.
    #
    # ⛔ Và khi chữ đầy đủ của một đoạn KHÔNG lấy được, câu trả lời là không rerank
    # câu này — không phải lặng lẽ chấm `p.excerpt` thay vào, tức rơi về đúng cái
    # 0,512 vừa đo là tệ hơn không rerank. Bản đầu làm thế qua `_text.get(…, p.excerpt)`.
    # Một khối biến mất giữa hai truy vấn là chuyện của tầng nạp; ở đây nó chỉ được
    # ghi log, không được đổi thành một phép đo sai.
    #
    # ⛔ Không có câu hỏi thì không rerank: cặp `("", khối)` vẫn ra điểm, nhưng điểm
    # ấy là tiên nghiệm của model về khối chứ không phải độ liên quan — sắp theo nó
    # là xáo ngẫu nhiên tám dòng đầu. Bản đầu truyền `question or ""` và chấm luôn.
    if reranker is not None and question and len(pack.passages) > 1:
        _depth = _policy.depth if _policy.depth > 0 else len(pack.passages)
        _head = pack.passages[:_depth]
        _rows = await (
            await conn.execute(
                _RERANK_TEXT,
                {"tenant_id": tenant_id,
                 "principals": principals,
                 "as_of": as_of,
                 "eids": [p.entity_id for p in _head],
                 "ords": [p.ordinal for p in _head]},
            )
        ).fetchall()
        _text = {(r["eid"], r["ordinal"]): r["text"] for r in _rows}
        _missing = [p for p in _head if (str(p.entity_id), p.ordinal) not in _text]
        if _missing:
            log.warning(
                "rerank: thiếu chữ đầy đủ cho %d/%d đoạn — giữ nguyên thứ tự truy hồi",
                len(_missing), len(_head),
            )
        else:
            # `prepend_title`: chủ đề mail / tên tệp lên đầu vế tài liệu. Với Gmail
            # chủ đề nằm ở subject, và khối thân bài thường không nhắc lại nó.
            _texts: list[str | None] = [
                (f"{p.entity_name}\n" if _policy.prepend_title else "")
                + _text[(str(p.entity_id), p.ordinal)]
                for p in _head
            ]
            # `to_thread`: một lượt cross-encoder là 400–3.000 ms CPU thuần, và
            # `build_pack` chạy trong event loop của FastAPI — chấm ngay trên loop là
            # dừng MỌI truy vấn khác đang chờ trong ngần ấy thời gian. (`encoder.encode`
            # trong `seed.py` vẫn chặn loop theo cùng cách; đó là món nợ riêng, chưa
            # trả ở đây.)
            pack.passages = await asyncio.to_thread(
                rerank_passages,
                pack.passages,
                question,
                reranker,
                texts=_texts,
                depth=_depth,
                fusion=_policy.fusion,
            )

    # ⛔ Nhát cắt ở tầng TRUY HỒI cũng phải được khai, không chỉ nhát cắt ngân sách.
    #
    # `limit` chặn số đoạn lấy về, và `unmatched_limit` bỏ phần còn lại trong im lặng;
    # chỉ phép cắt ngân sách mới ghi `truncated["passages"]`. Trước bản vá này một gói
    # cắt cụt không mang khẳng định nào về độ mới, nên sự im lặng ấy vô hại. Giờ mỗi
    # dòng sống sót đều mang một ngày TỰ TIN — nên một gói chỉ còn khối Willow CŨ sẽ
    # in `(dated 2025-04-08)` và không gì khác, và model đọc nó như THE tài liệu trong
    # khi `truncated` lẫn `gaps` đều rỗng.
    #
    # Hai nhát cắt, hai khoá riêng: `passages_fetch` khi chạm trần `limit`,
    # `passages_unmatched` cho phần `unmatched_limit` bỏ. Cái sau đi qua `stats` —
    # một tham số TUỲ CHỌN — thay vì đổi kiểu trả về của `fetch_passages`, thứ sẽ
    # chạm mọi chỗ gọi.
    if len(pack.passages) == _fetch_limit:
        pack.truncated["passages_fetch"] = _fetch_limit
    if _fetch_stats.get("passages_unmatched_dropped"):
        pack.truncated["passages_unmatched"] = _fetch_stats[
            "passages_unmatched_dropped"
        ]
    # ⛔ Câu hỏi GỌI TÊN một thứ corpus chưa từng thấy ⇒ không đoạn nào là bằng
    # chứng cho nó.
    #
    # `MATCH_FLOOR` không bắt được ca này và không thể bắt được: nó là ngưỡng trên
    # `ts_rank_cd`, còn truy vấn toàn văn vẫn khớp bằng những chữ CÒN LẠI của câu hỏi.
    # Hỏi "what is my Northwind Traders account number" thì `account` và `number`
    # kéo về những đoạn ghi điểm thừa ngưỡng, và gói trả bằng chứng cho một hãng
    # không tồn tại. Đo 22/08: rò 2 đoạn ở `D3`, 7 đoạn ở `D4`.
    #
    # Đoạn trích KHÔNG bị xoá — chúng vẫn là ngữ cảnh, và hạn ngạch không-khớp đã
    # bó số lượng. Thứ bị lấy đi là NHÃN BẰNG CHỨNG, vì đó mới là thứ nói với agent
    # rằng nó được phép nói chắc.
    unseen = (
        await unseen_terms(
            conn,
            tenant_id=tenant_id,
            question=question,
            principals=principals,
            as_of=as_of,
            corpus_size=None if lexicon is None else lexicon.total,
        )
        if question
        else []
    )

    # ⛔ `not unseen` là bắt buộc. Khi phép gỡ nhãn ở cuối hàm chạy, điều kiện này
    # thành ĐÚNG vô điều kiện và gap thứ hai khẳng định SAI SỰ THẬT: những đoạn ấy CÓ
    # khớp chữ — đó chính là lý do chúng được lấy về. Hai gap còn gợi ý hai công cụ
    # mâu thuẫn nhau, và `_fit_budget` không bao giờ cắt gap nên cả hai bị tính vào
    # ngân sách mãi mãi.
    # ⛔ `any(p.matched)` một mình là ĐỦ ĐIỀU KIỆN SAI cho "gói này có bằng chứng".
    #
    # `matched` chấm trên `ts_rank_cd` của các lexeme RỜI, và với ngôn ngữ mà
    # `to_tsvector('simple', …)` cắt theo ÂM TIẾT thì một âm tiết trùng nhau là đủ
    # vượt ngưỡng. Đo 26/08, `q05` hỏi doanh thu một chi nhánh KHÔNG TỒN TẠI: đúng
    # một đoạn được gắn `matched`, và nó khớp qua `chi` của "chi phí" — một TỪ KHÁC.
    # Một cờ dương tính giả bịt cả gap, nên gói im lặng và agent trả lời chắc nịch
    # về một chi nhánh không có thật. Đó là ca `silent_wrong` của cổng P0.
    #
    # `question_grounded` hỏi câu khác và hỏi ở mức TỪ: corpus đọc được có chứa CỤM
    # nào của câu hỏi không. Trên corpus demo nó bắt 5/8 ca phải-từ-chối với **0/18
    # bắt nhầm** — số đo và cả ba phương án đã bỏ nằm ở `_GROUNDED`.
    #
    # ⛔ Là HOẶC, không phải thay thế. `not any(p.matched)` vẫn phải đứng nguyên: ca
    # `q15` không có đoạn nào khớp NHƯNG vẫn bám cụm từ, và nếu để `grounded` quyết
    # một mình thì gap của nó biến mất, biến một ca đã cảnh báo thành `silent_wrong`.
    # ⛔ Và nó LƯỜI: chỉ hỏi khi câu trả lời còn đổi được điều gì. Không đoạn nào
    # khớp thì gap đã nổ, `unseen` có gì thì nhánh dưới đã lo — trả tiền cho một
    # vòng Postgres nữa ở hai ca đó là trả cho một phán quyết không ai đọc.
    has_match = any(p.matched for p in pack.passages)
    grounded = True
    if question and not unseen and has_match:
        grounded = await question_grounded(
            conn,
            tenant_id=tenant_id,
            question=question,
            principals=principals,
            as_of=as_of,
        )

    if question and not unseen and (not has_match or not grounded):
        # Nói ra khi không có bằng chứng nguyên văn NÀO KHỚP. Hai ca khác nhau, cùng
        # một hậu quả: hoặc chưa lập chỉ mục nội dung, hoặc có nội dung nhưng không
        # chỗ nào nhắc tới thứ được hỏi. Im lặng thì cả hai trông y hệt một gói đầy
        # đủ, và agent trả lời câu hỏi khái niệm bằng bộ khung — tức là đoán.
        pack.gaps.append(
            Gap(
                about="document content",
                why=(
                    "no passage has been indexed for these objects"
                    if not pack.passages
                    else "there is content, but NO phrase from the question appears "
                    "in it — the passages below share only scattered words"
                    # ⛔ Chỉ ca MỚI mới đổi lời. "Không đoạn nào khớp" là một phán
                    # quyết KHÁC và đã có bài kiểm giữ chỗ: nó phân biệt "có nội
                    # dung mà không khớp" với "chưa lập chỉ mục", và nuốt nó vào
                    # đây là bỏ mất một phân biệt người đọc gói cần.
                    if not grounded and has_match
                    else "there is content, but NO passage matches the question"
                ),
                suggest_tool="read the document on the platform",
            )
        )

    # ── Bước 4: ngân sách ──────────────────────────────────────────────────
    _fit_budget(pack)

    # ⛔ Gỡ nhãn bằng chứng SAU khi cắt ngân sách, không phải trước.
    #
    # `_fit_budget` cấp cho đoạn KHỚP tới `_MATCHED_PASSAGE_CEILING` (65%) phần
    # `available`, và nó đọc chính cờ `matched` này. Gỡ trước thì `matched_cost` đứng
    # ở 0, trần 65% sụp về phần chia thường ~45%, và ở ngân sách chật entity/quan hệ/
    # fact đuổi được những đoạn lẽ ra sống — tác dụng phụ không ai khai của một tính
    # năng tự nhận phạm vi chỉ là "bỏ một cái nhãn".
    #
    # ⚠️ GIỚI HẠN CÓ ĐO, nói ra vì tôi KHÔNG vá được ở đây: phép gỡ là TOÀN GÓI. Câu
    # ghép hai vế — một vế đáp được, một vế gọi tên thứ không tồn tại — mất nhãn ở CẢ
    # đoạn trả lời đúng. Ba luật thu hẹp đã dựng và ĐO, không luật nào tách được ca
    # ghép khỏi `D3`/`D4` trên corpus thật:
    #
    #     luật                          D3       D4       ca ghép
    #     df phần "đã thấy" < 50        ✓ gỡ    ✗ giữ    ✓ giữ
    #     đếm unseen ≥ đếm seen hiếm    ✓ gỡ    ✓ gỡ     ✗ gỡ nhầm
    #     cover > 0                     6/12    11/12    11/12   ← không tách
    #
    # Chiều sai của nó là chiều AN TOÀN: agent mất quyền nói chắc chứ không nhận một
    # khẳng định sai. Ghi ra thay vì bịa ngưỡng thứ tư cho khớp ba điểm dữ liệu.
    # ⛔ KHÔNG BÁM CỤM cũng phải gỡ nhãn, y như `unseen`. Thêm gap mà để nguyên cờ
    # `matched` là dựng ra một gói TỰ MÂU THUẪN: nó vừa nói "không cụm từ nào của câu
    # hỏi xuất hiện ở đây" vừa trình một đoạn dưới nhãn bằng chứng, và agent đọc cái
    # nhãn chứ không đọc lời phân trần.
    #
    # 📌 Đo 27/08 trên `q05` khi mới có gap mà chưa gỡ nhãn: gói ra ĐÚNG như thế —
    # 1 gap, và một đoạn `matched=True` (cover 0,166) khớp qua đúng âm tiết `chi` của
    # "chi phí". Cổng P0 xanh vì `must_mention: ['GAPS']` đã thoả, trong khi thứ sinh
    # ra ca `silent_wrong` — cái nhãn — vẫn còn nguyên. Cổng xanh mà bệnh chưa khỏi.
    if unseen or not grounded:
        # `Passage` bất biến có chủ đích — nó mang chữ nguyên văn của khách ra ngoài,
        # nên không chỗ nào được sửa nó tại chỗ. Dựng lại thay vì gán.
        pack.passages = [replace(x, matched=False) for x in pack.passages]

    if unseen:
        pack.gaps.append(
            Gap(
                about=", ".join(_doc_lexeme(w) for w in sorted(unseen)[:5]),
                why=(
                    "câu hỏi nhắc tới thứ KHÔNG có ở đâu trong dữ liệu đã nạp — "
                    "những đoạn dưới đây chỉ chia sẻ từ ngữ, không trả lời được"
                ),
                suggest_tool="tìm trực tiếp trên nền tảng",
            )
        )
    # ⚠️ `used_tokens` là tổng của những mục ĐÃ ĐỊNH GIÁ, và nó thiếu hai thứ —
    # nói ra thay vì để người sau phát hiện:
    #
    #   khung   `# CONTEXT`, các dòng `## …`, dòng `## TRUNCATED` — đo được ~90 token
    #   sửa sau `_fit_budget` gỡ nhãn `matched` và nối gap SAU khi con số đã chốt
    #
    # ⛔ Đã thử đặt `used_tokens = estimate_tokens(render())` để nó đo hiện vật, và
    # BỎ. Lý do không phải vì ý ấy sai mà vì CÂY THƯỚC không cộng tuyến tính:
    # `estimate_tokens` có luật toàn-văn-bản (`_DIACRITIC_TEXT_RATIO = 0.02`) tính
    # MỌI ký tự Latin theo giá tiếng Việt khi đoạn có ≥2% dấu. Trên gói TRỘN VI+EN
    # thì tổng-từng-dòng = 3.007 còn đo-cả-chuỗi = 5.143 với **cùng một nội dung**.
    #
    # 📌 Đối chiếu tokenizer thật (`tokenizers` + moss-minilm, nhớ `no_truncation()`):
    # `estimate_tokens` lệch **0,60×–1,56×**, và ước THẤP 40% trên gói tiếng Anh. Nên
    # phần thiếu ~4% ở đây nằm SÂU DƯỚI nhiễu của chính thước đo, và đổi cách tính để
    # đuổi theo nó là làm cho con số tự nhất quán chứ không làm cho nó đúng.
    #
    # Việc phải làm trước là một bộ đếm token đáng tin. Cho tới lúc đó, mọi kết luận
    # về token — kể cả mục tiêu −50% của §10.2 — đứng trên cát.
    pack.shown_entity_ids = _shown_entity_ids(pack)
    return pack


def _shown_entity_ids(pack: ContextPack) -> list[str]:
    """Mọi entity mà gói ĐÃ ĐƯA RA, không trùng, theo thứ tự các mục.

    Bốn chỗ một entity lộ ra: dòng `ENTITIES`, đoạn trích (tên + chữ nguyên văn), dòng
    `FACTS` (tên), dòng `RELATIONS` (tên hai đầu). Gọi SAU `_fit_budget`, nên chỉ những gì
    còn lại sau các nhát cắt mới được tính — thứ bị cắt chưa từng rời hệ.
    """
    shown: dict[str, None] = {}
    for entity_id in (
        *(str(e["id"]) for e in pack.entities),
        *(p.entity_id for p in pack.passages),
        *(str(f["entity_id"]) for f in pack.facts),
        *(str(end) for r in pack.relations for end in (r["src_entity_id"], r["dst_entity_id"])),
    ):
        shown.setdefault(entity_id, None)
    return list(shown)


def _kind_quota_order(
    entities: list[dict[str, Any]], *, evidenced: AbstractSet[str] = frozenset()
) -> list[dict[str, Any]]:
    """Xếp lại để nhát cắt ngân sách chừa chỗ cho mỗi LOẠI, không chỉ cắt đuôi.

    ⛔ Nhát cắt cũ trung thực về mặt nó tự mô tả — "cắt từ đuôi danh sách đã xếp
    hạng" — và đó chính là vấn đề: nó mù về loại. Đo 18/08 trên đống rơm 305 tài
    liệu, câu *"tài liệu này do ai sở hữu"*: entity `person` mang đúng câu trả
    lời TỚI được đồ thị (score 0,8780, depth 1, via `owns`) và xếp **hạng 34
    trên 200**, trong khi ngân sách 2.000 token chứa **23** entity. Gói về tới
    agent đầy đủ về mọi mặt trừ đúng mặt được hỏi.

    Nâng ngân sách chữa được ca đó và không chữa được gì thêm: quét cùng ngày,
    `arrived` đi 1 → 2 (ở 3.000) → 3 (ở 4.000) rồi ĐỨNG YÊN suốt tới 16.000, với
    số entity phình từ 22 lên 165. Tức tiền mua được hai câu, rồi không mua được
    gì nữa. Chừa chỗ mua đúng hai câu đó với 0 token thêm.

    `entities` vào đây ĐÃ xếp theo điểm, nên `by_kind[kind][i]` là cái tốt thứ
    `i` của loại đó. Dải bảo đảm được xếp lại theo THỨ TỰ ĐẦU VÀO — tức theo điểm,
    rồi theo đúng các khoá hoà của `ranked` (được gọi tên, hạng RRF) — để phần đầu
    gói vẫn là phần liên quan nhất; đuôi giữ nguyên thứ tự cũ. Bản trước xếp lại
    bằng `(-score, id)`, tức dựng lại khoá từ đầu và xoá hai khoá hoà kia.

    ⛔ Ghế bảo đảm chỉ dành cho entity ở depth ≤ `_PROMOTION_MAX_DEPTH`, hoặc có
    đoạn trích trong gói (`evidenced` — tập entity còn đoạn SAU nhát cắt đoạn, nên
    nó là tập cuối cùng chứ không phải danh sách trước khi cắt). Cổng này chạm
    PROMOTE, không chạm tư cách thành viên: entity không đủ điều kiện vẫn nằm ở
    đuôi đúng vị trí điểm của nó và vào gói nếu ngân sách còn — câu trả lời đi qua
    `owns` không có đoạn nào, và một cổng trên danh sách sẽ cắt đúng nó.

    Dict không có `depth` (người gọi cũ, bộ kiểm dựng tay) được coi là depth 0.
    `by_kind` vẫn đếm MỌI loại — kể cả loại không còn ai đủ điều kiện — để lối tắt
    "một loại ⇒ đồng nhất" giữ nguyên nghĩa cũ.
    """
    position = {entity["id"]: n for n, entity in enumerate(entities)}
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        if entity["kind"] in _NON_EVIDENCE_KINDS:
            continue
        by_kind.setdefault(entity["kind"], []).append(entity)
    # Một loại duy nhất ⇒ dải bảo đảm là chính tiền tố theo điểm ⇒ phép đồng nhất.
    if len(by_kind) < 2:
        return entities

    def _eligible(entity: dict[str, Any]) -> bool:
        return (
            entity.get("depth", 0) <= _PROMOTION_MAX_DEPTH or entity["id"] in evidenced
        )

    head: list[dict[str, Any]] = []
    promoted: set[str] = set()
    for rows in by_kind.values():
        seats = [e for e in rows if _eligible(e)][:_MIN_PER_KIND]
        head.extend(seats)
        promoted.update(e["id"] for e in seats)
    head.sort(key=lambda e: position[e["id"]])
    return head + [e for e in entities if e["id"] not in promoted]


# Chữ ĐẦY ĐỦ của các khối sắp được rerank. `JOIN unnest` chứ không `= ANY(SELECT
# (a,b))` — Postgres không so hàng theo cách đó và trả "subquery has too few
# columns", một thông báo không trỏ vào chỗ sai.
#
# ⛔ Có `{acl}`, dù mọi cặp `(entity, ordinal)` vào đây đều vừa qua cổng của
# `_PASSAGES`. Đây là câu đọc CHỮ NGUYÊN VĂN, và luật của `fetch_passages` áp y
# nguyên: hàm đưa chữ ra ngoài thì tự kiểm, không tin danh sách id được truyền vào.
# Một chỗ gọi tương lai truyền nhầm cặp id từ đường khác sẽ đưa chữ ngoài quyền
# vào cross-encoder — không rò ra gói, nhưng điểm rerank sẽ mang dấu vết của nó.
# Bản đầu thiếu vị từ này; một đồng nghiệp review chỉ ra.
_RERANK_TEXT = with_acl("""
SELECT ch.entity_id::text AS eid, ch.ordinal, ch.text
  FROM chunks ch
  JOIN entities e ON e.id = ch.entity_id
  JOIN unnest(%(eids)s::uuid[], %(ords)s::int[]) AS t(eid, ord)
    ON ch.entity_id = t.eid AND ch.ordinal = t.ord
 WHERE ch.tenant_id = %(tenant_id)s
   AND {acl}
""")


def _fit_budget(pack: ContextPack) -> None:
    """Cắt cho vừa ngân sách, nhưng LUÔN giữ `gaps` và `stale`.

    Chúng là phần nhỏ nhất của gói và là phần duy nhất ngăn agent nói chắc về thứ
    nó không biết. Cắt chúng để nhét thêm dữ liệu là đổi thứ chống ảo giác lấy thứ
    gây ảo giác.
    """
    # `gaps` giữ nguyên: chúng nói brain KHÔNG BIẾT thứ gì, mỗi gói chỉ có một
    # vài, và mất chúng là mất đúng thứ ngăn agent nói chắc về khoảng trống.
    # ⚠️ KHUNG không được dự trù, và đó là lựa chọn có đo.
    #
    # `render()` phát ra `# CONTEXT`, một dòng `## …` cho mỗi mục, và dòng
    # `## TRUNCATED` — đo được **90 token** trên một gói thật, không nằm trong phép
    # định giá nào. Trừ chúng ra trước thì hợp đồng ngân sách chặt hơn; nhưng đã thử
    # và BỎ, vì ở ngân sách nhỏ khung ăn gần hết phần chia: ở 600 token gói chỉ dùng
    # 251, ở 80 token thì không còn chỗ cho một đoạn trích nào.
    #
    # Siết hợp đồng bằng cách bỏ đói nội dung là đổi chất lượng lấy sự gọn gàng của
    # một con số. `used_tokens` ở cuối `build_pack` giờ đo HIỆN VẬT, nên phần vượt
    # không còn bị giấu — nó hiện ra và trở thành một quyết định CHÍNH SÁCH, đo được,
    # thay vì một sai số không ai thấy.
    gap_used = estimate_tokens("\n".join(f"{g.about}{g.why}" for g in pack.gaps))

    # `stale` thì bám theo số fact, nên nó phải có trần — xem `_WARNING_BUDGET_SHARE`.
    stale_allowance = max(0, int(pack.budget_tokens * _WARNING_BUDGET_SHARE) - gap_used)
    kept_stale: list[StaleFact] = []
    stale_used = 0
    for warning in pack.stale:
        cost = estimate_tokens(_stale_line(warning))
        # ⛔ Dòng ĐẦU đi qua vô điều kiện. Bó danh sách là đúng; im lặng hẳn thì
        # không — agent sẽ đọc dữ liệu cũ mà tưởng là mới, tức đổi thứ chống ảo
        # giác lấy thứ gây ảo giác, đúng điều docstring của hàm này cấm. Với ngân
        # sách nhỏ (`test_passages_never_evict_gaps_or_stale` chạy ở 150 token)
        # phần chia 15% không đủ cho một dòng nào, và không có ngoại lệ này thì
        # `stale` về rỗng trong khi `truncated["stale"]` nói đã có thứ bị cắt.
        if kept_stale and stale_used + cost > stale_allowance:
            pack.truncated["stale"] = len(pack.stale) - len(kept_stale)
            break
        kept_stale.append(warning)
        stale_used += cost
    pack.stale = kept_stale

    reserved = gap_used + stale_used
    available = max(0, pack.budget_tokens - reserved)

    # Đoạn trích lấy phần của mình TRƯỚC, và chỉ phần của mình. Để chúng cạnh
    # tranh tự do với quan hệ thì một tài liệu dài đẩy hết bộ khung ra ngoài; để
    # chúng xếp sau thì bộ khung — vốn nhiều dòng, mỗi dòng rẻ — nuốt hết chỗ và
    # gói không còn câu chữ nào. Cả hai đều hỏng, chỉ khác hướng.
    _tail = {
        name: sum(sh for _, sh in _SECTION_SHARES[idx:])
        for idx, (name, _) in enumerate(_SECTION_SHARES)
    }
    _shares = dict(_SECTION_SHARES)
    pool = available

    def _cap(name: str) -> int:
        """Phần của mục này trong những gì CÒN LẠI, cộng mọi thứ các mục sau không cần.

        ⛔ Vế đầu phải chia theo TỈ LỆ trên `pool`, không phải kẹp sàn bằng
        `min(pool, …)`. Bản trước làm thế và biến phép chia thành kẻ-đứng-trước-
        lấy-hết: khi đoạn khớp được phép vượt phần của nó, `pool` cạn dần, rồi
        `relations` — đứng ngay trước `facts` — nuốt trọn phần bị bóp vì
        `min(pool, floor)` cho nó lấy hết những gì còn, và `facts` nhận
        `min(0, …) = 0`.
        #
        # Đo trên corpus thật bởi bộ 14 ca: `fact = 0` ở MỌI ngân sách tới
        # **16.000**, trong khi `relations` được 3–16 chỗ. Tức bản vá "cấp sàn cho
        # bốn mục" đã tự phục hồi đúng cái lỗi nó sinh ra để chữa, dưới một cái
        # tên khác.
        #
        # Chia theo tỉ lệ thì khi thiếu, MỌI mục còn lại co cùng nhau; khi thừa,
        # phần thừa vẫn tràn về trước như cũ.
        """
        tail = _tail[name]
        if tail <= 0:
            return max(0, pool)
        fair = int(pool * _shares[name] / tail)
        surplus = max(0, pool - int(available * tail))
        return max(0, min(pool, fair + surplus))

    def _take(
        items: list[Any], cap: int, cost: Callable[[Any], int]
    ) -> tuple[list[Any], int]:
        kept: list[Any] = []
        used = 0
        for item in items:
            price = cost(item)
            # ⛔ Một món RIÊNG NÓ đã quá phần chia thì bỏ NÓ và xét tiếp — không dừng.
            #
            # Món ấy không vừa ở bất kỳ vị trí nào, nên bỏ nó không đổi thứ hạng của
            # gì cả; còn dừng ở nó là bỏ luôn mọi món vừa vặn đứng sau. Đo 2026-09-02
            # trên corpus GH Archive: một fact ~640 token (cả thân issue, ghi bởi
            # parser P0 trước khi có `Envelope.prose`) xếp đầu mục `facts` vì nó chia
            # sẻ nhiều từ nhất với câu hỏi, mục nhận 360 token, và gói ra 28 entity,
            # 24 quan hệ, **0 fact** — bộ vàng `ĐẬU 0/5`. Hai lớp trên ngăn văn xuôi
            # đi vào; lớp này ngăn một món quá cỡ từ BẤT KỲ nguồn nào tắt cả mục.
            #
            # Chỉ món quá cỡ so với CẢ phần chia mới bị nhảy qua. Món không vừa phần
            # CÒN LẠI vẫn dừng vòng như cũ, để thứ bị cắt vẫn là đuôi danh sách đã
            # xếp hạng chứ không phải một món ngắn hơn chen lên trước.
            if price > cap:
                continue
            if used + price > cap:
                break
            kept.append(item)
            used += price
        return kept, used

    def _passage_cost(x: Any) -> int:
        # ⛔ Tính CẢ phần vỏ dòng, không riêng chữ.
        #
        # Bản trước chỉ cộng `excerpt` + `entity_name`, bỏ qua `- [tên#0]`, nhãn
        # "chỉ là ngữ cảnh", `(dated …)` và `(url)`. Đo bằng chính bộ ước lượng của
        # repo ở ngân sách 2.000 (chế độ hẹp, 15–17 đoạn được giữ): `used_tokens`
        # đứng ở 1.282 trong khi `estimate_tokens(render())` đi từ 1.315 lên 1.392 —
        # **+77 token (+5,9%) không cái đồng hồ nào thấy**. Rồi `pool -= passage_used`
        # cấp chỗ cho entity/quan hệ/fact dựa trên khoảng trống KHÔNG có thật.
        #
        # Đây đúng là kiểu hỏng mà chú thích ngay dưới đã ghi cho entity — lặp lại ở
        # mục ngay trên nó. Và `assert pack.used_tokens <= pack.budget_tokens` không
        # bắt được vì nó so phép ước lượng với chính nó.
        when = f" (dated {require_utc(x.modified_at):%Y-%m-%d})" if x.modified_at else ""
        tag = "" if x.matched else _UNMATCHED_TAG
        url = f" ({x.url})" if x.url else ""
        return estimate_tokens(
            f"- [{x.entity_name}#{x.ordinal}]{tag}{when} {x.excerpt}{url}"
        )

    # Đoạn KHỚP CHỮ lấy phần của mình trước, tới trần; phần chia ở dưới quyết
    # định phần còn lại, gồm cả đoạn chỉ-là-ngữ-cảnh.
    matched_cap = int(available * _MATCHED_PASSAGE_CEILING)
    matched_cost = 0
    for passage in pack.passages:
        if not passage.matched:
            continue
        nxt = matched_cost + _passage_cost(passage)
        if nxt > matched_cap:
            break
        matched_cost = nxt

    kept_passages, passage_used = _take(
        list(pack.passages),
        min(pool, max(_cap("passages"), matched_cost)) if pack.passages else 0,
        _passage_cost,
    )
    if len(kept_passages) < len(pack.passages):
        pack.truncated["passages"] = len(pack.passages) - len(kept_passages)
    pack.passages = kept_passages
    pool -= passage_used
    # Tập entity CÒN đoạn sau nhát cắt trên — thứ `_kind_quota_order` gọi là bằng
    # chứng. Lấy sau khi cắt, không phải trước: đoạn bị cắt không nằm trong gói.
    evidenced = frozenset(passage.entity_id for passage in pack.passages)

    # ⛔ ENTITY cũng tốn chỗ, và trước đây không ai tính chúng.
    #
    # `render()` in một dòng cho mỗi entity và `traverse` mặc định `limit=200`, nên
    # ~2.400–3.600 token entity có thể nuốt trọn ngân sách 2.000 TRƯỚC đoạn trích
    # đầu tiên — trong khi `used_tokens` không đếm chúng, nên `query_log.pack_tokens`
    # báo một con số nhỏ và tràn không hiện ra ở đâu cả.
    # `pack.entities` vẫn đang ở đúng thứ tự `ranked` của `build_pack` — không nhát
    # xếp nào chen vào giữa — nên vị trí trong nó LÀ thứ hạng, kể cả hai khoá hoà.
    rank_of = {e["id"]: n for n, e in enumerate(pack.entities)}
    kept_entities, entity_used = _take(
        _kind_quota_order(pack.entities, evidenced=evidenced), _cap("entities"),
        lambda e: estimate_tokens(_entity_line(e)),
    )
    if len(kept_entities) < len(pack.entities):
        pack.truncated["entities"] = len(pack.entities) - len(kept_entities)
    # Quyết định GIỮ GÌ theo quota, nhưng IN RA theo thứ hạng: `render()` là thứ
    # agent đọc, và một danh sách bị xáo để phục vụ nhát cắt sẽ đọc như thể `folder`
    # ở 0,7 liên quan hơn `file` ở 1,0. Xếp theo VỊ TRÍ chứ không dựng lại
    # `(-score, id)`: dựng lại là vứt khoá `named` và khoá hạng RRF của `ranked`.
    kept_entities.sort(key=lambda e: rank_of[e["id"]])
    pack.entities = kept_entities

    # ⛔ MỖI mục có `_cap` RIÊNG. Bản trước dùng CHUNG một `remaining`, nên
    # `relations` — đứng trước — ăn hết và `facts` chưa bao giờ nhận được gì.
    pool -= entity_used
    used = 0
    section_line: dict[str, Callable[[dict[str, Any]], str]] = {
        "relations": _relation_line,
        "facts": _fact_line,
    }
    for section, items in (("relations", pack.relations), ("facts", pack.facts)):
        line_of = section_line[section]

        def cost_of(x: dict[str, Any], _f: Callable[[dict[str, Any]], str] = line_of) -> int:
            return estimate_tokens(_f(x))

        kept, spent = _take(items, _cap(section), cost_of)
        if len(kept) < len(items):
            pack.truncated[section] = len(items) - len(kept)
        # Cắt từ đuôi danh sách đã xếp hạng, nên thứ bị bỏ là thứ ít liên quan nhất.
        items[:] = kept
        used += spent
        pool -= spent

    pack.used_tokens = used + passage_used + entity_used + reserved
