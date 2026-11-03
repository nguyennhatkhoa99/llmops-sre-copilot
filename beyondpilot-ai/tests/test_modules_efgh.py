from __future__ import annotations

import datetime as dt
import io
import time

import httpx
import pytest
from PIL import ExifTags, Image, ImageDraw

from beyondpilot.core.contracts import PlantReader, TagValue
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.settings import ResilienceConfig
from beyondpilot.modules.forecast import ForecastModule, SeriesSpec, anomalies, mape
from beyondpilot.modules.ot import LineSpec, OtModule
from beyondpilot.modules.radar import Provider, RadarModule
from beyondpilot.modules.vision import Rubric, VisionModule, distance_m
from beyondpilot.slots.allocator import ortools as ortools_alloc
from beyondpilot.slots.allocator import proportional
from beyondpilot.slots.detector import static as static_detector
from beyondpilot.slots.fetcher import http as http_fetcher
from beyondpilot.slots.fetcher import static as static_fetcher
from beyondpilot.slots.forecaster import lightgbm as lgb_forecaster
from beyondpilot.slots.forecaster import seasonal_naive
from beyondpilot.slots.model_registry import memory as memory_models
from beyondpilot.slots.plant_reader import static as static_plant
from beyondpilot.slots.records import memory as memory_records
from beyondpilot.slots.review import memory as memory_review
from beyondpilot.slots.soft_sensor import linear
from beyondpilot.slots.table_source import memory as memory_source
from tests.fakes import ScriptedLlm

RES = ResilienceConfig(attempts=2, base_delay=0, timeout=5)
NOW = dt.datetime(2026, 11, 3, 10, 0)
STORE = (10.7750, 106.6694)

# ---------- E: vision ----------


def photo(*, taken="2026:11:03 08:00:00", gps=(10.775, 106.6694), color=(200, 30, 30)) -> bytes:
    image = Image.new("RGB", (64, 48), color)
    ImageDraw.Draw(image).rectangle((5, 5, 30, 40), fill=(20, 20, 220))
    exif = Image.Exif()
    if taken:
        exif[ExifTags.Base.DateTime] = taken
    if gps:
        lat, lon = gps
        exif[ExifTags.IFD.GPSInfo] = {1: "N", 2: (lat, 0.0, 0.0), 3: "E", 4: (lon, 0.0, 0.0)}
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


RUBRIC = Rubric(
    criteria=[
        {"id": "vi_tri", "description": "Kệ ngang tầm mắt", "max": 40},
        {"id": "sach_se", "description": "Kệ sạch", "max": 20},
    ],
    min_score=70,
    min_count={"milo_hop": 3},
)
GOOD_GRADE = {"scores": {"vi_tri": 36, "sach_se": 18}, "feedback": "Tốt, giữ kệ gọn."}


def vision(llm, detections=None):
    review, records = memory_review.Impl(), memory_records.Impl()
    det = static_detector.Impl(
        detections if detections is not None else [{"label": "milo_hop"}] * 4
    )
    mod = VisionModule(
        detector=det, llm=llm, records=records, rubrics={"milo": RUBRIC}, review=review
    )
    return mod, review


async def test_good_photo_scores_without_review():
    mod, review = vision(ScriptedLlm(GOOD_GRADE))
    result = await mod.check(photo(), store_id="ch-1", rubric="milo", store_location=STORE, now=NOW)
    assert result.score == 90.0 and not result.needs_review and result.fraud_flags == []
    assert result.counts == {"milo_hop": 4} and review.items == []


async def test_resubmitted_photo_is_flagged():
    mod, review = vision(ScriptedLlm(GOOD_GRADE, repeat_last=True))
    data = photo()
    await mod.check(data, store_id="ch-1", rubric="milo", store_location=STORE, now=NOW)
    second = await mod.check(data, store_id="ch-1", rubric="milo", store_location=STORE, now=NOW)
    assert second.needs_review and any("đã gửi" in f for f in second.fraud_flags)
    assert review.items[0]["kind"] == "image_check"


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"taken": "2026:10:01 08:00:00"}, "quá 24 giờ"),
        ({"taken": None}, "giờ chụp"),
        ({"gps": (10.80, 106.70)}, "cách cửa hàng"),
        ({"gps": None}, "GPS"),
    ],
)
async def test_fraud_checks(kwargs, needle):
    mod, _ = vision(ScriptedLlm(GOOD_GRADE))
    result = await mod.check(
        photo(**kwargs), store_id="ch-2", rubric="milo", store_location=STORE, now=NOW
    )
    assert result.needs_review and any(needle in f for f in result.fraud_flags)


