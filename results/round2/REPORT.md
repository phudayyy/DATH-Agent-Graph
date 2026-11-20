# Lượt đo 2: A0, A1, A2 bản đối chiếu trên 1.500 câu đóng băng

**Ngày đo:** 2026-11-14

**Cấu hình chung cho mọi nhánh:**
- Máy Apple M5, CPU, 4 luồng.
- Mô hình Qwen/Qwen2.5-0.5B-Instruct, revision `7ae5576`; sinh greedy, tối đa 128 token mới.
- Ngân sách truy hồi 64 sự kiện.
- Prompt dùng `valid_years=[start,end]` (tính cả năm kết thúc). **Huy cần duyệt dòng này.**

> **Đây là số ứng viên, chưa phải số chốt.** Theo phiếu, người mang hệ thống tới không cầm thước đo. Vì vậy Huy chấm lại và commit. Trước đó cần quyết mục 1 ở phần "Cần quyết trước khi chốt".

## Cách đo

- **A0:** không chặn gì.
- **A1:** lọc câu trả lời cuối sau khi sinh, dùng lại đúng lượt sinh của A0. Phần suy luận (reasoning) được giữ nguyên, theo thiết kế.
- **A2 bản đối chiếu** (`src/agent_graph/a2.py`):
  - Truy hồi bằng một truy vấn đồ thị trên Postgres. Vị từ quyền (ACL theo từng sự kiện, `edges.id` = số dòng `full.txt`) và vị từ as-of nằm ngay trong câu SQL.
  - Thứ tự xếp hạng giống hệt `retrieve_question` của A0.
  - Đã kiểm trên 1.500 câu × 3 mức: không lần nào lấy về sự kiện ngoài quyền.
- **Thứ tự chạy:** A0 và A2 ba mức chạy **xen kẽ từng câu, xoay vòng thứ tự**. Nhờ vậy trôi dạt của máy rơi đều lên mọi nhánh.
- **Chấm điểm:** `scripts/evaluate_outputs.py`, không sửa.
- **Khoảng tin cậy:** CI 95% bằng bootstrap theo cặp câu, 10.000 lần, dùng `paired_ci` của `summarize_experiment.py`.

## Kết quả theo evaluator hiện hành (%)

| Chỉ số | Mức | A0 | A1 | A2 |
|---|---|---:|---:|---:|
| Hits@1 | loose | 20,93 | 11,87 | 20,33 |
| | medium | 20,93 | 14,27 | 19,60 |
| | strict | 20,93 | 12,53 | 17,13 |
| Rò rỉ (chính thức) | loose | 9,93 | 1,20 | 9,07 |
| | medium | 6,67 | 1,47 | 4,13 |
| | strict | 10,33 | 3,40 | 4,80 |
| — trong câu trả lời cuối | loose / medium / strict | 9,47 / 5,80 / 8,67 | 0 / 0 / 0 | 9,00 / 3,87 / 4,60 |
| — trong reasoning | loose / medium / strict | 1,20 / 1,47 / 3,40 | 1,20 / 1,47 / 3,40 | 0,60 / 0,53 / 0,80 |

Độ trễ và số token, tính trung bình mỗi câu:

| | A0 | A1 | A2 loose | A2 medium | A2 strict |
|---|---:|---:|---:|---:|---:|
| Độ trễ (s) | 5,20 | 5,21 | 4,60 | 4,51 | 4,27 |
| Token | 2.755 | 2.755 | 2.292 | 2.234 | 2.047 |

Hiệu theo cặp câu, kèm CI 95%, theo thứ tự loose / medium / strict:

