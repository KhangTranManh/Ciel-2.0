# Ciel 2.0 — Tổng hợp cấu trúc & lý do đằng sau

File này giải thích **Ciel 2.0 được xây dựng như thế nào và TẠI SAO lại như vậy** —
không chỉ liệt kê cấu trúc mà còn nối lại lý do đằng sau từng quyết định thiết kế, để
đọc một lần là hiểu được bức tranh tổng thể. Chi tiết ổn định (rule, quy ước) nằm ở
[`instructionAI/`](instructionAI/); thay đổi mới nhất nằm ở [`note.md`](note.md).

---

## 1. Ciel 2.0 là gì

Ciel là một trợ lý AI **thực sự hành động** trên máy/tài khoản của Master (đọc/ghi file,
gửi email, chạy shell, tra cứu giá, tìm kiếm web, điều khiển màn hình) — không phải một
chatbot mô tả việc có thể làm. Nó không phải "1 lệnh gọi LLM bọc trong giao diện chat":
nó là một **pipeline nhiều tầng model**, mỗi tầng một vai trò riêng, cộng với một lớp an
toàn được thiết kế **tách biệt hoàn toàn** khỏi việc lọc nội dung.

---

## 2. Triết lý cốt lõi — lý do MỌI quyết định thiết kế đều xuất phát từ đây

> **Quyết định là Python tất định (deterministic); model chỉ lên kế hoạch hoặc soạn thảo.**

Tại sao nguyên tắc này quan trọng đến mức xuyên suốt toàn bộ project:

- **Model có thể đổi, hạ cấp, hoặc bị nhà cung cấp thay silently** (đã xảy ra thật: Vilao
  hết quota 27/07, phải chuyển sang endpoint `custom` chỉ bằng đổi 2 biến `.env`). Nếu an
  toàn/logic nghiệp vụ phụ thuộc vào việc model "nhớ" phải làm gì, một model yếu hơn hoặc
  một alias khác sẽ âm thầm phá vỡ nó.
- **Một guard khoá cứng vào ĐÚNG chuỗi model xuất ra là giòn** — bài học lặp lại nhiều lần
  trong session: model mạnh hơn diễn đạt lại (paraphrase) và né qua guard. Luôn khớp theo
  **pattern/ý định**, không khớp theo chuỗi chính xác.
- Hệ quả trực tiếp: **hầu như mọi cơ chế an toàn/nghiệp vụ trong Ciel đều là code Python
  thuần**, không phải "prompt bảo model cẩn thận". Middleware (tầng duy nhất dùng LLM để
  kiểm tra) cố tình *fail-open* và *chỉ* áp dụng cho email — không bao giờ là điểm lỗi
  duy nhất cho một hành động gửi thật.

---

## 3. Kiến trúc lõi: Brain → Router → Middleware → Worker

```
User input → RAG recall → Router (Brain) phân loại ý định
    → chat/tool/code/multi_tool → Safety Gate → Execute → Self-Healing (nếu lỗi)
    → Worker định dạng câu trả lời → Middleware (nếu là email) → trả lời
```

- **Brain/Router** — model rẻ/nhanh, chỉ trả về JSON quyết định (KHÔNG bao giờ tự viết
  câu trả lời cuối cùng cho Master — xem mục 6, đây từng là 1 bug thật).
- **Worker** — model soạn nội dung/code thực tế, luôn nhận đúng câu chữ gốc của Master.
- **Middleware** — tầng thứ 3, tuỳ chọn, review ngữ nghĩa nội dung email trước khi gửi —
  cố ý *fail-open* (nếu lỗi, vẫn cho gửi) vì nó là lớp kiểm tra bổ sung, không phải điểm
  chặn duy nhất.

Xung quanh lõi này: **tool pack tự đăng ký** (`skills/internal/`, `skills/external/`) —
thêm 1 file `*_ops.py` có hàm `get_*_tools()` là tự động xuất hiện trong danh sách tool,
không cần sửa gì ở lõi. Đây là lý do vì sao 30+ tool tồn tại mà `core/` không hề "biết"
về từng cái riêng lẻ.

---

## 4. Bảy tier năng lực — TẠI SAO mỗi cái tồn tại

Đây là phần quan trọng nhất để hiểu "tại sao thành ra thế này": mỗi tier đóng đúng 1
khoảng trống cụ thể giữa "thực thi lệnh" và "theo đuổi mục tiêu".

