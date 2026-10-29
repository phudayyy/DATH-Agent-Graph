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

Bất kỳ ai clone repository này về đều có thể chạy lại các lệnh sau. Hãy chạy lần lượt B0 → B4; B1/B2 sẽ tạo hoặc ghi đè các file trong `eval_set/` và `permission/output/`.

- **B0**: audit định dạng và thống kê KG `data_v2`; ghi/cập nhật `data/audit_report.json` và báo cáo Markdown, không sửa `full.txt`.
- **B1**: chọn và khóa 1.500 `uniq_id` với seed 42; tạo hoặc ghi đè `eval_set/selected_question_ids.json`, `eval_set/eval_set.json` và metadata.
- **B2**: sinh roles và permission labels cho `loose`, `medium`, `strict`; tạo hoặc ghi đè file tương ứng trong `permission/output/`.
- **B3**: chạy phép sinh hai lần vào `permission/output_repro_a/` và `permission/output_repro_b/`, rồi so sánh file để kiểm tra deterministic. Script xóa hai thư mục kiểm tra cũ này trước khi chạy; không ghi đè labels chính thức trong `permission/output/`.
- **B4**: chạy các kiểm thử tự động bắt buộc cho permission, leakage và as-of. Lệnh này chỉ kiểm tra, không sinh kết quả thí nghiệm; exit code 0 nghĩa là toàn bộ test pass. Số test có thể thay đổi khi bộ test được cập nhật.

```bash
# B0. Kiểm tra tính toàn vẹn của dữ liệu data_v2
python scripts/audit_data_v2.py

# B1. Khóa tập 1.500 câu hỏi với seed 42
python scripts/select_eval_set.py --seed 42 --n 1500

# B2. Sinh nhãn phân quyền 3 mức cho 1.500 câu
python scripts/generate_permissions.py --seed 42

# B3. Xác minh tính tất định (chạy lại B2 hai lần độc lập, so sánh byte-by-byte)
python scripts/verify_reproduce.py --seed 42

# B4. Chạy bộ unit test bắt buộc (chấm 15 điểm tiêu chí A3)
python -m pytest tests/ -v
```

---

## 5. Output Contract

Để evaluator của Huy có thể chấm điểm độc lập cho mọi nhánh (A0, A1, A2-public, A2-private), mỗi nhánh khi chạy qua 1.500 câu phải trả về đúng cấu trúc JSON gồm 5 trường:

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

## 6. Mô hình sinh chung và A0 (CPU)

Đề bài yêu cầu các baseline dùng chung một mô hình sinh và cùng ngân sách truy hồi. A0/A1 hiện dùng **`Qwen/Qwen2.5-1.5B-Instruct`** local, CPU, cùng prompt và cùng giới hạn 64 facts truy hồi; model card ghi giấy phép Apache 2.0. Qwen weights được tải/cached tự động ở lần chạy đầu, không commit vào Git. Đây là lựa chọn model thí nghiệm của nhóm, không phải một model API.

Trong Python environment của dự án cần có PyTorch CPU, `transformers` và `accelerate`. Lệnh cài dưới đây cài các package cần để nạp model, không tải weights model:

```powershell
python -m pip install torch transformers accelerate
```

Chạy A0 trên tập khóa. Lệnh chạy CPU với 4 thread, không bật lọc permission hay as-of; lần đầu sẽ tải model từ Hugging Face. Output JSONL có answer, reasoning tóm tắt kèm event IDs, latency và tổng token prompt+câu trả lời. Metadata ghi model, retrieval budget và generation settings:

```powershell
python scripts/run_a0.py --threads 4 --retrieval-budget 64 --max-new-tokens 128
```

Lệnh tạo `results/a0/outputs_A0.jsonl`, `metrics_A0.json` và `run_metadata.json`; sidecar ghi revision commit đã resolve từ Hugging Face. Có thể truyền `--revision <commit>` để khóa một revision thay vì `main`. Hits@1 vẫn theo cách chấm CronKGQA: một đáp án hạng nhất đúng nếu khớp một trong các gold answers; điều này dùng chung metric, không khẳng định model Qwen là CRONKGQA. Mô hình sinh có rationale ngắn kèm event citations để evaluator chấm leakage riêng ở final và reasoning.

Phú cần dùng đúng model ID `Qwen/Qwen2.5-1.5B-Instruct`, `retrieval_budget_events=64`, `max_new_tokens=128` và ghi metadata sidecar cho A2-public/A2-private nếu muốn kết quả so sánh đúng điều kiện đề. A2 vẫn do Phú hiện thực.

