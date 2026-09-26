"""核心算法与 FastAPI 接口测试。"""
import random
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.bilingual import (
    MERGE_PENALTY_MS,
    SKIP_PENALTY_MS,
    align_bilingual,
)
from app.chapters import ChapterError, plan_chapters
from app.cutavoid import (
    CutAvoidError,
    avoid_cuts,
    first_blocked_order,
    normalize_cuts,
)
from app.cutrejoin import (
    DROPPED,
    KEPT,
    SPLIT,
    TRIMMED,
    Cut,
    CutRejoinError,
    cut_cue,
    merge_cuts,
    parse_cuts,
    rejoin_cues,
    removed_before,
)
from app.linewrap import (
    WrapFailure,
    WrapPlan,
    build_atoms,
    char_display_width,
    is_cjk_char,
    is_han,
    normalize_text,
    plan_wrap,
    wrap_document,
)
from app.mapping import AnchorError, PiecewiseMapping, build_anchors
from app.placement import (
    Block,
    PlacementError,
    count_switches,
    export_vtt,
    intersection_ms,
    merge_blocks,
    parse_blocks,
    plan_positions,
)
from app.reading_load import count_display_chars, find_reading_zones
from app.selection import ConflictPair, SelectionError, SelectionPlan, select_cues
from app.srt import BOM, Cue, SrtParseError, format_srt, parse_document, parse_srt, rewrite_srt
from app.terms import TermError, TermRule, apply_terms, build_rules, scan_line
from app.timecode import format_timecode, parse_timecode
from app.topic_clip import (
    TopicClipError,
    build_keyword_groups,
    check_starts_monotonic,
    find_topic_clip,
)

client = TestClient(app)


# ---------- 时间码 ----------

def test_parse_and_format_timecode():
    assert parse_timecode("00:01:23,456") == 83_456
    assert parse_timecode("1:2:3.4") == 3_723_400  # 毫秒补 3 位 -> 400
    assert format_timecode(83_456) == "00:01:23,456"
    assert format_timecode(-5) == "00:00:00,000"


def test_invalid_timecode():
    with pytest.raises(ValueError):
        parse_timecode("00:60:00,000")
    with pytest.raises(ValueError):
        parse_timecode("not-a-time")
    with pytest.raises(ValueError):
        parse_timecode("")


# ---------- SRT ----------

SAMPLE = (
    "1\r\n"
    "00:00:05,000 --> 00:00:08,000\r\n"
    "你好\n\n"
    "2\n"
    "00:00:10,000 --> 00:00:12,500\n"
    "第二行上\n第二行下\n"
)


def test_parse_preserves_index_and_text():
    cues = parse_srt(SAMPLE)
    assert len(cues) == 2
    assert cues[0].index == "1"
    assert cues[0].text == "你好"
    assert cues[1].text == "第二行上\n第二行下"
    assert cues[1].end - cues[1].start == 2500


def test_parse_missing_index_auto_numbers():
    cues = parse_srt("00:00:01,000 --> 00:00:02,000\n无序号块\n")
    assert cues[0].index == "1"


def test_parse_errors():
    with pytest.raises(SrtParseError):
        parse_srt("")
    with pytest.raises(SrtParseError):
        parse_srt("1\n00:00:05,000 --> 00:00:05,000\n等长\n")
    with pytest.raises(SrtParseError):
        parse_srt("1\n00:00:05,000 --> 00:00:03,000\n倒退\n")


def test_roundtrip_srt():
    out = format_srt(parse_srt(SAMPLE))
    assert "00:00:05,000 --> 00:00:08,000" in out
    assert "第二行上\n第二行下" in out


# ---------- 无损重写 ----------

POS_SAMPLE = (
    BOM
    + "1\r\n"
    + "  00:00:05,000 --> 00:00:08,000  X1:10 X2:630 Y1:20 Y2:60\r\n"
    + "你好\r\n"
    + "\r\n"
    + "2\r\n"
    + "00:00:10,000\t-->\t00:00:12,500  Position:50%,30%,0\r\n"
    + "第二行上\r\n第二行下\r\n"
)


def test_rewrite_identity_is_byte_identical():
    # 恒等锚点：除时间码按规范重写外，其余内容（BOM/CRLF/参数/箭头空格）逐字节不动
    cues = parse_srt(POS_SAMPLE)
    out = rewrite_srt(POS_SAMPLE, [(c.start, c.end) for c in cues])
    assert out == POS_SAMPLE


def test_rewrite_only_touches_timing_timecodes():
    cues = parse_srt(POS_SAMPLE)
    out = rewrite_srt(POS_SAMPLE, [(1_000, 4_000), (6_000, 8_500)])

    # 开头 BOM 唯一保留
    assert out.startswith(BOM)
    assert BOM not in out[1:]  # 正文内没有额外 BOM

    # 原有 CRLF 换行、定位参数、箭头前后空格全部保留
    assert (
        "  00:00:01,000 --> 00:00:04,000  X1:10 X2:630 Y1:20 Y2:60\r\n"
    ) in out
    assert (
        "00:00:06,000\t-->\t00:00:08,500  Position:50%,30%,0\r\n"
    ) in out

    # 序号与正文原样
    assert "1\r\n" in out
    assert "你好\r\n" in out
    assert "第二行上\r\n第二行下\r\n" in out

    # 旧时间码不再残留
    assert "00:00:05,000" not in out
    assert "00:00:12,500" not in out


def test_rewrite_preserves_blank_lines_and_lf():
    src = (
        "1\n00:00:05,000 --> 00:00:08,000\n第一句\n"
        "\n"
        "\n"  # 多余空行也保留
        "2\n00:00:10,000 --> 00:00:12,500 X1:1\r\n第二句\n"
    )
    cues = parse_srt(src)
    out = rewrite_srt(src, [(0, 1_000), (2_000, 3_000)])
    assert out.count("\r") == 1  # 仅第二块原有的那个 CR 保留，其余仍是 LF
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n\n2\n")
    assert out.endswith("00:00:02,000 --> 00:00:03,000 X1:1\r\n第二句\n")


def test_rewrite_without_bom_stays_bomless():
    src = "1\n00:00:05,000 --> 00:00:08,000\n你好\n"
    out = rewrite_srt(src, [(1_000, 2_000)])
    assert not out.startswith(BOM)
    assert out == "1\n00:00:01,000 --> 00:00:02,000\n你好\n"


def test_rewrite_collapses_multiple_leading_boms_to_one():
    src = BOM + BOM + "1\n00:00:05,000 --> 00:00:08,000\n你好\n"
    cues = parse_srt(src)
    assert len(cues) == 1
    out = rewrite_srt(src, [(1_000, 2_000)])
    assert out == BOM + "1\n00:00:01,000 --> 00:00:02,000\n你好\n"


def test_rewrite_invalid_srt_raises():
    with pytest.raises(SrtParseError):
        rewrite_srt("garbage without timing", [(1_000, 2_000)])


# ---------- 锚点与映射 ----------

def test_build_anchors_requires_two():
    with pytest.raises(AnchorError, match="至少需要 2 组"):
        build_anchors([("00:00:01,000", "00:00:00,500")])


def test_original_not_strictly_increasing_rejected():
    with pytest.raises(AnchorError, match="原时间未严格递增"):
        build_anchors([
            ("00:00:10,000", "00:00:08,000"),
            ("00:00:10,000", "00:00:09,000"),
        ])
    with pytest.raises(AnchorError, match="原时间未严格递增"):
        build_anchors([
            ("00:00:20,000", "00:00:08,000"),
            ("00:00:10,000", "00:00:09,000"),
        ])


def test_corrected_not_strictly_increasing_rejected():
    with pytest.raises(AnchorError, match="正确时间未严格递增"):
        build_anchors([
            ("00:00:10,000", "00:00:09,000"),
            ("00:00:20,000", "00:00:09,000"),
        ])


def test_invalid_anchor_time_rejected():
    with pytest.raises(AnchorError, match="原时间无效"):
        build_anchors([("bad", "00:00:09,000"), ("00:00:20,000", "00:00:19,000")])


def _mapping():
    # 10s->8s（-2s），100s->92s（-8s），斜率 56/60
    anchors = build_anchors([
        ("00:00:10,000", "00:00:08,000"),
        ("00:01:40,000", "00:01:32,000"),
    ])
    return PiecewiseMapping(anchors)


def test_mapping_hits_anchor_exactly():
    m = _mapping()
    assert m.map_ms(10_000) == 8_000
    assert m.map_ms(100_000) == 92_000


def test_mapping_interpolation_between_anchors():
    m = _mapping()
    # 中间点 55s -> 50s（线性中点）
    assert m.map_ms(55_000) == 50_000


def test_mapping_extrapolates_with_nearest_segment_slope():
    m = _mapping()
    # 右端外推：160s + 60s -> 92s + 56s = 148s
    assert m.map_ms(160_000) == 148_000
    # 左端外推：0 -> 8000 + (0-10000)*56/60 = 8000 - 9333.33 = -1333
    assert m.map_ms(0) == -1333


def test_three_anchor_piecewise_slopes():
    anchors = build_anchors([
        ("00:00:00,000", "00:00:00,000"),
        ("00:01:00,000", "00:00:54,000"),   # 斜率 0.9
        ("00:02:00,000", "00:02:00,000"),   # 斜率 1.1
    ])
    m = PiecewiseMapping(anchors)
    # 第一区段：30s -> 27s
    assert m.map_ms(30_000) == 27_000
    # 第二区段：90s -> 54s + (30s)*1.1 = 87s
    assert m.map_ms(90_000) == 87_000
    # 末段斜率外推：150s -> 120s + 30s*1.1 = 153s
    assert m.map_ms(150_000) == 153_000


# ---------- HTTP 接口 ----------

def request_body():
    return {
        "srt": SAMPLE,
        "anchors": [
            {"original": "00:00:10,000", "corrected": "00:00:08,000"},
            {"original": "00:01:40,000", "corrected": "00:01:32,000"},
        ],
    }


def test_health_and_index():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200


