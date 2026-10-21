# CO3101 G1 - Khung Đánh Giá Đề Tài (Evaluation Framework)

Đề tài: **Đo lường tác động của cơ chế lọc quyền truy cập và lọc thời gian (as-of) lên hệ thống Temporal KGQA.**

---

## 1. Dữ liệu CRONQUESTIONS bản data_v2

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

## 2. Khóa tập đánh giá 1.500 câu & Hạt giống (Seed)

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

## 3. Bộ sinh mạng lưới quyền (Permission Generator)

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

## 4. Các lệnh chạy tái lập (Reproduce Steps)

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

## 5. Output Contract

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
