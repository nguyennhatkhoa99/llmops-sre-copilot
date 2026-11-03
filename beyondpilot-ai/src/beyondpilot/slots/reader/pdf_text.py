"""Reader for PDFs that already have a text layer (pypdfium2).

If a PDF has almost no text per page it is probably a scan; the document comes
back with ``metadata["needs_ocr"] = True`` so module D can send it to OCR.
"""

from __future__ import annotations

import asyncio

import pypdfium2 as pdfium

from beyondpilot.core.contracts import Document
from beyondpilot.core.errors import AppError, ErrorCode


class Impl:
    def __init__(self, min_chars_per_page: int = 40, max_pages: int = 200) -> None:
        self.min_chars_per_page = min_chars_per_page
        self.max_pages = max_pages

    def _read(self, data: bytes) -> Document:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise AppError(ErrorCode.INVALID_INPUT, "file is not a readable PDF") from exc
        try:
            pages = len(pdf)
            if pages > self.max_pages:
                raise AppError(ErrorCode.INVALID_INPUT, f"PDF has more than {self.max_pages} pages")
            texts = []
            for i in range(pages):
                page = pdf[i]
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range().strip())
                textpage.close()
                page.close()
        finally:
            pdf.close()
        chars = sum(len(t) for t in texts)
        needs_ocr = pages > 0 and chars / pages < self.min_chars_per_page
        text = "\n\n".join(f"<!-- trang {i + 1} -->\n{t}" for i, t in enumerate(texts) if t)
        return Document("", text, {"reader": "pdf_text", "pages": pages, "needs_ocr": needs_ocr})

    async def read(self, data: bytes, *, mime: str) -> Document:
        if mime != "application/pdf":
            raise AppError(ErrorCode.INVALID_INPUT, f"pdf_text reader cannot read {mime}")
        return await asyncio.to_thread(self._read, data)