def test_api_correct_success():
    resp = client.post("/api/correct", json=request_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cues"][0]["index"] == "1"
    assert data["cues"][0]["text"] == "你好"
    assert data["cues"][0]["corrected_start"] == format_timecode(
        _mapping().map_ms(5000)
    )
    assert "你好" in data["corrected_srt"]


def test_api_lossless_preserves_bom_crlf_and_positioning():
    src = (
        BOM
        + "1\r\n"
        + "00:00:05,000 --> 00:00:08,000 X1:10 X2:630\r\n"
        + "你好\r\n\r\n"
        + "2\r\n"
        + "00:00:10,000 --> 00:00:12,500\r\n"
        + "第二行\r\n"
    )
    body = {
        "srt": src,
        "anchors": [
            {"original": "00:00:05,000", "corrected": "00:00:05,000"},
            {"original": "00:00:10,000", "corrected": "00:00:10,000"},
        ],
    }
    resp = client.post("/api/correct", json=body)
    assert resp.status_code == 200, resp.text
    # 恒等锚点下，返回体应与输入逐字符一致（BOM/CRLF/定位参数均无损）
    assert resp.json()["corrected_srt"] == src


def test_api_bomless_input_returns_bomless_srt():
    src = "1\n00:00:05,000 --> 00:00:08,000 X1:10\n你好\n"
    body = {
        "srt": src,
        "anchors": [
            {"original": "00:00:05,000", "corrected": "00:00:06,000"},
            {"original": "00:00:10,000", "corrected": "00:00:11,000"},
        ],
    }
    resp = client.post("/api/correct", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()["corrected_srt"]
    assert not out.startswith(BOM)
    assert out == "1\n00:00:06,000 --> 00:00:09,000 X1:10\n你好\n"


def test_api_rejects_bad_anchors_with_reason():
    body = request_body()
    body["anchors"][1]["original"] = "00:00:10,000"  # 与第一组相等
    resp = client.post("/api/correct", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "anchors"
    assert "原时间未严格递增" in resp.json()["detail"]["message"]


def test_api_rejects_bad_srt():
    body = request_body()
    body["srt"] = "garbage without timing"
    resp = client.post("/api/correct", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


# ---------- 字幕位置规划 ----------

PLACEMENT_SRT = (
    "1\n00:00:00,000 --> 00:00:10,000\n第一句\n\n"
    "2\n00:00:10,000 --> 00:00:20,000\n第二句\n\n"
    "3\n00:00:20,000 --> 00:00:30,000\n第三句\n"
)


def _placement_cues():
    return parse_srt(PLACEMENT_SRT)


def test_merge_blocks_unions_overlapping_and_adjacent():
    blocks = [Block(2_000, 8_000), Block(6_000, 12_000), Block(12_000, 15_000)]
    assert merge_blocks(blocks) == [Block(2_000, 15_000)]
    # 不重叠不相接的保持两段，乱序输入按开始时间排序
    assert merge_blocks([Block(20_000, 22_000), Block(0, 5_000)]) == [
        Block(0, 5_000),
        Block(20_000, 22_000),
    ]


def test_intersection_sums_overlap_with_cue():
    blocks = [Block(2_000, 8_000), Block(100_000, 110_000)]
    # 字幕 [0,10s] 与第一段交集 6s，与第二段无交集
    assert intersection_ms(0, 10_000, blocks) == 6_000
    # 仅端点相接不算遮挡（右开区间语义，毫秒整数）
    assert intersection_ms(8_000, 9_000, blocks) == 0


def test_parse_blocks_merges_and_validates():
    blocks = parse_blocks(
        [("00:00:02,000", "00:00:08,000"), ("00:00:06,000", "00:00:12,000")],
        "画面上方",
    )
    assert blocks == [Block(2_000, 12_000)]

    with pytest.raises(PlacementError, match="结束时间"):
        parse_blocks([("00:00:10,000", "00:00:10,000")], "画面下方")
    with pytest.raises(PlacementError, match="画面上方第 2 个"):
        parse_blocks(
            [("00:00:01,000", "00:00:02,000"), ("bad", "00:00:03,000")],
            "画面上方",
        )


def test_plan_prefers_unoccluded_side_and_counts_switch():
    cues = _placement_cues()
    top = [Block(2_000, 12_000)]   # 字幕1 遮挡8s、字幕2 遮挡2s
    bottom = [Block(22_000, 28_000)]  # 字幕3 下方遮挡6s

    # 代价 0：字幕1/2 下方无遮挡，字幕3 上方无遮挡 → 下/下/上，1 次换位
    p = plan_positions(cues, top, bottom, 0)
    assert [x.position for x in p] == ["bottom", "bottom", "top"]
    assert count_switches(p) == 1
    assert sum(x.occluded_ms for x in p) == 0
    # 逐条遮挡时长独立于最终选择
    assert p[0].top_occluded_ms == 8_000 and p[0].bottom_occluded_ms == 0
    assert p[2].top_occluded_ms == 0 and p[2].bottom_occluded_ms == 6_000


def test_plan_switch_cost_keeps_side_when_cheaper():
    cues = _placement_cues()
    top = [Block(2_000, 12_000)]
    bottom = [Block(22_000, 28_000)]
    # 换位 5000 < 留在下方的遮挡 6000：仍翻到上方
    assert [x.position for x in plan_positions(cues, top, bottom, 5_000)] == [
        "bottom",
        "bottom",
        "top",
    ]
    # 换位 7000 > 6000：全留下方，字幕3 承受 6000ms 遮挡
    p = plan_positions(cues, top, bottom, 7_000)
    assert [x.position for x in p] == ["bottom", "bottom", "bottom"]
    assert p[2].occluded_ms == 6_000 and p[2].still_occluded


def test_plan_ties_prefer_bottom_per_cue():
    cues = _placement_cues()
    # 无遮挡、零代价：所有组合同分，逐条优先下方
    p = plan_positions(cues, [], [], 0)
    assert [x.position for x in p] == ["bottom"] * 3

    # 词典序验证：字幕1 双侧无遮挡时，无论后续如何选择，字幕1 都应取下方
    # 字幕2 下方遮挡 1000 必须翻上，字幕3 双侧无遮挡（下方与字幕2 换位代价为 0）
    bottom = [Block(10_000, 11_000)]
    p2 = plan_positions(cues, [], bottom, 0)
    assert [x.position for x in p2] == ["bottom", "top", "bottom"]
    assert p2[0].position == "bottom"


def test_export_vtt_uses_line_positions():
    from app.placement import CuePlacement

    cues = _placement_cues()
    placements = [
        CuePlacement("bottom", 8_000, 0),
        CuePlacement("bottom", 2_000, 0),
        CuePlacement("top", 0, 6_000),
    ]
    vtt = export_vtt(cues, placements)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:10.000 line:90%" in vtt
    assert "00:00:20.000 --> 00:00:30.000 line:10%" in vtt
    assert "第一句" in vtt


def test_placement_page_served():
    resp = client.get("/placement")
    assert resp.status_code == 200
    assert "字幕位置规划" in resp.text


def placement_body(**overrides):
    body = {
        "srt": PLACEMENT_SRT,
        "top_blocks": [
            {"start": "00:00:02,000", "end": "00:00:08,000"},
            {"start": "00:00:06,000", "end": "00:00:12,000"},
        ],
        "bottom_blocks": [{"start": "00:00:22,000", "end": "00:00:28,000"}],
        "switch_cost_ms": 5000,
    }
    body.update(overrides)
    return body


def test_api_placement_success():
    resp = client.post("/api/placement", json=placement_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [c["position"] for c in data["cues"]] == ["bottom", "bottom", "top"]
    # 同侧重叠时段先求并集再回传
    assert data["merged_top_blocks"] == [
        {"start": "00:00:02,000", "end": "00:00:12,000"}
    ]
    assert data["merged_bottom_blocks"] == [
        {"start": "00:00:22,000", "end": "00:00:28,000"}
    ]
    assert data["switch_count"] == 1
    assert data["total_occluded_ms"] == 0
    assert data["still_occluded_cues"] == []
    assert data["cues"][0]["top_occluded_ms"] == 8000
    assert data["vtt"].startswith("WEBVTT\n\n")
    assert "line:90%" in data["vtt"] and "line:10%" in data["vtt"]


def test_api_placement_reports_still_occluded():
    resp = client.post(
        "/api/placement",
        json=placement_body(
            top_blocks=[{"start": "00:00:00,000", "end": "00:00:30,000"}],
            bottom_blocks=[{"start": "00:00:00,000", "end": "00:00:30,000"}],
            switch_cost_ms=0,
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 两侧全遮挡：仍能选出位置，且全部进入 still_occluded_cues；同分全部下方
    assert len(data["still_occluded_cues"]) == 3
    assert all(c["still_occluded"] for c in data["cues"])
    assert all(c["position"] == "bottom" for c in data["cues"])


@pytest.mark.parametrize("bad_cost", [-1, 1.5, True, False, "5", "abc"])
def test_api_placement_rejects_non_nonneg_int_cost(bad_cost):
    resp = client.post("/api/placement", json=placement_body(switch_cost_ms=bad_cost))
    assert resp.status_code == 422


def test_api_placement_requires_cost_field():
    body = placement_body()
    del body["switch_cost_ms"]
    assert client.post("/api/placement", json=body).status_code == 422


def test_api_placement_rejects_invalid_blocks():
    resp = client.post(
        "/api/placement",
        json=placement_body(
            top_blocks=[{"start": "00:00:10,000", "end": "00:00:10,000"}]
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "blocks"
    assert "结束时间" in resp.json()["detail"]["message"]


def test_api_placement_rejects_bad_timecode_and_srt():
    resp = client.post(
        "/api/placement",
        json=placement_body(
            bottom_blocks=[{"start": "not-a-time", "end": "00:00:10,000"}]
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "blocks"

    resp = client.post("/api/placement", json=placement_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


def test_api_placement_accepts_empty_blocks():
    resp = client.post(
        "/api/placement",
        json={"srt": PLACEMENT_SRT, "switch_cost_ms": 0},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert all(c["position"] == "bottom" for c in data["cues"])
    assert data["merged_top_blocks"] == []
    assert data["merged_bottom_blocks"] == []


# ---------- 阅读负荷热区 ----------

LOAD_SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\n一二三四五六\n\n"            # 3 字/秒
    "2\n00:00:01,000 --> 00:00:03,000\n四五六\n\n"                # 1.5 字/秒
    "3\n00:00:03,000 --> 00:00:04,000\n七八九十\n"                # 4 字/秒
)


def _load_cues():
    return parse_srt(LOAD_SRT)


def test_count_display_chars_strips_all_unicode_whitespace():
    assert count_display_chars("你好 世界\n第二行\t末　了") == 9
    assert count_display_chars("\n\t 　") == 0


def test_cue_loads_rate_and_duration():
    loads, _ = find_reading_zones(_load_cues(), Fraction(100), 0)
    assert [l.char_count for l in loads] == [6, 3, 4]
    assert [l.duration_ms for l in loads] == [2000, 2000, 1000]
    assert [float(l.rate) for l in loads] == [3.0, 1.5, 4.0]
    assert [l.order for l in loads] == [1, 2, 3]


def test_overlapping_rates_add_on_half_open_timeline():
    # 阈值 4：仅 [1s, 2s) 内 3 + 1.5 = 4.5 严格超过；[2s,3s) 仅 1.5 不超
    _, zones = find_reading_zones(_load_cues(), Fraction(4), 0)
    assert len(zones) == 1
    assert (zones[0].start, zones[0].end) == (1000, 2000)
    assert zones[0].peak_rate == Fraction(9, 2)
    assert zones[0].cue_orders == (1, 2)


def test_threshold_is_strict_greater_than():
    # 阈值 4.5：合计恰好 4.5，严格超过不成立；字幕3 速率 4 也不超 -> 无热区
    _, zones = find_reading_zones(_load_cues(), Fraction(45, 10), 0)
    assert zones == []


def test_touching_cues_do_not_overlap():
    # 字幕1 结束 == 字幕2 开始（3s）：半开时间轴上首尾相接不算重叠
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:03,000\n一二三四五六七八\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\n一二三四五六七八\n"
    )
    # 每条 8 字 / 3 秒；阈值 8/3：单条不严格超过，相接点也不叠加 -> 无热区
    _, zones = find_reading_zones(cues, Fraction(8, 3), 0)
    assert zones == []
    # 阈值严格小于单条速率时，相接的两段各自超阈后合并为 [0,6s)
    _, merged = find_reading_zones(cues, Fraction("2.6"), 0)
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end) == (0, 6000)
    assert merged[0].cue_orders == (1, 2)
    assert merged[0].peak_rate == Fraction(8, 3)


def test_adjacent_over_segments_merge_into_one_zone():
    # 字幕1：8字/4s=2；字幕2：10字/2s=5；字幕3：9字/3s=3
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:04,000\n一二三四五六七八\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n一二三四五六七八九十\n\n"
        "3\n00:00:04,000 --> 00:00:07,000\n一二三四五六七八九\n"
    )
    # 分段：[0,2)=2；[2,4)=7（字幕1+2）；[4,7)=3（字幕3，与上一段相接）
    # 阈值 2：[0,2) 不严格超，[2,4) 与 [4,7) 在 4s 处相接 -> 合并为 [2,7)
    _, zones = find_reading_zones(cues, Fraction(2), 0)
    assert len(zones) == 1
    assert (zones[0].start, zones[0].end) == (2000, 7000)
    assert zones[0].peak_rate == 7
    assert zones[0].cue_orders == (1, 2, 3)


def test_min_duration_filters_after_merge():
    # 同一热区 1000ms：下限 1000 保留，1001 丢弃
    _, keep = find_reading_zones(_load_cues(), Fraction(4), 1000)
    assert len(keep) == 1 and keep[0].end - keep[0].start == 1000
    _, drop = find_reading_zones(_load_cues(), Fraction(4), 1001)
    assert drop == []
    # 下限 0 不影响
    _, zero = find_reading_zones(_load_cues(), Fraction(4), 0)
    assert len(zero) == 1


def test_ms_precision_not_whole_second_sampling():
    # 仅 150ms 的毫秒级峰值也能检出（整秒采样会漏掉）
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:10,000\n慢\n\n"
        "2\n00:00:05,000 --> 00:00:05,150\n甲乙丙丁戊己庚\n"
    )
    _, zones = find_reading_zones(cues, Fraction(40), 0)
    assert [(z.start, z.end) for z in zones] == [(5000, 5150)]
    # 峰值为 7 字 / 0.15 秒（慢速字幕的 0.1 字/秒可忽略）
    assert zones[0].peak_rate > 46
    assert zones[0].cue_orders == (1, 2)


def test_float_threshold_compared_exactly():
    # 0.1+0.2 在二进制浮点下 != 0.3；合计速率恰为 0.3（3字/10秒）时，
    # 以十进制短表示构造的阈值 0.3 不应误判为严格超过
    cues = parse_srt("1\n00:00:00,000 --> 00:00:10,000\n三个字\n")
    _, zones = find_reading_zones(cues, Fraction("0.3"), 0)
    assert zones == []
    _, zones_over = find_reading_zones(cues, Fraction("0.299999"), 0)
    assert len(zones_over) == 1


def test_cue_orders_are_input_positions_sorted():
    # 时间乱序输入：参与序号仍按输入顺序返回，跨分段合并后升序去重
    cues = parse_srt(
        "9\n00:00:02,000 --> 00:00:05,000\n高速重叠字幕内容在这里\n\n"
        "7\n00:00:00,000 --> 00:00:03,000\n另一条更早开始的字幕哦\n"
    )
    _, zones = find_reading_zones(cues, Fraction(3), 0)
    assert zones
    # SRT 序号原样（9、7），但热区引用的是输入顺序号 (1, 2)
    assert zones[0].cue_orders == (1, 2)
    assert [c.index for c in cues] == ["9", "7"]


def test_reading_load_page_served():
    resp = client.get("/reading-load")
    assert resp.status_code == 200
    assert "阅读负荷热区" in resp.text
    assert "/api/reading-load" in resp.text


def load_body(**overrides):
    body = {
        "srt": LOAD_SRT,
        "rate_threshold": 4,
        "min_duration_ms": 1000,
    }
    body.update(overrides)
    return body


def test_api_reading_load_success():
    resp = client.post("/api/reading-load", json=load_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cue_count"] == 3
    assert data["zone_count"] == 1
    assert data["cues"][0]["order"] == 1
    assert data["cues"][0]["index"] == "1"
    assert data["cues"][0]["char_count"] == 6
    assert data["cues"][0]["rate"] == 3.0
    zone = data["zones"][0]
    assert zone["start"] == "00:00:01,000"
    assert zone["end"] == "00:00:02,000"
    assert zone["duration_ms"] == 1000
    assert zone["peak_rate"] == 4.5
    assert zone["cue_orders"] == [1, 2]


def test_api_reading_load_no_zone():
    resp = client.post(
        "/api/reading-load", json=load_body(rate_threshold=100, min_duration_ms=0)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["zones"] == []


def test_api_reading_load_rejects_bad_srt():
    resp = client.post("/api/reading-load", json=load_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


@pytest.mark.parametrize("bad_threshold", [0, -1, -0.1, True, False, "5", None])
def test_api_reading_load_rejects_bad_threshold(bad_threshold):
    resp = client.post(
        "/api/reading-load", json=load_body(rate_threshold=bad_threshold)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "rate_threshold"


@pytest.mark.parametrize("bad_duration", [-1, 1.5, True, False, "5", None])
def test_api_reading_load_rejects_bad_duration(bad_duration):
    resp = client.post(
        "/api/reading-load", json=load_body(min_duration_ms=bad_duration)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "min_duration_ms"


def test_api_reading_load_accepts_float_threshold():
    resp = client.post(
        "/api/reading-load", json=load_body(rate_threshold=4.5, min_duration_ms=0)
    )
    assert resp.status_code == 200, resp.text
    # 合计恰好 4.5：严格超过不成立
    assert resp.json()["zones"] == []


def test_api_reading_load_nan_token_is_422_not_500():
    resp = client.post(
        "/api/reading-load",
        content=(
            '{"srt":"1\\n00:00:01,000 --> 00:00:02,000\\n好\\n",'
            '"rate_threshold":NaN,"min_duration_ms":0}'
        ),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "rate_threshold"


# ---------- 字幕专名统一 ----------

TERMS_SRT = (
    "1\r\n"
    "00:00:01,000 --> 00:00:03,000\r\n"
    "他新买的 IPHONE 到货了\r\n"
    "\r\n"
    "2\n"
    "00:00:04,000 --> 00:00:06,000 X1:10\n"
    "用 iphone 拍视频\n"
    "第二行 iphone15\n"
)


def test_build_rules_rejects_empty_alias_and_standard():

    with pytest.raises(TermError, match="第 2 条规则"):
        build_rules([("北京大学", ["北大"]), ("清华", [""])])
    with pytest.raises(TermError, match="空别名"):
        build_rules([("北京大学", ["  "])])
    with pytest.raises(TermError, match="标准写法不能为空"):
        build_rules([("  ", ["北大"])])


def test_build_rules_rejects_same_alias_different_standard():

    with pytest.raises(TermError, match="同一别名只能对应一个标准写法"):
        build_rules([("北京大学", ["PKU"]), ("北大青鸟", ["PKU"])])
    # 同一别名指向同一标准写法允许（跨规则重复，各自按输入顺序保留）
    rules = build_rules([("北京大学", ["PKU"]), ("北京大学", ["PKU", "北大"])])
    assert rules[0].aliases == ("PKU",)
    assert rules[1].aliases == ("PKU", "北大")


def test_scan_line_longest_wins_and_case_sensitive():

    rules = build_rules([("北京大学", ["Peking University", "Peking Univ", "北大"])])
    out, hits = scan_line("Peking University 与北大", rules)
    assert out == "北京大学 与北京大学"
    # “Peking University”17 字符 + 空格 + “与”，“北大”起点为原文位置 19
    assert [h[0] for h in hits] == [0, 19]
    assert [h[1] for h in hits] == ["Peking University", "北大"]

    # 最长别名优先：同一位置长别名压过其前缀
    out2, hits2 = scan_line("Peking Univ 在此", rules)
    assert out2 == "北京大学 在此"
    assert hits2 == [(0, "Peking Univ", "北京大学")]

    # 区分大小写：小写 peking 不命中
    out3, hits3 = scan_line("peking university", rules)
    assert out3 == "peking university"
    assert hits3 == []


def test_scan_line_equal_length_uses_rule_order():

    # 合法输入下等长别名在同一位置只能是同一字符串（冲突校验已拒绝一对多），
    # 同别名经 build_rules 去重后保留在最早输入的规则中——等长同分自然由
    # scan_line 遍历到的首条规则承担。这里用直接构造的规则覆盖：
    # 1) 别名只在首条规则命中时取首条规则；
    first = TermRule(standard="先", aliases=("AA",), order=1)
    other = TermRule(standard="后", aliases=("AB",), order=2)
    out, hits = scan_line("AA 在此", [first, other])
    assert out == "先 在此"
    assert hits == [(0, "AA", "先")]

    # 2) 等长别名即使出现在后输入的规则，长度不占优也不会抢匹配；
    #    更长别名来自后输入规则时，最长优先仍压过顺序
    longer = TermRule(standard="长", aliases=("AAA",), order=2)
    out2, hits2 = scan_line("AAA", [first, longer])
    assert out2 == "长"
    assert hits2 == [(0, "AAA", "长")]


def test_scan_line_replacement_text_not_rematched():

    # 标准写法自身包含别名：只在原文位置匹配一次。若替换文本被重新扫描，
    # “甲”→“甲甲”会无限增长；正确行为是单次命中后整段跳过
    rules = build_rules([("甲甲", ["甲"])])
    assert scan_line("甲", rules) == ("甲甲", [(0, "甲", "甲甲")])

    # 多字原文：每个原文“甲”各命中一次，替换出来的“甲甲”不回扫
    out, hits = scan_line("甲甲", rules)
    assert out == "甲甲甲甲"
    assert hits == [(0, "甲", "甲甲"), (1, "甲", "甲甲")]

    # 连续不同别名各命中一次，位置基于原文逐个推进
    rules2 = build_rules([("X", ["甲", "乙"])])
    out2, hits2 = scan_line("甲乙甲", rules2)
    assert out2 == "XXX"
    assert [(h[0], h[1]) for h in hits2] == [(0, "甲"), (1, "乙"), (2, "甲")]


def test_apply_terms_only_touches_body_and_preserves_format():

    doc = parse_document(TERMS_SRT)
    rules = build_rules([("iPhone", ["IPHONE", "iphone"])])
    out, hits = apply_terms(
        TERMS_SRT, rules, [(r.start_line, r.end_line) for r in doc.body_ranges]
    )

    # 序号、时间轴（含 X1 排版参数）、CRLF/LF 混排、空行原样
    assert out.startswith("1\r\n00:00:01,000 --> 00:00:03,000\r\n")
    assert "\r\n\r\n2\n00:00:04,000 --> 00:00:06,000 X1:10\n" in out
    assert "他新买的 iPhone 到货了\r\n" in out
    assert "用 iPhone 拍视频\n第二行 iPhone15\n" in out
    # 时间轴与序号不被误改
    assert "00:00:04,000" in out
    # 命中清单：字幕输入序号、正文行号（块内从 1）、0 基列位置、原文、标准
    assert [(h.cue_order, h.line_no, h.column, h.source) for h in hits] == [
        (1, 1, 5, "IPHONE"),
        (2, 1, 2, "iphone"),
        (2, 2, 4, "iphone"),
    ]


def test_apply_terms_preserves_bom_and_cr():

    src = BOM + "1\r00:00:01,000 --> 00:00:02,000\r北大很好\r"
    doc = parse_document(src)
    rules = build_rules([("北京大学", ["北大"])])
    out, hits = apply_terms(
        src, rules, [(r.start_line, r.end_line) for r in doc.body_ranges]
    )
    assert out == BOM + "1\r00:00:01,000 --> 00:00:02,000\r北京大学很好\r"
    assert len(hits) == 1 and hits[0].cue_order == 1 and hits[0].column == 0


def test_apply_terms_no_hit_is_identical_input():

    # 无命中：原文原样返回（多个 BOM 也逐字符保留，不走重写补 BOM 路径）
    src = BOM + BOM + "1\n00:00:01,000 --> 00:00:02,000\n无别名\n"
    doc = parse_document(src)
    rules = build_rules([("X", ["不存在的别名"])])
    out, hits = apply_terms(
        src, rules, [(r.start_line, r.end_line) for r in doc.body_ranges]
    )
    assert out == src
    assert hits == []

    # 空规则列表同样原样返回
    out2, hits2 = apply_terms(
        src, [], [(r.start_line, r.end_line) for r in doc.body_ranges]
    )
    assert out2 == src and hits2 == []


def test_apply_terms_skips_index_and_timing_lines():

    src = "1\n00:00:01,000 --> 00:00:02,000\n正文不含别名\n"
    doc = parse_document(src)
    # 别名恰好等于序号字符与时间轴子串，也只能在正文匹配 -> 无命中
    rules = build_rules([("X", ["1", "00:00:01,000"])])
    out, hits = apply_terms(
        src, rules, [(r.start_line, r.end_line) for r in doc.body_ranges]
    )
    assert out == src and hits == []


def terms_body(**overrides):
    body = {
        "srt": TERMS_SRT,
        "rules": [
            {"standard": "iPhone", "aliases": ["IPHONE", "iphone"]},
            {"standard": "北京大学", "aliases": ["Peking University"]},
        ],
    }
    body.update(overrides)
    return body


def test_terms_page_served():
    resp = client.get("/terms")
    assert resp.status_code == 200
    assert "字幕专名统一" in resp.text
    assert "/api/terms" in resp.text


def test_api_terms_success_and_hits():
    resp = client.post("/api/terms", json=terms_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["hit_count"] == 3
    assert data["hits"][0] == {
        "cue_order": 1,
        "line_no": 1,
        "column": 5,
        "source": "IPHONE",
        "standard": "iPhone",
    }
    assert "他新买的 iPhone 到货了\r\n" in data["revised_srt"]
    # 原文的 CRLF/LF 混排与时间轴排版参数保留
    assert "00:00:04,000 --> 00:00:06,000 X1:10\n" in data["revised_srt"]
    assert data["revised_srt"].count("\r\n") == TERMS_SRT.count("\r\n")


def test_api_terms_no_hit_returns_original():
    body = terms_body(rules=[{"standard": "X", "aliases": ["不存在"]}])
    resp = client.post("/api/terms", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["hit_count"] == 0
    assert data["hits"] == []
    assert data["revised_srt"] == TERMS_SRT


def test_api_terms_empty_rules_is_identity():
    resp = client.post("/api/terms", json={"srt": TERMS_SRT, "rules": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["revised_srt"] == TERMS_SRT


def test_api_terms_rejects_empty_alias():
    body = terms_body()
    body["rules"][0]["aliases"].append("  ")
    resp = client.post("/api/terms", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "terms"
    assert "空别名" in detail["message"]


def test_api_terms_rejects_conflicting_alias():
    body = terms_body()
    body["rules"].append({"standard": "别的公司", "aliases": ["iphone"]})
    resp = client.post("/api/terms", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "terms"
    assert "iphone" in detail["message"]


def test_api_terms_rejects_bad_srt():
    resp = client.post("/api/terms", json=terms_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


def test_api_terms_bom_and_blank_lines_lossless():
    src = (
        BOM
        + "1\r\n00:00:01,000 --> 00:00:02,000\r\n北大\r\n\r\n\r\n"
        + "2\n00:00:03,000 --> 00:00:04,000\n也是北大\n"
    )
    body = {"srt": src, "rules": [{"standard": "北京大学", "aliases": ["北大"]}]}
    resp = client.post("/api/terms", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()["revised_srt"]
    assert out.startswith(BOM + "1\r\n")
    assert "北京大学\r\n\r\n\r\n" in out
    assert out.endswith("2\n00:00:03,000 --> 00:00:04,000\n也是北京大学\n")
    assert resp.json()["hit_count"] == 2


def test_api_terms_multiple_boms_collapse_to_one_on_change():
    # 有命中需要重写时，多个开头 BOM 最多保留一个（与 /api/correct 一致）；
    # 无命中时则逐字符原样返回（见 test_apply_terms_no_hit_is_identical_input）
    src = BOM + BOM + "1\n00:00:01,000 --> 00:00:02,000\n北大\n"
    body = {"srt": src, "rules": [{"standard": "北京大学", "aliases": ["北大"]}]}
    resp = client.post("/api/terms", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()["revised_srt"]
    assert out == BOM + "1\n00:00:01,000 --> 00:00:02,000\n北京大学\n"
    assert not out.startswith(BOM + BOM)


def test_api_terms_longer_alias_in_later_rule_wins():
    # 短别名来自先输入的规则、更长别名来自后输入规则：仍按最长选择
    src = "1\n00:00:01,000 --> 00:00:02,000\nABCD 与 AB\n"
    body = {
        "srt": src,
        "rules": [
            {"standard": "短", "aliases": ["AB"]},
            {"standard": "长", "aliases": ["ABCD"]},
        ],
    }
    resp = client.post("/api/terms", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "长 与 短" in data["revised_srt"]
    assert [(h["column"], h["source"], h["standard"]) for h in data["hits"]] == [
        (0, "ABCD", "长"),
        (7, "AB", "短"),
    ]



# ---------- 字幕智能断行 ----------

def test_char_width_east_asian_combining_and_other():
    assert char_display_width("中") == 2          # W
    assert char_display_width("，") == 2          # F（全角）
    assert char_display_width("Ａ") == 2          # 全角字母 F
    assert char_display_width("A") == 1
    assert char_display_width("1") == 1
    assert char_display_width("é") == 1           # 预组合字符本身占 1 列
    assert char_display_width("́") == 0            # U+0301 组合附加符
    assert char_display_width(" ") == 1


def test_han_and_cjk_punct_classification():
    assert is_han("中")
    assert is_han("㐀") and is_han("𠀀")         # 扩展 A / 扩展 B
    assert not is_han("A") and not is_han("，")
    assert is_cjk_char("，") and is_cjk_char("。")
    assert is_cjk_char("（") and is_cjk_char("《")
    assert is_cjk_char("·") and is_cjk_char("—") and is_cjk_char("’")
    assert not is_cjk_char(",") and not is_cjk_char("A")


def test_normalize_collapses_all_whitespace():
    assert normalize_text("一 二\n三\t四　五") == "一 二 三 四 五"
    assert normalize_text("\r\n  你好　世界 \t ") == "你好 世界"


def test_build_atoms_merges_english_but_splits_cjk():
    atoms, gaps = build_atoms("hello world你好")
    assert [a.text for a in atoms] == ["hello", "world", "你", "好"]
    assert gaps == [True, False, False]  # hello/world 之间有空格，其余相邻
    assert [a.width for a in atoms] == [5, 5, 2, 2]
    assert [a.cjk for a in atoms] == [False, False, True, True]
    # 英文单词内部的标点并入单词，不单独成原子
    atoms2, _ = build_atoms("iPhone15, plus")
    assert [a.text for a in atoms2] == ["iPhone15,", "plus"]
    # 组合附加符并入前一原子，不产生额外原子
    atoms3, _ = build_atoms("café 你好")
    assert [a.text for a in atoms3] == ["café", "你", "好"]
    assert atoms3[0].width == 4  # 4 个基字符，组合符 0 列


def test_wrap_balances_lines_by_squared_slack():
    # 6 个汉字共 12 列、8 列 2 行：均衡 3/3（代价 4+4）优于贪心 4/2（0+16）
    plan = plan_wrap("一二三四五六", 8, 2)
    assert isinstance(plan, WrapPlan)
    assert [line.text for line in plan.lines] == ["一二三", "四五六"]
    assert [line.width for line in plan.lines] == [6, 6]
    assert plan.breaks == (3,)


def test_wrap_tie_break_prefers_earlier_break_sequence():
    # 原子宽度 [2,2,1,2,2]（共 9 列，a 两侧是汉字故独立成原子），7 列 2 行：
    # 断在第 2 个原子后为 4/5（代价 3²+2²=13），断在第 3 个原子后为 5/4
    # （代价 2²+3²=13），完全同分且均为全局最优 -> 取断点序列靠前的 (2,)。
    # 更窄的断点（6/3）代价更大，故同分的恰是这两个断法
    plan = plan_wrap("中中a中中", 7, 2)
    assert isinstance(plan, WrapPlan)
    assert [line.text for line in plan.lines] == ["中中", "a中中"]
    assert [line.width for line in plan.lines] == [4, 5]
    assert plan.breaks == (2,)


def test_wrap_spaces_count_one_column_and_breakable():
    plan = plan_wrap("hello world foo bar", 10, 3)
    assert isinstance(plan, WrapPlan)
    assert [line.text for line in plan.lines] == ["hello", "world", "foo bar"]
    assert [line.width for line in plan.lines] == [5, 5, 7]
    # 行内保留空格，空格计 1 列：4 个汉字 8 列 + 3 个空格 3 列 = 11 列
    plan2 = plan_wrap("一 二 三 四", 11, 1)
    assert isinstance(plan2, WrapPlan)
    assert [line.text for line in plan2.lines] == ["一 二 三 四"]
    assert plan2.lines[0].width == 11
    # 10 列放不下 11 列的单行 -> 必须断成 2 行
    plan3 = plan_wrap("一 二 三 四", 10, 2)
    assert isinstance(plan3, WrapPlan)
    assert len(plan3.lines) == 2


def test_wrap_single_line_fits_when_possible():
    plan = plan_wrap("一二三四五", 10, 1)
    assert [line.text for line in plan.lines] == ["一二三四五"]
    assert plan.breaks == ()


def test_wrap_no_break_inside_english_word():
    # 每个单词必须整体成行
    plan = plan_wrap("internationalization test", 20, 2)
    assert [line.text for line in plan.lines] == ["internationalization", "test"]
    failure = plan_wrap("internationalization", 19, 2)
    assert isinstance(failure, WrapFailure)
    assert "不可断开的片段" in failure.reason and "20 列" in failure.reason


def test_wrap_forbids_open_punct_at_line_end():
    # 8 列：原子 你 好 （ 中 文 ） 世 界
    # “好（”（宽 6）与“（中”（宽 4）后断行都会让“（”处行尾/“）”处行首，
    # 可行断点须避开开标点行尾：断在“中”后 -> 行1=你好（中
    plan = plan_wrap("你好（中文）世界", 8, 2)
    assert [line.text for line in plan.lines] == ["你好（中", "文）世界"]
    assert not any(line.text.endswith("（") for line in plan.lines)


def test_wrap_forbids_close_punct_at_line_start():
    # 11 个原子共 22 列、12 列 2 行：恰好对半断会让第 6 个原子“，”成为
    # 第二行行首（闭标点），故只能把逗号留在第一行行尾（12/10）
    plan = plan_wrap("一二三四五，六七八九十", 12, 2)
    assert isinstance(plan, WrapPlan)
    assert [line.text for line in plan.lines] == ["一二三四五，", "六七八九十"]
    assert [line.width for line in plan.lines] == [12, 10]
    assert not any(line.text.startswith("，") for line in plan.lines)


def test_wrap_failure_when_too_many_lines_required():
    failure = plan_wrap("一二三四五六", 4, 1)
    assert isinstance(failure, WrapFailure)
    assert "行数" in failure.reason


def test_wrap_failure_when_only_kinsoku_violating_splits_exist():
    # 12 列恰好只能 6/6 对半，而第 7 个字符是“，” -> 必出现行首闭标点
    failure = plan_wrap("这是中文内容，还有更多字", 12, 2)
    assert isinstance(failure, WrapFailure)
    assert "行首不得为闭标点" in failure.reason


def test_wrap_document_preserves_eol_bom_and_positioning():
    src = (
        BOM
        + "1\r\n00:00:01,000 --> 00:00:03,000\r\n一二三四五六\r\n\r\n"
        + "2\n00:00:04,000 --> 00:00:06,000 X1:10\n你好世界\n"
    )
    out, results = wrap_document(src, 8, 2)
    # BOM 唯一；第一块原 CRLF 风格保留（新增内部换行也用 CRLF）
    assert out.startswith(BOM)
    assert out.startswith(BOM + "1\r\n00:00:01,000 --> 00:00:03,000\r\n")
    assert "一二三\r\n四五六\r\n" in out
    # 第二块 LF 与定位参数原样
    assert "00:00:04,000 --> 00:00:06,000 X1:10\n" in out
    assert results[0].changed and not results[1].changed


def test_wrap_document_identity_when_nothing_changes():
    src = BOM + BOM + "1\n00:00:01,000 --> 00:00:02,000\n一二三四五\n"
    out, results = wrap_document(src, 20, 2)
    assert out == src  # 多个 BOM 也逐字符保留
    assert results[0].success and not results[0].changed


def test_wrap_document_multiple_boms_collapse_to_one_on_change():
    src = BOM + BOM + "1\n00:00:01,000 --> 00:00:02,000\n一二三四五六\n"
    out, _ = wrap_document(src, 8, 2)
    assert out == BOM + "1\n00:00:01,000 --> 00:00:02,000\n一二三\n四五六\n"


def test_wrap_document_cr_only_file_uses_cr_for_new_breaks():
    src = "1\r00:00:01,000 --> 00:00:02,000\r一二三四五六\r"
    out, _ = wrap_document(src, 8, 2)
    assert out == "1\r00:00:01,000 --> 00:00:02,000\r一二三\r四五六\r"


def test_wrap_document_keeps_original_failure_cue_and_blank_lines():
    src = (
        "1\n00:00:01,000 --> 00:00:02,000\n一二三四五六\n\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nsupercalifragilistic\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\n五六七八九十\n"
    )
    out, results = wrap_document(src, 8, 2)
    status = [(r.cue_order, r.success, r.changed) for r in results]
    assert status == [(1, True, True), (2, False, False), (3, True, True)]
    # 失败字幕原文不动
    assert "2\n00:00:03,000 --> 00:00:04,000\nsupercalifragilistic\n" in out
    # 多余空行保留
    assert "一二三\n四五六\n\n\n2\n" in out
    assert results[1].reason and "不可断开的片段" in results[1].reason


def test_wrap_document_collapses_inner_newlines_to_spaces():
    src = "1\n00:00:01,000 --> 00:00:03,000\n你好\n世界\n"
    out, results = wrap_document(src, 20, 1)
    assert out == "1\n00:00:01,000 --> 00:00:03,000\n你好 世界\n"
    assert [line.text for line in results[0].lines] == ["你好 世界"]


def test_wrap_page_served():
    resp = client.get("/wrap")
    assert resp.status_code == 200
    assert "字幕智能断行" in resp.text
    assert "/api/wrap" in resp.text


WRAP_SRT = (
    "1\r\n00:00:01,000 --> 00:00:03,000\r\n一二三四五六\r\n\r\n"
    "2\n00:00:04,000 --> 00:00:06,000 X1:10\n你好 世界 hello\n第二行内容\n\n"
    "3\n00:00:07,000 --> 00:00:08,000\nsupercalifragilistic\n"
)


def wrap_body(**overrides):
    body = {"srt": WRAP_SRT, "max_columns": 8, "max_lines": 2}
    body.update(overrides)
    return body


def test_api_wrap_success_and_failure_counts():
    resp = client.post("/api/wrap", json=wrap_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cue_count"] == 3
    # 块1 可断 3/3；块2 空白折叠后宽 19 列、8 列至少 4 行；块3 单词 20 列超宽
    assert data["wrapped_count"] == 1
    assert data["failed_count"] == 2
    assert data["changed_count"] == 1
    cue1 = data["cues"][0]
    assert cue1["cue_order"] == 1 and cue1["index"] == "1" and cue1["changed"]
    assert [[l["text"], l["width"]] for l in cue1["lines"]] == [
        ["一二三", 6], ["四五六", 6]
    ]
    assert [f["cue_order"] for f in data["failures"]] == [2, 3]
    assert "最多行数" in data["failures"][0]["reason"]
    assert "不可断开的片段" in data["failures"][1]["reason"]


def test_api_wrap_srt_is_lossless_except_wrapped_bodies():
    data = client.post("/api/wrap", json=wrap_body()).json()
    out = data["wrapped_srt"]
    assert out.startswith("1\r\n00:00:01,000 --> 00:00:03,000\r\n")
    assert "一二三\r\n四五六\r\n" in out
    # 失败块 2：原文（含 X1 定位参数、LF、两行正文）完全不动
    assert (
        "2\n00:00:04,000 --> 00:00:06,000 X1:10\n"
        "你好 世界 hello\n第二行内容\n"
    ) in out
    # 失败块 3：超长单词原样
    assert "3\n00:00:07,000 --> 00:00:08,000\nsupercalifragilistic\n" in out
    # 块1 原本 1 行 CRLF、断行后变 2 行 CRLF（新增内部换行沿用原 CRLF）；
    # 失败块 2、3 的 LF 风格保持不变
    assert "一二三\r\n四五六\r\n" in out
    assert out.count("\n") - out.count("\r\n") == WRAP_SRT.count("\n") - WRAP_SRT.count("\r\n")


def test_api_wrap_no_change_returns_input_identically():
    # 每条正文均为单行且放得下（含 CRLF / LF 混排）：归一化不改变任何正文，
    # wrapped_srt 与输入逐字符一致
    single_line_srt = (
        "1\r\n00:00:01,000 --> 00:00:03,000\r\n一二三四五六\r\n\r\n"
        "2\n00:00:04,000 --> 00:00:06,000 X1:10\n你好 世界 hello\n\n"
        "3\n00:00:07,000 --> 00:00:08,000\nsupercalifragilistic\n"
    )
    resp = client.post(
        "/api/wrap",
        json={"srt": single_line_srt, "max_columns": 100, "max_lines": 1},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["wrapped_srt"] == single_line_srt
    assert data["changed_count"] == 0
    assert all(c["success"] for c in data["cues"])
    assert data["failures"] == []


def test_api_wrap_multiline_body_newlines_collapse_even_when_wide():
    # 多行正文即使在足够宽、单行放得下时，原有换行也会折成空格（规格要求）
    src = "1\n00:00:01,000 --> 00:00:03,000\n你好\n世界\n"
    resp = client.post(
        "/api/wrap", json={"srt": src, "max_columns": 100, "max_lines": 1}
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()["wrapped_srt"]
    assert out == "1\n00:00:01,000 --> 00:00:03,000\n你好 世界\n"
    assert resp.json()["changed_count"] == 1


@pytest.mark.parametrize("bad_value", [0, -1, 1.5, True, False, "8", None])
def test_api_wrap_rejects_bad_max_columns(bad_value):
    resp = client.post("/api/wrap", json=wrap_body(max_columns=bad_value))
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "max_columns"


@pytest.mark.parametrize("bad_value", [0, -1, 1.5, True, False, "2", None])
def test_api_wrap_rejects_bad_max_lines(bad_value):
    resp = client.post("/api/wrap", json=wrap_body(max_lines=bad_value))
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "max_lines"


def test_api_wrap_requires_fields():
    assert client.post("/api/wrap", json={"srt": WRAP_SRT}).status_code == 422
    assert client.post(
        "/api/wrap", json={"max_columns": 8, "max_lines": 2}
    ).status_code == 422


def test_api_wrap_rejects_bad_srt():
    resp = client.post("/api/wrap", json=wrap_body(srt="garbage without timing"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


# ---------- 字幕章节规划 ----------

# 5 条首尾相接、各 3000ms 的字幕：整段 15000ms
CHAPTERS_SRT = (
    "1\n00:00:00,000 --> 00:00:03,000\n第一句\n\n"
    "2\n00:00:03,000 --> 00:00:06,000\n第二句\n\n"
    "3\n00:00:06,000 --> 00:00:09,000\n第三句\n\n"
    "4\n00:00:09,000 --> 00:00:12,000\n第四句\n\n"
    "5\n00:00:12,000 --> 00:00:15,000\n第五句\n"
)

# 停顿位置与时长均衡冲突的样例：第 1 条后停顿 100ms、第 2 条后停顿 5000ms
PAUSE_SRT = (
    "1\n00:00:00,000 --> 00:00:17,500\n第一句\n\n"
    "2\n00:00:17,600 --> 00:00:17,650\n第二句\n\n"
    "3\n00:00:22,650 --> 00:00:40,000\n第三句\n"
)


def _chapter_cues():
    return parse_srt(CHAPTERS_SRT)


def test_plan_chapters_balances_durations_and_tie_prefers_earlier_split():
    # 目标 7500ms：切在第 2 条后（6000/9000）与第 3 条后（9000/6000）同分，
    # 同分取切分点序列更靠前者 -> 第 2 条后
    plan = plan_chapters(_chapter_cues(), 2, 0)
    assert [(ch.cue_order_from, ch.cue_order_to) for ch in plan.chapters] == [(1, 2), (3, 5)]
    assert [(ch.start, ch.end) for ch in plan.chapters] == [(0, 6000), (6000, 15000)]
    assert [ch.order for ch in plan.chapters] == [1, 2]
    assert plan.total_duration_ms == 15000
    # 偏差平方和的 K² 倍 = (6000·2−15000)² + (9000·2−15000)²
    assert plan.duration_cost_num == 2 * (6000 * 2 - 15000) ** 2
    assert plan.pause_cost == 0
    assert [sp.after_cue_order for sp in plan.splits] == [2]
    assert plan.splits[0].gap_ms == 0 and plan.splits[0].pause_shortfall_ms == 0


def test_plan_chapters_preferred_pause_moves_split_to_longer_gap():
    cues = parse_srt(PAUSE_SRT)
    # P=0：纯按时长均衡，切在第 1 条后（17500/22400 比 17650/17350 更靠近 20000）
    plan0 = plan_chapters(cues, 2, 0)
    assert [sp.after_cue_order for sp in plan0.splits] == [1]
    # P=5000：第 2 条后的 5000ms 停顿不再受罚，切分点移到第 2 条后
    plan5 = plan_chapters(cues, 2, 5000)
    assert [sp.after_cue_order for sp in plan5.splits] == [2]
    assert plan5.splits[0].gap_ms == 5000
    assert plan5.splits[0].pause_shortfall_ms == 0
    assert plan5.pause_cost == 0


def test_plan_chapters_pause_shortfall_squared_when_gap_below_p():
    # 首尾相接（间隔 0）、P=6000：切分处缺口 6000，惩罚 6000²
    plan = plan_chapters(_chapter_cues(), 2, 6000)
    assert [sp.after_cue_order for sp in plan.splits] == [2]
    assert plan.splits[0].pause_shortfall_ms == 6000
    assert plan.pause_cost == 6000 * 6000


def test_plan_chapters_three_way_tie_picks_earliest_split_sequence():
    # 7 条各 2000ms 相接：目标 14000/3，(4000,4000,6000)、(4000,6000,4000)、
    # (6000,4000,4000) 三种切法同分 -> 取切分点序列最靠前的 (2, 4)
    cues = parse_srt("".join(
        f"{i}\n{format_timecode((i - 1) * 2000)} --> {format_timecode(i * 2000)}\n第{i}句\n\n"
        for i in range(1, 8)
    ))
    plan = plan_chapters(cues, 3, 0)
    assert [sp.after_cue_order for sp in plan.splits] == [2, 4]
    assert [(ch.cue_order_from, ch.cue_order_to) for ch in plan.chapters] == [
        (1, 2),
        (3, 4),
        (5, 7),
    ]
    assert [ch.end - ch.start for ch in plan.chapters] == [4000, 4000, 6000]


def test_plan_chapters_single_chapter_and_one_cue_per_chapter():
    cues = _chapter_cues()
    one = plan_chapters(cues, 1, 800)
    assert len(one.chapters) == 1 and one.splits == ()
    assert (one.chapters[0].cue_order_from, one.chapters[0].cue_order_to) == (1, 5)
    assert one.duration_cost_num == 0 and one.pause_cost == 0

    each = plan_chapters(cues, 5, 0)
    assert [(ch.cue_order_from, ch.cue_order_to) for ch in each.chapters] == [
        (1, 1), (2, 2), (3, 3), (4, 4), (5, 5)
    ]
    assert [sp.after_cue_order for sp in each.splits] == [1, 2, 3, 4]


def test_plan_chapters_rejects_more_chapters_than_cues():
    with pytest.raises(ChapterError, match="少于章节数"):
        plan_chapters(_chapter_cues(), 6, 0)


def test_plan_chapters_rejects_when_feasible_splits_not_enough():
    # 第 1、2 条时间重叠（不可切），仅第 2 条后可切：切 3 章需要 2 个切分点
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:10,000\n第一句\n\n"
        "2\n00:00:05,000 --> 00:00:15,000\n第二句\n\n"
        "3\n00:00:15,000 --> 00:00:20,000\n第三句\n"
    )
    with pytest.raises(ChapterError, match="仅 1 处"):
        plan_chapters(cues, 3, 0)
    # 切 2 章可行：只能切在第 2 条后（前条结束 15000 不晚于后条开始 15000）
    plan = plan_chapters(cues, 2, 0)
    assert [sp.after_cue_order for sp in plan.splits] == [2]
    assert [(ch.cue_order_from, ch.cue_order_to) for ch in plan.chapters] == [(1, 2), (3, 3)]


def test_chapters_page_served():
    resp = client.get("/chapters")
    assert resp.status_code == 200
    assert "字幕章节规划" in resp.text
    assert "/api/chapters" in resp.text


def chapters_body(**overrides):
    body = {"srt": CHAPTERS_SRT, "chapter_count": 2, "preferred_pause_ms": 0}
    body.update(overrides)
    return body


def test_api_chapters_success():
    resp = client.post("/api/chapters", json=chapters_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chapter_count"] == 2
    assert data["preferred_pause_ms"] == 0
    assert data["cue_count"] == 5
    assert data["total_duration_ms"] == 15000
    assert data["target_duration_ms"] == 7500.0
    assert data["duration_cost"] == 4500000.0
    assert data["pause_cost"] == 0
    assert data["total_cost"] == 4500000.0

    first, second = data["chapters"]
    assert first["order"] == 1
    assert first["start"] == "00:00:00,000" and first["end"] == "00:00:06,000"
    assert first["duration_ms"] == 6000 and first["deviation_ms"] == -1500.0
    assert (first["cue_order_from"], first["cue_order_to"], first["cue_count"]) == (1, 2, 2)
    assert second["start"] == "00:00:06,000" and second["end"] == "00:00:15,000"
    assert second["duration_ms"] == 9000 and second["deviation_ms"] == 1500.0
    assert (second["cue_order_from"], second["cue_order_to"], second["cue_count"]) == (3, 5, 3)

    assert data["splits"] == [
        {
            "after_cue_order": 2,
            "before_cue_order": 3,
            "gap_ms": 0,
            "pause_shortfall_ms": 0,
            "penalty": 0,
        }
    ]


def test_api_chapters_preferred_pause_changes_split():
    resp = client.post(
        "/api/chapters",
        json=chapters_body(srt=PAUSE_SRT, chapter_count=2, preferred_pause_ms=5000),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [s["after_cue_order"] for s in data["splits"]] == [2]
    assert data["splits"][0]["gap_ms"] == 5000
    assert data["splits"][0]["pause_shortfall_ms"] == 0
    assert data["pause_cost"] == 0
    assert [c["cue_order_from"] for c in data["chapters"]] == [1, 3]

    # 同一 SRT、P=0 时切在第 1 条后（纯时长均衡）
    resp0 = client.post(
        "/api/chapters",
        json=chapters_body(srt=PAUSE_SRT, chapter_count=2, preferred_pause_ms=0),
    )
    assert [s["after_cue_order"] for s in resp0.json()["splits"]] == [1]


def test_api_chapters_pause_shortfall_adds_squared_penalty():
    resp = client.post("/api/chapters", json=chapters_body(preferred_pause_ms=6000))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["splits"][0]["pause_shortfall_ms"] == 6000
    assert data["splits"][0]["penalty"] == 6000 * 6000
    assert data["pause_cost"] == 6000 * 6000
    assert data["total_cost"] == 4500000.0 + 6000 * 6000


def test_api_chapters_fractional_target_and_cost_rounded():
    # 7 条各 2000ms、K=3：目标 14000/3 ≈ 4666.667ms，
    # 偏差平方和 = (2·(−2000)² + 4000²) / 9 = 24000000/9 ≈ 2666666.667
    srt = "".join(
        f"{i}\n{format_timecode((i - 1) * 2000)} --> {format_timecode(i * 2000)}\n第{i}句\n\n"
        for i in range(1, 8)
    )
    resp = client.post(
        "/api/chapters", json={"srt": srt, "chapter_count": 3, "preferred_pause_ms": 0}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["target_duration_ms"] == round(14000 / 3, 3)
    assert data["duration_cost"] == round(24000000 / 9, 3)
    assert data["total_cost"] == data["duration_cost"]
    assert [s["after_cue_order"] for s in data["splits"]] == [2, 4]


@pytest.mark.parametrize("bad_value", [0, -1, 1.5, True, False, "3", None])
def test_api_chapters_rejects_bad_chapter_count(bad_value):
    resp = client.post("/api/chapters", json=chapters_body(chapter_count=bad_value))
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "chapter_count"


@pytest.mark.parametrize("bad_value", [-1, 1.5, True, False, "800", None])
def test_api_chapters_rejects_bad_preferred_pause(bad_value):
    resp = client.post("/api/chapters", json=chapters_body(preferred_pause_ms=bad_value))
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "preferred_pause_ms"


def test_api_chapters_requires_fields():
    assert client.post("/api/chapters", json={"srt": CHAPTERS_SRT}).status_code == 422
    assert client.post(
        "/api/chapters", json={"chapter_count": 2, "preferred_pause_ms": 0}
    ).status_code == 422


def test_api_chapters_rejects_bad_srt():
    resp = client.post("/api/chapters", json=chapters_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


def test_api_chapters_reports_reason_when_not_splittable():
    # 字幕条数少于章节数
    resp = client.post("/api/chapters", json=chapters_body(chapter_count=6))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "chapters"
    assert "少于章节数" in detail["message"]

    # 相邻字幕时间重叠导致可切位置不足
    overlapping = (
        "1\n00:00:00,000 --> 00:00:10,000\n第一句\n\n"
        "2\n00:00:05,000 --> 00:00:15,000\n第二句\n\n"
        "3\n00:00:15,000 --> 00:00:20,000\n第三句\n"
    )
    resp = client.post(
        "/api/chapters",
        json={"srt": overlapping, "chapter_count": 3, "preferred_pause_ms": 0},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "chapters"
    assert "仅 1 处" in detail["message"]
    assert "第 2 条" in detail["message"]


# ---------- 字幕主题短片段检索 ----------

CLIP_SRT = (
    "1\n00:00:00,000 --> 00:00:03,000\n片头音乐渐入\n\n"
    "2\n00:00:03,200 --> 00:00:07,000\n今天聊聊人工智能的新进展\n\n"
    "3\n00:00:07,200 --> 00:00:11,000\n大模型在翻译任务上表现突出\n\n"
    "4\n00:00:11,200 --> 00:00:15,000\n接下来进入体育新闻\n\n"
    "5\n00:00:15,200 --> 00:00:19,000\n昨晚足球比赛十分精彩\n\n"
    "6\n00:00:19,200 --> 00:00:23,000\n主队三比一获胜，人工智能裁判抢镜\n\n"
    "7\n00:00:23,200 --> 00:00:32,000\n最后是天气预报\n\n"
    "8\n00:00:27,200 --> 00:00:31,000\n明天多云，人工智能助手提醒带伞\n"
)


def _clip_cues():
    return parse_srt(CLIP_SRT)


def test_build_keyword_groups_strips_folds_and_dedupes():
    groups = build_keyword_groups([(" 人工智能 ", 1), ("模型", 2)])
    assert [g.word for g in groups] == ["人工智能", "模型"]
    assert [g.folded for g in groups] == ["人工智能", "模型"]

    # 折叠后重复（ASCII 大小写）
    with pytest.raises(TopicClipError, match="第 2 组"):
        build_keyword_groups([("ABC", 1), ("abc", 2)])
    # Unicode casefold：Straße 与 STRASSE 折叠后同为 strasse
    with pytest.raises(TopicClipError, match="重复"):
        build_keyword_groups([("Straße", 1), ("STRASSE", 1)])


def test_build_keyword_groups_rejects_empty_and_count():
    with pytest.raises(TopicClipError, match="第 1 组关键词为空"):
        build_keyword_groups([("   ", 1)])
    with pytest.raises(TopicClipError, match="第 2 组关键词为空"):
        build_keyword_groups([("甲", 1), ("\t ", 1)])
    with pytest.raises(TopicClipError, match="正整数"):
        build_keyword_groups([("甲", 0)])
    with pytest.raises(TopicClipError, match="1～8 组"):
        build_keyword_groups([("甲", 1)] * 9)
    with pytest.raises(TopicClipError, match="1～8 组"):
        build_keyword_groups([])


def test_casefold_match_and_same_cue_counts_once():
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:02,000\neine STRASSE\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\ndie straße dort\n"
    )
    groups = build_keyword_groups([("Strasse", 2)])
    result = find_topic_clip(cues, groups)
    assert result.found
    # 两条字幕折叠后都含 strasse -> 各计一次
    assert (result.clip.cue_order_from, result.clip.cue_order_to) == (1, 2)
    assert result.clip.word_hits[0].hit_count == 2

    # 同一条字幕内同一词出现多次只计一次
    cues2 = parse_srt("1\n00:00:00,000 --> 00:00:02,000\n甲甲甲\n")
    result2 = find_topic_clip(cues2, build_keyword_groups([("甲", 2)]))
    assert not result2.found
    assert result2.totals == (1,)


def test_window_start_is_first_start_and_end_is_latest_end():
    # 窗口内最晚结束来自中间的长字幕（第 3 条与第 2 条重叠、自身结束更早）
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:05,000\n甲\n\n"
        "2\n00:00:02,000 --> 00:00:20,000\n乙\n\n"
        "3\n00:00:09,000 --> 00:00:10,000\n丙\n"
    )
    result = find_topic_clip(
        cues, build_keyword_groups([("甲", 1), ("乙", 1), ("丙", 1)])
    )
    # 必须跨三条；起点取首条开始 0，终点取窗口内最晚结束 20000（中间一条）
    assert (result.clip.cue_order_from, result.clip.cue_order_to) == (1, 3)
    assert (result.clip.start, result.clip.end) == (0, 20000)


def test_tie_break_duration_then_start_then_order():
    # 两个等长（6000ms）窗口，中间用不含关键词的间隔拉开：取起点更早者
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:02,000\n甲\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n无关\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\n甲乙\n\n"
        "4\n00:00:10,000 --> 00:00:12,000\n无关\n\n"
        "5\n00:00:12,000 --> 00:00:14,000\n无关\n\n"
        "6\n00:00:14,000 --> 00:00:16,000\n甲\n\n"
        "7\n00:00:16,000 --> 00:00:18,000\n无关\n\n"
        "8\n00:00:18,000 --> 00:00:20,000\n甲乙\n"
    )
    # 窗口 [1..3]：start0 end6000 dur6；窗口 [6..8]：start14000 end20000 dur6；
    # 跨两段的窗口都包含间隔、时长更长 -> 同分取起点早者
    result = find_topic_clip(cues, build_keyword_groups([("甲", 2), ("乙", 1)]))
    assert (result.clip.cue_order_from, result.clip.cue_order_to) == (1, 3)
    assert (result.clip.start, result.clip.end) == (0, 6000)


def test_no_solution_reports_per_word_missing():
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:02,000\n甲\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n甲乙\n"
    )
    result = find_topic_clip(cues, build_keyword_groups([("甲", 3), ("乙", 2)]))
    assert not result.found
    assert result.totals == (2, 1)  # 全片甲 2 条、乙 1 条 -> 缺 1、1


def test_check_starts_monotonic_allows_equal_reports_order():
    ok = parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n甲\n\n"
        "2\n00:00:01,000 --> 00:00:03,000\n乙\n"
    )
    check_starts_monotonic(ok)  # 相等允许，不抛异常

    bad = parse_srt(
        "9\n00:00:05,000 --> 00:00:08,000\n晚开始\n\n"
        "7\n00:00:03,000 --> 00:00:05,000\n早开始\n"
    )
    with pytest.raises(TopicClipError, match="第 2 条") as exc_info:
        check_starts_monotonic(bad)
    # 指出输入序号（与 SRT 自身序号无关）并回显两侧 SRT 序号
    assert "SRT 序号 7" in str(exc_info.value)
    assert "SRT 序号 9" in str(exc_info.value)


def test_topic_clip_page_served():
    resp = client.get("/topic-clip")
    assert resp.status_code == 200
    assert "字幕主题短片段检索" in resp.text
    assert "/api/topic-clip" in resp.text


def clip_body(**overrides):
    body = {
        "srt": CLIP_SRT,
        "keywords": [
            {"word": "人工智能", "required_count": 2},
            {"word": "模型", "required_count": 1},
        ],
    }
    body.update(overrides)
    return body


def test_api_topic_clip_success():
    resp = client.post("/api/topic-clip", json=clip_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] is True
    assert data["cue_count"] == 8
    clip = data["clip"]
    # 窗口 [2..6]：首条开始 3200，窗口内最晚结束 23000（第 7 条不在窗口内）
    assert clip["start"] == "00:00:03,200"
    assert clip["end"] == "00:00:23,000"
    assert clip["duration_ms"] == 19800
    assert (clip["cue_order_from"], clip["cue_order_to"], clip["cue_count"]) == (2, 6, 5)
    # 逐词命中（同一字幕每词至多一次）
    assert clip["word_hits"] == [
        {"word": "人工智能", "required_count": 2, "hit_count": 2, "missing_count": 0},
        {"word": "模型", "required_count": 1, "hit_count": 1, "missing_count": 0},
    ]
    # 顶层 keywords 与 clip.word_hits 为同一份逐词统计
    assert data["keywords"] == clip["word_hits"]
    # 窗口内逐条字幕及命中词
    orders = [c["cue_order"] for c in clip["cues"]]
    assert orders == [2, 3, 4, 5, 6]
    assert clip["cues"][0]["index"] == "2"
    assert clip["cues"][0]["hit_words"] == ["人工智能"]
    assert clip["cues"][1]["hit_words"] == ["模型"]
    assert clip["cues"][3]["hit_words"] == []
    assert clip["cues"][4]["hit_words"] == ["人工智能"]


def test_api_topic_clip_casefold_and_repeat_in_one_cue():
    src = (
        "1\n00:00:00,000 --> 00:00:02,000\nSTRASSE straße\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\ndie Strasse\n"
    )
    body = {"srt": src, "keywords": [{"word": "straße", "required_count": 2}]}
    resp = client.post("/api/topic-clip", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] is True
    # 第一条内两种写法各出现一次，但同一字幕只计一次 -> 必须跨两条
    assert (data["clip"]["cue_order_from"], data["clip"]["cue_order_to"]) == (1, 2)
    assert data["clip"]["word_hits"][0]["hit_count"] == 2


def test_api_topic_clip_no_solution_is_200_with_missing():
    body = clip_body(keywords=[
        {"word": "人工智能", "required_count": 4},
        {"word": "模型", "required_count": 2},
    ])
    resp = client.post("/api/topic-clip", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] is False
    assert data["clip"] is None
    # 全片人工智能 3 条（第 2、6、8），模型 1 条（第 3）-> 缺 1、1
    assert data["keywords"] == [
        {"word": "人工智能", "required_count": 4, "hit_count": 3, "missing_count": 1},
        {"word": "模型", "required_count": 2, "hit_count": 1, "missing_count": 1},
    ]


def test_api_topic_clip_rejects_empty_and_duplicate_keyword():
    body = clip_body(keywords=[{"word": "   ", "required_count": 1}])
    resp = client.post("/api/topic-clip", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "topic"
    assert "第 1 组关键词为空" in detail["message"]

    body = clip_body(keywords=[
        {"word": "ABC", "required_count": 1},
        {"word": "abc", "required_count": 1},
    ])
    resp = client.post("/api/topic-clip", json=body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "topic"
    assert "第 2 组" in detail["message"] and "第 1 组" in detail["message"]


def test_api_topic_clip_rejects_non_positive_count():
    for bad in [0, -1, 1.5, True, False, "2", None]:
        body = clip_body(keywords=[{"word": "甲", "required_count": bad}])
        resp = client.post("/api/topic-clip", json=body)
        assert resp.status_code == 422, bad
        assert resp.json()["detail"]["field"] == "required_count"


def test_api_topic_clip_keywords_count_bounds():
    # 0 组与 9 组均被请求体校验拒绝
    resp = client.post("/api/topic-clip", json={"srt": CLIP_SRT, "keywords": []})
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "keywords"

    nine = [{"word": f"词{i}", "required_count": 1} for i in range(9)]
    resp = client.post("/api/topic-clip", json={"srt": CLIP_SRT, "keywords": nine})
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "keywords"


def test_api_topic_clip_rejects_non_monotonic_starts():
    bad = (
        "1\n00:00:05,000 --> 00:00:08,000\n晚开始\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\n早开始\n"
    )
    resp = client.post(
        "/api/topic-clip",
        json={"srt": bad, "keywords": [{"word": "开始", "required_count": 1}]},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "order"
    assert "第 2 条" in detail["message"]


def test_api_topic_clip_rejects_bad_srt():
    resp = client.post(
        "/api/topic-clip",
        json={"srt": "garbage", "keywords": [{"word": "甲", "required_count": 1}]},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


def test_api_topic_clip_requires_fields():
    assert client.post("/api/topic-clip", json={"srt": CLIP_SRT}).status_code == 422
    assert client.post(
        "/api/topic-clip", json={"keywords": [{"word": "甲", "required_count": 1}]}
    ).status_code == 422


# ---------- 双语字幕配对 ----------

def _cue(start, end, index="1", text="x"):
    return Cue(index=index, start=start, end=end, text=text)


def _result_ops(result):
    """把 AlignResult 还原为操作优先级序列（0=1:1 1=1:2 2=2:1 3=跳过左 4=跳过右）。"""
    ops = {}
    for g in result.groups:
        ops[g.step] = {"1:1": 0, "1:2": 1, "2:1": 2}[g.kind]
    for s in result.skipped_left:
        ops[s.step] = 3
    for s in result.skipped_right:
        ops[s.step] = 4
    return tuple(ops[k] for k in sorted(ops))


def _brute_align(left, right):
    """暴力枚举全部操作序列，返回 (最小总代价, 同分最优操作序列)。"""
    best = None

    def rec(i, j, c, ops):
        nonlocal best
        if i == len(left) and j == len(right):
            key = (c, tuple(ops))
            if best is None or key < best:
                best = key
            return
        if i < len(left) and j < len(right):
            rec(i + 1, j + 1,
                c + abs(left[i].start - right[j].start) + abs(left[i].end - right[j].end),
                ops + [0])
        if i < len(left) and j + 1 < len(right):
            rec(i + 1, j + 2,
                c + abs(left[i].start - right[j].start)
                + abs(left[i].end - right[j + 1].end) + MERGE_PENALTY_MS,
                ops + [1])
        if i + 1 < len(left) and j < len(right):
            rec(i + 2, j + 1,
                c + abs(left[i].start - right[j].start)
                + abs(left[i + 1].end - right[j].end) + MERGE_PENALTY_MS,
                ops + [2])
        if i < len(left):
            rec(i + 1, j, c + SKIP_PENALTY_MS, ops + [3])
        if j < len(right):
            rec(i, j + 1, c + SKIP_PENALTY_MS, ops + [4])

    rec(0, 0, 0, [])
    return best


def test_align_perfect_one_to_one():
    left = [_cue(1000, 2000), _cue(3000, 4000)]
    right = [_cue(1000, 2000), _cue(3100, 4000)]
    result = align_bilingual(left, right)
    assert [g.kind for g in result.groups] == ["1:1", "1:1"]
    assert [g.cost for g in result.groups] == [0, 100]
    assert result.total_cost == 100
    assert result.skipped_left == () and result.skipped_right == ()
    assert [g.step for g in result.groups] == [1, 2]


def test_align_one_to_two_merge_when_cheaper():
    left = [_cue(1000, 4000)]
    right = [_cue(1000, 2000), _cue(2000, 4000)]
    result = align_bilingual(left, right)
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.kind == "1:2"
    assert (group.left_from, group.left_to) == (0, 0)
    assert (group.right_from, group.right_to) == (0, 1)
    # 跨度起止差 0 + 0，合并多一条 +500
    assert (group.start_diff_ms, group.end_diff_ms) == (0, 0)
    assert group.merge_penalty_ms == MERGE_PENALTY_MS
    assert group.cost == 500
    assert result.total_cost == 500


def test_align_two_to_one_merge_when_cheaper():
    left = [_cue(1000, 2000), _cue(2000, 4000)]
    right = [_cue(1000, 4000)]
    result = align_bilingual(left, right)
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.kind == "2:1"
    assert (group.left_from, group.left_to) == (0, 1)
    assert (group.right_from, group.right_to) == (0, 0)
    assert group.cost == 500
    assert result.total_cost == 500


def test_align_skip_unmatched_cue():
    left = [_cue(1000, 2000), _cue(10000, 11000)]
    right = [_cue(1000, 2000)]
    result = align_bilingual(left, right)
    assert len(result.groups) == 1
    assert result.groups[0].cost == 0
    assert [s.cue_index for s in result.skipped_left] == [1]
    assert result.skipped_left[0].skip_cost_ms == SKIP_PENALTY_MS
    assert result.skipped_right == ()
    assert result.skip_cost == 3000
    assert result.total_cost == 3000


def test_align_tie_prefers_pair_over_skips():
    # 1:1 代价 2500+3500=6000 与跳过两条 3000+3000=6000 相同，优先 1:1
    left = [_cue(0, 1000)]
    right = [_cue(2500, 4500)]
    result = align_bilingual(left, right)
    assert [g.kind for g in result.groups] == ["1:1"]
    assert result.total_cost == 6000
    assert result.skipped_left == () and result.skipped_right == ()


def test_align_tie_prefers_11_over_12():
    # 1:2（4000）与 1:1 + 跳过右（1000+3000=4000）同分，操作序列 (1:1, 跳过右) 优先
    left = [_cue(0, 1000)]
    right = [_cue(0, 2000), _cue(2000, 4500)]
    result = align_bilingual(left, right)
    assert [g.kind for g in result.groups] == ["1:1"]
    assert result.groups[0].right_from == 0
    assert [s.cue_index for s in result.skipped_right] == [1]
    assert result.total_cost == 4000


def test_align_tie_prefers_11_over_21():
    # 2:1（4000）与 1:1 + 跳过左（1000+3000=4000）同分，操作序列 (1:1, 跳过左) 优先
    left = [_cue(0, 2000), _cue(2000, 4500)]
    right = [_cue(0, 1000)]
    result = align_bilingual(left, right)
    assert [g.kind for g in result.groups] == ["1:1"]
    assert result.groups[0].left_from == 0
    assert [s.cue_index for s in result.skipped_left] == [1]
    assert result.total_cost == 4000


def test_align_tie_prefers_skip_left_before_skip_right():
    # 两侧时间相差太远，配对不如都跳过；同分跳过左排在跳过右之前
    left = [_cue(0, 1000)]
    right = [_cue(5000, 6000)]
    result = align_bilingual(left, right)
    assert result.groups == ()
    assert result.skipped_left[0].step == 1
    assert result.skipped_right[0].step == 2
    assert result.total_cost == 6000


def test_align_empty_side_skips_all():
    result = align_bilingual([], [_cue(0, 1000)])
    assert result.groups == ()
    assert len(result.skipped_right) == 1
    assert result.total_cost == SKIP_PENALTY_MS
    result = align_bilingual([_cue(0, 1000)], [])
    assert len(result.skipped_left) == 1
    assert result.total_cost == SKIP_PENALTY_MS


def _rand_cue(rng, index):
    start = rng.randint(0, 6000)
    return _cue(start, start + rng.randint(1, 4000), index=str(index))


def test_align_matches_bruteforce_on_random_small():
    rng = random.Random(20260925)
    for _ in range(300):
        left = [_rand_cue(rng, k + 1) for k in range(rng.randint(1, 4))]
        right = [_rand_cue(rng, k + 1) for k in range(rng.randint(1, 4))]

        result = align_bilingual(left, right)
        best_cost, best_ops = _brute_align(left, right)
        assert result.total_cost == best_cost
        assert _result_ops(result) == best_ops

        # 代价拆分自洽
        assert result.pair_cost + result.skip_cost == result.total_cost
        assert sum(g.cost for g in result.groups) == result.pair_cost
        skipped = len(result.skipped_left) + len(result.skipped_right)
        assert skipped * SKIP_PENALTY_MS == result.skip_cost

        # 组代价 = 起止差 + 合并代价，跨度取组内首条开始 → 末条结束
        for g in result.groups:
            assert g.cost == g.start_diff_ms + g.end_diff_ms + g.merge_penalty_ms
            assert g.start_diff_ms == abs(left[g.left_from].start - right[g.right_from].start)
            assert g.end_diff_ms == abs(left[g.left_to].end - right[g.right_to].end)
            extra = (g.left_to - g.left_from) + (g.right_to - g.right_from)
            assert g.merge_penalty_ms == extra * MERGE_PENALTY_MS

        # 每条字幕恰好出现一次（配对组内或被跳过），步号 1..N 连续
        left_seen = [False] * len(left)
        right_seen = [False] * len(right)
        for g in result.groups:
            for p in range(g.left_from, g.left_to + 1):
                assert not left_seen[p]
                left_seen[p] = True
            for p in range(g.right_from, g.right_to + 1):
                assert not right_seen[p]
                right_seen[p] = True
        for s in result.skipped_left:
            assert not left_seen[s.cue_index]
            left_seen[s.cue_index] = True
        for s in result.skipped_right:
            assert not right_seen[s.cue_index]
            right_seen[s.cue_index] = True
        assert all(left_seen) and all(right_seen)
        total_steps = len(result.groups) + skipped
        steps = sorted(
            [g.step for g in result.groups]
            + [s.step for s in result.skipped_left]
            + [s.step for s in result.skipped_right]
        )
        assert steps == list(range(1, total_steps + 1))


ALIGN_LEFT = (
    "1\n00:00:01,000 --> 00:00:04,000\n这是一个\n比较长的句子\n\n"
    "2\n00:00:05,000 --> 00:00:06,500\n第二句\n\n"
    "3\n00:00:20,000 --> 00:00:21,000\n右侧没有的句子\n"
)
ALIGN_RIGHT = (
    "1\n00:00:01,000 --> 00:00:02,000\nThis is\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\na longer sentence\n\n"
    "3\n00:00:05,100 --> 00:00:06,500\nSecond sentence\n"
)


def align_body(**overrides):
    body = {"left_srt": ALIGN_LEFT, "right_srt": ALIGN_RIGHT}
    body.update(overrides)
    return body


def test_bilingual_align_page_served():
    resp = client.get("/bilingual-align")
    assert resp.status_code == 200
    assert "双语字幕配对" in resp.text
    assert "/api/bilingual-align" in resp.text


def test_api_bilingual_align_success():
    resp = client.post("/api/bilingual-align", json=align_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["left_count"] == 3
    assert data["right_count"] == 3
    assert data["pair_count"] == 2
    assert data["unmatched_left_count"] == 1
    assert data["unmatched_right_count"] == 0
    assert data["merge_penalty_ms"] == 500
    assert data["skip_penalty_ms"] == 3000

    # 第一组：左 1 条对右 2 条（1:2），跨度完全对齐，仅合并代价 500
    first = data["pairs"][0]
    assert first["step"] == 1
    assert first["kind"] == "1:2"
    assert [c["index"] for c in first["left"]] == ["1"]
    assert [c["cue_order"] for c in first["right"]] == [1, 2]
    assert first["left"][0]["text"] == "这是一个\n比较长的句子"
    assert (first["left_start"], first["left_end"]) == ("00:00:01,000", "00:00:04,000")
    assert (first["right_start"], first["right_end"]) == ("00:00:01,000", "00:00:04,000")
    assert first["start_diff_ms"] == 0
    assert first["end_diff_ms"] == 0
    assert first["merge_penalty_ms"] == 500
    assert first["cost"] == 500

    # 第二组：1:1，起始差 100
    second = data["pairs"][1]
    assert second["step"] == 2
    assert second["kind"] == "1:1"
    assert second["start_diff_ms"] == 100
    assert second["end_diff_ms"] == 0
    assert second["merge_penalty_ms"] == 0
    assert second["cost"] == 100

    # 左侧第 3 条未匹配（跳过）
    skipped = data["unmatched_left"][0]
    assert skipped["step"] == 3
    assert skipped["cue_order"] == 3
    assert skipped["index"] == "3"
    assert skipped["text"] == "右侧没有的句子"
    assert skipped["skip_cost_ms"] == 3000
    assert data["unmatched_right"] == []

    assert data["pair_cost"] == 600
    assert data["skip_cost"] == 3000
    assert data["total_cost"] == 3600


def test_api_bilingual_align_rejects_bad_left_srt():
    resp = client.post("/api/bilingual-align", json=align_body(left_srt="garbage"))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "srt"
    assert "左侧" in detail["message"]


def test_api_bilingual_align_rejects_bad_right_srt():
    bad = "1\n00:00:05,000 --> 00:00:05,000\n等长\n"
    resp = client.post("/api/bilingual-align", json=align_body(right_srt=bad))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "srt"
    assert "右侧" in detail["message"]


def test_api_bilingual_align_requires_fields():
    assert client.post("/api/bilingual-align", json={}).status_code == 422
    assert client.post(
        "/api/bilingual-align", json={"left_srt": ALIGN_LEFT}
    ).status_code == 422
    assert client.post(
        "/api/bilingual-align", json={"right_srt": ALIGN_RIGHT}
    ).status_code == 422


# ---------- 重叠字幕择优保留 ----------

SELECTION_SRT = (
    "1\n00:00:00,000 --> 00:00:04,000\n第一句\n\n"
    "2\n00:00:03,500 --> 00:00:07,500\n第二句与第一句重叠\n\n"
    "3\n00:00:07,600 --> 00:00:11,000\n第三句\n\n"
    "4\n00:00:10,000 --> 00:00:13,000\n第四句与第三句重叠\n\n"
    "5\n00:00:13,200 --> 00:00:17,000\n第五句\n"
)


def _selection_cues():
    return parse_srt(SELECTION_SRT)


def test_select_default_priority_maximizes_count_then_lex():
    cues = _selection_cues()
    # 间隔 0：相容链 1->3（4000<=7600）、3 与 4 重叠（11000>10000 不相容），
    # 4->5（13000<=13200）；可行：1,3,5 与 1,4,5 均 3 条 -> 取字典序小的 1,3,5
    plan = select_cues(cues, {}, [], 0)
    assert isinstance(plan, SelectionPlan)
    assert plan.kept_orders == (1, 3, 5)
    assert plan.total_priority == 3


def test_select_min_gap_enforced_inclusive_boundary():
    cues = _selection_cues()
    # 1 结束 4000、3 开始 7600：间隔 3600 恰好允许相邻（间隔 0 时 1,3,5 可行）
    assert select_cues(cues, {}, [], 0).kept_orders == (1, 3, 5)
    # 间隔 3600 时 3->5 不再相容（11000+3600>13200）：2 条最优方案同分，
    # 取字典序最小的 [1,3]
    plan = select_cues(cues, {}, [], 3600)
    assert plan.kept_orders == (1, 3)
    # 间隔 3601 时连 1->3 都不相容：退化为 2 条方案，字典序最小为 [1,4]
    plan2 = select_cues(cues, {}, [], 3601)
    assert plan2.kept_orders == (1, 4)
    assert plan2.total_priority == 2


def test_select_higher_priority_can_beat_more_cues():
    cues = _selection_cues()
    # 间隔 0、第 4 条优先级 10：1,4（不能带 5，因为 13000>13200? 13000<=13200 可带 5）
    # 实际 1,4,5 = 1+10+1=12 仍 3 条且优先级更高；改测第 2 条优先级 10：
    # 2 与 3 不相容（7500>7600? 7500<=7600 相容！），2,3,5=10+1+1=12 胜过 1,3,5=3
    plan = select_cues(cues, {2: 10}, [], 0)
    assert plan.kept_orders == (2, 3, 5)
    assert plan.total_priority == 12

    # 但让第 2 条与第 3 条真正互斥（间隔 500：7500+500>7600）：
    # 带 2 的可行最优：2,4（8000<=10000；但 4 与 5 在 500ms 间隔下不相容）
    # 与 2,5 同为 11 分、2 条，字典序取 [2,4]；不带 2 的 1,3,5 = 3 分
    plan2 = select_cues(cues, {2: 10}, [], 500)
    assert plan2.kept_orders == (2, 4)
    assert plan2.total_priority == 11


def test_select_required_cues_split_segments():
    cues = _selection_cues()
    # 必须保留第 4 条（与 3 重叠）：前段在 4 之前且须与第 4 条开始 10000 相容，
    # 第 1 条结束 4000 可留、第 3 条结束 11000 不可；后段第 5 条 13000<=13200 可留
    plan = select_cues(cues, {}, [4], 0)
    assert plan.kept_orders == (1, 4, 5)
    # 必须保留第一条：与默认解一致（第一条本就在字典序最优解里）
    assert select_cues(cues, {}, [1], 0).kept_orders == (1, 3, 5)


def test_select_required_conflict_returns_pair():
    cues = _selection_cues()
    # 第 3、4 条时间重叠（11000 > 10000），间隔 0 也不相容
    result = select_cues(cues, {}, [3, 4], 0)
    assert isinstance(result, tuple)
    assert result == (ConflictPair(first=3, second=4),)
    # 提高间隔要求后，原本相容的 1、3 也会冲突：4000+3601>7600
    result2 = select_cues(cues, {}, [1, 3], 3601)
    assert isinstance(result2, tuple)
    assert result2 == (ConflictPair(first=1, second=3),)


def test_select_required_duplicates_and_out_of_range():
    cues = _selection_cues()
    # 重复必保序号在算法层去重后合法（接口层会以 422 拒绝重复）
    plan = select_cues(cues, {}, [3, 3], 0)
    assert isinstance(plan, SelectionPlan) and 3 in plan.kept_orders
    with pytest.raises(SelectionError, match="超出范围"):
        select_cues(cues, {}, [6], 0)
    with pytest.raises(SelectionError, match="超出范围"):
        select_cues(cues, {6: 1}, [], 0)
    with pytest.raises(SelectionError, match="正整数"):
        select_cues(cues, {1: 0}, [], 0)
    with pytest.raises(SelectionError, match="非负"):
        select_cues(cues, {}, [], -1)


def test_select_single_cue_and_all_overlapping_lex():
    one = parse_srt("9\n00:00:01,000 --> 00:00:02,000\n仅一条\n")
    assert select_cues(one, {1: 3}, [1], 100).kept_orders == (1,)
    assert select_cues(one, {1: 3}, [], 100).total_priority == 3

    # 三条两两重叠、默认优先级：只保留 1 条，字典序最小 -> 第 1 条
    overlap = parse_srt(
        "1\n00:00:00,000 --> 00:00:10,000\n甲\n\n"
        "2\n00:00:01,000 --> 00:00:11,000\n乙\n\n"
        "3\n00:00:02,000 --> 00:00:12,000\n丙\n"
    )
    assert select_cues(overlap, {}, [], 0).kept_orders == (1,)
    # 第 3 条高优先级则保留第 3 条
    assert select_cues(overlap, {3: 5}, [], 0).kept_orders == (3,)


def test_selection_page_served():
    resp = client.get("/selection")
    assert resp.status_code == 200
    assert "重叠字幕择优保留" in resp.text
    assert "/api/selection" in resp.text


def selection_body(**overrides):
    body = {
        "srt": SELECTION_SRT,
        "priorities": [],
        "required_orders": [],
        "min_gap_ms": 0,
    }
    body.update(overrides)
    return body


def test_api_selection_success_default():
    resp = client.post("/api/selection", json=selection_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["compatible"] is True
    assert data["cue_count"] == 5
    assert data["kept_count"] == 3 and data["dropped_count"] == 2
    assert data["total_priority"] == 3
    assert data["conflicts"] == []
    kept = [c for c in data["cues"] if c["kept"]]
    assert [c["cue_order"] for c in kept] == [1, 3, 5]
    assert [c["priority"] for c in data["cues"]] == [1, 1, 1, 1, 1]
    # 舍弃条目标记
    assert [(c["cue_order"], c["kept"]) for c in data["cues"]] == [
        (1, True), (2, False), (3, True), (4, False), (5, True)
    ]
    # 下载用 SRT：原序号、原时间、原正文，舍弃条目整块删除
    srt = data["selected_srt"]
    assert srt == (
        "1\n00:00:00,000 --> 00:00:04,000\n第一句\n\n"
        "3\n00:00:07,600 --> 00:00:11,000\n第三句\n\n"
        "5\n00:00:13,200 --> 00:00:17,000\n第五句\n"
    )
    # 起止时间与逐条视图一致
    assert kept[0]["start"] == "00:00:00,000" and kept[0]["end"] == "00:00:04,000"
    assert kept[0]["index"] == "1"


def test_api_selection_priorities_and_required():
    body = selection_body(priorities=[{"cue_order": 2, "priority": 10}], min_gap_ms=500)
    resp = client.post("/api/selection", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [c["cue_order"] for c in data["cues"] if c["kept"]] == [2, 4]
    assert data["total_priority"] == 11
    assert data["cues"][1]["priority"] == 10

    # 必须保留第 4 条：3 被舍弃，1、4、5 保留；required 标记回显
    resp2 = client.post(
        "/api/selection", json=selection_body(required_orders=[4])
    )
    data2 = resp2.json()
    assert [c["cue_order"] for c in data2["cues"] if c["kept"]] == [1, 4, 5]
    assert [c["cue_order"] for c in data2["cues"] if c["required"]] == [4]
    assert "4\n00:00:10,000 --> 00:00:13,000\n第四句与第三句重叠" in data2["selected_srt"]


def test_api_selection_required_conflict_is_200():
    resp = client.post(
        "/api/selection", json=selection_body(required_orders=[3, 4])
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["compatible"] is False
    assert data["conflicts"] == [{"first": 3, "second": 4}]
    assert data["selected_srt"] == ""
    assert data["cues"] == []
    assert data["kept_count"] == 0 and data["dropped_count"] == 0


def test_api_selection_rejects_bad_srt():
    resp = client.post("/api/selection", json=selection_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


def test_api_selection_rejects_duplicate_and_out_of_range():
    # 优先级序号越界
    resp = client.post(
        "/api/selection", json=selection_body(priorities=[{"cue_order": 9, "priority": 1}])
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "selection"
    assert "超出范围" in resp.json()["detail"]["message"]

    # 同一序号重复设置优先级
    resp = client.post(
        "/api/selection",
        json=selection_body(priorities=[
            {"cue_order": 2, "priority": 1}, {"cue_order": 2, "priority": 3}
        ]),
    )
    assert resp.status_code == 422
    assert "重复设置" in resp.json()["detail"]["message"]

    # 必保序号越界
    resp = client.post(
        "/api/selection", json=selection_body(required_orders=[0])
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/selection", json=selection_body(required_orders=[6])
    )
    assert resp.status_code == 422
    assert "超出范围" in resp.json()["detail"]["message"]

    # 必保序号重复
    resp = client.post(
        "/api/selection", json=selection_body(required_orders=[3, 3])
    )
    assert resp.status_code == 422
    assert "重复" in resp.json()["detail"]["message"]


@pytest.mark.parametrize("bad_priority", [0, -1, 1.5, True, False, "2", None])
def test_api_selection_rejects_bad_priority_value(bad_priority):
    resp = client.post(
        "/api/selection",
        json=selection_body(priorities=[{"cue_order": 1, "priority": bad_priority}]),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "priority"


@pytest.mark.parametrize("bad_order", [0, -1, 1.5, True, False, "1", None])
def test_api_selection_rejects_bad_priority_order(bad_order):
    resp = client.post(
        "/api/selection",
        json=selection_body(priorities=[{"cue_order": bad_order, "priority": 1}]),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "cue_order"


@pytest.mark.parametrize("bad_gap", [-1, 1.5, True, False, "0", None])
def test_api_selection_rejects_bad_gap(bad_gap):
    resp = client.post("/api/selection", json=selection_body(min_gap_ms=bad_gap))
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "min_gap_ms"


def test_api_selection_requires_srt_and_gap():
    assert client.post(
        "/api/selection", json={"priorities": [], "required_orders": []}
    ).status_code == 422
    assert client.post(
        "/api/selection", json={"srt": SELECTION_SRT}
    ).status_code == 422


# ---------- 删段后字幕接轴 ----------

CUTREJOIN_SRT = (
    "1\n00:00:00,000 --> 00:00:04,000\n大家好，欢迎收看本期节目\n\n"
    "2\n00:00:04,000 --> 00:00:08,000\n今天聊一个轻松的话题\n\n"
    "3\n00:00:08,000 --> 00:00:12,000\n这一段完全落在删除区间里\n\n"
    "4\n00:00:12,000 --> 00:00:17,000\n这一句的头部被裁掉\n\n"
    "5\n00:00:17,000 --> 00:00:26,000\n这一句横跨删除区间被拆成两段\n\n"
    "6\n00:00:26,000 --> 00:00:30,000\n感谢收看，我们下期再见\n"
)

CUTREJOIN_CUTS = [
    {"start": "00:00:08,000", "end": "00:00:13,000"},
    {"start": "00:00:13,000", "end": "00:00:15,000"},
    {"start": "00:00:20,000", "end": "00:00:24,000"},
]


def _cutrejoin_cuts():
    return parse_cuts([(c["start"], c["end"]) for c in CUTREJOIN_CUTS])


def test_parse_cuts_merges_overlapping_and_touching():
    # 乱序输入：相接的 [8000,13000)+[13000,15000) 合并，重叠的 [20000,24000)+[22000,26000) 合并
    cuts = parse_cuts([
        ("00:00:20,000", "00:00:24,000"),
        ("00:00:08,000", "00:00:13,000"),
        ("00:00:13,000", "00:00:15,000"),
        ("00:00:22,000", "00:00:26,000"),
    ])
    assert cuts == [Cut(8000, 15000), Cut(20000, 26000)]
    # 恰好相接（前段结束 == 后段开始）也合并；包含关系被吞并
    assert merge_cuts([Cut(0, 1000), Cut(1000, 2000), Cut(500, 1500)]) == [Cut(0, 2000)]
    # 不相接的区间保持分开并按开始升序
    assert merge_cuts([Cut(3000, 4000), Cut(0, 1000)]) == [Cut(0, 1000), Cut(3000, 4000)]


def test_parse_cuts_rejects_invalid():
    with pytest.raises(CutRejoinError, match="第 1 个删除区间的开始时间无效"):
        parse_cuts([("not-a-time", "00:00:01,000")])
    with pytest.raises(CutRejoinError, match="第 2 个删除区间的结束时间无效"):
        parse_cuts([("00:00:01,000", "00:00:02,000"), ("00:00:03,000", "bad")])
    # 结束等于或早于开始都拒绝
    with pytest.raises(CutRejoinError, match="严格晚于"):
        parse_cuts([("00:00:02,000", "00:00:02,000")])
    with pytest.raises(CutRejoinError, match="严格晚于"):
        parse_cuts([("00:00:03,500", "00:00:02,000")])


def test_removed_before_boundaries():
    cuts = [Cut(8000, 15000), Cut(20000, 24000)]
    assert removed_before(cuts, 0) == 0
    assert removed_before(cuts, 8000) == 0       # 恰为删除开始：该段不计入
    assert removed_before(cuts, 15000) == 7000   # 恰为删除结束：该段完整计入
    assert removed_before(cuts, 20000) == 7000
    assert removed_before(cuts, 24000) == 11000
    assert removed_before(cuts, 99999) == 11000


def test_cut_cue_untouched_and_dropped():
    cuts = [Cut(8000, 15000)]
    assert cut_cue(0, 4000, cuts) == [(0, 4000)]       # 与删除区间不交
    assert cut_cue(9000, 14000, cuts) == []            # 完全落入删除区间
    assert cut_cue(8000, 15000, cuts) == []            # 与删除区间完全重合


def test_cut_cue_touching_boundaries_kept():
    cuts = [Cut(8000, 15000)]
    # 边界恰好相接不算重叠：字幕结束 == 删除开始、字幕开始 == 删除结束
    assert cut_cue(4000, 8000, cuts) == [(4000, 8000)]
    assert cut_cue(15000, 19000, cuts) == [(15000, 19000)]
    # 字幕开始 == 删除开始（或结束 == 删除结束）：端点落入删除区间
    assert cut_cue(8000, 12000, cuts) == []
    assert cut_cue(10000, 15000, cuts) == []
    assert cut_cue(8000, 20000, cuts) == [(15000, 20000)]
    assert cut_cue(0, 15000, cuts) == [(0, 8000)]


def test_cut_cue_trims_and_splits():
    cuts = [Cut(8000, 15000), Cut(20000, 24000)]
    assert cut_cue(5000, 10000, cuts) == [(5000, 8000)]              # 裁尾
    assert cut_cue(12000, 18000, cuts) == [(15000, 18000)]           # 裁头
    assert cut_cue(5000, 18000, cuts) == [(5000, 8000), (15000, 18000)]  # 中间挖洞拆分
    # 跨两个删除区间拆成三段
    assert cut_cue(5000, 26000, cuts) == [(5000, 8000), (15000, 20000), (24000, 26000)]


def test_rejoin_cues_status_segments_and_numbering():
    cues = parse_srt(CUTREJOIN_SRT)
    results = rejoin_cues(cues, _cutrejoin_cuts())
    assert [r.status for r in results] == [KEPT, KEPT, DROPPED, TRIMMED, SPLIT, KEPT]
    assert [r.cue_order for r in results] == [1, 2, 3, 4, 5, 6]
    # 保留片段从 1 连续编号
    assert [s.new_order for r in results for s in r.segments] == [1, 2, 3, 4, 5, 6]
    # 第 4 条裁头：原片段 [15000,17000) 前移 7000，时长不变
    seg = results[3].segments[0]
    assert (seg.original_start, seg.original_end) == (15000, 17000)
    assert (seg.start, seg.end) == (8000, 10000)
    # 第 5 条拆分：两段分别前移 7000 / 11000
    assert [(s.original_start, s.original_end, s.start, s.end) for s in results[4].segments] == [
        (17000, 20000, 10000, 13000),
        (24000, 26000, 13000, 15000),
    ]
    # 完整保留的第 6 条也按累计删除时长前移
    seg6 = results[5].segments[0]
    assert (seg6.original_start, seg6.original_end) == (26000, 30000)
    assert (seg6.start, seg6.end) == (15000, 19000)
    # 丢弃条目没有片段
    assert results[2].segments == ()


def test_rejoin_cues_zero_start_cut():
    # 删除起点为零：片头内容被删除，后续内容前移到 0
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:02,000\n片头被删\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\n后续前移\n"
    )
    results = rejoin_cues(cues, parse_cuts([("00:00:00,000", "00:00:02,000")]))
    assert [r.status for r in results] == [DROPPED, KEPT]
    seg = results[1].segments[0]
    assert (seg.start, seg.end) == (0, 3000)
    assert seg.new_order == 1


def test_cut_rejoin_page_served():
    resp = client.get("/cut-rejoin")
    assert resp.status_code == 200
    assert "删段后字幕接轴" in resp.text
    assert "/api/cut-rejoin" in resp.text


def cutrejoin_body(**overrides):
    body = {"srt": CUTREJOIN_SRT, "cuts": CUTREJOIN_CUTS}
    body.update(overrides)
    return body


def test_api_cut_rejoin_success():
    resp = client.post("/api/cut-rejoin", json=cutrejoin_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 相接区间 [8000,13000)+[13000,15000) 已合并
    assert data["merged_cuts"] == [
        {"start": "00:00:08,000", "end": "00:00:15,000", "duration_ms": 7000},
        {"start": "00:00:20,000", "end": "00:00:24,000", "duration_ms": 4000},
    ]
    assert data["total_removed_ms"] == 11000
    assert data["cue_count"] == 6
    assert data["kept_cue_count"] == 5 and data["dropped_cue_count"] == 1
    assert data["segment_count"] == 6
    assert [c["status"] for c in data["cues"]] == [
        "kept", "kept", "dropped", "trimmed", "split", "kept"
    ]
    # 原序号到新序号的对应表（一条原字幕可对应多个新序号）
    assert [(m["cue_order"], m["index"], m["new_orders"]) for m in data["mapping"]] == [
        (1, "1", [1]), (2, "2", [2]), (3, "3", []),
        (4, "4", [3]), (5, "5", [4, 5]), (6, "6", [6]),
    ]
    # 连续编号的接轴 SRT：保留片段前移、正文保留
    assert data["rejoined_srt"] == (
        "1\n00:00:00,000 --> 00:00:04,000\n大家好，欢迎收看本期节目\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\n今天聊一个轻松的话题\n\n"
        "3\n00:00:08,000 --> 00:00:10,000\n这一句的头部被裁掉\n\n"
        "4\n00:00:10,000 --> 00:00:13,000\n这一句横跨删除区间被拆成两段\n\n"
        "5\n00:00:13,000 --> 00:00:15,000\n这一句横跨删除区间被拆成两段\n\n"
        "6\n00:00:15,000 --> 00:00:19,000\n感谢收看，我们下期再见\n"
    )
    # 片段视图同时给出原区间与接轴后区间
    seg = data["cues"][4]["segments"][1]
    assert seg["new_order"] == 5
    assert seg["original_start"] == "00:00:24,000"
    assert seg["original_end"] == "00:00:26,000"
    assert seg["start"] == "00:00:13,000"
    assert seg["end"] == "00:00:15,000"


def test_api_cut_rejoin_preserves_multiline_text_and_index():
    srt = "7\n00:00:00,000 --> 00:00:10,000\n第一行\n第二行\n"
    body = cutrejoin_body(
        srt=srt, cuts=[{"start": "00:00:04,000", "end": "00:00:06,000"}]
    )
    data = client.post("/api/cut-rejoin", json=body).json()
    assert [c["status"] for c in data["cues"]] == ["split"]
    # 拆分后的片段各自保留完整多行正文；输出重新连续编号
    assert data["rejoined_srt"] == (
        "1\n00:00:00,000 --> 00:00:04,000\n第一行\n第二行\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\n第一行\n第二行\n"
    )
    assert data["mapping"] == [{"cue_order": 1, "index": "7", "new_orders": [1, 2]}]


def test_api_cut_rejoin_all_dropped_returns_empty_srt():
    srt = "1\n00:00:01,000 --> 00:00:02,000\n被删\n"
    body = cutrejoin_body(
        srt=srt, cuts=[{"start": "00:00:00,000", "end": "00:00:03,000"}]
    )
    resp = client.post("/api/cut-rejoin", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["rejoined_srt"] == ""
    assert data["segment_count"] == 0
    assert data["kept_cue_count"] == 0 and data["dropped_cue_count"] == 1
    assert data["mapping"] == [{"cue_order": 1, "index": "1", "new_orders": []}]


def test_api_cut_rejoin_rejects_bad_cuts():
    # 结束不晚于开始
    resp = client.post(
        "/api/cut-rejoin",
        json=cutrejoin_body(cuts=[{"start": "00:00:02,000", "end": "00:00:02,000"}]),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["type"] == "cuts"
    assert "严格晚于" in detail["message"]

    # 时间格式错误，消息指出第几个区间
    resp = client.post(
        "/api/cut-rejoin",
        json=cutrejoin_body(cuts=[
            {"start": "00:00:01,000", "end": "00:00:02,000"},
            {"start": "abc", "end": "00:00:03,000"},
        ]),
    )
    assert resp.status_code == 422
    assert "第 2 个删除区间" in resp.json()["detail"]["message"]


def test_api_cut_rejoin_rejects_bad_srt():
    resp = client.post("/api/cut-rejoin", json=cutrejoin_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"


def test_api_cut_rejoin_requires_fields_and_nonempty_cuts():
    assert client.post("/api/cut-rejoin", json={}).status_code == 422
    assert client.post(
        "/api/cut-rejoin", json={"cuts": CUTREJOIN_CUTS}
    ).status_code == 422
    # 删除区间至少 1 段
    resp = client.post("/api/cut-rejoin", json={"srt": CUTREJOIN_SRT, "cuts": []})
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "cuts"


# ---------- 镜头切点避让 ----------

CUTAVOID_SRT = (
    "1\n00:00:05,000 --> 00:00:07,000\n开场字幕\n\n"
    "2\n00:00:08,000 --> 00:00:10,000\n第二句字幕\n\n"
    "3\n00:00:16,000 --> 00:00:19,000\n第三句字幕\n\n"
    "4\n00:00:22,000 --> 00:00:25,000\n结尾字幕\n"
)
CUTAVOID_POINTS = [7000, 9000, 17000, 25000]


def test_normalize_cuts_dedup_and_sort():
    assert normalize_cuts([3000, 1000, 3000, 0]) == (0, 1000, 3000)
    assert normalize_cuts([]) == ()
    with pytest.raises(CutAvoidError, match="第 2 个镜头切点"):
        normalize_cuts([1, -1])


def test_avoid_cuts_boundary_coincidence_is_free():
    # 切点全部落在原边界上（开始或结束）：M=0 即可，零挪动
    cues = parse_srt(CUTAVOID_SRT)
    plan = avoid_cuts(cues, [5000, 7000, 8000, 10000, 25000], 0, 0)
    assert plan.starts == (5000, 8000, 16000, 22000)
    assert plan.total_shift_ms == 0


def test_avoid_cuts_shifts_within_window():
    cues = parse_srt(CUTAVOID_SRT)
    # 9000 在第 2 条 [8000,10000) 内部：前移到 7000（切点边界）；
    # 17000 在第 3 条 [16000,19000) 内部：后移到 17000（切点边界）；
    # 25000 恰为第 4 条结束边界，不冲突
    plan = avoid_cuts(cues, CUTAVOID_POINTS, 2000, 0)
    assert plan.starts == (5000, 7000, 17000, 22000)
    assert plan.total_shift_ms == 2000


def test_avoid_cuts_lexicographic_tie_break():
    # 原间隔 1000 < 要求的 2000：要么第 1 条前移到 4000、要么第 2 条后移到 9000，
    # 代价同为 1000；同分取新起点序列字典序最小者
    cues = parse_srt(CUTAVOID_SRT)
    plan = avoid_cuts(cues, [], 2000, 2000)
    assert plan.starts == (4000, 8000, 16000, 22000)
    assert plan.total_shift_ms == 1000
    for i in range(3):
        end_i = plan.starts[i] + (cues[i].end - cues[i].start)
        assert plan.starts[i + 1] - end_i >= 2000


def test_avoid_cuts_new_start_floor_zero():
    cues = parse_srt("1\n00:00:00,500 --> 00:00:03,500\n靠近片头\n")
    # 切点 1000 在原区间内部；禁区为 [0, 999]，窗口 [0, 2500] 内最小可行起点为 1000
    plan = avoid_cuts(cues, [1000], 2000, 0)
    assert plan.starts == (1000,)
    assert plan.total_shift_ms == 500


def test_avoid_cuts_infeasible_reports_first_blocked_order():
    cues = parse_srt(CUTAVOID_SRT)
    # M=500 时第 2 条窗口 [7500,8500]（时长 2000）整体被切点 9000 覆盖，
    # 且第 1 条保持原安排时第 2 条无法更早
    with pytest.raises(CutAvoidError) as exc_info:
        avoid_cuts(cues, [9000], 500, 0)
    assert exc_info.value.blocked_order == 2

    # 第一条自身无解时序号为 1（M=0，切点落在内部）
    with pytest.raises(CutAvoidError) as exc_info:
        avoid_cuts(cues, [6000], 0, 0)
    assert exc_info.value.blocked_order == 1

    # 贪心可行性判定与之一致
    assert first_blocked_order(cues, (9000,), 500, 0) == 2
    assert first_blocked_order(cues, (), 2000, 0) is None


def test_avoid_cuts_min_gap_infeasible_reports_first_blocked_order():
    # 两条紧邻 [0,2000) [2000,3000)，要求间隔 1000 且 M=0：第 2 条窗口只有 2000
    cues = parse_srt(
        "1\n00:00:00,000 --> 00:00:02,000\n一\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n二\n"
    )
    with pytest.raises(CutAvoidError) as exc_info:
        avoid_cuts(cues, [], 0, 1000)
    assert exc_info.value.blocked_order == 2


def test_cut_avoid_page_served():
    resp = client.get("/cut-avoid")
    assert resp.status_code == 200
    assert "镜头切点避让" in resp.text
    assert "/api/cut-avoid" in resp.text


def cutavoid_body(**overrides):
    body = {
        "srt": CUTAVOID_SRT,
        "cut_points": CUTAVOID_POINTS,
        "max_move_ms": 2000,
        "min_gap_ms": 0,
    }
    body.update(overrides)
    return body


def test_api_cut_avoid_success_lossless_timeline_only():
    resp = client.post("/api/cut-avoid", json=cutavoid_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["feasible"] is True
    assert data["blocked_order"] is None
    assert data["cut_points"] == [7000, 9000, 17000, 25000]
    assert data["cue_count"] == 4
    assert data["total_shift_ms"] == 2000
    assert [(c["cue_order"], c["shift_ms"]) for c in data["cues"]] == [
        (1, 0), (2, -1000), (3, 1000), (4, 0)
    ]
    # 时长不变
    for c in data["cues"]:
        assert parse_timecode(c["end"]) - parse_timecode(c["start"]) == (
            parse_timecode(c["original_end"]) - parse_timecode(c["original_start"])
        )
    # 只改时间轴：序号、正文、LF 换行全部保留
    assert data["adjusted_srt"] == (
        "1\n00:00:05,000 --> 00:00:07,000\n开场字幕\n\n"
        "2\n00:00:07,000 --> 00:00:09,000\n第二句字幕\n\n"
        "3\n00:00:17,000 --> 00:00:20,000\n第三句字幕\n\n"
        "4\n00:00:22,000 --> 00:00:25,000\n结尾字幕\n"
    )


def test_api_cut_avoid_preserves_crlf_and_no_change_is_identity():
    srt = "1\r\n00:00:01,000 --> 00:00:03,000\r\n原封不动\r\n"
    # 切点恰为结束边界、零挪动量：输出与输入逐字符一致（含 CRLF）
    body = cutavoid_body(srt=srt, cut_points=[3000], max_move_ms=0, min_gap_ms=0)
    data = client.post("/api/cut-avoid", json=body).json()
    assert data["feasible"] is True
    assert data["total_shift_ms"] == 0
    assert data["adjusted_srt"] == srt


def test_api_cut_avoid_empty_cut_points_needs_no_movement():
    resp = client.post(
        "/api/cut-avoid",
        json=cutavoid_body(cut_points=[], max_move_ms=0, min_gap_ms=0),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feasible"] is True
    assert data["cut_points"] == []
    assert data["total_shift_ms"] == 0
    assert [c["shift_ms"] for c in data["cues"]] == [0, 0, 0, 0]
    assert data["adjusted_srt"] == CUTAVOID_SRT


def test_api_cut_avoid_infeasible_returns_blocked_order_http_200():
    resp = client.post(
        "/api/cut-avoid", json=cutavoid_body(cut_points=[9000], max_move_ms=500)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feasible"] is False
    assert data["blocked_order"] == 2
    assert data["adjusted_srt"] == ""
    assert data["cues"] == []
    assert data["total_shift_ms"] == 0


def test_api_cut_avoid_validates_fields():
    # 负切点
    resp = client.post(
        "/api/cut-avoid", json=cutavoid_body(cut_points=[-1])
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "cut_points"

    # 最大挪动量超过 2000
    resp = client.post(
        "/api/cut-avoid", json=cutavoid_body(max_move_ms=2001)
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["field"] == "max_move_ms"
    assert "2000" in detail["message"]

    # 小数最大挪动量
    resp = client.post(
        "/api/cut-avoid", json=cutavoid_body(max_move_ms=1.5)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "max_move_ms"

    # 负最小间隔
    resp = client.post(
        "/api/cut-avoid", json=cutavoid_body(min_gap_ms=-1)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "min_gap_ms"

    # 缺字段
    assert client.post("/api/cut-avoid", json={"srt": CUTAVOID_SRT}).status_code == 422


def test_api_cut_avoid_rejects_bad_srt():
    resp = client.post("/api/cut-avoid", json=cutavoid_body(srt="garbage"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["type"] == "srt"