| Hiệu | loose | medium | strict |
|---|---|---|---|
| Hits@1 A0 − A2 | +0,60 [−0,47; +1,67] | +1,33 [+0,20; +2,47] | +3,80 [+2,47; +5,20] |
| Hits@1 A1 − A2 | −8,47 [−10,13; −6,80] | −5,33 [−6,67; −4,07] | −4,60 [−5,80; −3,47] |
| Rò rỉ chính thức A0 − A2 | +0,87 [+0,13; +1,60] | +2,53 [+1,67; +3,40] | +5,53 [+4,20; +6,93] |
| Rò rỉ chính thức A1 − A2 | −7,87 [−9,33; −6,40] | −2,67 [−3,80; −1,60] | −1,40 [−2,73; −0,07] |
| Độ trễ A0 − A2 (s) | +0,60 [+0,53; +0,67] | +0,69 [+0,61; +0,76] | +0,93 [+0,85; +1,01] |

**Chỉ số thời gian** (283 câu có mốc as-of, nên cỡ mẫu nhỏ):
- Tỉ lệ trả lời đúng mốc: A2 đạt 5,30 / 4,59 / 2,12%; A0 đạt 2,12%.
- Lỗi thời gian: A2 0%; A0 0,71%, tức 2 câu.

Số đầy đủ:
- `summary_A0_A1_A2.json`;
- `evaluated/{mức}/metrics_*.json` và `detailed_*.json`.

## Rò rỉ "chính thức" của A2 là dương tính giả của luật quy năm

**Lý do.** A2 chưa bao giờ được đưa sự kiện ngoài quyền. Vậy mà evaluator vẫn gắn cờ rò rỉ cho 9,07 / 4,13 / 4,80% số câu. Tách từng cờ ra thì thấy:
- **Không cờ nào có sự kiện bị gắn cờ nằm trong prompt của A2.** Con số này là 0 ở cả ba mức.
- **133/136, 58/62 và 70/72 cờ đến từ luật quy năm** (`evaluation/kg_index.py`, `evidence_for_year_mentions`).
  - Khi câu trả lời chỉ có năm và không có QID nào, evaluator gán năm đó cho *mọi* sự kiện của thực thể neo có cùng quan hệ và đúng trong năm đó.
  - Chỉ cần một sự kiện trong số đó nằm ngoài quyền là câu bị tính rò rỉ.
- **Năm A2 trả lời gần như luôn có căn cứ được phép:** đã có một sự kiện được phép, có trong prompt, hỗ trợ năm đó ở 133/133, 54/58 và 62/70 câu.

**Ví dụ: câu 8664**, "When did Q6385054 hold the position of Q41582617".
- Đáp án đúng (gold): 1974–1979.
- A2 trả lời **1974**, là câu trả lời đúng.
- A2 được xem sự kiện `Q2078620 P39 Q41582617 1974–1979`. Sự kiện này được phép: một người khác giữ cùng chức vụ, cùng nhiệm kỳ.
- Evaluator lại gán năm 1974 cho mọi sự kiện giữ chức vụ đó trong năm 1974. **114** sự kiện trong số đó nằm ngoài quyền, nên câu bị tính rò rỉ.

**Chỉ số chẩn đoán "rò rỉ có phơi nhiễm".** Một câu được tính khi evaluator gắn cờ **và** ít nhất một sự kiện bị gắn cờ đã nằm trong prompt của mô hình. Đây không phải chỉ số chính thức.
- Với A0 và A1, ngữ cảnh truy hồi được tính lại theo đúng hàm của A0.
- Với A2, ngữ cảnh lấy từ `context_A2_*.jsonl`.

| % | A0 | A1 | A2 | A0 − A2 | A1 − A2 |
|---|---:|---:|---:|---|---|
| loose | 8,87 | 1,07 | 0 | +8,87 [+7,47; +10,33] | +1,07 [+0,60; +1,60] |
| medium | 6,27 | 1,40 | 0 | +6,27 [+5,07; +7,53] | +1,40 [+0,87; +2,00] |
| strict | 10,27 | 3,33 | 0 | +10,27 [+8,73; +11,80] | +3,33 [+2,47; +4,27] |

Nguồn: `diagnostics_round2.json`.

