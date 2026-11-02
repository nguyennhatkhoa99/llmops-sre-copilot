"""Generate docs/beyondpilot/core-flow.drawio (2 pages)."""
import sys
from xml.sax.saxutils import escape, quoteattr

BLUE = "fillColor=#dae8fc;strokeColor=#6c8ebf;"
PURPLE = "fillColor=#e1d5e7;strokeColor=#9673a6;"
YELLOW = "fillColor=#fff2cc;strokeColor=#d6b656;"
ORANGE = "fillColor=#ffe6cc;strokeColor=#d79b00;"
GREEN = "fillColor=#d5e8d4;strokeColor=#82b366;"
RED = "fillColor=#f8cecc;strokeColor=#b85450;"
GREY = "fillColor=#f5f5f5;strokeColor=#999999;"
WHITE = "fillColor=#ffffff;strokeColor=#6c8ebf;"
BOX = "rounded=1;whiteSpace=wrap;html=1;fontSize=14;"
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;labelBackgroundColor=#ffffff;"
TEXT = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;"
RHOMBUS = "rhombus;shape=rhombus;perimeter=rhombusPerimeter;whiteSpace=wrap;html=1;fontSize=14;" + YELLOW


def lab(title, sub=None):
    return title if not sub else f"{title}<br><small>{sub}</small>"


