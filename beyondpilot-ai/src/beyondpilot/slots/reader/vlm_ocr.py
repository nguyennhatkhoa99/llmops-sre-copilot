"""OCR with a vision model (GLM-OCR or Qwen-VL behind LiteLLM).

Images are sent as they are; PDFs are rendered page by page to PNG first
(pypdfium2 + Pillow). Config: ``llm: "@ocr_llm"`` pointing to an llm slot whose
model is the OCR model.
"""

from __future__ import annotations

import asyncio
import io

from beyondpilot.core.contracts import Document, Llm, Message
from beyondpilot.core.errors import AppError, ErrorCode

IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
PROMPT = (
    "Chép lại toàn bộ chữ trong ảnh, giữ nguyên thứ tự đọc, tiêu đề và bảng (bảng viết dạng "
    "Markdown). Không tóm tắt, không thêm chữ không có trong ảnh. Nếu không có chữ, trả về rỗng."
)


def render_pdf(data: bytes, max_pages: int, scale: float) -> list[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) > max_pages:
            raise AppError(ErrorCode.INVALID_INPUT, f"PDF has more than {max_pages} pages for OCR")
        images = []
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="PNG")
            images.append(buffer.getvalue())
            page.close()
        return images
    finally:
        pdf.close()


class Impl:
    def __init__(self, *, llm: Llm, max_pages: int = 30, scale: float = 2.0) -> None:
        self.llm = llm
        self.max_pages = max_pages
        self.scale = scale

    async def read(self, data: bytes, *, mime: str) -> Document:
        if mime in IMAGE_MIMES:
            pages = [data]
        elif mime == "application/pdf":
            pages = await asyncio.to_thread(render_pdf, data, self.max_pages, self.scale)
        else:
            raise AppError(ErrorCode.INVALID_INPUT, f"OCR reader cannot read {mime}")
        texts = []
        for image in pages:
            result = await self.llm.complete(
                [Message("user", PROMPT, images=(image,))], temperature=0.0
            )
            texts.append(result.text.strip())
        text = "\n\n".join(f"<!-- trang {i + 1} -->\n{t}" for i, t in enumerate(texts) if t)
        return Document("", text, {"reader": "vlm_ocr", "pages": len(pages)})
