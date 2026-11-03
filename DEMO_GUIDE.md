# Demo Guide — W4

## Trước khi chạy lệnh

Mở **PowerShell** rồi chuyển vào thư mục dự án trước. Làm bước này mỗi khi mở terminal mới:

```powershell
Set-Location 'D:\MinhHuy\ĐATH'
Get-Location
```

`Get-Location` cần hiện `D:\MinhHuy\ĐATH`. Nếu chưa vào thư mục này, lệnh `python scripts\...` sẽ tìm script tương đối trong thư mục hiện tại (ví dụ `C:\Users\duong\scripts\...`) và báo không tìm thấy file.

## Mục tiêu demo

Chứng minh 2 điều:
1. **Random seed** — chọn 1.500 câu deterministic, ai chạy lại cũng ra y hệt
2. **Role tự động** — mỗi câu hỏi được gán role bằng thuật toán, không gán tay

---

## Bước 1 — Audit data (read-only, không sửa)

```powershell
python scripts/audit_data_v2.py
```

Output:
- `data/audit_report.json`
- `data/AUDIT_REPORT.md`
- Xác nhận: 328.635 events, 125.726 entities, 203 relations, 0 malformed

---

## Bước 2 — Khóa 1.500 câu bằng seed cố định

```powershell
python scripts/select_eval_set.py --seed 42 --n 1500
```

Output:
```
pool=18050 selected=1500 seed=42
first10=[14, 27, 40, 45, 103, 112, 136, 185, 191, 224]
```

File tạo ra:
- `eval_set/selected_question_ids.json` — 1.500 ID + seed
- `eval_set/eval_set.json` — nội dung đầy đủ 1.500 câu
- `eval_set/eval_metadata.json` — pool size, dedup rule

**Demo point:** Chạy lại lần 2 → `first10` giống hệt. Đổi `--seed 999` → ra tập câu khác hoàn toàn.

---

## Bước 3 — Sinh nhãn quyền tự động (3 mức chặt)

```powershell
python scripts/generate_permissions.py --seed 42
```

Output:
```
eval set: 1500 questions, seed=42
Generating loose permissions...
  loose: 5 roles, avg 262908 events/role (80.0% coverage)
Generating medium permissions...
  medium: 12 roles, avg 164317 events/role (50.0% coverage)
Generating strict permissions...
  strict: 25 roles, avg 75405 events/role (22.9% coverage)
```

File tạo ra:
- `permission/output/roles_{loose,medium,strict}.json` — danh sách role + event được phép
- `permission/output/permissions_{loose,medium,strict}.jsonl` — mỗi file 1.500 dòng, mỗi dòng = 1 câu → 1 role
- `permission/output/generation_summary.json`

**Demo point:** Mở `permissions_medium.jsonl`, chỉ 1 dòng:
```json
{"question_id": 14, "role_id": "role_medium_3", "allowed_event_ids": []}
```
→ Câu 14 được gán vào `role_medium_3` (sports domain) bằng scoring overlap entity/relation, không ai ngồi gán tay.

---

## Bước 4 — Verify reproduce (byte-for-byte)

```powershell
python scripts/verify_reproduce.py --seed 42
```

Output:
```
run A...
run B...
roles_loose.json: IDENTICAL
permissions_loose.jsonl: IDENTICAL
roles_medium.json: IDENTICAL
permissions_medium.jsonl: IDENTICAL
roles_strict.json: IDENTICAL
permissions_strict.jsonl: IDENTICAL
permissions_loose.jsonl lines=1500 (expect 1500)
permissions_medium.jsonl lines=1500 (expect 1500)
permissions_strict.jsonl lines=1500 (expect 1500)
REPRODUCE CHECK: PASS
```

**Demo point:** Chạy 2 lần → 6 file IDENTICAL, mỗi file đúng 1.500 dòng. Đây là bằng chứng determinism.

---

## Bước 5 — Pytest (15 điểm A3)

```powershell
python -m pytest tests/ -v
```

Kết quả hiển thị số kiểm thử hiện có và từng trạng thái pass/fail; số lượng có thể đổi khi test được cập nhật. Phần leakage kiểm tra QID/entity được nối về KG event, còn phần as-of truy vấn index ở các mốc và biên trái.

