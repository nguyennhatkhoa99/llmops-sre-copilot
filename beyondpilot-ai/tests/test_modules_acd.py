from __future__ import annotations

import pytest

from beyondpilot.core.contracts import AccessFilter, Document
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.modules.content_check import RED, YELLOW, ContentCheckModule, Rule
from beyondpilot.modules.documents import DocType, DocumentModule, check_fields, sniff_mime
from beyondpilot.modules.rag import index_documents
from beyondpilot.modules.report import ReportModule, ReportSpec
from beyondpilot.slots.embedder import hashing
from beyondpilot.slots.facts import python as python_facts
from beyondpilot.slots.notifier import memory as memory_notifier
from beyondpilot.slots.reader import pdf_text, plain, vlm_ocr
from beyondpilot.slots.records import memory as memory_records
from beyondpilot.slots.retriever.hybrid import Impl as Hybrid
from beyondpilot.slots.review import memory as memory_review
from beyondpilot.slots.store import memory as memory_store
from beyondpilot.slots.table_source import memory as memory_source
from tests.fakes import ScriptedLlm, make_pdf

# ---------- A: report ----------

SALES = [
    {"quy": "Q3", "nam": 2026, "doanh_thu": 1250, "khach": "Đại lý Lan 0912345678"},
    {"quy": "Q2", "nam": 2026, "doanh_thu": 1000, "khach": "Đại lý Hùng"},
]
SPEC = ReportSpec(
    title="Doanh thu Milo",
    source="sales",
    metrics={
        "rev_q3": {"agg": "sum", "column": "doanh_thu", "where": {"quy": "Q3"}},
        "rev_q2": {"agg": "sum", "column": "doanh_thu", "where": {"quy": "Q2"}},
    },
    labels={"rev_q3": "Doanh thu Q3 (tỷ)", "rev_q2": "Doanh thu Q2 (tỷ)"},
    allowed_params=("nam",),
)


def report_module(llm, review=None):
    return ReportModule(
        source=memory_source.Impl({"sales": SALES}),
        facts=python_facts.Impl(),
        llm=llm,
        reports={"milo": SPEC},
        review=review,
    )


async def test_report_renders_expressions_and_goes_to_review():
    review = memory_review.Impl()
    llm = ScriptedLlm("Doanh thu Q3 đạt {{ rev_q3 }} tỷ, tăng {{ pct(rev_q3, rev_q2) }}%.")
    result = await report_module(llm, review).build("milo", {"nam": 2026})
    assert result.text == "Doanh thu Q3 đạt 1.250 tỷ, tăng 25%."
    assert "Doanh thu Milo" in result.html and "1.250" in result.html
    assert result.review_id == review.items[0]["id"]
    assert "0912345678" not in llm.prompt() and "Đại lý" not in llm.prompt()  # only numbers sent


async def test_report_falls_back_to_fact_list():
    llm = ScriptedLlm("tăng 40%", "tăng 41%", "tăng 42%")
    result = await report_module(llm).build("milo", {})
    assert result.used_fallback and "- Doanh thu Q3 (tỷ): 1.250" in result.text


@pytest.mark.parametrize(
    ("report", "params"), [("nope", {}), ("milo", {"quy": "Q3"}), ("milo", {"nam": 1999})]
)
async def test_report_input_errors(report, params):
    with pytest.raises(AppError) as info:
        await report_module(ScriptedLlm("x")).build(report, params)
    assert info.value.code is ErrorCode.INVALID_INPUT


# ---------- C: content check ----------

APPROVED = """# Claim đã duyệt
## Canxi
Milo cung cấp canxi giúp xương chắc khỏe khi dùng trong chế độ ăn cân bằng.

## Năng lượng
Milo cung cấp năng lượng cho trẻ hoạt động.
"""


async def content_module(llm, review=None):
    store, emb = memory_store.Impl(), hashing.Impl()
    await index_documents([Document("claims-milo", APPROVED)], "claims", emb, store)
    await index_documents([Document("noi-bo", "Milo canxi nội bộ chưa duyệt")], "w1", emb, store)
    return ContentCheckModule(
        llm=llm,
        retriever=Hybrid(embedder=emb, store=store),
        claims_workspace="claims",
        banned=[Rule(r"chữa\s+bệnh", "Không được nói thực phẩm chữa bệnh")],
        required=[Rule(r"chế độ ăn cân bằng", "Thiếu câu 'chế độ ăn cân bằng'", YELLOW)],
        review=review,
    )


