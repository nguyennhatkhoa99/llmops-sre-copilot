# BeyondPilot AI service: phần core

Lõi dùng chung cho AI service của BeyondPilot, dựng theo flow core trong `docs/beyondpilot/` (branch `docs/beyondpilot-flows`). Cả 8 module A–H đã cắm vào lõi này qua contract, mỗi module một endpoint.

## Chạy thử

```bash
cd beyondpilot-ai
```

```bash
uv venv && uv pip install -e ".[dev]"
```

```bash
.venv/bin/python -m pytest
```

Chạy server với config mẫu:

```bash
cp config.yaml.template config.yaml
```

```bash
BEYONDPILOT_API_KEY=dev-key LITELLM_API_KEY=x .venv/bin/uvicorn beyondpilot.api.app:create_app --factory --port 8000
```

- `GET /internal/v1/health`: liveness, không cần key.
- `GET /internal/v1/ready`: readiness, báo số slot đã lắp và backend safety đang dùng.
- `GET /api/v1/ping` với header `X-API-Key`: kiểm key của Backend.
- Các endpoint của 8 module, đều cần header `X-API-Key` (bảng ở mục "8 module").

Config mẫu dùng Valkey, pgvector, LiteLLM và TEI thật. Chạy thử không cần các dịch vụ đó thì dùng các bản cài trong bộ nhớ: `cache: memory`, `embedder: hashing`, `store: memory`, `reranker: passthrough`, `llm: echo`.

Mỗi module chỉ cần thư viện của nó khi được bật trong config:

| Extra | Cho | Gồm |
|---|---|---|
| `rag` | B, D, E, G, H (lưu trữ) | redis (Valkey), psycopg, psycopg-pool |
| `documents` | D | pypdfium2, Pillow |
| `vision` | E | Pillow |
| `ml` | A (DuckDB), F | duckdb, numpy, lightgbm, mlflow, ortools |
| `snowflake` | A, F (nguồn Snowflake) | snowflake-connector-python |
| `ot` | G (OPC UA) | asyncua |
 Thêm Presidio + LLM Guard: `uv pip install -e ".[safety]"`. Thêm xuất PDF, PPTX: `".[render]"`. Thêm OpenTelemetry: `".[otel]"`.

## 10 khối core

| Khối | File | Việc chính |
|---|---|---|
| contracts | `core/contracts.py` | Protocol cho từng loại slot: Llm, Embedder, Store, Retriever, Reranker, Source, Reader, Writer, Checker. `AccessFilter` mang `workspace_id` và `doc_ids` |
| registry | `core/registry.py` | `kind: llm, type: litellm` nạp class `Impl` trong `slots/llm/litellm.py`. Sai tên, thiếu file, sai contract thì dừng khởi động |
| settings | `core/settings.py`, `config.yaml.template` | Đọc YAML, thay `${TÊN}` bằng biến môi trường (OpenBao cấp). Thiếu biến thì dừng khởi động |
| errors | `core/errors.py` | `ErrorCode` + `CATALOGUE` (status, câu báo lỗi). Chỉ raise `AppError(code)`; chi tiết chỉ vào log |
| resilience | `core/resilience.py` | Retry đúng 1 lớp, backoff tăng gấp đôi, theo `Retry-After`, timeout mỗi lần thử. Hết lượt thì trả `AppError` |
| diagnostics | `core/diagnostics.py` | `log_scope` đo thời gian và mở span OTel, `track_tokens` đếm token. Không ghi câu hỏi, câu trả lời hay nội dung tài liệu vào log |
| safety | `core/safety.py` | Che PII, chặn prompt injection. Dùng Presidio và LLM Guard nếu đã cài, không thì dùng regex (có số điện thoại, CCCD và câu tiếng Việt) |
| validator + evidence DSL | `core/evidence.py`, `core/validator.py` | Writer viết số bằng `{{ biểu thức }}` trên facts. Số gõ tay phải khớp một fact hoặc một biểu thức. Sai thì viết lại, tối đa 2 lần, rồi dùng template |
| renderer | `core/renderer.py`, `templates/` | Jinja2 ra HTML (tự escape). PDF, PPTX nếu cài extra `render` |
| eval harness | `eval/` | Khung promptfoo và định dạng bộ case vàng, để chạy trong CI |

Lớp HTTP (`api/`): app factory, xác thực `X-API-Key` so khớp hằng thời gian với mọi key, mọi lỗi trả về `{code, message}`.

