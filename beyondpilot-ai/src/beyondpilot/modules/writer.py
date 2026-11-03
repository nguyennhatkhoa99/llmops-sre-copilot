"""Shared narrative writer: an LLM that may only write numbers as evidence expressions.

Used by module A (reports) and module F (forecast explanations). The model sees
the facts with their ids and labels and writes ``{{ expr }}`` for every number;
``generate_validated`` checks the result and falls back to a plain list.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from beyondpilot.core.contracts import Llm, Message
from beyondpilot.core.evidence import format_vi
from beyondpilot.core.validator import ValidatedOutput, fact_list_template, generate_validated

SYSTEM = """Bạn viết báo cáo ngắn bằng tiếng Việt cho quản lý Nestlé Việt Nam.

Quy tắc về số liệu (bắt buộc):
- Không bao giờ gõ một con số. Mọi con số viết dưới dạng {{ biểu thức }} dùng mã fact.
- Ví dụ: "Doanh thu đạt {{ rev_q3 }} tỷ, tăng {{ pct(rev_q3, rev_q2) }}% so với quý trước."
- Hàm được dùng: pct(a, b) là % thay đổi từ b sang a; share(a, b) là a chiếm bao nhiêu % của b;
  round(x, n), abs, min, max, sum; cùng các phép + - * /.
- Chỉ dùng các mã fact trong danh sách. Không bịa thêm số.
Viết 3 đến 6 câu, đi thẳng vào ý chính."""


def facts_block(facts: Mapping[str, float], labels: Mapping[str, str]) -> str:
    return "\n".join(
        f"- {fid}: {labels.get(fid, fid)} = {format_vi(float(v))}" for fid, v in facts.items()
    )


async def write_narrative(
    llm: Llm,
    facts: Mapping[str, float],
    labels: Mapping[str, str],
    instructions: str,
    *,
    max_retries: int = 2,
) -> ValidatedOutput:
    async def generate(errors: Sequence[str]) -> str:
        user = f"Các fact:\n{facts_block(facts, labels)}\n\nYêu cầu: {instructions}"
        if errors:
            user += "\n\nBản trước chưa đạt, sửa các lỗi sau:\n" + "\n".join(
                f"- {e}" for e in errors
            )
        result = await llm.complete(
            [Message("system", SYSTEM), Message("user", user)], temperature=0.2
        )
        return result.text

    return await generate_validated(
        generate, facts, fallback=fact_list_template(labels), max_retries=max_retries
    )