| Tier | Vấn đề nó giải quyết | Module |
|---|---|---|
| **1 — Agent loop** | Một kế hoạch phẳng (flat plan) không thể diễn đạt "nếu X thì Y" — VD "kiểm tra git status, nếu sạch thì commit" là **không thể biểu diễn được**, không chỉ khó lên kế hoạch. | `core/continuation.py` |
| **2 — Task state** | Việc bị ngắt giữa chừng (crash, đóng terminal) biến mất không dấu vết; "đang làm gì?" phải đoán. | `core/task_state.py` |
| **3 — Permissions** | Approval trước đây nhị phân (Y/N) và đến **giữa lúc thực thi** — từ chối bước 3/4 thì bước 1-2 đã lỡ làm rồi. | `core/permissions.py` |
| **4 — Context discipline** | **99% mỗi lệnh gọi Brain là overhead cố định** (router prompt 37%, tool list 33%, persona 28%, câu hỏi thật của Master chỉ 0.4%). Không có ngân sách token, không có nhật ký kiểm tra. | `core/context.py` |
| **5 — Interruptibility** | Một yêu cầu dài không dừng được — CLI là vòng lặp `input()` chặn, cách duy nhất thoát khỏi 1 lệnh 566 giây là kill process. | `request_cancel()` |
| **6 — Proactivity** | Ciel trước đây chỉ biết trả lời, không bao giờ tự lên tiếng khi có điều kiện thật xảy ra (VD: 1 việc bị treo, giá vàng chạm ngưỡng). | `core/notifier.py`, `core/triggers.py` |
| **7 — User model** | Kho `facts.json` **pull-only và trống rỗng** suốt nhiều tháng — model phải tự đoán đúng key rồi chủ động gọi `get_fact`; không ai làm vậy. Một bộ nhớ chỉ hoạt động khi ai đó nhớ dùng nó thì không phải bộ nhớ. | `core/user_model.py` |

**Một luật xuyên suốt cả 7 tier**: mọi *quyết định* (có nên lặp lại vòng lặp không, có
nên hỏi xác nhận không, có nên lên tiếng không, có nên tin 1 trait không) đều là **hàm
Python thuần, miễn phí, chạy trước** — model chỉ được hỏi khi tín hiệu tất định đã nói
"cần thêm ý kiến ở đây". Đây là lý do một request bình thường tốn **0 lệnh gọi thêm** dù
có 7 tier chạy phía sau.

---

## 5. Ba front-end — cùng 1 lõi, khác cách vào

```
main.py          → CLI, input() chặn, --voice/--speak
main_api.py       → FastAPI + WebSocket, phục vụ UI React/Tauri + frontend Vercel
main_telegram.py  → Bot Telegram, long-poll trực tiếp Bot API
```

Cả 3 đều là **wrapper mỏng** quanh `core.agent_loop.AgentLoop` / `core.llm_connector.CielCore`
— không có logic nghiệp vụ riêng ở tầng front-end. Mỗi front-end chỉ khác nhau ở
**cách hỏi xác nhận** (CLI: `input()` chặn; API: JSON qua WebSocket; Telegram: nút bấm
Yes/No) nhưng dùng chung 1 chữ ký hàm `confirm_callback(tool_name, preview, tool_args) -> bool`.

**Vì sao Telegram cần allow-list `chat_id`**: đây là front-end có thể chạy tool thật
(shell, email, xoá file) — nếu không chặn người lạ nhắn tin, họ điều khiển được máy thật.
Việc kiểm tra này xảy ra **trước khi** tin nhắn chạm tới `core.process()`, không nằm
trong tool hay prompt.

---

## 6. Vì sao Docker được cấu trúc thành 2 file compose riêng

```
docker/Dockerfile                    → 1 image dùng chung
docker/docker-compose.api.yml        → service ciel-api (main_api.py, cho Vercel)
docker/docker-compose.telegram.yml   → service ciel-telegram (main_telegram.py)
```

Tách 2 file thay vì 1 file có 2 service: để mỗi front-end có **vòng đời độc lập** — build/
start/stop/deploy cái này không đụng cái kia, có thể đặt trên 2 host khác nhau sau này.

**Vì sao vision bị tắt trong Docker**: `vision_act`/`vision_describe` cần màn hình/chuột
thật (`pyautogui`) — container Linux không có. `.env`'s `DISABLED_SKILL_MODULES=vision_ops`
khiến `ToolManager` bỏ qua cả module **trước khi import**, không phải lỗi runtime.

**Vì sao vẫn giữ `sentence-transformers`/torch dù nặng ~3.5GB**: từng thử đổi sang
`DefaultEmbeddingFunction` (ONNX) của ChromaDB để bỏ torch — phát hiện ChromaDB **lưu
cứng lựa chọn embedding function NGAY TRONG collection**, nên đổi runtime không migrate
được collection thật đã có (1250+ bộ nhớ thật) — nó chỉ lỗi lúc truy vấn. Giữ lại thư
viện cũ, chỉ cài `torch` bản CPU-only trước để tránh kéo theo CUDA (~10GB → ~3.5GB).

---

## 7. Những bug thật đã tìm ra trong quá trình này — và bài học chung

Tất cả bug dưới đây được tìm thấy bằng cách **đọc lại `thoughts.log` thật**, không phải
từ test tự động — đây chính là lý do file log không bao giờ được thay đổi định dạng.

1. **Router tự viết câu trả lời cuối thay vì chỉ định tuyến.** `task` trong quyết định
   của Router chỉ nên là gợi ý — nhưng model mạnh đôi khi tự soạn sẵn câu trả lời (kể cả
   sai ngôn ngữ) rồi giao thẳng cho Worker "đọc lại nguyên văn". Sửa: Worker luôn nhận
   câu chữ GỐC của Master; `task` chỉ đi kèm như 1 gợi ý không ràng buộc.