async def test_too_few_products_and_bad_grade():
    mod, _ = vision(ScriptedLlm({"scores": {"vi_tri": 99}}, "x", "y"), [{"label": "milo_hop"}])
    result = await mod.check(photo(), store_id="ch-3", rubric="milo", store_location=STORE, now=NOW)
    assert result.score is None and result.needs_review
    assert any("cần ít nhất 3" in i for i in result.issues)


async def test_vision_rejects_non_images():
    mod, _ = vision(ScriptedLlm())
    with pytest.raises(AppError) as info:
        await mod.check(b"%PDF-1.4", store_id="ch-1", rubric="milo")
    assert info.value.code is ErrorCode.INVALID_INPUT


def test_distance():
    assert distance_m((10.775, 106.6694), (10.775, 106.6694)) == 0
    assert 2700 < distance_m((10.775, 106.6694), (10.80, 106.6694)) < 2850  # 0.025° ≈ 2.78 km


# ---------- F: forecast ----------

WEEK = [100, 120, 130, 125, 140, 200, 180]


def sales_rows(days=56, regions=("Bắc", "Nam"), spike_at=None):
    rows = []
    for region_i, region in enumerate(regions):
        for d in range(days):
            value = WEEK[d % 7] * (1 + region_i * 0.5)
            if spike_at is not None and d == spike_at:
                value *= 6
            rows.append({"ngay": f"2026-08-{d:03d}", "vung": region, "so_thung": value})
    return rows


class Zeros:
    async def forecast(self, history, horizon):
        return [0.0] * horizon


def forecast_module(forecaster, llm, rows=None):
    return ForecastModule(
        source=memory_source.Impl({"ban_hang": rows or sales_rows()}),
        forecaster=forecaster,
        allocator=proportional.Impl(),
        llm=llm,
        series={"milo": SeriesSpec("ban_hang", "ngay", "so_thung", "vung")},
        model_registry=memory_models.Impl(),
        mape_threshold=20,
    )


async def test_forecast_publishes_good_model_and_allocates():
    llm = ScriptedLlm("Miền Nam cần {{ nhu_cau_nam }} thùng, được phân {{ phan_bo_nam }}.")
    mod = forecast_module(seasonal_naive.Impl(7), llm)
    result = await mod.run("milo", horizon=7, supply=1000)
    assert result.published and result.model_mape == 0.0
    groups = {g.group: g for g in result.groups}
    assert groups["Bắc"].demand == sum(WEEK)
    assert sum(g.allocation for g in result.groups) == pytest.approx(1000)
    assert groups["Nam"].allocation > groups["Bắc"].allocation
    assert result.explanation.startswith("Miền Nam cần 1.492,5 thùng")
    assert result.run_version == "1"


async def test_forecast_rejects_bad_model_and_uses_baseline():
    mod = forecast_module(Zeros(), ScriptedLlm("sai 40%", "sai 41%", "sai 42%"))
    result = await mod.run("milo", horizon=7)
    assert not result.published and "vượt ngưỡng" in result.reason
    assert result.groups[0].forecast == [float(v) for v in WEEK]  # seasonal naive used instead
    assert "- Sai số MAPE" in result.explanation  # writer fell back to the fact list


async def test_forecast_needs_enough_history():
    mod = forecast_module(seasonal_naive.Impl(7), ScriptedLlm(), rows=sales_rows(days=10))
    with pytest.raises(AppError) as info:
        await mod.run("milo", horizon=7)
    assert "not enough history" in info.value.detail


def test_anomalies_and_mape():
    values = [float(WEEK[d % 7]) for d in range(28)]
    values[10] = 2000.0
    found = anomalies([str(i) for i in range(28)], values)
    assert [a["date"] for a in found] == ["10"]
    assert mape([100, 200], [110, 180]) == pytest.approx(10.0)


async def test_proportional_respects_caps_and_refills():
    alloc = await proportional.Impl().allocate({"a": 100, "b": 300}, 300, {"b": 150})
    assert alloc["b"] == pytest.approx(150) and alloc["a"] == pytest.approx(100)
    short = await proportional.Impl().allocate({"a": 100, "b": 300}, 200, {})
    assert short == {"a": pytest.approx(50), "b": pytest.approx(150)}