Các test liên quan đến seed & role:
| Test | Chứng minh |
|------|-----------|
| `test_deterministic_same_seed` | Cùng seed → cùng role + permission |
| `test_different_seed_different_output` | Khác seed → output khác |
| `test_select_1500_deterministic` | Cùng seed → cùng 1.500 ID |
| `test_three_tightness_levels` | 3 mức loose/medium/strict có coverage khác nhau |
| `test_role_assignment_coverage` | Mọi câu đều được gán role hợp lệ |
| `test_known_role_events_match_manual` | Role giả → khớp tính tay |
| `test_public_role_exists` | Role public (baseline) luôn tồn tại |

---

## Tóm tắt thứ tự chạy

```powershell
Set-Location 'D:\MinhHuy\ĐATH'

python scripts\audit_data_v2.py                       # B0: audit
python scripts\select_eval_set.py --seed 42 --n 1500  # B1: khóa 1500 câu
python scripts\generate_permissions.py --seed 42      # B2: sinh role tự động
python scripts\verify_reproduce.py --seed 42          # B3: verify determinism
python -m pytest tests/ -v                             # B4: chạy các test hiện có
```

Kết quả cần thấy: `first10=[14, 27, 40, 45, ...]`, `REPRODUCE CHECK: PASS`, và toàn bộ test pass.

---

# Demo Guide — W5 đến W15

Mỗi tuần dưới đây ghi mục tiêu demo, lệnh cần chạy và bằng chứng nên trình bày. Chỉ đánh dấu hoàn thành khi đã có output thật trong `results/`; các đường dẫn A2 là output Phú cần bàn giao. Các lệnh dùng PowerShell từ thư mục repo. Nếu mở terminal mới, chạy lại lệnh sau trước khi chạy bất kỳ lệnh demo nào:

```powershell
Set-Location 'D:\MinhHuy\ĐATH'
Get-Location
```

## W5 — Chạy A0 và lấy Hits@1 đầu tiên

**Mục tiêu:** chạy A0 hết 1.500 câu đã khóa, chứng minh output tuân thủ contract và có Hits@1 đầu tiên. Trình bày thêm kết quả rà soát tài liệu về mức độ trùng lặp đóng góp.

Nếu môi trường Python chưa cài dependencies:

```powershell
python -m pip install torch transformers accelerate
```

Chạy A0 trên CPU bằng model mặc định `Qwen/Qwen2.5-0.5B-Instruct` (A1 phải dùng cùng model và revision; lần đầu có thể tải model về cache):

```powershell
python scripts/run_a0.py --threads 4 --retrieval-budget 64 --max-new-tokens 128
```

Terminal sẽ báo tiến độ nạp KG/model, sau đó hiện progress bar cho 1.500 câu; mỗi output được flush xuống file ngay sau khi sinh xong. Nếu đang thấy `waiting for first answer...`, model vẫn đang xử lý câu đầu tiên và thanh tiến độ chưa thể tăng.

Output cần có:

- `results/a0/outputs_A0.jsonl` — đúng 1.500 dòng, mỗi dòng đủ 5 trường output contract.
- `results/a0/metrics_A0.json` — Hits@1, latency, token usage và thống kê theo loại câu.
- `results/a0/run_metadata.json` — model, revision và cấu hình chạy.

Chấm leakage của cùng một output A0 dưới nhãn quyền medium (đây là chấm hậu nghiệm; A0 vẫn không lọc quyền):

```powershell
python scripts/evaluate_outputs.py --outputs results/a0/outputs_A0.jsonl --branch A0-medium --tightness medium --permission-file permission/output/permissions_medium.jsonl --run-metadata results/a0/run_metadata.json
```

**Trong demo:** mở vài dòng JSONL, đối chiếu số câu với `eval_set/eval_set.json`, rồi trình bày Hits@1 và cấu hình từ metadata. Báo rõ nếu model chưa chạy xong hoặc có câu lỗi; không thay bằng số minh họa. Ghi nhận bài liên quan đã đọc, điểm trùng và khác biệt của đề tài.

## W6 — Vá A0/evaluator và kiểm thử

**Mục tiêu:** xử lý lỗi phát hiện ở W5, kiểm tra lại output contract và evaluator. Đây là tuần vá mốc trước, không có tính năng nghiên cứu mới bắt buộc.

```powershell
python -m pytest tests/ -v
python scripts/evaluate_outputs.py --outputs results/a0/outputs_A0.jsonl --branch A0-medium --tightness medium --permission-file permission/output/permissions_medium.jsonl --run-metadata results/a0/run_metadata.json
```

**Trong demo:** trình bày lỗi đã sửa, kết quả test thực tế và một file `metrics_A0-medium.json` mới. Nếu đổi code sinh output thì chạy lại A0; nếu chỉ sửa evaluator thì chấm lại output đã lưu và ghi rõ điều đó.