2. **Worker tự bịa lý do khi bị từ chối 1 việc.** Khi Brain quyết định không làm gì đó
   (VD: hiểu nhầm 1 lệnh huỷ không liên quan thành huỷ cả yêu cầu), Worker chỉ nhận được
   "đừng làm X" mà không có lý do thật — nên tự bịa 1 lý do nghe hợp lý nhưng SAI ("không
   có quyền điều khiển trình duyệt" — trong khi thực ra có). Sửa: luôn kèm
   `hidden_thought.reasoning` thật của Brain, không để Worker phải đoán.
3. **`_self_correct()` xoá mất kết quả ĐÚNG khi lần "tự sửa" tiếp theo thất bại.**
   `read_document` đọc CV hoàn chỉnh, nhưng Brain tự đánh giá nhầm là "thiếu" rồi thử lại
   bằng `execute_shell_command` — thất bại 2 lần, và code cũ **ghi đè vô điều kiện** lên
   kết quả tốt ban đầu. Sửa: chỉ ghi đè khi lần thử mới thực sự tốt hơn (dùng lại
   `StepRecord.failed()` — cùng cơ chế phát hiện lỗi tất định của Tier 1).
4. **Path kiểu Windows thất bại âm thầm khi chạy trong Docker (Linux).**
   `Path("D:/...").is_absolute()` trả về `True` trên Windows nhưng `False` trên Linux
   (không có khái niệm ổ đĩa) — nên 1 path hợp lệ trỏ vào sandbox bị nối chồng thành 1
   path rác. Sửa: nhận diện thêm bằng regex `^[A-Za-z]:/`, tìm đoạn `ciel_workspace/`
   trong path và remap đúng vào sandbox thật của host đang chạy.
5. **`category:primary` không đủ để lọc quảng cáo trong Gmail.** Gmail tự xếp nhiều
   email job-alert/marketing vào tab Chính tuỳ tài khoản — không có câu truy vấn nào lọc
   đúng 100% mà không lỡ loại luôn thư thật (đã xác nhận: Master cần giữ lại thư ITviec
   thật). Bài học: việc phân loại "cần chú ý" vs "rác tự động" thuộc về **cách trình bày
   kết quả** (prompt tóm tắt), không phải câu truy vấn.

**Bài học chung xuyên suốt cả 5 bug**: khi 1 component không biết lý do/dữ liệu thật, nó
sẽ **tự bịa ra** thứ gì đó nghe hợp lý. Cách sửa luôn giống nhau — đưa thẳng sự thật
(reasoning, page count, kết quả gốc) vào dữ liệu mà component đó đọc, thay vì kỳ vọng nó
tự suy luận đúng hoặc chỉ dặn dò suông trong prompt.

---

## 8. Cấu trúc thư mục (rút gọn, xem đầy đủ ở `instructionAI/architecture.md`)

```
main.py / main_api.py / main_telegram.py   # 3 entry point, cùng 1 core
docker/                                     # Dockerfile dùng chung + 2 compose file
core/                                       # Brain→Router→Middleware→Worker + 7 tier
  ├── llm_connector.py                       # CielCore — bộ điều phối trung tâm
  ├── router.py                              # phân loại ý định
  ├── continuation.py / task_state.py / ...  # T1-T7
  ├── telegram_interface.py                  # front-end Telegram
  └── rag_manager.py                         # bộ nhớ dài hạn (ChromaDB)
agent_system/                               # cấu hình model + LangGraph pipeline
skills/{internal,external}/                 # tool pack tự đăng ký
persona/official_ciel_personality.txt       # tính cách Ciel (sửa thủ công)
backtest/                                   # test suite (script, không dùng pytest)
scripts/                                    # health_check.py, daily_digest.py (CI hàng ngày)
instructionAI/                              # tài liệu ổn định cho AI làm việc trên project
ciel_workspace/ · agent_output/              # sandbox — mọi file tool chỉ được chạm vào đây
```

---

## 9. Triết lý kiểm thử

- **5 suite không dùng LLM** (`test_context`, `test_outbound`, `test_proactive`,
  `test_user_model`, `test_conversation_bugs` — 369+ assertion) chạy trong vài giây,
  dùng làm cổng chặn cho mọi thay đổi ở `core/`.
- **Test fixture thường là đoạn hội thoại THẬT đã từng gây lỗi**, copy nguyên văn từ
  `thoughts.log` — không phải dữ liệu giả tưởng tượng.
- **Lưu ý quan trọng**: `test_conversation_bugs.py` **không cô lập log** — nó tạo
  `CielCore()` thật, nên mỗi lần chạy sẽ ghi thêm dữ liệu fixture vào chính
  `ciel_data/logs/thoughts.log` thật. Đã từng gây nhầm lẫn tưởng là bug đang lặp lại
  sống thật trong lúc thực ra chỉ là log bị ghi chồng từ việc chạy test nhiều lần.

---

*File này tổng hợp lại toàn bộ quá trình làm việc — muốn đào sâu bất kỳ phần nào, xem
`instructionAI/` (kiến trúc ổn định) hoặc `note.md` (trạng thái/changelog mới nhất).*