async def test_ortools_allocation_is_fair():
    alloc = await ortools_alloc.Impl().allocate({"a": 100, "b": 300}, 200, {})
    assert alloc["a"] / 100 == pytest.approx(alloc["b"] / 300, abs=1e-6)
    assert sum(alloc.values()) == pytest.approx(200)


async def test_lightgbm_forecaster_runs():
    history = [float(WEEK[d % 7]) for d in range(70)]
    out = await lgb_forecaster.Impl(n_estimators=50).forecast(history, 7)
    assert len(out) == 7 and all(v >= 0 for v in out)
    assert mape(WEEK, out) < 25


# ---------- G: OT ----------

LINE = LineSpec(
    inputs={"nhiet_do_say": "L1.Temp", "toc_do": "L1.Speed"},
    setpoints={"nhiet_do_say": {"min": 160, "max": 190, "max_step": 5}},
    target={"name": "do_am", "goal": 3.0, "max": 3.5},
    ranges={"toc_do": (0.5, 2.0)},
)
SENSOR = linear.Impl({"nhiet_do_say": -0.05, "toc_do": 1.0}, intercept=11.0)  # độ ẩm


def ot_module(values, reader=None):
    review, records = memory_review.Impl(), memory_records.Impl()
    mod = OtModule(
        reader=reader or static_plant.Impl(values),
        soft_sensor=SENSOR,
        records=records,
        lines={"line1": LINE},
        review=review,
    )
    return mod, review, records


async def test_advice_moves_toward_target_within_step():
    # 170 °C, speed 1.0 -> moisture 3.5; goal 3.0 needs 180 °C, but one step is at most 5.
    mod, review, records = ot_module({"L1.Temp": 170, "L1.Speed": 1.0})
    advice = await mod.advise("line1")
    assert advice.recommended == {"nhiet_do_say": 175.0}
    assert advice.predicted_after == pytest.approx(3.25) and advice.applied is False
    assert review.items[0]["kind"] == "setpoint_advice"
    audit = await records.get("ot_audit", advice.audit_id)
    assert audit["decision"] == "pending" and audit["inputs"]["toc_do"] == 1.0


async def test_advice_keeps_setpoint_when_already_best():
    mod, *_ = ot_module({"L1.Temp": 180, "L1.Speed": 1.0})
    advice = await mod.advise("line1")
    assert advice.recommended == {"nhiet_do_say": 180.0} and "Giữ nguyên" in advice.reason


class StaleReader:
    async def read_tags(self, tags):
        old = time.time() - 600
        return {t: TagValue(170.0 if "Temp" in t else 1.0, old) for t in tags}


@pytest.mark.parametrize(
    ("values", "reader", "needle"),
    [
        ({"L1.Temp": 170, "L1.Speed": 9.0}, None, "ngoài khoảng"),
        ({"L1.Temp": 170}, None, "không đọc được"),
        ({}, StaleReader(), "dữ liệu cũ"),
    ],
)
async def test_bad_signals_stop_advice(values, reader, needle):
    mod, review, _ = ot_module(values, reader)
    with pytest.raises(AppError) as info:
        await mod.advise("line1")
    assert info.value.code is ErrorCode.NOT_READY and needle in info.value.detail
    assert review.items == []


def test_plant_reader_contract_has_no_write():
    assert [m for m in dir(PlantReader) if not m.startswith("_")] == ["read_tags"]


# ---------- H: radar ----------

PAGE_A = """Bộ Y tế ban hành quy định mới về ghi nhãn sữa cho trẻ dưới 6 tuổi.
Quy định có hiệu lực từ ngày 1/1/2027 và áp dụng cho toàn bộ sản phẩm sữa bột."""
PAGE_B = """Đối thủ X ra mắt sữa lúa mạch mới giá 9.000 đồng mỗi hộp.
Sản phẩm bán tại các cửa hàng tiện lợi từ tháng 11."""


def radar(llm, pages=None, records=None):
    fetcher = static_fetcher.Impl(
        pages if pages is not None else {"https://a.gov.vn/1": PAGE_A, "https://b.vn/2": PAGE_B}
    )
    review = memory_review.Impl()
    records = records or memory_records.Impl()
    mod = RadarModule(
        fetcher=fetcher,
        llm=llm,
        records=records,
        review=review,
        providers=[
            Provider("cong_bao", ["https://a.gov.vn/1"]),
            Provider("doi_thu", ["https://b.vn/2", "https://b.vn/missing"]),
        ],
        keywords={"ghi nhãn": 3, "sữa": 2},
        min_score=1.0,
    )
    return mod, review, records


