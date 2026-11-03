# PHIẾU ĐỀ TÀI · ĐỒ ÁN TỔNG HỢP CO3101

**Trường Đại học Bách Khoa, ĐHQG-HCM**
**Khoa Khoa học và Kỹ thuật Máy tính**

Nhóm 06 · Tầng ngữ cảnh có phân quyền cho agent · học kỳ 261 · giảng viên hướng dẫn: TS. Nguyễn An Khương

| Mục | Nội dung |
|---|---|
| Mã đề tài | G1 |
| Tên tiếng Việt | Đo cái giá phải trả khi tầng ngữ cảnh chặn theo quyền và chặn theo mốc thời gian |
| Tên tiếng Anh | The accuracy cost of permission and as-of-time filtering in an agent context layer |
| Sinh viên | 2411166 Dương Minh Huy; 2412677 Nguyễn Triệu Phú |
| Quy mô | Mở rộng, hướng tới bài báo (try-hard) · độ khó 5/5 · tiềm năng bài báo: Cao |
| Giờ người ước lượng | Bắt buộc 112 giờ; 5,1 giờ mỗi sinh viên mỗi tuần; trần của một tín chỉ là 50 tới 60 giờ mỗi sinh viên |

---

## 1. Bài toán

Bản đặc tả MCP ngày 28/07/2026 có hẳn mục Authorization, nhưng phần uỷ quyền nằm ở tầng vận chuyển và tính theo phạm vi OAuth cho cả máy chủ, không phải cho từng tài nguyên. Trang Resources của chính bản đặc tả đó nói tập tài nguyên trả về CÓ THỂ thay đổi theo uỷ quyền của lượt gọi, và nói quyền trên tài nguyên NÊN được kiểm trước khi thao tác, nhưng không định nghĩa mô hình quyền nào. Nói cách khác, giao thức cho phép lọc theo người hỏi mà không quy định cách làm, và để toàn bộ việc đó cho người hiện thực.

Phần đã có người làm: một bài ở TrustNLP 2026 cho thấy lọc sau khi truy hồi để lọt thông tin ngoài quyền trong phần lớn ca thử, nên THỨ TỰ CHẶN đã có câu trả lời và đề tài này không đi làm lại. Phần hệ thống cũng đã có người làm, theo hướng đánh chỉ mục và phân vùng theo vai trò.

Chỗ còn trống, và là chỗ đề tài này đứng: chưa ai đặt kiểm soát truy cập cạnh truy hồi CÓ THỜI GIAN, và chưa có bộ đánh giá hỏi đáp trên đồ thị tri thức có gắn quyền. Bộ chuẩn gần nhất về mặt phương pháp nằm ở text-to-SQL chứ không ở truy hồi.

Một sinh viên trong nhóm đã có sẵn hệ thống và đã tự chạy một khung đánh giá trên đó: bộ vàng 20 câu, corpus 305 tài liệu, không rò quyền nào trong 5 lần chạy. Đề tài học kỳ này KHÔNG phải làm lại việc đó mà là làm cho nó nói được điều gì.

Chỗ yếu của bằng chứng hiện có nằm ở cỡ mẫu và ở chỗ không có đường đối chứng: 5 lần chạy lặp trên cùng 20 câu nên cỡ mẫu hiệu dụng gần 20 hơn 100, và theo quy tắc chia ba thì không rò trên 20 phép thử chỉ cho cận trên 95 phần trăm chừng 14 phần trăm. Nói gọn, số hiện có chưa phân biệt được không rò với rò mười bốn phần trăm, và nó chỉ đo trên một hệ duy nhất, không so với cách làm nào khác.

## 2. Câu hỏi nghiên cứu

Khi tầng ngữ cảnh vừa chặn theo quyền của người hỏi vừa chặn theo mốc as-of, độ đúng mất bao nhiêu so với đồ thị không chặn, và hai phép chặn cộng dồn hay độc lập?

Phát biểu để bác bỏ được: gọi `delta_P` là phần độ đúng mất khi chỉ bật chặn quyền, `delta_T` khi chỉ bật chặn thời gian, `delta_PT` khi bật cả hai. Giả thuyết không cần bác bỏ là `delta_PT = delta_P + delta_T`.

