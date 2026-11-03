from __future__ import annotations

import pytest

from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.evidence import EvidenceError, evaluate, format_vi, parse_number, render
from beyondpilot.core.renderer import Renderer
from beyondpilot.core.validator import fact_list_template, generate_validated, validate

FACTS = {"rev_q3": 1250.0, "rev_q2": 1000.0, "share_milo": 0.183}


def test_evaluate_expressions():
    assert evaluate("pct(rev_q3, rev_q2)", FACTS) == pytest.approx(25.0)
    assert evaluate("round(share_milo * 100, 1)", FACTS) == pytest.approx(18.3)
    assert evaluate("rev_q3 - rev_q2", FACTS) == 250


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "rev_q3.real",
        "unknown + 1",
        "open('x')",
        "rev_q3 / 0",
        "pct(rev_q3)",
        "[1, 2]",
        "True + 1",
    ],
)
def test_unsafe_or_bad_expressions_rejected(expr):
    with pytest.raises(EvidenceError):
        evaluate(expr, FACTS)


def test_vietnamese_number_format():
    assert format_vi(1234567) == "1.234.567"
    assert format_vi(25.0) == "25"
    assert format_vi(18.25) == "18,2"
    assert format_vi(0.183) == "0,18"
    assert parse_number("1.234,5") == 1234.5
    assert parse_number("1.200") == 1200
    assert parse_number("25%") == 25
    assert parse_number("3.5") == 3.5


def test_render_replaces_expressions():
    out = render("Doanh thu quý 3 tăng {{ pct(rev_q3, rev_q2) }}% lên {{ rev_q3 }} tỷ.", FACTS)
    assert out.text == "Doanh thu quý 3 tăng 25% lên 1.250 tỷ."
    assert not out.errors


def test_validate_accepts_traceable_numbers():
    # "quý 3" and "2026" are labels; 1.000 is typed but equals fact rev_q2.
    text = (
        "Quý 3 năm 2026 doanh thu {{ rev_q3 }} tỷ, "
        "tăng {{ pct(rev_q3, rev_q2) }}% so với 1.000 tỷ của Q2."
    )
    result = validate(text, FACTS)
    assert result.ok, result.errors
    assert result.text.endswith("tăng 25% so với 1.000 tỷ của Q2.")


def test_derived_numbers_must_be_expressions():
    # 25% is pct(rev_q3, rev_q2), but typed by hand it cannot be traced: write it as {{ }}.
    result = validate("Doanh thu {{ rev_q3 }} tỷ, tăng 25%.", FACTS)
    assert not result.ok and "25%" in result.errors[0]


def test_label_numbers_ignored_but_percent_after_label_checked():
    assert validate("Tháng 10 và bước 2 không phải số liệu.", FACTS).ok
    assert not validate("Quý 3 tăng 31%.", FACTS).ok


def test_validate_rejects_invented_numbers():
    result = validate("Doanh thu tăng 30% lên {{ rev_q3 }} tỷ.", FACTS)
    assert not result.ok
    assert any("30%" in e for e in result.errors)


def test_validate_reports_bad_expression():
    result = validate("Tăng {{ pct(rev_q4, rev_q3) }}%", FACTS)
    assert not result.ok and "rev_q4" in result.errors[0]


async def test_generate_validated_retries_with_feedback():
    seen_feedback = []
    drafts = iter(["tăng 40%", "tăng {{ pct(rev_q3, rev_q2) }}%"])

    async def writer(errors):
        seen_feedback.append(list(errors))
        return next(drafts)

    out = await generate_validated(writer, FACTS, fallback=fact_list_template({}), max_retries=2)
    assert out.text == "tăng 25%"
    assert out.attempts == 2 and not out.used_fallback
    assert seen_feedback[0] == [] and seen_feedback[1]  # second try got the errors


async def test_generate_validated_falls_back_to_template():
    calls = {"n": 0}

    async def writer(errors):
        calls["n"] += 1
        return "tăng 99%"

    out = await generate_validated(
        writer, FACTS, fallback=fact_list_template({"rev_q3": "Doanh thu Q3"}), max_retries=2
    )
    assert calls["n"] == 3  # first try + 2 rewrites
    assert out.used_fallback
    assert "- Doanh thu Q3: 1.250" in out.text


async def test_broken_fallback_raises():
    async def writer(errors):
        return "bịa 77"

    with pytest.raises(AppError) as info:
        await generate_validated(writer, FACTS, fallback=lambda f: "cũng bịa 88", max_retries=0)
    assert info.value.code is ErrorCode.VALIDATION_FAILED


def test_renderer_html_escapes_content():
    html = Renderer().html(
        "report.html",
        title="Báo cáo <Milo>",
        subtitle="Q3",
        body="ok",
        facts=[("Doanh thu", "1.250")],
    )
    assert "Báo cáo &lt;Milo&gt;" in html
    assert '<td class="num">1.250</td>' in html


def test_renderer_pdf_pptx_without_extras():
    renderer = Renderer()
    for call in (lambda: renderer.pdf("<p>x</p>"), lambda: renderer.pptx([{"title": "x"}])):
        try:
            data = call()
        except AppError as exc:
            assert exc.code is ErrorCode.FEATURE_UNAVAILABLE
        else:
            assert isinstance(data, bytes) and data
