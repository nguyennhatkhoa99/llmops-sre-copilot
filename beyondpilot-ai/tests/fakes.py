"""Test doubles shared by the module tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from beyondpilot.core.contracts import LlmResult, Message

Answer = str | dict | list | Callable[[Sequence[Message]], str]


class ScriptedLlm:
    """Answers in order; a dict/list answer is sent as JSON, a callable gets the messages."""

    def __init__(self, *answers: Answer, repeat_last: bool = False) -> None:
        self.answers = list(answers)
        self.repeat_last = repeat_last
        self.calls: list[list[Message]] = []

    async def complete(self, messages: Sequence[Message], **options: Any) -> LlmResult:
        self.calls.append(list(messages))
        if not self.answers:
            raise AssertionError("ScriptedLlm ran out of answers")
        answer = (
            self.answers[0] if self.repeat_last and len(self.answers) == 1 else self.answers.pop(0)
        )
        if callable(answer):
            answer = answer(messages)
        if isinstance(answer, dict | list):
            answer = json.dumps(answer, ensure_ascii=False)
        return LlmResult(str(answer), model="scripted")

    def prompt(self, call: int = -1) -> str:
        return "\n".join(m.content for m in self.calls[call])


def make_pdf(pages: Sequence[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page ("" = blank page)."""
    objects: list[bytes] = []
    kids = []
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page_id} 0 R")
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode("latin-1") if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    tree = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    all_objects = [catalog, tree, *objects, font]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(all_objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(all_objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    size = len(all_objects) + 1
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