Thiết kế là một bảng hai nhân hai, lặp trên ba mức độ chặt của mạng lưới quyền. Đếm cho đúng ngay từ đầu để khỏi hứa thừa: chỉ có **TÁM** lần chạy phân biệt được, không phải mười hai.

- Hai ô tắt chặn quyền chạy một lần mỗi ô vì kết quả không phụ thuộc độ chặt.
- Hai ô bật chặn quyền chạy ba lần mỗi ô theo ba mức.
- Hai lần chạy ở ô tắt cả hai phép chặn dùng chung cho cả ba mức, và phải ghi rõ điều đó trong bảng chứ không chép lại số thành ba dòng như thể đã đo ba lần.

## 3. Dữ liệu và giấy phép

**CRONQUESTIONS** (giấy phép MIT, 410.000 câu hỏi, đồ thị 125.000 thực thể, 203 quan hệ, 328.000 sự kiện có mốc thời gian theo năm), từ Saxena, Chakrabarti và Talukdar, ACL-IJCNLP 2021.

**BẮT BUỘC** dùng bản `data_v2.zip` chứ không dùng `data.zip`: chính tác giả ghi trong README rằng 31.951 trên 410.000 câu của bản đầu có lỗi dữ liệu. Khoá một tập con 1.500 câu lấy ngẫu nhiên có hạt giống ở W4, vì 410.000 câu là quá sức một học kỳ.

Nhãn quyền là **TỔNG HỢP**, không có thật: sinh một mạng lưới vai trò trên các quan hệ và thực thể của đồ thị bằng một hạt giống cố định, ba mức độ chặt.

Lý do phải tổng hợp chứ không lấy quyền thật: quyền thật trong hệ của Phú đến từ ACL của 26 nguồn SaaS của một tổ chức, tức vừa là dữ liệu riêng vừa không công bố được, nên không ai lặp lại được kết quả. Một mạng lưới tổng hợp có hạt giống thì ai cũng dựng lại được.

Phải viết thẳng vào báo cáo và vào bản thảo rằng nhãn thời gian là thật còn nhãn quyền là tổng hợp, nên mọi con số về rò rỉ nói về cơ chế chứ không nói về một hệ thật đang chạy.

## 4. Baseline bắt buộc

Ba nhánh, cùng một mô hình sinh, cùng một ngân sách truy hồi:

- **(A0)** Không chặn gì, cho trần độ đúng và cho mức phơi tối đa.
- **(A1)** Chặn sau khi sinh, tức sinh xong rồi mới xoá hoặc từ chối nếu có sự kiện ngoài quyền. Đây là cách làm phổ biến trong sản phẩm.
- **(A2)** Đẩy vị từ quyền và vị từ as-of vào thẳng truy vấn đồ thị. A2 có **HAI** hiện thực và cả hai đều bắt buộc:
  1. Một hiện thực đối chiếu trên kho công khai, viết trong học kỳ, ai cũng chạy lại được.
  2. Hệ riêng của Phú gọi qua MCP.

Hiện thực đối chiếu không phải phương án dự phòng mà là điều kiện để kết quả tái lập được.

> Một đồ án không có baseline thì không có gì để so, và con số đẹp tới đâu cũng không nói lên điều gì.

## 5. Chỉ số đo

- **Hits@1** theo đúng cách chấm của CronKGQA, để số của đề tài đặt cạnh được số đã công bố.
- **Tỉ lệ rò**: tỉ lệ câu trả lời có nhắc ít nhất một sự kiện ngoài phạm vi quyền của người hỏi, đo riêng cho phần trả lời cuối và cho phần suy luận trung gian.
- **Tỉ lệ lỗi thời**: tỉ lệ câu đúng theo trạng thái mới nhất nhưng sai theo mốc as-of được hỏi.
- **Độ trễ** và **số token**.

Tính chất an toàn phải phát biểu rõ mỗi lần nhắc tới, không được viết gọn thành "an toàn": bí mật của các sự kiện ngoài quyền, trước một đối thủ CHỈ ĐỌC, kiểm soát được câu hỏi bằng ngôn ngữ tự nhiên, nhưng không kiểm soát được đồ thị, không kiểm soát được máy chủ MCP, và không có kênh phụ nào. Ngoài đúng mô hình này thì đề tài không phát biểu gì.