## W7 — A1, A2-public và evaluator chung

**Mục tiêu:** A1 chạy được; A2-public của Phú chạy được; evaluator chấm được output A2 theo cùng contract. MCP thuộc A2-private, không phải phần Huy cần demo ở mốc này.

Chạy thử A1 ở mức medium:

```powershell
$ModelRevision = (Get-Content results/a0/run_metadata.json | ConvertFrom-Json).model_revision
python scripts/run_a1.py --tightness medium --threads 4 --retrieval-budget 64 --max-new-tokens 128 --revision $ModelRevision
python scripts/evaluate_outputs.py --outputs results/a1/outputs_A1_medium.jsonl --branch A1-medium --tightness medium --permission --permission-file permission/output/permissions_medium.jsonl --run-metadata results/a1/run_metadata_A1_medium.json
```

Chấm output A2-public do Phú bàn giao (đổi đường dẫn theo file thật):

```powershell
python scripts/evaluate_outputs.py --outputs path\to\A2-public.jsonl --branch A2-public-medium --tightness medium --permission --asof --permission-file permission/output/permissions_medium.jsonl --run-metadata path\to\A2-public-metadata.json
```

**Trong demo:** nói rõ A0 tắt cả hai filter; A1 chỉ lọc permission sau khi sinh và giữ reasoning để evaluator chấm leakage riêng; A2 bật cả permission và as-of trong truy vấn. Ô OFF/ON là ablation as-of-only, không phải A1. Cho thấy A2-public đi qua cùng evaluator, IDs khớp đủ 1.500 câu, metadata ghi cùng model/retrieval budget. Nếu A2-public chưa chạy, ghi rõ phụ thuộc đang chờ Phú; không trình bày A2-private/MCP như phần Huy đã làm.

## W8 — Vá A1/A2 integration và kiểm thử hồi quy

**Mục tiêu:** sửa lỗi từ W7, làm rõ nguyên nhân nếu A1 từ chối nhiều câu hoặc provenance không đủ để kiểm tra event-level. Chạy lại test và evaluator sau khi sửa.

```powershell
python -m pytest tests/ -v
python scripts/evaluate_outputs.py --outputs results/a1/outputs_A1_medium.jsonl --branch A1-medium --tightness medium --permission --permission-file permission/output/permissions_medium.jsonl --run-metadata results/a1/run_metadata_A1_medium.json
```

**Trong demo:** so sánh pass rate/Hits@1/leakage trước và sau sửa, kèm số câu được chấm leakage. Không diễn giải leakage `null` hoặc không chấm được thành 0%.

## W9 — Đóng băng thiết kế và chạy ba mức quyền

**Mục tiêu:** khóa cấu hình, dữ liệu, seed và cách tính metrics; chạy các mức loose/medium/strict. Rà tài liệu lần hai.

Chạy A1 cho ba mức (cùng model, revision, retrieval budget và token budget):

```powershell
$ModelRevision = (Get-Content results/a0/run_metadata.json | ConvertFrom-Json).model_revision
python scripts/run_a1.py --tightness loose --threads 4 --retrieval-budget 64 --max-new-tokens 128 --revision $ModelRevision
python scripts/run_a1.py --tightness medium --threads 4 --retrieval-budget 64 --max-new-tokens 128 --revision $ModelRevision
python scripts/run_a1.py --tightness strict --threads 4 --retrieval-budget 64 --max-new-tokens 128 --revision $ModelRevision
```

Sau đó chấm từng output bằng `scripts/evaluate_outputs.py`, với `--permission`, permission file và metadata khớp mức tương ứng. Lưu cấu hình đóng băng và revision model cạnh kết quả.

**Trong demo:** trình bày ba cấu hình và số liệu thực, xác nhận mỗi output có đúng 1.500 ID, nêu những thay đổi thiết kế sau W7/W8 và kết quả rà soát tài liệu lần hai.

## W10 — Vá kiểm thử và hoàn tất dữ liệu phân tích

**Mục tiêu:** xử lý các vấn đề sau W9, đảm bảo evaluator xử lý thống nhất các nhánh và ghi rõ mẫu số của từng metric.

```powershell
python -m pytest tests/ -v
```

