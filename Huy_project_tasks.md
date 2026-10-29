# CO3101 G1 — Những việc Huy cần làm

## 1. Vai trò của Huy

Huy phụ trách **phần ĐO / evaluation** của đề tài. Huy xây bộ đánh giá độc lập để đo tác động của permission filtering và as-of-time filtering.

**Huy không làm A2.** A2 thuộc Phú. Huy xây bộ đánh giá để chấm kết quả của A0, A1 và A2.

Huy sở hữu **bốn thứ** chạy được mà không cần một dòng mã nào của Phú:
1. Tập đánh giá 1.500 câu
2. Bộ sinh mạng lưới quyền
3. Khung đo (evaluator)
4. Hai nhánh A0 và A1

## 2. Câu hỏi nghiên cứu

> Khi tầng ngữ cảnh vừa chặn theo quyền của người hỏi vừa chặn theo mốc as-of, độ đúng mất bao nhiêu so với đồ thị không chặn, và hai phép chặn cộng dồn hay độc lập?

Phát biểu để bác bỏ:

- `ΔP` = phần độ đúng mất khi chỉ bật chặn quyền.
- `ΔT` = khi chỉ bật chặn thời gian.
- `ΔPT` = khi bật cả hai.

Giả thuyết không cần bác bỏ:

```text
ΔPT = ΔP + ΔT
```

Thiết kế là bảng 2×2, lặp trên 3 mức độ chặt của mạng lưới quyền.

## 3. Dữ liệu đánh giá

- Dùng **CRONQUESTIONS `data_v2.zip`** (giấy phép **MIT**).
  - **BẮT BUỘC** dùng `data_v2.zip`, không dùng `data.zip`: chính tác giả ghi 31.951/410.000 câu của bản đầu có lỗi dữ liệu.
- Đồ thị: **125.000 thực thể**, **203 quan hệ**, **328.000 sự kiện** có mốc thời gian theo năm.
- Từ 410.000 câu hỏi, khóa một tập **1.500 câu**.
- Chọn bằng random seed cố định để có thể tái lập.
- Công bố tập/seed trong repo và README.
- Nhãn thời gian là **dữ liệu thật** của CRONQUESTIONS.
- Nhãn quyền là **TỔNG HỢP (synthetic)**, sinh bằng permission generator của nhóm, không phải ACL thật của hệ thống Phú.
  - Lý do: quyền thật trong hệ Phú đến từ ACL của 26 nguồn SaaS của một tổ chức, vừa là dữ liệu riêng vừa không công bố được → không ai lặp lại được.
  - Mạng lưới tổng hợp có hạt giống thì ai cũng dựng lại được.

> **Disclaimer bắt buộc trong báo cáo và bản thảo:** phải viết thẳng rằng nhãn thời gian là thật còn nhãn quyền là tổng hợp, nên mọi con số về rò rỉ nói về cơ chế chứ không nói về một hệ thật đang chạy.

### Việc Huy cần làm
1. Tải và chuẩn hóa `data_v2`.
2. Viết script chọn 1.500 câu.
3. Khóa seed.
4. Lưu metadata cần thiết để chạy lại.

## 4. Permission Generator

Huy xây bộ sinh mạng lưới quyền:

```text
CRONQUESTIONS
      ↓
Permission Generator
      ↓
Role / permission network
      ↓
3 mức độ chặt
```

Có **3 mức độ chặt**.

Bộ sinh phải deterministic:

> Cùng input + cùng seed → cùng permission labels.

Không tự tay gán quyền cho 1.500 câu.

## 5. Bộ đánh giá chung

Huy xây một evaluator dùng chung cho A0, A1 và output của A2.

### Threat model

Tính chất an toàn phải phát biểu rõ mỗi lần nhắc tới, không được viết gọn thành "an toàn":

> Bí mật của các sự kiện ngoài quyền, trước một đối thủ **CHỈ ĐỌC**, kiểm soát được câu hỏi bằng ngôn ngữ tự nhiên, nhưng **không** kiểm soát được đồ thị, **không** kiểm soát được máy chủ MCP, và **không** có kênh phụ nào. Ngoài đúng mô hình này thì đề tài không phát biểu gì.

### Metrics
- **Hits@1** theo cách chấm của CronKGQA (để số của đề tài đặt cạnh được số đã công bố).
- **Leakage rate (tỉ lệ rò)**: tỉ lệ câu trả lời có nhắc ít nhất một sự kiện ngoài phạm vi quyền của người hỏi.
  - Phải **đo RIÊNG** cho phần **trả lời cuối (final answer)** và phần **suy luận trung gian (intermediate reasoning)**.
  - Lý do: nếu chỉ chấm câu trả lời cuối thì nhánh A1 sẽ được điểm cao giả tạo (vì A1 xóa answer nhưng reasoning vẫn rò).