## 6. Kiểm thử bắt buộc

Ba kiểm thử chạy bằng pytest trong `tests/`:

1. Bộ sinh mạng lưới quyền với cùng một hạt giống sinh lại đúng cùng một phân quyền, và với một vai trò giả đã biết đáp án thì tập sự kiện nhìn thấy được đúng bằng tập tính tay.
2. Hàm chấm tỉ lệ rò cho đúng kết quả tay trên năm câu trả lời giả, trong đó bắt buộc có một câu để lọt sự kiện ngoài quyền CHỈ ở phần suy luận chứ không ở câu trả lời cuối, vì nếu chỉ chấm câu trả lời cuối thì nhánh A1 sẽ được điểm cao giả tạo.
3. Truy vấn as-of trên một thực thể giả có ba khoảng valid time trả về đúng ba giá trị khác nhau tại ba mốc, và tại đúng biên thì trả về theo quy ước nửa khoảng đóng bên trái đã ghi trong README.

Ba kiểm thử này chấm 15 điểm trong tiêu chí A3. Chúng bắt loại lỗi không làm chương trình sập mà chỉ làm con số sai, và đó đúng là loại lỗi mà đồ án dạng này hay mắc.

## 7. Sản phẩm phải nộp

- Kho mã tái lập được.
- Tập đánh giá có quyền và có mốc thời gian, công bố kèm hạt giống và kèm bộ sinh.
- Bảng hai nhân hai `delta_P`, `delta_T`, `delta_PT` trên ba mức độ chặt, kèm khoảng tin cậy bootstrap.
- Một trang khai ranh giới giữa phần có trước học kỳ và phần làm trong học kỳ.
- Báo cáo 12 đến 15 trang.
- Bản thảo 6 trang nếu qua cổng W11.

**Phạm vi tối thiểu**, tức phần còn lại nếu cắt hết phần mở rộng: bảng hai nhân hai ở MỘT mức độ chặt duy nhất, chạy trọn trên hiện thực đối chiếu công khai, không dùng hệ riêng, không quét độ chặt, không bài báo. Phần này chạy được trên CPU và không phụ thuộc một dòng mã nào có trước học kỳ. Đó là lý do đề tài này giao được cho một nhóm mà một người mang theo hệ thống có sẵn.

## 8. Mốc tuần

| Tuần | Mốc |
|---|---|
| **W4** | Hai việc, cả hai đều là điều kiện tồn tại của đề tài. **(1) ĐÓNG BĂNG MỐC NỀN:** Phú nộp mã băng commit của hệ riêng tính tới 28/09/2026, số kiểm thử đang xanh tại đúng commit đó, toàn bộ số liệu đánh giá đã có, và một trang khai rõ cái gì có trước học kỳ, cái gì làm trong học kỳ. Trang này vào báo cáo và vào mục đóng góp của bản thảo. **(2)** Huy tải CRONQUESTIONS bản data_v2, dựng bộ sinh mạng lưới quyền, và khoá tập 1.500 câu kèm hạt giống vào README. |
| **W5** | Nhánh A0 chạy trọn một vòng trên tập đã khoá, có số Hits@1 đầu tiên. |
| **W6** | Không có mốc mới (vá mốc tuần trước, viết kiểm thử). |
| **W7** | Nhánh A1 và hiện thực đối chiếu của A2 cùng chạy được. |
| **W8** | Không có mốc mới (vá mốc tuần trước, viết kiểm thử). |
| **W9** | Đóng băng thiết kế thí nghiệm, chạy quét ba mức độ chặt. |
| **W10** | Không có mốc mới (vá mốc tuần trước, viết kiểm thử). |
| **W11** | Đủ số cho bảng hai nhân hai kèm khoảng tin cậy; quyết đi tiếp hay dừng phần bài báo. |
| **W12** | Tuần đệm, thầy đọc bản thảo. |
| **W13** | Bản thảo đầy đủ và phản biện chéo. |
| **W14** | Nộp. |
| **W15** | Bảo vệ. |

## 9. Cổng đi tiếp hay dừng