Tham khảo: [Qwen2.5-1.5B-Instruct model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct); [CronKGQA paper](https://aclanthology.org/2021.acl-long.520/) để đối chiếu quy ước Hits@1.

## 7. Chạy A1 và chấm output của mọi nhánh

MCP xuất hiện ở **A2-private**: hệ riêng của Phú gọi qua MCP. A2-public là hiện thực đối chiếu trên kho công khai. Huy không xây MCP server/A2; evaluator của Huy nhận JSONL từ hai nhánh này rồi chấm bằng cùng metrics.

A1 dùng cùng model, prompt, KG retrieval budget và token budget như A0; sau khi sinh mới kiểm tra event evidence với permission labels, không bật as-of. Nếu không kiểm chứng được event-level provenance thì A1 từ chối trả lời. Lệnh thứ nhất chạy mức quyền `medium` và ghi output cùng metadata. Lệnh thứ hai chấm output với nhãn quyền và gắn metadata vào metrics:

```powershell
python scripts/run_a1.py --tightness medium --threads 4 --retrieval-budget 64 --max-new-tokens 128
python scripts/evaluate_outputs.py --outputs results/a1/outputs_A1_medium.jsonl --branch A1-medium --tightness medium --permission --permission-file permission/output/permissions_medium.jsonl --run-metadata results/a1/run_metadata_A1_medium.json
```

Để chấm A0 hoặc output A2-public/A2-private, đưa JSONL đúng 5 trường vào cùng evaluator. Lệnh A0 phía dưới chỉ bật permission labels làm **tham chiếu để đo leakage**; vì không có `--permission`, nó không bật lọc quyền trong nhánh A0. Lệnh A2 bật cả `--permission` và `--asof` để ghi rõ ô thí nghiệm ON/ON. Thay `path/to/A2-public.jsonl` bằng đường dẫn output thật từ Phú:

```powershell
python scripts/evaluate_outputs.py --outputs results/a0/outputs_A0.jsonl --branch A0 --tightness medium --permission-file permission/output/permissions_medium.jsonl --run-metadata results/a0/run_metadata.json
python scripts/evaluate_outputs.py --outputs path/to/A2-public.jsonl --branch A2-public --tightness medium --permission --asof --permission-file permission/output/permissions_medium.jsonl --run-metadata path/to/A2-public-metadata.json
```

Với A0, có thể chấm lại **cùng một output đã sinh** ba lần bằng ba file permission labels khác nhau để đo leakage dưới từng mức quyền; đây là chấm hậu nghiệm, không phải ba lần chạy mô hình. `--outputs` là file JSONL đầu vào; `--branch` đặt tên nhánh cho file kết quả; `--tightness` ghi mức quyền; `--permission` và `--asof` đánh dấu điều kiện bật trong ô thí nghiệm; `--permission-file` chọn labels dùng để đo leakage (và làm context cho nhánh bật permission); `--run-metadata` gắn sidecar ghi model ID, retrieval/generation settings vào metrics để soát việc các nhánh dùng cùng cấu hình. `--eval-set` và `--kg` chỉ cần đổi nếu dữ liệu nằm chỗ khác; `--out-dir` đổi thư mục kết quả. Với một ô không có permission labels, leakage được ghi `null`/không đo được, không bị tính thành 0%. Evaluator giữ event ID theo số dòng zero-based của `full.txt`, bỏ qua khoảng `start > end`, và dùng khoảng `[start_year, end_year)` với `end_year=0` là còn hiệu lực.

Temporal error được tính khi evaluator suy ra được nhãn as-of và latest từ graph cho câu `simple_entity` có năm, head và một relation rõ ràng. Những template khác không bị gán nhãn thay thế bằng `answers` và không nằm trong mẫu số temporal; metrics nêu số câu đủ điều kiện qua `temporal_with_asof`. Leakage attribution nối QID được nhắc tới với fact liên quan câu hỏi trong KG vì Output Contract không có trường provenance riêng; đây là phép đo dựa trên KG, không chứng minh được những sự kiện không thể suy ra từ các QID trong text.

Sau khi đã chấm và có đủ 8 file `detailed_*.json`, dùng lệnh dưới đây để tổng hợp (không chạy lại mô hình). `--a0` và `--asof-only` là hai ô OFF dùng chung; sáu cờ còn lại nhận kết quả permission-only và ON/ON cho từng mức. Mỗi file phải có cùng question IDs. `--bootstrap` đặt số lần bootstrap (mặc định 10.000), `--seed` đặt seed để tái lập khoảng tin cậy, `--output` đổi đường dẫn file tổng hợp:

```powershell
python scripts/summarize_experiment.py --a0 results/evaluated/detailed_A0.json --asof-only results/evaluated/detailed_asof-only.json --permission-loose results/evaluated/detailed_permission-loose.json --permission-medium results/evaluated/detailed_permission-medium.json --permission-strict results/evaluated/detailed_permission-strict.json --both-loose results/evaluated/detailed_both-loose.json --both-medium results/evaluated/detailed_both-medium.json --both-strict results/evaluated/detailed_both-strict.json
```

Script này xuất `ΔP`, `ΔT`, `ΔPT`, độ lệch cộng tính và khoảng tin cậy 95% vào `results/experiment_summary.json`. Các đường dẫn tên nhánh chỉ là ví dụ; `--branch` khi chấm output quyết định tên file `detailed_*.json`.

---