- **Temporal-error rate (tỉ lệ lỗi thời)**: tỉ lệ câu đúng theo trạng thái mới nhất nhưng sai theo mốc as-of được hỏi.
- Latency.
- Token usage.

Ví dụ output contract:

```json
{
  "question_id": "...",
  "answer": "...",
  "reasoning": "...",
  "latency_ms": 0,
  "tokens": 0
}
```

Mục tiêu là tất cả nhánh được chấm bằng **cùng một thước đo**.

## 6. A0 — Huy làm

A0 = **không chặn permission và không chặn as-of**.

```text
Question
   ↓
Retrieval / Agent
   ↓
Answer
   ↓
Huy Evaluator
```

A0 tạo baseline về độ đúng và mức phơi nhiễm khi không filtering.

## 7. A1 — Huy làm

A1 = **post-generation filtering**.

```text
Question
   ↓
Agent sinh answer
   ↓
Kiểm tra permission
   ↓
Xóa / từ chối nếu có event ngoài quyền
   ↓
Huy Evaluator
```

**Không có A1-private trong đề.**

## 8. A2 — Phú làm

A2 = đưa cả permission predicate và as-of predicate vào truy vấn đồ thị.

Phú phụ trách hai implementation:

```text
A2-public
A2-private (hệ Phú gọi qua MCP)
```

Huy **không code A2**. Huy chỉ nhận output A2 và đưa qua evaluator chung:

```text
A2-public ──┐
            ├──→ Huy Evaluator → Metrics
A2-private ┘
```

A2-public là implementation đối chiếu bắt buộc để kết quả có thể tái lập.

## 9. Thí nghiệm 2×2

| Permission | As-of | Nhánh |
|---|---|---|
| OFF | OFF | A0 |
| ON | OFF | Permission only |
| OFF | ON | As-of only |
| ON | ON | A2 |

Tính:

- `ΔP`: độ đúng mất khi bật permission.
- `ΔT`: độ đúng mất khi bật as-of.
- `ΔPT`: độ đúng mất khi bật cả hai.

Kiểm tra giả thuyết:

```text
ΔPT = ΔP + ΔT
```

Thí nghiệm được xét trên 3 mức độ chặt của permission network.

Đề quy định **8 lần chạy phân biệt**, không phải 12:
- hai ô permission-off chạy 1 lần;
- hai ô permission-on chạy 3 lần theo 3 mức;
- các lần permission-off dùng chung cho ba mức.
- **Phải ghi rõ** trong bảng rằng ô OFF dùng chung, không chép lại số thành ba dòng như thể đã đo ba lần.

## 10. Pytest bắt buộc

> Ba kiểm thử này chấm **15 điểm trong tiêu chí A3**. Chúng bắt loại lỗi không làm chương trình sập mà chỉ làm con số sai.

### Test 1 — Permission generator
Cùng seed phải sinh cùng kết quả. Một role giả có tập event nhìn thấy được đã biết phải khớp với kết quả tính tay.

### Test 2 — Leakage scorer
Có 5 synthetic answers, trong đó bắt buộc có case leak **chỉ ở reasoning**, không ở final answer. Lý do: nếu chỉ chấm final answer thì A1 được điểm cao giả tạo.

### Test 3 — As-of
Fake entity có 3 khoảng valid-time; kiểm tra 3 mốc và boundary theo quy ước **left-closed** (nửa khoảng đóng bên trái, ghi trong README).

## 11. Deliverables của Huy

```text
evaluation/
├── hits_at_1.py
├── leakage.py
├── temporal_error.py
├── metrics.py
└── runner.py

permission/
└── generator.py

a0/
└── ...

a1/
└── ...

tests/
├── test_permission.py
├── test_leakage.py
└── test_asof.py
```

Ngoài code:
- 1.500 câu + seed;
- permission labels;
- permission generator;
- kết quả A0/A1;
- evaluator dùng được cho A2;
- bảng `ΔP`, `ΔT`, `ΔPT`;
- bootstrap confidence intervals;
- tài liệu phân định phần làm trước và trong học kỳ;
- **báo cáo 12–15 trang** (chung cả nhóm — Huy viết phần dữ liệu, evaluator, A0, A1, kết quả thí nghiệm);
- **bản thảo 6 trang** nếu qua cổng W11 (chung cả nhóm).

## 12. Mốc quan trọng

### W4
- Có `data_v2`.
- Khóa 1.500 câu + seed.
- Có permission generator.
- Bắt đầu evaluator.
- README ghi cách tái lập.

### W5
- A0 chạy trọn trên tập đã khóa.
- Có Hits@1 đầu tiên.
- **Rà lại tài liệu** xem đóng góp có bị trùng công trình đã có không.