## 8 module

| Module | Endpoint | Chuỗi slot | Bước kiểm tra cuối |
|---|---|---|---|
| A. facts + narrative | `POST /api/v1/reports` | table_source → facts → writer (`{{ biểu thức }}`) → validator → renderer | Số liệu khớp facts; sai thì viết lại tối đa 2 lần, rồi dùng danh sách facts |
| B. RAG | `POST /api/v1/chat` | cache → rewriter → embedder → store → retriever → reranker → synthesizer | Câu nào cũng có trích dẫn, số có trong nguồn được trích |
| C. check Xanh/Vàng/Đỏ | `POST /api/v1/checks/content` | tách claim → luật bằng code → tra claim đã duyệt (retriever của B) → giải thích | Vàng/Đỏ thì vào Review UI; kết quả không kiểm được thì là Vàng, không bao giờ Xanh |
| D. đọc tài liệu | `POST /api/v1/documents` (multipart) | preparer → reader (text, PDF, OCR) → signals → schema (LLM, JSON) → checker → records → nạp vào kho của B | Còn ô nghi ngờ thì vào hàng ngoại lệ trên Review UI |
| E. thị giác | `POST /api/v1/checks/image` (multipart) | chống gian lận → detector → chấm rubric (VLM, JSON) → luật trưng bày | Nghi gian lận, điểm thấp hoặc không chấm được thì vào Review UI |
| F. ML, tối ưu | `POST /api/v1/forecasts` | dữ liệu → bất thường → backtest so với mô hình nền → dự báo → phân bổ → giải thích | MAPE vượt ngưỡng hoặc thua mô hình nền thì dùng mô hình nền, `published=false` |
| G. điều khiển OT | `POST /api/v1/setpoints/advise` | đọc OPC UA (chỉ đọc) → kiểm tín hiệu → soft-sensor → mô phỏng → khuyến nghị | Luôn chờ kỹ sư duyệt; `applied` luôn `false`; lưu audit |
| H. radar | `POST /api/v1/radar/runs` | provider → fetcher (chặn SSRF, giới hạn ngân sách) → extractor (quote nguyên văn) → gộp trùng → chấm điểm → digest | Bản tin vào Review UI; chỉ bản đã duyệt mới sang Outbox |

Những điểm chung:
- **Bản cài cho dev:** mỗi slot đều có một bản chạy không cần dịch vụ ngoài (`memory`, `static`, `hashing`, `echo`, `seasonal_naive`, `proportional`, `linear`), để chạy thử và để test.
- **Đọc JSON từ LLM:** C, D, E, H dùng `core/jsonout.py`. LLM trả sai định dạng thì được gửi lỗi để viết lại; vẫn sai thì chuyển cho người, không đoán.
- **Hàng duyệt và tiến độ:** Review UI và Outbox do Backend quản lý. AI service chỉ gửi item qua slot `review` (có idempotency key để retry không tạo bản trùng) và gửi tiến độ qua slot `notifier`.
- **Dùng chung slot giữa các module:** viết `"@rag.store"` để trỏ sang slot của module khác. Module D mặc định ghi vào kho của B, nên tài liệu vừa nạp hỏi đáp được ngay.
- **Không có câu SQL nào đi từ API:** báo cáo, chuỗi dữ liệu và truy vấn Snowflake đều khai báo trong config theo tên.

## Module B: hỏi đáp có trích dẫn

```bash
curl -X POST localhost:8000/api/v1/chat -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d '{"question": "Hàng hư hỏng được đổi trong bao lâu?", "workspace_id": "w1", "doc_ids": ["chinh-sach-doi-tra"]}'
```

`workspace_id` và `doc_ids` do Backend gửi: đó là những gì người dùng được xem. AI service lọc theo danh sách này ở mọi bước.

| # | Slot | `type:` mặc định | File |
|---|---|---|---|
| 0 | cache | `valkey` (dev: `memory`) | `slots/cache/` |
| 1 | rewriter | dùng slot `llm`, bật bằng `options.rewrite` | `modules/rag/pipeline.py` |
| 2 | embedder | `litellm` → bge-m3 (dev: `hashing`) | `slots/embedder/` |
| 3 | store | `pgvector`, vector + full-text, lọc quyền trong SQL (dev: `memory`, có BM25) | `slots/store/` |
| 4 | retriever | `hybrid`, chuẩn hoá điểm rồi gộp `alpha * vector + (1 - alpha) * text` | `slots/retriever/hybrid.py` |
| 5 | reranker | `tei` → bge-reranker-v2-m3 (dev: `passthrough`) | `slots/reranker/` |
| 6 | synthesizer | dùng slot `llm` (Qwen3 qua LiteLLM) | `modules/rag/prompts.py` |

