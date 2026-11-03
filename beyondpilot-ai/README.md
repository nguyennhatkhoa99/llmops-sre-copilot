# BeyondPilot AI service: phần core

Lõi dùng chung cho AI service của BeyondPilot, dựng theo flow core trong `docs/beyondpilot/` (branch `docs/beyondpilot-flows`). Các module A–H sẽ cắm vào lõi này qua contract; chưa có module nào trong commit này.

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

- Use case và module (bắt đầu từ `answer_question` / module B).
- Progress hook về Backend.
- Slot thật cho embedder, store, reranker (bge-m3, pgvector, bge-reranker).
- Dockerfile, Helm chart.
- Bộ case vàng thật cho promptfoo; `eval/cases/example.yaml` mới chỉ là định dạng mẫu.