**Trong demo:** chọn một câu và lần theo question ID từ eval set → permission label → output → detailed result; giải thích riêng mẫu số leakage final/reasoning và temporal error. Temporal error được đo ở A0/A1 dù bộ lọc as-of tắt; chỉ tính trên câu đủ điều kiện theo phạm vi đã ghi trong README.

## W11 — Đủ 8 runs, bootstrap CI và cổng paper

**Mục tiêu:** có đủ tám lần chạy phân biệt theo thiết kế, tính paired bootstrap CI và quyết định có tiếp tục paper hay không.

Các ô OFF/OFF (A0) và OFF/ON (as-of-only ablation) được chạy một lần dùng chung; permission-only là A1, còn permission+as-of là A2-public, mỗi ô chạy theo ba mức quyền. A0 không lọc permission/as-of; A1 chỉ lọc permission sau khi sinh; A2 lọc cả hai trong truy vấn. Đảm bảo mỗi file `detailed_*.json` có cùng 1.500 question IDs.

Chấm output as-of-only do Phú bàn giao với permission OFF; permission file ở đây chỉ dùng làm nhãn tham chiếu leakage hậu nghiệm:

```powershell
python scripts/evaluate_outputs.py --outputs path\to\A2-public-asof-only.jsonl --branch asof-only --tightness medium --asof --permission-file permission/output/permissions_medium.jsonl --run-metadata path\to\A2-public-asof-only-metadata.json
```

```powershell
python scripts/summarize_experiment.py --a0 results/evaluated/detailed_A0.json --asof-only results/evaluated/detailed_asof-only.json --permission-loose results/evaluated/detailed_permission-loose.json --permission-medium results/evaluated/detailed_permission-medium.json --permission-strict results/evaluated/detailed_permission-strict.json --both-loose results/evaluated/detailed_both-loose.json --both-medium results/evaluated/detailed_both-medium.json --both-strict results/evaluated/detailed_both-strict.json --bootstrap 10000 --seed 42
```

**Output cần có:** `results/experiment_summary.json` với ΔP, ΔT, ΔPT, CI bootstrap ghép cặp 95% cho từng mức và CI cho độ lệch `ΔPT - ΔP - ΔT`. Chấm lại một output A0 với ba permission files không tạo thêm model run. Nếu thiếu bất kỳ ô nào trong tám ô, chưa đạt cổng W11.

**Trong demo:** trình bày bảng 2×2, ghi rõ hai ô permission-off dùng chung, kết quả CI, số câu đủ điều kiện của từng metric và quyết định paper theo cổng đề tài.

## W12 — Tuần đệm và bản thảo cho thầy đọc

**Mục tiêu:** dành thời gian sửa kết quả/diễn giải và gửi bản thảo cho thầy đọc; không tạo thêm run trừ khi phát hiện lỗi cần sửa.

**Trong demo:** trình bày bản thảo hiện tại, bảng kết quả có nguồn từ `experiment_summary.json`, các giới hạn về nhãn quyền synthetic và độ mịn thời gian theo năm. Ghi lại phản hồi của thầy và danh sách sửa.

## W13 — Bản thảo đầy đủ và phản biện chéo

**Mục tiêu:** hoàn tất bản thảo 6 trang nếu qua cổng W11, hoàn thiện phần báo cáo chung và phản biện chéo.

**Trong demo:** chỉ ra câu hỏi nghiên cứu, thiết kế, dữ liệu/seed, kết quả, CI, giới hạn và các thay đổi sau phản biện. Mọi con số cần truy về đúng output/evaluator run.

## W14 — Nộp

**Mục tiêu:** nộp báo cáo và các deliverables theo yêu cầu môn.

**Checklist demo:** báo cáo 12–15 trang; bản thảo 6 trang nếu được tiếp tục; mã nguồn và hướng dẫn chạy; seed/eval set/permission labels; kết quả và metadata; disclaimer nhãn quyền synthetic. Kiểm tra bản nộp mở được và không đưa model weights hay file chạy sinh ra vào gói mã nguồn nếu không được yêu cầu.

## W15 — Bảo vệ

**Mục tiêu:** trình bày vấn đề, phương pháp, bằng chứng thực nghiệm và giới hạn; chạy demo tái lập ngắn nếu được yêu cầu.

**Trong demo:** dùng kết quả đã lưu để tránh phụ thuộc tải model/mạng tại buổi bảo vệ. Chuẩn bị giải thích A0/A1/A2, MCP chỉ ở A2-private của Phú, cách đọc ΔP/ΔT/ΔPT và vì sao nhãn quyền tổng hợp không đại diện cho ACL của một hệ thống thật.