Sau bước 6 là kiểm trích dẫn (`modules/rag/citations.py`):
- Câu nào cũng phải có `[n]`, và `n` phải là nguồn có thật.
- Mọi con số trong câu phải có trong nguồn mà câu đó trích.

Sai thì gửi lỗi cho model viết lại, tối đa `validator.max_retries` lần. Vẫn sai thì trả về các đoạn nguồn liên quan nhất kèm trích dẫn. Câu trả lời dự phòng này không được cache.

Các lớp bảo vệ:
- Store lọc theo workspace và `doc_ids` ngay trong SQL. Pipeline lọc lại lần nữa sau retriever và sau reranker.
- Key cache gồm câu hỏi, workspace và danh sách tài liệu, nên hai người khác quyền không dùng chung câu trả lời.
- Đoạn nguồn được bọc trong thẻ `<nguon>` và prompt dặn model không làm theo chỉ dẫn nằm trong đó.
- Câu hỏi đi qua safety trước: che PII, chặn prompt injection.

Chia chunk theo heading (`modules/rag/indexing.py`): mỗi chunk giữ đường dẫn heading ở đầu, ví dụ "Chính sách đổi trả > Hàng hư hỏng". Module D sẽ gọi `index_documents` sau khi đọc file.

Bảng cho pgvector: `slots/store/pgvector.py` có `schema_sql()` (vector 1024 chiều, index HNSW, GIN cho full-text). Postgres không có từ điển tiếng Việt nên full-text dùng cấu hình `simple`; có thể thay bằng BM25 thật (ParadeDB `pg_search`) mà không phải sửa module.

## Lấy gì từ pilot

- `guardrails/pii.py`, `injection.py` → `core/safety.py`. Đã sửa lỗi của pilot: regex key và IP chỉ trả về phần capture group (ví dụ chỉ `sk-`), giờ trả về cả chuỗi.
- `middleware/auth.py` → `api/auth.py`, đổi phép so `in` sang `hmac.compare_digest`.
- `services/llm_router.py` → `slots/llm/litellm.py`. Bỏ fallback tự viết; LiteLLM lo routing. Tắt retry của OpenAI client để chỉ còn một lớp retry.
- `config.py` → `core/settings.py`, đổi từ biến môi trường `SRE_*` sang YAML + `${...}`.

## Evidence DSL

```text
Doanh thu quý 3 đạt {{ rev_q3 }} tỷ, tăng {{ pct(rev_q3, rev_q2) }}% so với quý trước.
```

Hàm có sẵn: `pct(a, b)`, `share(a, b)`, `round(x, n)`, `abs`, `min`, `max`, `sum`, cùng `+ - * /`. Biểu thức được đọc bằng `ast` với danh sách node cho phép, nên không chạy được code. Số được viết theo kiểu Việt Nam (`1.250`, `18,3`). Số chỉ kỳ hoặc vị trí (quý 3, tháng 10, bước 2) và năm được bỏ qua.

## Chưa làm

- **Chạy với dịch vụ thật:** các slot thật (pgvector, Valkey, TEI, LiteLLM, Snowflake, MLflow, OPC UA, máy chủ RT-DETR) mới chỉ test bằng bản giả lập.
- **Hàng đợi Procrastinate cho module D:** endpoint `/documents` đang xử lý ngay trong request.
- **Làm mờ mặt người trong ảnh (E):** chưa có, cần một model nhận diện khuôn mặt.
- **Soft-sensor thật cho G:** đang dùng bản tuyến tính hệ số cấu hình tay; bản LightGBM cần file model huấn luyện từ dữ liệu nhà máy.
- **Xoá chunk cũ khi nạp lại tài liệu (D):** nạp lại cùng `doc_id` thì ghi đè các chunk cùng vị trí, nhưng chunk thừa của bản cũ vẫn còn.
- **Dockerfile, Helm chart.**
- **Bộ case vàng thật cho promptfoo.**