- **Tới hết W4:** trang khai ranh giới đã nộp và tập 1.500 câu đã khoá kèm hạt giống. Không đạt thì đề tài thu về phạm vi tối thiểu ngay, không chờ.
- **Tới hết W7:** hiện thực đối chiếu của A2 chạy được trên kho công khai. Không đạt thì bỏ nhánh hệ riêng.
- **Tới hết W11:** đủ tám lần chạy phân biệt được, tức bốn ô của bảng hai nhân hai với hai ô có nhân ba mức độ chặt, kèm khoảng tin cậy bootstrap. Thiếu thì bỏ phần bài báo, giữ phần báo cáo.

## 10. Rủi ro và cách chặn

1. **Phần đã có trước lấn phần của học kỳ.** Đây là rủi ro lớn nhất và nó là rủi ro học thuật chứ không phải kỹ thuật. Chặn bằng ba việc: trang khai ranh giới ở W4; quy ước mọi commit của học kỳ nằm trong một kho riêng chứ không đẩy vào kho cũ; và 20 điểm vấn đáp hỏi thẳng vào phần làm trong học kỳ.
2. **Hệ riêng không tái lập được vì là kho riêng:** vì thế hiện thực đối chiếu trên kho công khai là bắt buộc, không phải tuỳ chọn. Tới hết W7 chưa có hiện thực đối chiếu thì bỏ hẳn nhánh hệ riêng và đề tài chạy trọn trên kho công khai.
3. **Huy thành người phụ trong hệ của bạn cùng nhóm:** chặn bằng phân công. Huy sở hữu tập đánh giá, bộ sinh quyền, khung đo và hai nhánh A0 với A1, cả bốn thứ đều chạy được mà không cần một dòng mã nào của Phú. Đường tới hạn của đề tài không đi qua hệ riêng.
4. **Bị làm trước:** rà lại tài liệu ở W5 và W9. Đã có bài về thứ tự chặn, nên nếu đóng góp trôi về phía đó thì đề tài mất chỗ đứng. Đóng góp phải nằm ở tương tác giữa hai phép chặn và ở bộ đánh giá.
5. **Mốc thời gian của CRONQUESTIONS chỉ mịn tới năm,** và đã có bài chỉ ra đúng hạn chế này rồi đề xuất bộ mịn hơn. Ghi vào phần hạn chế, không đổi bộ dữ liệu giữa học kỳ.
6. **Nhãn quyền tổng hợp:** không được viết một câu nào ngụ ý đây là số đo trên hệ thật.

## 11. Phân công

| Sinh viên | Phần việc |
|---|---|
| **Dương Minh Huy** (2411166) | Nửa **ĐO**, và là phạm vi tối thiểu đủ đạt môn mà không cần một dòng mã nào của bạn cùng nhóm: tập đánh giá 1.500 câu chốt từ CRONQUESTIONS bản data_v2, bộ sinh mạng lưới quyền có hạt giống ở ba mức độ chặt, khung đo gồm Hits@1 theo cách chấm của CronKGQA, tỉ lệ rò và tỉ lệ lỗi thời, cùng hai nhánh A0 không chặn và A1 chặn sau khi sinh. Là người chịu trách nhiệm cuối về số liệu. |
| **Nguyễn Triệu Phú** (2412677) | Nửa **BỊ ĐO**: đẩy vị từ quyền và vị từ as-of vào thẳng truy vấn đồ thị, tức nhánh A2, ở hai hiện thực. Hiện thực đối chiếu trên kho công khai viết trong học kỳ là BẮT BUỘC và phải xong trước W7; hệ riêng gọi qua MCP là nhánh thứ hai. Cộng với ablation tách riêng tác dụng của hai vị từ, và trang khai ranh giới giữa phần có trước học kỳ và phần làm trong học kỳ. |

## 12. Tài liệu phải đọc