SIG_A = {
    "signals": [
        {
            "title": "Quy định mới về ghi nhãn sữa",
            "summary": "Ghi nhãn sữa cho trẻ dưới 6 tuổi",
            "quote": "Quy định có hiệu lực từ ngày 1/1/2027",
        }
    ]
}
SIG_B = {
    "signals": [
        {
            "title": "Đối thủ X ra mắt sữa lúa mạch",
            "summary": "Giá 9.000 đồng",
            "quote": "Đối thủ X ra mắt sữa lúa mạch mới giá 9.000 đồng mỗi hộp.",
        }
    ]
}


async def test_radar_builds_digest_with_verified_quotes():
    fake = {"signals": [{"title": "Tin bịa", "summary": "x", "quote": "câu không có trong trang"}]}
    mod, review, _ = radar(ScriptedLlm(SIG_A, fake, SIG_B))
    result = await mod.run(today=dt.date(2026, 11, 3))
    assert result.run_id == "2026-W45"
    assert [s.title for s in result.signals][0] == "Quy định mới về ghi nhãn sữa"  # highest score
    assert len(result.signals) == 2 and all(s.new for s in result.signals)
    assert '> "Quy định có hiệu lực từ ngày 1/1/2027"' in result.digest
    assert {"url": "https://b.vn/missing", "reason": "UPSTREAM_ERROR"} in result.skipped
    assert review.items[0]["kind"] == "radar_digest"


async def test_radar_marks_repeats_and_dedupes():
    records = memory_records.Impl()
    first, _, _ = radar(ScriptedLlm(SIG_A, SIG_B), records=records)
    await first.run(today=dt.date(2026, 11, 3))
    again = {
        "signals": [
            *SIG_B["signals"],
            {
                "title": "Đối thủ X ra mắt sữa lúa mạch!",
                "summary": "trùng",
                "quote": "Sản phẩm bán tại các cửa hàng tiện lợi từ tháng 11.",
            },
        ]
    }
    second, _, _ = radar(ScriptedLlm(SIG_A, again), records=records)
    result = await second.run(today=dt.date(2026, 11, 10))
    assert len(result.signals) == 2 and not any(s.new for s in result.signals)
    seen = await records.get("radar_seen", result.signals[0].fingerprint)
    assert seen["first_run"] == "2026-W45"


async def test_radar_page_budget():
    mod, _, _ = radar(ScriptedLlm(SIG_A, repeat_last=True))
    mod.max_pages = 1
    result = await mod.run(today=dt.date(2026, 11, 3))
    assert sum(1 for s in result.skipped if "ngân sách" in s["reason"]) == 2


def fetcher(handler, resolver=lambda host: ["93.184.216.34"], **kw):
    return http_fetcher.Impl(
        allowed_domains=["a.gov.vn", "b.vn"],
        resilience=RES,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=resolver,
        **kw,
    )


async def test_http_fetcher_extracts_visible_text():
    html = "<html><script>var x='bí mật'</script><h1>Tin</h1><p>Nội dung <b>chính</b></p></html>"
    f = fetcher(lambda r: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    page = await f.fetch("https://a.gov.vn/x")
    assert page.text == "Tin\nNội dung chính"


@pytest.mark.parametrize(
    ("url", "resolver"),
    [
        ("https://evil.com/x", lambda h: ["93.184.216.34"]),
        ("http://a.gov.vn/x", lambda h: ["93.184.216.34"]),
        ("https://a.gov.vn/x", lambda h: ["10.0.0.5"]),
        ("https://b.vn/x", lambda h: ["169.254.169.254"]),
    ],
)
async def test_http_fetcher_ssrf_guard(url, resolver):
    calls = []
    f = fetcher(lambda r: calls.append(r) or httpx.Response(200, text="x"), resolver=resolver)
    with pytest.raises(AppError) as info:
        await f.fetch(url)
    assert info.value.code is ErrorCode.INVALID_INPUT and calls == []


async def test_http_fetcher_checks_redirect_targets():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest"})

    with pytest.raises(AppError):
        await fetcher(handler).fetch("https://a.gov.vn/x")


async def test_http_fetcher_caps_size():
    f = fetcher(
        lambda r: httpx.Response(200, text="a" * 5000, headers={"content-type": "text/plain"}),
        max_bytes=100,
    )
    assert len((await f.fetch("https://a.gov.vn/x")).text) == 100