async def test_content_green_claim_with_citation():
    llm = ScriptedLlm(
        {"claims": ["Milo giúp xương chắc khỏe"]},
        {"status": "xanh", "reason": "Khớp claim canxi", "citations": [1], "safe_text": ""},
    )
    review = memory_review.Impl()
    mod = await content_module(llm, review)
    result = await mod.check("Milo giúp xương chắc khỏe trong chế độ ăn cân bằng.")
    assert result.status == "xanh" and result.claims[0].citations[0]["doc_id"] == "claims-milo"
    assert review.items == []
    assert "nội bộ chưa duyệt" not in llm.prompt(1)  # only the claims workspace is searched


async def test_content_banned_phrase_is_red_and_reviewed():
    llm = ScriptedLlm(
        {"claims": ["Milo chữa bệnh còi xương"]},
        {"status": "xanh", "reason": "?", "citations": [1], "safe_text": ""},
    )
    review = memory_review.Impl()
    result = await (await content_module(llm, review)).check("Milo chữa bệnh còi xương.")
    assert result.status == RED and result.claims[0].status == RED
    assert {i["message"] for i in result.rule_issues} >= {"Không được nói thực phẩm chữa bệnh"}
    assert review.items[0]["kind"] == "content_check"


async def test_content_unverifiable_output_is_yellow():
    llm = ScriptedLlm({"claims": ["Milo cung cấp năng lượng"]}, "?", "??", "???")
    result = await (await content_module(llm)).check(
        "Milo cung cấp năng lượng trong chế độ ăn cân bằng."
    )
    assert result.claims[0].status == YELLOW


async def test_content_green_without_citation_rejected():
    llm = ScriptedLlm(
        {"claims": ["Milo cung cấp năng lượng"]},
        {"status": "xanh", "reason": "", "citations": []},
        {"status": "xanh", "reason": "", "citations": [5]},
        {
            "status": "vang",
            "reason": "thiếu điều kiện",
            "citations": [1],
            "safe_text": "Milo cung cấp năng lượng cho trẻ hoạt động.",
        },
    )
    result = await (await content_module(llm)).check(
        "Milo cung cấp năng lượng trong chế độ ăn cân bằng."
    )
    assert result.claims[0].status == YELLOW
    assert result.claims[0].safe_text.startswith("Milo cung cấp")


async def test_content_split_failure_falls_back_to_sentences():
    llm = ScriptedLlm(
        "x", "y", "z", {"status": "vang", "reason": "", "citations": [1]}, repeat_last=True
    )
    result = await (await content_module(llm)).check(
        "Milo cung cấp canxi. Milo cung cấp năng lượng. chế độ ăn cân bằng"
    )
    assert len(result.claims) >= 2


# ---------- D: documents ----------

INVOICE = """HÓA ĐƠN GIÁ TRỊ GIA TĂNG
Số hóa đơn: HD-00123
Ngày: 15/10/2026
Khách hàng: Đại lý Lan, SĐT 0912345678
Tiền hàng: 1.000.000
Thuế VAT: 100.000
Tổng tiền: 1.100.000
"""

DOC_TYPES = {
    "hoa_don": DocType(
        keywords=["hóa đơn", "vat"],
        fields={
            "so_hoa_don": {"type": "string", "required": True},
            "ngay": {"type": "date", "required": True},
            "tien_hang": {"type": "number"},
            "thue": {"type": "number"},
            "tong_tien": {"type": "number", "required": True},
        },
        sums=[{"parts": ["tien_hang", "thue"], "total": "tong_tien"}],
    )
}


def doc_module(llm, *, ocr_llm=None):
    store, emb = memory_store.Impl(), hashing.Impl()
    review, notifier, records = memory_review.Impl(), memory_notifier.Impl(), memory_records.Impl()
    mod = DocumentModule(
        text_reader=plain.Impl(),
        pdf_reader=pdf_text.Impl(),
        ocr_reader=vlm_ocr.Impl(llm=ocr_llm) if ocr_llm else None,
        llm=llm,
        embedder=emb,
        store=store,
        records=records,
        doc_types=DOC_TYPES,
        review=review,
        notifier=notifier,
    )
    return mod, store, review, notifier, records


GOOD_FIELDS = {
    "so_hoa_don": "HD-00123",
    "ngay": "2026-10-15",
    "tien_hang": 1000000,
    "thue": 100000,
    "tong_tien": 1100000,
}