class Page:
    def __init__(self, pid, name):
        self.pid, self.name, self.cells = pid, name, []

    def v(self, cid, value, style, x, y, w, h, parent="1"):
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(value)} style="{style}" vertex="1" '
            f'parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" '
            f'as="geometry"/></mxCell>')

    def e(self, cid, src, tgt, label="", extra="", points=None, parent="1"):
        pts = ""
        if points:
            pts = '<Array as="points">' + "".join(
                f'<mxPoint x="{x}" y="{y}"/>' for x, y in points) + "</Array>"
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(label)} style="{EDGE}{extra}" edge="1" '
            f'parent="{parent}" source="{src}" target="{tgt}"><mxGeometry relative="1" '
            f'as="geometry">{pts}</mxGeometry></mxCell>')

    def xml(self, w, h):
        return (f'<diagram id="{self.pid}" name={quoteattr(self.name)}><mxGraphModel '
                f'adaptiveColors="auto" grid="1" gridSize="10" page="1" pageWidth="{w}" '
                f'pageHeight="{h}"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                + "".join(self.cells) + "</root></mxGraphModel></diagram>")


def page1():
    p = Page("core", "1. Flow core")
    p.v("title", "Flow phần core: một request hỏi đáp đi qua AI service", TEXT + "fontSize=20;fontStyle=1;", 40, -10, 900, 40)
    p.v("http", lab("Lớp HTTP", "X-API-Key · DTO theo version"), BOX + BLUE, 40, 40, 200, 60)
    p.v("uc", lab("Use case answer_question", "chọn module B"), BOX + BLUE, 40, 150, 200, 60)
    p.v("safety", lab("safety", "Presidio che PII · LLM Guard"), BOX + PURPLE, 40, 260, 200, 60)
    p.v("d1", "Input an toàn?", RHOMBUS, 40, 360, 200, 90)
    p.v("err", lab("AppError(code)", "tra CATALOGUE → {code, message}"), BOX + RED, 320, 150, 220, 60)

    # Module container with the slot chain of module B
    p.v("mod", "Module B · RAG: chuỗi slot của answer_question",
        "swimlane;shape=swimlane;startSize=30;html=1;fontSize=14;fontStyle=1;verticalAlign=top;align=right;spacingRight=12;" + "fillColor=#eef4fb;strokeColor=#6c8ebf;swimlaneFillColor=#f7faff;",
        40, 490, 820, 300)
    slot = BOX + "fontSize=13;" + WHITE
    p.v("s0", lab("<b>0 · Cache</b>", "Valkey · key = câu hỏi + workspace + quyền · hit thì trả luôn"), slot, 20, 50, 180, 80, "mod")
    p.v("s1", lab("<b>1 · Rewriter</b>", "type: llm_rewrite · tùy chọn · Qwen3"), slot, 220, 50, 180, 80, "mod")
    p.v("s2", lab("<b>2 · Embedder</b>", "type: tei · bge-m3 · 1024 chiều"), slot, 420, 50, 180, 80, "mod")
    p.v("s3", lab("<b>3 · Store</b>", "type: pgvector · vector + full-text · lọc workspace, doc_ids"), slot, 620, 50, 180, 80, "mod")
    p.v("s4", lab("<b>4 · Retriever</b>", "type: hybrid · BM25 tiếng Việt + vector · fusion convex"), slot, 620, 190, 180, 80, "mod")
    p.v("s5", lab("<b>5 · Reranker</b>", "type: tei · bge-reranker-v2-m3 · giữ top-k"), slot, 420, 190, 180, 80, "mod")
    p.v("s6", lab("<b>6 · Synthesizer</b>", "type: llm · Qwen3 · trích dẫn từng câu"), slot, 220, 190, 180, 80, "mod")
    p.e("i1", "s0", "s1", "miss", parent="mod")
    p.e("i2", "s1", "s2", parent="mod")
    p.e("i3", "s2", "s3", parent="mod")
    p.e("i4", "s3", "s4", parent="mod")
    p.e("i5", "s4", "s5", parent="mod")
    p.e("i6", "s5", "s6", parent="mod")

    p.v("d2", "validator:<br>số khớp nguồn?", RHOMBUS, 40, 830, 200, 90)
    p.v("regen", lab("Sinh lại kèm lỗi", "tối đa 2 lần, sau đó dùng template"), BOX + YELLOW, 320, 845, 220, 60)
    p.v("rend", lab("renderer", "chỉ khi cần file HTML · PDF · PPTX"), BOX + PURPLE, 40, 970, 200, 60)
    p.v("end", "Trả DTO về Backend<br><small>answer + citations</small>", BOX + "arcSize=40;" + GREEN, 40, 1080, 200, 50)

    p.v("cfg", lab("settings", "config.yaml + secret env/OpenBao"), BOX + PURPLE + "dashed=1;", 1200, 260, 220, 60)
    p.v("reg", lab("contracts + registry", "type: → class đúng Protocol"), BOX + PURPLE + "dashed=1;", 1200, 370, 220, 60)
    p.v("evalh", lab("eval harness", "promptfoo + case vàng, chạy trong CI"), BOX + GREY + "dashed=1;", 1200, 150, 220, 60)
    p.v("res", lab("resilience", "retry 1 lớp · backoff · Retry-After"), BOX + PURPLE, 920, 580, 220, 60)
    p.v("ext", lab("LiteLLM · Postgres · Valkey", "vLLM Qwen3 · TEI bge-m3 · pgvector"), BOX + ORANGE, 1200, 580, 220, 60)
    p.v("diag", lab("diagnostics", "Timer · token · không log nội dung"), BOX + PURPLE, 920, 845, 220, 60)
    p.v("obs", "Langfuse + OpenTelemetry", BOX + ORANGE, 1200, 845, 220, 60)
    p.v("leg", "Tím: khối core · Xanh dương: luồng chính · Ô trắng: slot (contract + bản cài chọn bằng type: trong config) · Cam: hệ thống ngoài · Đỏ: lỗi · "
        "Nét đứt: chạy lúc khởi động hoặc trong CI. Mỗi slot gọi ra ngoài đều đi qua resilience và diagnostics.",
        TEXT + "fontSize=13;verticalAlign=top;fontColor=#555555;", 320, 1070, 1100, 50)

    p.e("e1", "http", "uc")
    p.e("e2", "uc", "safety")
    p.e("e3", "safety", "d1")
    p.e("e4", "d1", "s0", "Có", "fontColor=#2D7600;")
    p.e("e5", "d1", "err", "Không", "fontColor=#B20000;exitX=1;exitY=0.5;entryX=0.45;entryY=1;")
    p.e("e6", "err", "http", "lỗi", "strokeColor=#b85450;fontColor=#B20000;exitX=0.5;exitY=0;entryX=1;entryY=0.5;")
    p.e("e7", "mod", "res", "mỗi lần gọi ra ngoài", "exitX=1;exitY=0.3;entryX=0;entryY=0.5;")
    p.e("e8", "res", "ext", "gọi")
    p.e("e9", "res", "err", "hết lần thử", "strokeColor=#b85450;fontColor=#B20000;exitX=0.36;exitY=0;entryX=1;entryY=0.5;",
        points=[(1000, 180)])
    p.e("e10", "s6", "d2", "", "exitX=0.3;exitY=1;entryX=0.5;entryY=0;")
    p.e("e11", "d2", "rend", "Có", "fontColor=#2D7600;")
    p.e("e12", "d2", "regen", "Không", "fontColor=#B20000;")
    p.e("e13", "regen", "s6", "thử lại", "exitX=0.45;exitY=0;entryX=0.89;entryY=1;")
    p.e("e14", "rend", "end")
    p.e("e15", "cfg", "reg", "khởi động", "dashed=1;")
    p.e("e16", "reg", "mod", "lắp slot", "dashed=1;exitX=0;exitY=0.5;entryX=0.7;entryY=0;", points=[(614, 400)])
    p.e("e17", "res", "diag", "đo, trace", "dashed=1;")
    p.e("e18", "diag", "obs")
    return p.xml(1460, 1160)


MODULES = [
    ("A · facts + narrative", "build_report", [
        ("Source", "Snowflake, Excel, CSV"), ("Facts builder", "DuckDB, Polars"),
        ("Fact sheet", "metric khai báo bằng config"), ("Writer", "Qwen3, JSON schema"),
        ("Validator", "sai → gửi lại, quá 2 lần → template"), ("Renderer", "HTML, PDF, PPTX")]),
    ("B · RAG có trích dẫn", "answer_question · ingest", [
        ("Loader", "Docling, openpyxl"), ("Chunker", "theo heading, FAQ theo dòng"),
        ("Embedder", "bge-m3 (TEI)"), ("Store", "pgvector + full-text"),
        ("Retriever", "BM25 tiếng Việt + vector"), ("Reranker", "bge-reranker-v2-m3"),
        ("Synthesizer", "Qwen3, trích dẫn từng câu")]),
    ("C · check Xanh/Vàng/Đỏ", "check_content", [
        ("Input", "copy, KV, slide"), ("Tách claim", "Qwen3"), ("Luật bằng code", "từ cấm, câu bắt buộc"),
        ("Tra kho claim", "dùng module B"), ("Giải thích", "LLM, trích điều luật"),
        ("Chấm màu", "kèm câu an toàn"), ("Người duyệt", "Review UI")]),
    ("D · đọc tài liệu", "ingest_document", [
        ("Preparer", "pypdfium2, phân loại trang"), ("Signals", "key, ô nhận diện form"),
        ("Reader", "GLM-OCR, Docling"), ("Schema", "Pydantic theo chứng từ"),
        ("Checker", "đối soát chéo"), ("Records", "PostgreSQL"), ("Ngoại lệ", "Review UI")]),
    ("E · thị giác", "check_content", [
        ("Ảnh vào", "app field, Zalo"), ("Chống gian lận", "imagehash, EXIF, GPS"),
        ("Detector", "RT-DETR"), ("Chấm rubric", "Qwen-VL"), ("Checker", "luật trưng bày"),
        ("Điểm + feedback", "gửi lại nhân viên")]),
    ("F · ML, tối ưu", "forecast / optimise", [
        ("Dữ liệu", "dbt, DuckDB"), ("Feature", "Polars"), ("Train", "LightGBM, statsforecast"),
        ("Bất thường, uplift", "PyOD, EconML"), ("Registry", "MLflow"), ("Tối ưu", "OR-Tools, HiGHS"),
        ("Giải thích", "qua module A")]),
    ("G · điều khiển OT", "advise_setpoint", [
        ("Đọc OPC UA", "asyncua, chỉ đọc"), ("Soft-sensor", "LightGBM"), ("Mô phỏng", "mô hình quá trình"),
        ("Khuyến nghị", "thông số đề xuất"), ("Kỹ sư duyệt", "không tự đóng vòng")]),
    ("H · radar tín hiệu", "run_radar", [
        ("Provider", "điều khoản, giá"), ("Fetcher", "httpx, SSRF guard, ngân sách"),
        ("Extractor", "LLM, kiểm trích dẫn"), ("Gộp trùng", "fingerprint, rapidfuzz"),
        ("Chấm điểm", "diff với tuần trước"), ("Review packet", "Review UI"), ("Outbox", "email, Teams")]),
]


def page2():
    p = Page("mods", "2. Chuỗi slot 8 module")
    p.v("title", "Chuỗi slot của 8 module (mỗi ô là một slot; dòng dưới là công nghệ mặc định)",
        TEXT + "fontSize=20;fontStyle=1;", 20, -10, 1400, 40)
    lane_h, top, pitch = 100, 40, 185
    for i, (name, uc, slots) in enumerate(MODULES):
        y = top + i * lane_h
        p.v(f"l{i}", "", "rounded=0;html=1;strokeColor=none;" + ("fillColor=#f5f5f5;" if i % 2 else "fillColor=#ffffff;"),
            0, y, 1530, lane_h)
        p.v(f"n{i}", lab(f"<b>{name}</b>", uc), BOX + "fontSize=13;" + PURPLE, 10, y + 18, 170, 64)
        for j, (t, s) in enumerate(slots):
            style = BOX + "fontSize=13;" + (YELLOW if "duyệt" in t.lower() or t in ("Ngoại lệ", "Review packet", "Người duyệt") else BLUE)
            p.v(f"s{i}_{j}", lab(f"<b>{t}</b>", s), style, 210 + j * pitch, y + 18, 160, 64)
            if j:
                p.e(f"e{i}_{j}", f"s{i}_{j - 1}", f"s{i}_{j}")
            else:
                p.e(f"en{i}", f"n{i}", f"s{i}_0", "", "dashed=1;")
    p.v("leg", "Xanh dương: slot tự động · Vàng: bước có người duyệt · Đổi công nghệ bằng type: trong config, "
        "không sửa code module. Bên thứ ba (Azure Document Intelligence, OpenAI…) chỉ là một bản cài khác của cùng slot.",
        TEXT + "fontSize=13;fontColor=#555555;", 10, top + 8 * lane_h + 8, 1500, 40)
    return p.xml(1540, 900)


out = sys.argv[1]
with open(out, "w", encoding="utf-8") as f:
    f.write('<mxfile host="drawio" agent="Claude">' + page1() + page2() + "</mxfile>\n")
print("wrote", out)
