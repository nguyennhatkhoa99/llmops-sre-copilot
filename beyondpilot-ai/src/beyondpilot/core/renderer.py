"""Turn results into files: Jinja2 -> HTML, WeasyPrint -> PDF, python-pptx -> PPTX.

HTML always works. PDF and PPTX need ``pip install beyondpilot-ai[render]``;
without it they raise ``FEATURE_UNAVAILABLE`` instead of failing at import time.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from beyondpilot.core.errors import AppError, ErrorCode

DEFAULT_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


class Renderer:
    def __init__(self, template_dir: str | Path | None = None) -> None:
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir or DEFAULT_TEMPLATES)),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
        )

    def html(self, template: str, **context: Any) -> str:
        return self.env.get_template(template).render(**context)

    def pdf(self, html: str) -> bytes:
        try:
            from weasyprint import HTML
        except ImportError as exc:
            raise AppError(ErrorCode.FEATURE_UNAVAILABLE, "weasyprint is not installed") from exc
        return HTML(string=html).write_pdf()

    def pptx(self, slides: Sequence[Mapping[str, Any]]) -> bytes:
        """Each slide: ``{"title": str, "bullets": [str, ...]}``."""
        try:
            from pptx import Presentation
        except ImportError as exc:
            raise AppError(ErrorCode.FEATURE_UNAVAILABLE, "python-pptx is not installed") from exc
        deck = Presentation()
        layout = deck.slide_layouts[1]  # title and content
        for spec in slides:
            slide = deck.slides.add_slide(layout)
            slide.shapes.title.text = str(spec.get("title", ""))
            body = slide.placeholders[1].text_frame
            bullets = [str(b) for b in spec.get("bullets", [])]
            body.text = bullets[0] if bullets else ""
            for bullet in bullets[1:]:
                body.add_paragraph().text = bullet
        out = io.BytesIO()
        deck.save(out)
        return out.getvalue()