async def test_ingest_invoice_ok_and_searchable():
    mod, store, review, notifier, records = doc_module(ScriptedLlm(GOOD_FIELDS))
    result = await mod.ingest(
        INVOICE.encode(),
        declared_mime="text/plain",
        workspace_id="w1",
        doc_id="hd-123",
        title="Hóa đơn 123",
    )
    assert result.status == "ok" and result.doc_type == "hoa_don" and result.issues == []
    assert result.fields["tong_tien"] == 1100000.0
    assert [e["seq"] for e in notifier.events] == [1, 2, 3, 4, 5]
    assert notifier.events[-1]["event"] == "done" and review.items == []
    saved = await records.get("documents", "w1:hd-123")
    assert saved["status"] == "ok"
    hits = await store.search_text("tổng tiền", AccessFilter("w1"), 5)
    assert hits and "0912345678" not in hits[0].chunk.text  # PII masked in the index


async def test_ingest_hallucinated_value_goes_to_review():
    bad = {**GOOD_FIELDS, "tong_tien": 1200000}
    mod, _, review, _, records = doc_module(ScriptedLlm(bad))
    result = await mod.ingest(
        INVOICE.encode(), declared_mime="text/plain", workspace_id="w1", doc_id="hd-1"
    )
    assert result.status == "pending_review" and result.review_id
    assert any("không thấy trong chứng từ" in i for i in result.issues)
    assert any("tổng các phần" in i for i in result.issues)
    assert review.items[0]["payload"]["doc_id"] == "hd-1"


def test_check_fields_types():
    clean, issues = check_fields(
        {
            "so_hoa_don": "HD-99999",
            "ngay": "15/10/2026",
            "tien_hang": "abc",
            "thue": None,
            "tong_tien": None,
        },
        DOC_TYPES["hoa_don"],
        INVOICE,
    )
    assert clean["ngay"] is None and clean["tien_hang"] is None
    assert any("YYYY-MM-DD" in i for i in issues)
    assert any("tong_tien: thiếu" in i for i in issues)
    assert any("so_hoa_don" in i and "nguyên văn" in i for i in issues)


async def test_ingest_pdf_with_text_layer():
    mod, *_ = doc_module(ScriptedLlm(GOOD_FIELDS))
    pdf = make_pdf(["Chinh sach doi tra: dai ly duoc doi hang hu hong trong vong 7 ngay"])
    result = await mod.ingest(pdf, declared_mime="text/plain", workspace_id="w1", doc_id="cs")
    assert result.reader == "pdf_text" and result.chunks == 1 and result.doc_type is None


async def test_scanned_pdf_goes_to_ocr():
    ocr = ScriptedLlm("Chính sách đổi trả trong 7 ngày", repeat_last=True)
    mod, store, *_ = doc_module(ScriptedLlm(), ocr_llm=ocr)
    result = await mod.ingest(
        make_pdf(["", ""]), declared_mime="application/pdf", workspace_id="w1", doc_id="scan"
    )
    assert result.reader == "vlm_ocr" and len(ocr.calls) == 2
    assert ocr.calls[0][0].images and ocr.calls[0][0].images[0].startswith(b"\x89PNG")


async def test_scanned_pdf_without_ocr_is_unavailable():
    mod, *_ = doc_module(ScriptedLlm())
    with pytest.raises(AppError) as info:
        await mod.ingest(
            make_pdf([""]), declared_mime="application/pdf", workspace_id="w1", doc_id="scan"
        )
    assert info.value.code is ErrorCode.FEATURE_UNAVAILABLE


@pytest.mark.parametrize(
    ("data", "mime", "doc_id"),
    [
        (b"", "text/plain", "d"),
        (b"MZ\x90\x00", "application/x-msdownload", "d"),
        (b"hello", "text/plain", "../etc/passwd"),
    ],
)
async def test_ingest_rejects_bad_input(data, mime, doc_id):
    mod, *_ = doc_module(ScriptedLlm())
    with pytest.raises(AppError) as info:
        await mod.ingest(data, declared_mime=mime, workspace_id="w1", doc_id=doc_id)
    assert info.value.code is ErrorCode.INVALID_INPUT


def test_sniff_mime_trusts_magic_bytes():
    assert sniff_mime(b"%PDF-1.4 ...", "text/plain") == "application/pdf"
    assert sniff_mime(b"\xff\xd8\xff\xe0", "application/pdf") == "image/jpeg"
    assert sniff_mime(b"abc", "text/markdown; charset=utf-8") == "text/markdown"
