# ĐATH - Agent Graph · CO3101 G1

Đề tài: **Đo lường tác động của cơ chế lọc quyền truy cập và lọc thời gian (as-of) lên hệ thống Temporal KGQA.**

## Tổng quan

Kho này gồm hai phần:

- **Hệ thống** (`src/`): một đồ thị tri thức tối thiểu trả lời câu hỏi [CRONQUESTIONS](https://github.com/apoorvumang/CronKGQA) qua một **gói ngữ cảnh** (context pack), giữ hai tính chất của hệ mà nó được cắt ra:
  - **Bi-temporal.** Mỗi sự kiện mang thời điểm nó đúng ngoài đời (`valid_from`/`valid_to`) và thời điểm được ghi nhận (`observed_at`/`expired_at`). Câu hỏi được hỏi *tại một mốc* (`as_of`), và chỉ những gì đúng tại mốc đó mới hiện ra.
  - **Lọc quyền, mặc định đóng (fail-closed).** Một thực thể chỉ hiện với principal mà một hàng ACL cấp, trong đúng những năm hàng đó bao phủ. Bước cắt quyền chạy trước mọi bước cắt gói, và gói cho biết có bao nhiêu đối tượng liên quan bị giữ lại — không cho biết là đối tượng nào.
- **Khung đánh giá** (mục *Khung đánh giá* bên dưới): dữ liệu `data_v2`, tập 1.500 câu đã khoá, bộ sinh mạng lưới quyền, các lệnh tái lập và định dạng kết quả.

## Yêu cầu hệ thống

- Python 3.12 trở lên và [uv](https://docs.astral.sh/uv/).
- Không cần cài Postgres hay Docker: Postgres chạy nhúng qua `pgserver`.

## Cài đặt

```bash
git clone https://github.com/phudayyy/DATH-Agent-Graph.git
cd DATH-Agent-Graph
uv sync --extra dev
```

## Chạy thử nhanh (Smoke test)

Một lệnh chạy từ đầu đến cuối trên bộ dữ liệu mẫu kèm sẵn trong `tests/data/` (cùng định dạng `data_v2`): dựng Postgres nhúng, nạp đồ thị, hỏi 4 câu, ghi mỗi câu một dòng JSON vào `runs/smoke.jsonl` và in độ phủ đáp án theo loại câu.

```bash
uv run python -m agent_graph.run --kg tests/data/kg --questions tests/data/questions.jsonl --out runs/smoke.jsonl
```

Kết quả mong đợi (mã thoát 0):

```
load: {"lines": 11, "malformed": 2, "out_of_range": 1, "reversed": 1, "self_loop": 1, "merged": 1, "facts": 5, "entities": 6, "grants": 6, "unknown_grant_items": 0}
    first_last:     0 / 1     đáp án nằm trong gói
 simple_entity:     2 / 2     đáp án nằm trong gói
   simple_time:     0 / 1     đáp án nằm trong gói
          tổng:     2 / 4
```

Bộ mẫu cố ý chứa dòng hỏng, dòng ngược năm, dòng tự trỏ và khoảng chồng nhau, nên dòng `load:` đếm đủ từng loại. Hai câu không nêu năm trượt là đúng thiết kế — xem *Cách hệ thống trả lời một câu hỏi*.

## Chạy trên dữ liệu thật

Sau khi tải `data_v2` theo mục *Dữ liệu CRONQUESTIONS bản data_v2* bên dưới, đổi file câu hỏi pickle sang JSON Lines **một lần** — pickle chạy mã nằm trong file khi đọc, nên chỉ mở file chính thức:

```bash
uv run python -m agent_graph.cronquestions convert data/wikidata_big/questions/test.pickle test.jsonl
uv run python -m agent_graph.run --kg data/wikidata_big/kg --questions test.jsonl --limit 1000 --out runs/test.jsonl
```

## Cấu trúc thư mục

```
src/brain/            lõi đồ thị chép từ hệ riêng tại mốc 28/09/2026, đã cắt gọn (xem PROVENANCE.md)
src/agent_graph/      phần viết cho đồ án
  schema.sql          lược đồ: tenants · entities · edges · acl
  cronquestions.py    đọc data_v2, nạp đồ thị và mạng quyền
  graph.py            lan một bước từ thực thể của câu hỏi, cắt quyền, dựng gói
  backend.py          giao diện nơi chạy: lõi trong kho này, hoặc hệ riêng trên máy tác giả
  run.py              chạy một tập câu, ghi kết quả
tests/                test chạy trên Postgres nhúng; tests/data là bộ mẫu cùng định dạng data_v2
PROVENANCE.md         nguồn gốc từng file chép từ hệ riêng
```

## Kiểm thử

```bash
uv run pytest -q
```

CI (`.github/workflows/ci.yml`) chạy `ruff`, `mypy` (strict) và `pytest` cho mỗi pull request và mỗi lần đẩy lên `main`.

## Khung đánh giá (Evaluation Framework)

### 1. Dữ liệu CRONQUESTIONS bản data_v2

Theo yêu cầu đề tài, dự án **bắt buộc dùng bản `data_v2.zip`** (giấy phép MIT), tuyệt đối không dùng bản `data.zip` v1 vì tác giả gốc đã xác nhận bản v1 có **31.951 trên 410.000 câu bị lỗi dữ liệu**.

* **Vị trí lưu trữ**: `data/wikidata_big/`
* **Quy mô đồ thị (`kg/full.txt`)**:
  * **328.635** sự kiện (events / facts) có mốc thời gian theo năm.
  * **125.726** thực thể (entities).
  * **203** quan hệ (relations).
* **Kết quả audit dữ liệu (chế độ chỉ đọc)**:
  * Không có dòng nào bị lỗi định dạng (0 malformed lines).
  * Phát hiện **652 sự kiện** có `start_year > end_year` (nhiễu từ Wikidata gốc). Giữ nguyên `event_id` theo số dòng của file `full.txt` để không làm xáo trộn index, nhưng coi đây là khoảng thời gian rỗng (không match với bất kỳ mốc as-of nào). Chi tiết xem tại `data/AUDIT_REPORT.md`.

---

### 2. Khóa tập đánh giá 1.500 câu & Hạt giống (Seed)

Để đảm bảo kết quả thực nghiệm là **khách quan, cố định và có thể tái lập 100%**, tập 1.500 câu hỏi được khóa chính thức với các thông số sau:

* **Hạt giống ngẫu nhiên (Random Seed)**: **`42`**
* **Nguồn trích xuất**: `data/wikidata_big/questions/test.pickle` (tập test gốc gồm 30.000 bản ghi).
* **Quy tắc khử trùng lặp (Dedup rule)**:
  * 30.000 bản ghi thực chất chỉ có **18.050 `uniq_id` phân biệt** (các bản ghi trùng `uniq_id` là các câu diễn đạt lại - paraphrase của cùng một câu hỏi và chung đáp án).
  * Shuffle danh sách 18.050 `uniq_id` với `seed = 42`.
  * Lấy đúng **1.500 câu đầu tiên**, sau đó sort tăng dần theo `uniq_id` để tập dữ liệu ổn định và dễ diff.
* **File lưu trữ chính thức (Single source of truth)**:
  * `eval_set/selected_question_ids.json`: Chứa đúng danh sách 1.500 `uniq_id` và seed.
  * `eval_set/eval_set.json`: Nội dung đầy đủ của 1.500 câu (câu hỏi chính, danh sách paraphrases, thực thể, quan hệ, mốc thời gian, đáp án vàng).
  * `eval_set/eval_metadata.json`: Metadata về kích thước pool và quy tắc dedup.
* **10 ID đầu tiên (`first10`) để đối soát nhanh**:
  ```python
  [14, 27, 40, 45, 103, 112, 136, 185, 191, 224]
  ```
* **Ví dụ câu hỏi đầu tiên trong tập khóa (`uniq_id: 14`)**:
  * *Question*: `"Who was Q558692's team member in Q1893"`
  * *Entities*: `Q558692` (Igor Shalimov), `Q1893` (Bologna FC 1909)
  * *Answers*: `['Q1097566', 'Q1143251', 'Q13372463', ...]`

---

### 3. Bộ sinh mạng lưới quyền (Permission Generator)

Do quyền thực tế của doanh nghiệp (ACL 26 nguồn SaaS) là dữ liệu bảo mật không thể công bố, đề tài sử dụng **mạng lưới quyền tổng hợp (synthetic)** được sinh có kiểm soát bằng thuật toán tất định (*deterministic*):

* **Mã nguồn**: `permission/generator.py`
* **Nguyên tắc**: Cùng dữ liệu đầu vào + cùng `seed = 42` $\rightarrow$ luôn sinh ra nhãn quyền **trùng khớp từng byte**.
* **3 mức độ chặt của mạng lưới quyền**:
  1. **`loose` (Lỏng)**: 5 vai trò (roles). Mỗi vai trò thấy trung bình **80,0%** đồ thị (~262.908 sự kiện).
  2. **`medium` (Vừa)**: 12 vai trò (roles). Mỗi vai trò thấy trung bình **50,0%** đồ thị (~164.317 sự kiện).
  3. **`strict` (Chặt)**: 25 vai trò (roles). Mỗi vai trò thấy trung bình **22,9%** đồ thị (~75.405 sự kiện).
  4. Ngoài ra, luôn tồn tại vai trò **`public`** (thấy 100% sự kiện) dùng cho baseline A0 (không lọc quyền).
* **Cơ chế gán quyền**: Mỗi vai trò giữ một lõi ngữ nghĩa (semantic core gồm các quan hệ theo nhóm chuyên môn như sports, employment, political...) kết hợp bù sự kiện ngẫu nhiên theo chỉ tiêu; câu hỏi được gán vào vai trò có độ trùng khớp thực thể/quan hệ cao nhất.
* **Kết quả sinh nhãn**:
  * Định nghĩa vai trò: `permission/output/roles_{loose,medium,strict}.json`
  * Nhãn quyền gán cho từng câu: `permission/output/permissions_{loose,medium,strict}.jsonl` (đúng 1.500 dòng/file).
  * Tổng hợp thông số: `permission/output/generation_summary.json`

---

### 4. Các lệnh chạy tái lập (Reproduce Steps)

Bất kỳ ai clone repository này về đều có thể chạy lại các lệnh sau để tái lập chính xác 100% dữ liệu:

```bash
# B0. Kiểm tra tính toàn vẹn của dữ liệu data_v2
python scripts/audit_data_v2.py

# B1. Khóa tập 1.500 câu hỏi với seed 42
python scripts/select_eval_set.py --seed 42 --n 1500

# B2. Sinh nhãn phân quyền 3 mức cho 1.500 câu
python scripts/generate_permissions.py --seed 42

# B3. Xác minh tính tất định (chạy lại B2 hai lần độc lập, so sánh byte-by-byte)
python scripts/verify_reproduce.py --seed 42
# -> Kết quả: PASS (tất cả file nhãn byte-identical, mỗi file đủ 1500 dòng)

# B4. Chạy bộ unit test bắt buộc (chấm 15 điểm tiêu chí A3)
python -m pytest tests/ -v
# -> Kết quả: 28 passed (7 test permission + 12 test leakage + 9 test as-of)
```

---

### 5. Output Contract

```json
{
  "question_id": 14,
  "answer": "Q1097566",
  "reasoning": "Chuỗi suy luận trung gian của mô hình...",
  "latency_ms": 120,
  "tokens": 450
}
```

* `question_id`: `uniq_id` của câu hỏi (khớp với tập 1.500 câu đã khóa).
* `answer`: Đáp án cuối cùng (chứa mã Wikidata Q-id để chấm Hits@1 và Leakage).
* `reasoning`: Suy luận trung gian (bắt buộc phải có vì độ rò rỉ thông tin được đo riêng ở cả reasoning và final answer).
* `latency_ms`, `tokens`: Thời gian chạy và số token tiêu thụ.

---

## Cách hệ thống trả lời một câu hỏi

| Bước | Diễn ra thế nào |
|---|---|
| Thời gian | Sự kiện `[start, end]` tính theo năm thành `[1/1/start, 1/1/(end+1))` theo UTC — năm kết thúc được tính, như CronKGQA. Câu nêu năm được hỏi tại 1/1 của năm đó; câu không nêu năm được hỏi tại thời điểm hiện tại. |
| Hạt giống | Thực thể của câu hỏi do CRONQUESTIONS cho sẵn (CronKGQA giả định nhận diện thực thể hoàn hảo). |
| Lan | Một bước từ những hạt giống người hỏi được thấy, qua các cạnh đúng tại mốc đó. |
| Cắt | Thực thể người hỏi không được thấy bị bỏ và được đếm; sau đó gói bị giới hạn 50 thực thể và 100 quan hệ. |

Hỏi tại một mốc duy nhất là ngữ nghĩa **ảnh chụp** của hệ gốc. Nó không trả lời được câu không nêu năm — *khi nào*, *đầu tiên/cuối cùng*, *trước/sau* — và bộ chạy báo điều đó thành con số thay vì che đi.

## Mạng quyền của bộ nạp

Không có `--acl` thì mọi thực thể được cấp cho `anyone` — thế giới mở, dùng làm mốc so sánh. Có `--acl` thì chỉ những dòng được liệt kê tồn tại, và thực thể không dòng nào nhắc tới thì không ai thấy:

```
# item <TAB> principal [<TAB> năm bắt đầu <TAB> năm kết thúc]
Q30	anyone
Q76	alice@lab.test	2001	2004
Q11696	domain:lab.test
```

Principal là một email, `group:<tên>`, `domain:<tên>` hoặc `anyone`; `--identity` chọn người hỏi.

## Giới hạn hiện tại

- Chưa có bộ chọn đáp án: bộ chạy báo **đáp án có nằm trong gói không** — trần trên của Hits@1 cho mọi cách trả lời chỉ đọc gói — chứ chưa ra Hits@1, và chưa xuất theo *Output Contract* bên trên.
- Quyền đang gán theo **thực thể**; nhãn quyền theo **sự kiện** của *Bộ sinh mạng lưới quyền* chưa được nối vào bộ nạp.
- Bộ nạp gộp các khoảng chồng nhau và bỏ dòng có năm bắt đầu sau năm kết thúc, nên chưa giữ `event_id` theo số dòng `full.txt` như mục *Dữ liệu CRONQUESTIONS bản data_v2* quy định.

## Nguồn gốc mã nguồn

`src/brain/` là một phần nhỏ của hệ riêng của tác giả (finch-brain), lấy tại mốc đóng băng 28/09/2026: chép nguyên văn từng byte rồi mới cắt gọn. [`PROVENANCE.md`](PROVENANCE.md) liệt kê từng file với git blob tại mốc và phần được giữ lại. Mọi thứ khác trong `src/agent_graph/` và `tests/` được viết cho đồ án này.

## Giấy phép

MIT — xem [`LICENSE`](LICENSE).