### W6, W8, W10
- Không có mốc mới; dùng để **vá mốc tuần trước** và **viết kiểm thử**.

### W7
- A1 chạy.
- A2-public của Phú chạy được.
- Evaluator chung chấm được output A2.

### W9
- Đóng băng thiết kế.
- Chạy 3 mức độ chặt.
- **Rà lại tài liệu** lần hai.

### W11
- Đủ 8 runs.
- Có bootstrap CI.
- Quyết định có làm paper hay không.

### W12
- Tuần đệm. Thầy đọc bản thảo.

### W13
- Bản thảo đầy đủ và phản biện chéo.

### W14
- Nộp.

### W15
- Bảo vệ.

## 13. Cổng đi tiếp hay dừng

| Cổng | Điều kiện | Nếu KHÔNG đạt |
|---|---|---|
| Hết W4 | Tập 1.500 câu đã khóa kèm hạt giống | Thu về phạm vi tối thiểu ngay, không chờ |
| Hết W7 | Hiện thực đối chiếu A2 chạy được trên kho công khai | Bỏ nhánh hệ riêng (A2-private) |
| Hết W11 | Đủ 8 runs + bootstrap CI | Bỏ phần bài báo, giữ phần báo cáo |

## 14. Rủi ro liên quan đến Huy

1. **Huy thành người phụ trong hệ của Phú**: chặn bằng phân công — Huy sở hữu tập đánh giá, bộ sinh quyền, khung đo và A0/A1. Đường tới hạn của đề tài không đi qua hệ riêng.
2. **Đóng góp bị trùng công trình đã có**: rà lại tài liệu ở W5 và W9. Đã có bài về thứ tự chặn → đóng góp phải nằm ở **tương tác giữa hai phép chặn** và ở **bộ đánh giá**, không phải thứ tự chặn.
3. **Mốc thời gian CRONQUESTIONS chỉ mịn tới năm**: đã có bài chỉ ra hạn chế này. Ghi vào phần hạn chế, không đổi bộ dữ liệu giữa học kỳ.
4. **Nhãn quyền tổng hợp**: không được viết một câu nào ngụ ý đây là số đo trên hệ thật.

## 15. Tài liệu Huy phải đọc

| Tài liệu | Ghi chú | Deadline |
|---|---|---|
| aclanthology.org/2021.acl-long.520 | CronKGQA và bộ CRONQUESTIONS (arXiv:2106.01515, kho github.com/apoorvumang/CronKGQA, MIT, dùng data_v2.zip) | — |
| arXiv:2406.14191 v4 (14/01/2026) | Khảo sát hỏi đáp trên đồ thị tri thức có thời gian | — |
| aclanthology.org/2023.acl-long.637 | MultiTQ, phê phán độ mịn theo năm của CRONQUESTIONS | — |
| aclanthology.org/2026.trustnlp-main.15 | Chặn quyền trước khi truy hồi, TrustNLP 2026, công trình gần nhất | **PHẢI đọc trước W5** |
| arXiv:2607.22115 | Chuẩn đánh giá text-to-SQL dưới kiểm soát truy cập theo vai trò, khuôn mẫu phương pháp gần nhất | — |
| arXiv:2505.01538 | HONEYBEE, phân vùng theo vai trò cho cơ sở dữ liệu vector | — |
| arXiv:2601.11199 | SD-RAG, chính sách công bố chọn lọc đặt trong truy hồi | — |
| arXiv:2509.14608 | Kiểm soát truy cập theo người tham gia, có triển khai thật | — |
| modelcontextprotocol.io/specification/2026-07-28 | basic/authorization và server/resources (bản đặc tả 28/07/2026) | — |
| SIGMOD Record 41(3) 2012 (doi 10.1145/2380776.2380786) | Định nghĩa valid time và transaction time trong SQL:2011 | — |

## 16. Minimum scope

Nếu phải cắt scope:

1. 2×2 experiment.
2. Một mức độ chặt duy nhất.
3. Chạy trên A2-public.
4. Không cần A2-private.
5. Không cần sweep 3 mức.
6. Không cần paper.

Minimum scope phải **CPU-capable** và không phụ thuộc code có từ trước học kỳ.

## 17. Nguyên tắc quan trọng

> **Huy là người cầm thước đo, không phải người xây A2.**

```text
                 PHÚ
                  │
              ┌───┴───┐
              ↓       ↓
         A2-public  A2-private
              │       │
              └───┬───┘
                  ↓
             HUY EVALUATOR
                  ↑
              ┌───┴───┐
              │       │
             A0      A1
              │       │
              └───┬───┘
                  ↓
               Metrics
```

Mục tiêu của Huy là làm cho **mọi nhánh được đo bằng cùng một thước**, để trả lời câu hỏi nghiên cứu một cách công bằng và tái lập được.
