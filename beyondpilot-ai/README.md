# BeyondPilot AI service: phần core

Lõi dùng chung cho AI service của BeyondPilot, dựng theo flow core trong `docs/beyondpilot/` (branch `docs/beyondpilot-flows`). Các module A–H cắm vào lõi này qua contract. Đã có module B (`answer_question`).

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
- `POST /api/v1/chat` với header `X-API-Key`: hỏi đáp có trích dẫn (module B).

Config mẫu dùng Valkey, pgvector, LiteLLM và TEI thật. Chạy thử không cần các dịch vụ đó thì dùng các bản cài trong bộ nhớ: `cache: memory`, `embedder: hashing`, `store: memory`, `reranker: passthrough`, `llm: echo`.

Thêm Valkey + PostgreSQL cho module B: `uv pip install -e ".[rag]"`. Thêm Presidio + LLM Guard: `uv pip install -e ".[safety]"`. Thêm xuất PDF, PPTX: `".[render]"`. Thêm OpenTelemetry: `".[otel]"`.

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

- Module A, C, D, E, F, G, H và endpoint nạp tài liệu.
- Progress hook về Backend.
- Chạy thử slot `pgvector`, `valkey`, `tei` với dịch vụ thật (hiện mới test bằng bản giả lập).
- Dockerfile, Helm chart.
- Bộ case vàng thật cho promptfoo; `eval/cases/example.yaml` mới chỉ là định dạng mẫu.