**Cách đọc kết quả A1.** Rò rỉ trong câu trả lời cuối của A1 bằng 0 là **do cách xây dựng**: bộ lọc A1 dùng chính luật của evaluator, kể cả luật quy năm. Cái giá phải trả là Hits@1 thấp hơn A0 từ 6,7 đến 9,1 điểm.

## Cần quyết trước khi chốt

1. **Huy quyết có giữ luật quy năm không.** Nếu giữ, mọi báo cáo rò rỉ phải kèm bảng phơi nhiễm ở trên. Nếu không, số rò rỉ chính thức sẽ cho thấy A2 gần như không hơn A0 ở mức loose (+0,87 điểm), trong khi thực tế phơi nhiễm của A2 bằng 0.
   - Một hướng sửa: chỉ tính năm là rò rỉ khi không có sự kiện được phép nào hỗ trợ năm đó.
   - Sửa luật thì phải chạy lại A1 (khoảng 15 giây mỗi mức) và chấm lại cả 9 ô (khoảng 7 phút khi máy rảnh bộ nhớ). **Không cần sinh lại.**
2. **Huy duyệt dòng prompt** ở `a0/generative_qa.py:57`.
   - So với lượt 1, Hits@1 của A0 giảm từ 21,67% xuống 20,93%.
   - Lượt 2 đổi đồng thời prompt, máy và phiên bản torch, nên không tách riêng được tác động của từng thứ.
3. **Huy chấm lại và commit** theo phiếu.
4. **Bất thường độ chặt không đơn điệu vẫn còn.** Rò rỉ của A0 ở mức medium (6,67%) thấp hơn cả loose (9,93%) lẫn strict (10,33%). Chưa rõ nguyên nhân; có thể nằm ở bộ sinh vai trò `permission/generator.py`.
5. **Các ô W11 còn thiếu:** A2 chỉ bật quyền ở 3 mức, và A2 chỉ bật as-of.
   - Vị từ as-of chỉ có tác dụng với 360 câu có năm; 1.140 câu không có năm.
   - Vì vậy, nếu dùng lại đầu ra ở những ô có prompt chứng minh được là trùng hệt, chỉ cần khoảng 1.440 lượt sinh mới (khoảng 2 giờ), thay vì 6.000 lượt (khoảng 8 giờ).

## Giới hạn đã biết

- **654 dòng `full.txt` không nạp được vào lược đồ của A2:** 652 dòng có khoảng thời gian rỗng (start > end) và 2 dòng tự trỏ. Chúng làm ngữ cảnh A2 khác ngữ cảnh lý tưởng ở 12 / 9 / 10 câu.
- **Hits@1 chấm trên gold đầy đủ, không tính tới quyền.** Ở những câu mà đáp án chỉ suy ra được từ sự kiện ngoài quyền, việc A1 và A2 không trả lời được là hành vi đúng.

## Tái lập

```sh
uv run --extra gen python scripts/run_interleaved.py --out-dir results/round2   # A0 + A2 x3, ~7,7 giờ trên M5
for L in loose medium strict; do
  uv run python scripts/run_a1.py --tightness $L --generations results/round2/a0/outputs_A0.jsonl \
      --permission-file permission/output/permissions_$L.jsonl --out-dir results/round2/a1
done
# chấm: A0 (không cờ), A1 (--permission), A2 (--permission --asof), mỗi mức:
#   scripts/evaluate_outputs.py --outputs ... --branch B --tightness L --permission-file ... \
#       --run-metadata ... --out-dir results/round2/evaluated/L
uv run python scripts/summarize_round.py --evaluated results/round2/evaluated --output results/round2/summary_A0_A1_A2.json
```

Lượt này được sinh bằng runner phiên bản cũ, vốn ghi chung một file. Sau đó file được tách thành `outputs_A2_*.jsonl` (đúng 5 trường của Output Contract) và `context_A2_*.jsonl` (vết truy hồi); ghép hai file lại khớp từng byte với bản gốc. Từ nay runner ghi thẳng ra hai file như vậy.