- aclanthology.org/2021.acl-long.520 (CronKGQA và bộ CRONQUESTIONS; arXiv:2106.01515; kho github.com/apoorvumang/CronKGQA, MIT, dùng data_v2.zip)
- arXiv:2406.14191 bản v4 ngày 14/01/2026 (khảo sát hỏi đáp trên đồ thị tri thức có thời gian, bản cập nhật gần nhất)
- aclanthology.org/2023.acl-long.637 (MultiTQ, phê phán độ mịn theo năm của CRONQUESTIONS)
- aclanthology.org/2026.trustnlp-main.15 (chặn quyền trước khi truy hồi, TrustNLP 2026, công trình gần nhất, **PHẢI đọc trước W5**)
- arXiv:2607.22115 (chuẩn đánh giá text-to-SQL dưới kiểm soát truy cập theo vai trò, khuôn mẫu phương pháp gần nhất)
- arXiv:2505.01538 (HONEYBEE, phân vùng theo vai trò cho cơ sở dữ liệu vector)
- arXiv:2601.11199 (SD-RAG, chính sách công bố chọn lọc đặt trong truy hồi)
- arXiv:2509.14608 (kiểm soát truy cập theo người tham gia, có triển khai thật)
- modelcontextprotocol.io/specification/2026-07-28/basic/authorization và .../server/resources (bản đặc tả 28/07/2026)
- SIGMOD Record 41(3) 2012, Kulkarni và Michels, doi 10.1145/2380776.2380786 (định nghĩa hai chiều thời gian trong SQL:2011: valid time và transaction time)

Mọi đường dẫn đối chiếu ngày 23 và 24/09/2026.

## 13. Ghi chú của giảng viên

Nhóm lập ngày 23/09, ngay sau khi Phú gửi hồ sơ, muộn mười tám ngày so với hạn chót 05/09 của cả lớp.

Lý do ghép: Phú mang tới đúng loại hệ thống mà đề tài cần đo, Huy mang tới năng lực truy hồi có thứ hạng thật và một chỗ đứng độc lập. Lý do KHÔNG ghép Phú với Trí: đề tài của Trí đã thành hình quanh một người, ghép thêm một người mạnh vào đó thì Trí không còn gì để sở hữu, và 20 điểm vấn đáp sẽ phơi ra điều đó ngay ở buổi bảo vệ.

Nguyên tắc phân công của nhóm này, phát biểu một lần cho rõ: **người mang hệ thống tới không được đồng thời là người cầm thước đo.** Vì thế toàn bộ tập đánh giá, bộ sinh quyền và khung đo thuộc về Huy, và đường tới hạn của đề tài không đi qua kho riêng của Phú.

**BA CÂU PHẢI HỎI Ở BUỔI W4**, hỏi thẳng và ghi biên bản:

1. Cái gì đã có trước ngày 28/09: nộp mã băng commit, số kiểm thử đang xanh tại đúng commit đó, và mọi số liệu đánh giá đã có.
2. Khung đánh giá em đã chạy gồm những gì: bộ vàng 20 câu chọn ra sao, corpus 305 tài liệu lấy từ đâu, 5 lần chạy khác nhau ở chỗ nào. Rồi chỉ cho em thấy bằng số vì sao nó chưa đủ: 5 lần lặp trên cùng 20 câu nên cỡ mẫu hiệu dụng gần 20, và theo quy tắc chia ba thì không rò trên 20 phép thử chỉ cho cận trên chừng 14 phần trăm. Đó là chỗ học kỳ này có việc để làm, và là cách mở đề tài tự nhiên nhất.
3. Hệ riêng có mở mã được không; nếu không thì mọi số của bản thảo phải lặp lại được trên hiện thực đối chiếu, và đó là lý do hiện thực đó bắt buộc.

Quy ước commit: mọi việc của học kỳ nằm trong một kho mới của môn, không đẩy vào kho cũ, để nhật ký commit đọc được như bằng chứng đóng góp.

Rủi ro tiến độ của Phú để mức trung bình chứ không thấp. Bằng chứng về nhịp làm việc có ba mảnh và cả ba cùng hướng: muộn mười tám ngày so với hạn, trả lời sai luồng sau hai lần được dặn, và nộp ảnh chụp màn hình thay cho tệp CV. Không mảnh nào đụng tới năng lực, nhưng cả ba nói đúng thứ cần biết trước khi giao một đề tài có bốn cổng. Nhịp trả lời sẽ được đọc lại ở cổng W4 và W7.

---

Sinh viên ký xác nhận đã đọc và đồng ý phạm vi: ......................................................

Ngày: ......../......../2026
