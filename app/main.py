"""字幕时间漂移校正、位置规划、阅读负荷分析与章节规划等字幕工具 Web 服务（FastAPI）。"""
from __future__ import annotations

import math
from pathlib import Path

from fractions import Fraction

from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictFloat, StrictInt, field_validator

from .bilingual import (
    MERGE_PENALTY_MS,
    SKIP_PENALTY_MS,
    SkippedCue,
    align_bilingual,
)
from .chapters import ChapterError, plan_chapters
from .cutavoid import CutAvoidError, avoid_cuts
from .cutrejoin import CutRejoinError, parse_cuts, rejoin_cues
from .mapping import AnchorError, PiecewiseMapping, build_anchors
from .placement import (
    Block,
    PlacementError,
    count_switches,
    export_vtt,
    parse_blocks,
    plan_positions,
)
from . import linewrap, terms as terms_lib
from .reading_load import find_reading_zones
from .selection import SelectionError, select_cues
from .srt import Cue, SrtParseError, format_srt, parse_document, parse_srt, rewrite_srt
from .timecode import format_signed_timecode, format_timecode
from .topic_clip import (
    TopicClipError,
    build_keyword_groups,
    check_starts_monotonic,
    find_topic_clip,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="字幕校正、位置规划、专名统一、阅读负荷、智能断行、章节规划、择优保留、主题片段检索、双语配对、删段接轴与镜头切点避让工具",
    description="粘贴 SRT 后可做时间漂移校正、按画面遮挡时段规划字幕上下位置、"
    "统一专名别名、分析重叠字幕叠加后的阅读负荷热区、按列数 / 行数限制智能断行、"
    "按章节数与优选停顿把整段字幕切成时长均衡的连续章节、"
    "按优先级与最小间隔从重叠字幕中择优保留必须保留条目、"
    "按多组关键词检索覆盖所需字幕条数的最短连续字幕片段、"
    "把左右两份字幕按时间跨度做 1:1 / 1:2 / 2:1 单调配对，"
    "按若干删除区间裁切字幕、拆分跨区间条目并按累计删除时长前移接轴，"
    "或在有限挪动范围内整体平移字幕以避开镜头切点并满足相邻最小间隔。",
    version="1.10.0",
)

_FIELD_LABELS = {
    "rate_threshold": "阅读速率阈值（字/秒）",
    "min_duration_ms": "最短持续时长（毫秒）",
    "switch_cost_ms": "相邻字幕上下换位代价（毫秒）",
    "max_columns": "每行最大显示列数",
    "max_lines": "每条字幕最多行数",
    "chapter_count": "章节数",
    "preferred_pause_ms": "优选停顿（毫秒）",
    "min_gap_ms": "最小间隔毫秒数",
    "priorities": "字幕优先级",
    "required_orders": "必须保留的序号",
    "cue_order": "字幕输入序号",
    "priority": "优先级",
    "keywords": "关键词",
    "required_count": "关键词所需字幕条数",
    "left_srt": "左侧 SRT 内容",
    "right_srt": "右侧 SRT 内容",
    "cuts": "删除区间",
    "cut_points": "镜头切点",
    "max_move_ms": "最大挪动量",
}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """请求体字段校验失败统一返回 422 中文说明。

    非有限浮点（NaN / Infinity）不能原样回显（标准 JSON 无法序列化，默认
    处理器会直接 500），这里按字段给出固定中文提示。
    """
    for err in exc.errors():
        loc = err.get("loc") or ()
        # 列表元素校验错误（如 cut_points[2]）定位到列表字段本身
        field = loc[-1] if loc else None
        if len(loc) >= 2 and isinstance(loc[-1], int):
            field = loc[-2]
        label = _FIELD_LABELS.get(str(field), "请求参数")
        value = err.get("input")
        if isinstance(value, float) and not math.isfinite(value):
            message = f"{label}必须是有限的数字。"
        elif err.get("type") in ("float_type", "int_type"):
            message = f"{label}必须是数字。"
        elif err.get("type") == "greater_than":
            message = f"{label}必须严格大于 0。"
        elif err.get("type") == "greater_than_equal":
            if field in (
                "max_columns",
                "max_lines",
                "chapter_count",
                "required_count",
                "cue_order",
                "priority",
            ):
                message = f"{label}必须为正整数（不小于 1）。"
            else:
                message = f"{label}不能小于 0。"
        elif err.get("type") == "less_than_equal":
            message = f"{label}不能大于 {err.get('ctx', {}).get('le', 2000)} 毫秒。"
        elif err.get("type") == "too_short":
            message = f"{label}至少需要 {err.get('ctx', {}).get('min_length', 1)} 组。"
        elif err.get("type") == "too_long":
            message = f"{label}最多只能填 {err.get('ctx', {}).get('max_length', 8)} 组。"
        else:
            message = f"{label}不合法。"
        return JSONResponse(
            status_code=422,
            content={"detail": {"type": "validation", "field": field, "message": message}},
        )
    return JSONResponse(status_code=422, content={"detail": {"type": "validation"}})


class AnchorPair(BaseModel):
    original: str = Field(..., description="字幕上观察到的原时间，HH:MM:SS,mmm")
    corrected: str = Field(..., description="人工确认的正确时间，HH:MM:SS,mmm")


class CorrectRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    anchors: list[AnchorPair] = Field(..., min_length=2)


class CorrectedCue(BaseModel):
    index: str
    original_start: str
    original_end: str
    corrected_start: str
    corrected_end: str
    text: str


class CorrectResponse(BaseModel):
    corrected_srt: str
    cues: list[CorrectedCue]
    warnings: list[str]


class OcclusionBlockIn(BaseModel):
    start: str = Field(..., description="遮挡开始时间，HH:MM:SS,mmm")
    end: str = Field(..., description="遮挡结束时间，HH:MM:SS,mmm，须严格晚于开始")


class PlacementRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    top_blocks: list[OcclusionBlockIn] = Field(
        default_factory=list, description="画面上方遮挡时段"
    )
    bottom_blocks: list[OcclusionBlockIn] = Field(
        default_factory=list, description="画面下方遮挡时段"
    )
    switch_cost_ms: StrictInt = Field(
        ..., ge=0, description="相邻字幕上下换位代价（非负整数毫秒）"
    )


class BlockView(BaseModel):
    start: str
    end: str


class PlacedCueView(BaseModel):
    index: str
    start: str
    end: str
    text: str
    position: str
    top_occluded_ms: int
    bottom_occluded_ms: int
    occluded_ms: int
    still_occluded: bool


class PlacementResponse(BaseModel):
    vtt: str
    cues: list[PlacedCueView]
    still_occluded_cues: list[PlacedCueView]
    merged_top_blocks: list[BlockView]
    merged_bottom_blocks: list[BlockView]
    total_occluded_ms: int
    switch_count: int


class ReadingLoadRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    rate_threshold: StrictFloat = Field(
        ..., gt=0, description="阅读速率阈值（字/秒），必须为正数"
    )
    min_duration_ms: StrictInt = Field(
        ..., ge=0, description="热区最短连续时长（非负整数毫秒）"
    )

    @field_validator("rate_threshold")
    @classmethod
    def _threshold_must_be_finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("阅读速率阈值必须是有限的正数，不能为 NaN 或 Infinity。")
        return value


class CueLoadView(BaseModel):
    order: int
    index: str
    start: str
    end: str
    char_count: int
    duration_ms: int
    rate: float
    text: str


class ReadingZoneView(BaseModel):
    start: str
    end: str
    duration_ms: int
    peak_rate: float
    cue_orders: list[int]


class ReadingLoadResponse(BaseModel):
    rate_threshold: float
    min_duration_ms: int
    cue_count: int
    zone_count: int
    cues: list[CueLoadView]
    zones: list[ReadingZoneView]


class TermRuleIn(BaseModel):
    standard: str = Field(..., description="标准写法（替换目标）")
    aliases: list[str] = Field(..., description="该标准写法的别名列表，逐个替换")


class TermsRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    rules: list[TermRuleIn] = Field(
        default_factory=list, description="专名规则，按输入顺序参与等长同分裁决"
    )


class TermHitView(BaseModel):
    cue_order: int
    line_no: int
    column: int
    source: str
    standard: str


class TermsResponse(BaseModel):
    revised_srt: str
    hits: list[TermHitView]
    hit_count: int


class WrapRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    max_columns: StrictInt = Field(
        ..., ge=1, description="每行最大显示列数（正整数）"
    )
    max_lines: StrictInt = Field(
        ..., ge=1, description="每条字幕最多行数（正整数）"
    )


class WrappedLineView(BaseModel):
    text: str
    width: int


class WrapCueView(BaseModel):
    cue_order: int
    index: str
    success: bool
    changed: bool
    original_text: str
    lines: list[WrappedLineView]
    reason: str | None = None


class WrapResponse(BaseModel):
    wrapped_srt: str
    cue_count: int
    wrapped_count: int
    failed_count: int
    changed_count: int
    cues: list[WrapCueView]
    failures: list[WrapCueView]


class ChaptersRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    chapter_count: StrictInt = Field(
        ..., ge=1, description="章节数 K（正整数）"
    )
    preferred_pause_ms: StrictInt = Field(
        ..., ge=0, description="优选停顿毫秒数 P（非负整数）"
    )


class ChapterView(BaseModel):
    order: int
    start: str
    end: str
    duration_ms: int
    deviation_ms: float
    cue_order_from: int
    cue_order_to: int
    cue_count: int


class ChapterSplitView(BaseModel):
    after_cue_order: int
    before_cue_order: int
    gap_ms: int
    pause_shortfall_ms: int
    penalty: int


class ChaptersResponse(BaseModel):
    chapter_count: int
    preferred_pause_ms: int
    cue_count: int
    total_duration_ms: int
    target_duration_ms: float
    duration_cost: float
    pause_cost: int
    total_cost: float
    chapters: list[ChapterView]
    splits: list[ChapterSplitView]


class KeywordGroupIn(BaseModel):
    word: str = Field(..., description="关键词原文，按 Unicode casefold 折叠后比较")
    required_count: StrictInt = Field(
        ..., ge=1, description="该词所需出现的字幕条数（正整数）"
    )


class TopicClipRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    keywords: list[KeywordGroupIn] = Field(
        ..., min_length=1, max_length=8, description="关键词组，按输入顺序回显"
    )


class WordHitView(BaseModel):
    word: str
    required_count: int
    hit_count: int
    missing_count: int


class ClipCueView(BaseModel):
    cue_order: int
    index: str
    start: str
    end: str
    text: str
    hit_words: list[str]


class ClipView(BaseModel):
    start: str
    end: str
    duration_ms: int
    cue_order_from: int
    cue_order_to: int
    cue_count: int
    word_hits: list[WordHitView]
    cues: list[ClipCueView]


class TopicClipResponse(BaseModel):
    found: bool
    cue_count: int
    keywords: list[WordHitView]
    clip: ClipView | None = None


class BilingualAlignRequest(BaseModel):
    left_srt: str = Field(..., description="左侧 SRT 内容（如中文字幕）")
    right_srt: str = Field(..., description="右侧 SRT 内容（如英文字幕）")


class AlignCueView(BaseModel):
    cue_order: int
    index: str
    start: str
    end: str
    text: str


class AlignPairView(BaseModel):
    step: int
    kind: str
    left: list[AlignCueView]
    right: list[AlignCueView]
    left_start: str
    left_end: str
    right_start: str
    right_end: str
    start_diff_ms: int
    end_diff_ms: int
    merge_penalty_ms: int
    cost: int


class UnmatchedCueView(BaseModel):
    step: int
    cue_order: int
    index: str
    start: str
    end: str
    text: str
    skip_cost_ms: int


class BilingualAlignResponse(BaseModel):
    left_count: int
    right_count: int
    pair_count: int
    unmatched_left_count: int
    unmatched_right_count: int
    merge_penalty_ms: int
    skip_penalty_ms: int
    pair_cost: int
    skip_cost: int
    total_cost: int
    pairs: list[AlignPairView]
    unmatched_left: list[UnmatchedCueView]
    unmatched_right: list[UnmatchedCueView]


class PriorityIn(BaseModel):
    cue_order: StrictInt = Field(..., ge=1, description="字幕输入序号（从 1 开始）")
    priority: StrictInt = Field(..., ge=1, description="该条字幕的优先级（正整数）")


class SelectionRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    priorities: list[PriorityIn] = Field(
        default_factory=list, description="按字幕输入序号设置的正整数优先级，未设置为 1"
    )
    required_orders: list[Annotated[StrictInt, Field(ge=1)]] = Field(
        default_factory=list,
        description="必须保留条目的输入序号（从 1 开始）",
    )
    min_gap_ms: StrictInt = Field(
        ..., ge=0, description="相邻保留条目之间的最小间隔（非负整数毫秒）"
    )


class SelectionCueView(BaseModel):
    cue_order: int
    index: str
    start: str
    end: str
    text: str
    priority: int
    kept: bool
    required: bool


class SelectionConflictView(BaseModel):
    first: int
    second: int


class SelectionResponse(BaseModel):
    compatible: bool
    cue_count: int
    min_gap_ms: int
    kept_count: int
    dropped_count: int
    total_priority: int
    selected_srt: str
    cues: list[SelectionCueView]
    conflicts: list[SelectionConflictView]


class CutIn(BaseModel):
    start: str = Field(..., description="删除区间开始时间，HH:MM:SS,mmm")
    end: str = Field(..., description="删除区间结束时间，HH:MM:SS,mmm，须严格晚于开始")


class CutRejoinRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    cuts: list[CutIn] = Field(
        ..., min_length=1, description="要从视频中删除的时间区间（至少 1 段）"
    )


class CutSegmentView(BaseModel):
    new_order: int
    original_start: str
    original_end: str
    start: str
    end: str


class CutCueView(BaseModel):
    cue_order: int
    index: str
    original_start: str
    original_end: str
    status: str
    segments: list[CutSegmentView]
    text: str


class CutMappingView(BaseModel):
    cue_order: int
    index: str
    new_orders: list[int]


class MergedCutView(BaseModel):
    start: str
    end: str
    duration_ms: int


class CutRejoinResponse(BaseModel):
    rejoined_srt: str
    merged_cuts: list[MergedCutView]
    total_removed_ms: int
    cue_count: int
    kept_cue_count: int
    dropped_cue_count: int
    segment_count: int
    cues: list[CutCueView]
    mapping: list[CutMappingView]


class CutAvoidRequest(BaseModel):
    srt: str = Field(..., description="原始 SRT 内容")
    cut_points: list[Annotated[StrictInt, Field(ge=0)]] = Field(
        default_factory=list, description="镜头切点（非负整数毫秒），自动去重并按时间升序处理"
    )
    max_move_ms: StrictInt = Field(
        ..., ge=0, le=2000, description="每条字幕起点前后最大挪动量（0～2000 的整数毫秒）"
    )
    min_gap_ms: StrictInt = Field(
        ..., ge=0, description="相邻字幕之间的最小间隔（非负整数毫秒）"
    )


class CutAvoidCueView(BaseModel):
    cue_order: int
    index: str
    original_start: str
    original_end: str
    start: str
    end: str
    shift_ms: int
    text: str


class CutAvoidResponse(BaseModel):
    feasible: bool
    blocked_order: int | None
    cut_points: list[int]
    max_move_ms: int
    min_gap_ms: int
    cue_count: int
    total_shift_ms: int
    adjusted_srt: str
    cues: list[CutAvoidCueView]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/placement")
def placement_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "placement.html")


@app.get("/reading-load")
def reading_load_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "reading_load.html")


@app.get("/terms")
def terms_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/wrap")
def wrap_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "wrap.html")


@app.get("/chapters")
def chapters_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "chapters.html")


@app.get("/topic-clip")
def topic_clip_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "topic_clip.html")


@app.get("/bilingual-align")
def bilingual_align_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "bilingual_align.html")


@app.get("/selection")
def selection_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "selection.html")


@app.get("/cut-rejoin")
def cut_rejoin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "cut_rejoin.html")


@app.get("/cut-avoid")
def cut_avoid_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "cut_avoid.html")


@app.post("/api/correct", response_model=CorrectResponse)
def correct(req: CorrectRequest) -> CorrectResponse | JSONResponse:
    # 1. 解析 SRT（保留原始序号与文本；无法解析仍返回 422）
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 2. 校验锚点：数量、时间格式、两侧时间严格递增
    pairs = [(pair.original, pair.corrected) for pair in req.anchors]
    try:
        anchors = build_anchors(pairs)
    except AnchorError as exc:
        raise HTTPException(
            status_code=422, detail={"type": "anchors", "message": str(exc)}
        )

    mapping = PiecewiseMapping(anchors)
    warnings: list[str] = []
    new_ranges: list[tuple[int, int]] = []

    # 3. 逐条字幕做分段线性映射
    for cue in cues:
        new_start_raw = mapping.map_ms(cue.start)
        new_end_raw = mapping.map_ms(cue.end)
        new_start = max(new_start_raw, 0)
        new_end = max(new_end_raw, 0)

        if new_start_raw < 0 or new_end_raw < 0:
            warnings.append(
                f"序号 {cue.index}：校正后早于 00:00:00,000，"
                f"时间已截断到 0（计算值 {format_signed_timecode(new_start_raw)}"
                f" → {format_signed_timecode(new_end_raw)}），"
                "建议在更早位置补充锚点。"
            )
        if new_end <= new_start:
            warnings.append(
                f"序号 {cue.index}：校正后字幕时长不为正"
                f"（{format_timecode(new_start)} → {format_timecode(new_end)}），"
                "该结果已保留但可能异常，请检查锚点。"
            )

        new_ranges.append((new_start, new_end))

    # 4. 无损重写：只替换各时间轴行的起止时间码；序号、正文、箭头空格、
    #    X1:/X2: 等定位参数、空行、原有换行及开头 BOM 全部保持原样
    try:
        result_srt = rewrite_srt(req.srt, new_ranges)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    cue_views = [
        CorrectedCue(
            index=cue.index,
            original_start=format_timecode(cue.start),
            original_end=format_timecode(cue.end),
            corrected_start=format_timecode(new_start),
            corrected_end=format_timecode(new_end),
            text=cue.text,
        )
        for cue, (new_start, new_end) in zip(cues, new_ranges)
    ]

    return CorrectResponse(corrected_srt=result_srt, cues=cue_views, warnings=warnings)


@app.post("/api/placement", response_model=PlacementResponse)
def placement(req: PlacementRequest) -> PlacementResponse | JSONResponse:
    # 1. 复用现有 SRT 解析器
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 2. 校验遮挡时段并各自求并集（换位代价由 StrictInt(ge=0) 保证为非负整数）
    try:
        top_blocks: list[Block] = parse_blocks(
            [(b.start, b.end) for b in req.top_blocks], "画面上方"
        )
        bottom_blocks: list[Block] = parse_blocks(
            [(b.start, b.end) for b in req.bottom_blocks], "画面下方"
        )
    except PlacementError as exc:
        raise HTTPException(
            status_code=422, detail={"type": "blocks", "message": str(exc)}
        )

    # 3. 动态规划选择每条字幕的位置（同分优先下方）
    placements = plan_positions(
        cues, top_blocks, bottom_blocks, req.switch_cost_ms
    )

    cue_views = [
        PlacedCueView(
            index=cue.index,
            start=format_timecode(cue.start),
            end=format_timecode(cue.end),
            text=cue.text,
            position=p.position,
            top_occluded_ms=p.top_occluded_ms,
            bottom_occluded_ms=p.bottom_occluded_ms,
            occluded_ms=p.occluded_ms,
            still_occluded=p.still_occluded,
        )
        for cue, p in zip(cues, placements)
    ]

    def block_views(blocks: list[Block]) -> list[BlockView]:
        return [
            BlockView(start=format_timecode(b.start), end=format_timecode(b.end))
            for b in blocks
        ]

    return PlacementResponse(
        vtt=export_vtt(cues, placements),
        cues=cue_views,
        still_occluded_cues=[v for v in cue_views if v.still_occluded],
        merged_top_blocks=block_views(top_blocks),
        merged_bottom_blocks=block_views(bottom_blocks),
        total_occluded_ms=sum(v.occluded_ms for v in cue_views),
        switch_count=count_switches(placements),
    )


@app.post("/api/reading-load", response_model=ReadingLoadResponse)
def reading_load(req: ReadingLoadRequest) -> ReadingLoadResponse | JSONResponse:
    # 1. 复用现有 SRT 解析器
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 2. 按起止事件扫描半开时间轴（阈值经十进制短表示转 Fraction 精确比较：
    #    输入 0.3 即数学意义的 0.3，杜绝二进制浮点误差影响严格大于判定）
    loads, zones = find_reading_zones(
        cues, Fraction(str(req.rate_threshold)), req.min_duration_ms
    )

    cue_views = [
        CueLoadView(
            order=load.order,
            index=cue.index,
            start=format_timecode(cue.start),
            end=format_timecode(cue.end),
            char_count=load.char_count,
            duration_ms=load.duration_ms,
            rate=round(float(load.rate), 3),
            text=cue.text,
        )
        for cue, load in zip(cues, loads)
    ]
    zone_views = [
        ReadingZoneView(
            start=format_timecode(zone.start),
            end=format_timecode(zone.end),
            duration_ms=zone.end - zone.start,
            peak_rate=round(float(zone.peak_rate), 3),
            cue_orders=list(zone.cue_orders),
        )
        for zone in zones
    ]

    return ReadingLoadResponse(
        rate_threshold=req.rate_threshold,
        min_duration_ms=req.min_duration_ms,
        cue_count=len(cues),
        zone_count=len(zones),
        cues=cue_views,
        zones=zone_views,
    )


@app.post("/api/terms", response_model=TermsResponse)
def unify_terms(req: TermsRequest) -> TermsResponse | JSONResponse:
    # 1. 先校验专名规则：空别名、同一别名指向不同标准写法均返回 422
    try:
        rules = terms_lib.build_rules([(r.standard, r.aliases) for r in req.rules])
    except terms_lib.TermError as exc:
        raise HTTPException(status_code=422, detail={"type": "terms", "message": str(exc)})

    # 2. 复用现有 SRT 解析，并取每条字幕正文在物理行序列中的区间
    try:
        doc = parse_document(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 3. 仅扫描正文各物理行：从左到右一次匹配原文，同位置最长别名优先，
    #    等长按规则输入顺序；命中的替换文本不再参与匹配。只改写正文命中片段，
    #    序号、时间轴、排版参数、空行、BOM 与换行风格保持原样
    revised, hits = terms_lib.apply_terms(
        req.srt, rules, [(r.start_line, r.end_line) for r in doc.body_ranges]
    )

    return TermsResponse(
        revised_srt=revised,
        hits=[
            TermHitView(
                cue_order=hit.cue_order,
                line_no=hit.line_no,
                column=hit.column,
                source=hit.source,
                standard=hit.standard,
            )
            for hit in hits
        ],
        hit_count=len(hits),
    )


@app.post("/api/wrap", response_model=WrapResponse)
def wrap_lines(req: WrapRequest) -> WrapResponse | JSONResponse:
    # 1. 复用同一套 SRT 解析器：结构不合法仍返回 422（detail.type 为 srt）
    # 2. 逐条做 DP 断行；排不下的字幕保留原正文，只记录输入顺序号与原因。
    #    只替换成功且正文确有变化的字幕正文物理行，序号、时间轴、定位参数、
    #    块间空行、原换行风格（CRLF/LF/CR）与开头 BOM 保持原样
    try:
        wrapped_srt, results = linewrap.wrap_document(
            req.srt, req.max_columns, req.max_lines
        )
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    cue_views = [
        WrapCueView(
            cue_order=r.cue_order,
            index=r.index,
            success=r.success,
            changed=r.changed,
            original_text=r.original_text,
            lines=[WrappedLineView(text=line.text, width=line.width) for line in r.lines],
            reason=r.reason,
        )
        for r in results
    ]

    return WrapResponse(
        wrapped_srt=wrapped_srt,
        cue_count=len(cue_views),
        wrapped_count=sum(1 for v in cue_views if v.success),
        failed_count=sum(1 for v in cue_views if not v.success),
        changed_count=sum(1 for v in cue_views if v.changed),
        cues=cue_views,
        failures=[v for v in cue_views if not v.success],
    )


@app.post("/api/chapters", response_model=ChaptersResponse)
def chapters(req: ChaptersRequest) -> ChaptersResponse | JSONResponse:
    # 1. 复用现有 SRT 解析器
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 2. 动态规划切章（K / P 由 StrictInt 校验保证为正整数 / 非负整数）；
    #    无法切成 K 章时返回 422 与具体原因
    try:
        plan = plan_chapters(cues, req.chapter_count, req.preferred_pause_ms)
    except ChapterError as exc:
        raise HTTPException(
            status_code=422, detail={"type": "chapters", "message": str(exc)}
        )

    # 3. 代价以分数精确还原（DP 内部按总代价的 K² 倍整数比较），展示保留 3 位小数
    k = req.chapter_count
    target = Fraction(plan.total_duration_ms, k)
    duration_cost = Fraction(plan.duration_cost_num, k * k)
    total_cost = duration_cost + plan.pause_cost

    return ChaptersResponse(
        chapter_count=k,
        preferred_pause_ms=req.preferred_pause_ms,
        cue_count=len(cues),
        total_duration_ms=plan.total_duration_ms,
        target_duration_ms=round(float(target), 3),
        duration_cost=round(float(duration_cost), 3),
        pause_cost=plan.pause_cost,
        total_cost=round(float(total_cost), 3),
        chapters=[
            ChapterView(
                order=ch.order,
                start=format_timecode(ch.start),
                end=format_timecode(ch.end),
                duration_ms=ch.end - ch.start,
                deviation_ms=round(float(Fraction(ch.end - ch.start) - target), 3),
                cue_order_from=ch.cue_order_from,
                cue_order_to=ch.cue_order_to,
                cue_count=ch.cue_order_to - ch.cue_order_from + 1,
            )
            for ch in plan.chapters
        ],
        splits=[
            ChapterSplitView(
                after_cue_order=sp.after_cue_order,
                before_cue_order=sp.after_cue_order + 1,
                gap_ms=sp.gap_ms,
                pause_shortfall_ms=sp.pause_shortfall_ms,
                penalty=sp.pause_shortfall_ms * sp.pause_shortfall_ms,
            )
            for sp in plan.splits
        ],
    )


@app.post("/api/topic-clip", response_model=TopicClipResponse)
def topic_clip(req: TopicClipRequest) -> TopicClipResponse | JSONResponse:
    # 1. 先校验关键词：空词（空串 / 纯空白，折叠后为空）、折叠后重复、
    #    所需条数非正整数均返回 422（required_count 另由 StrictInt 兜底）
    try:
        groups = build_keyword_groups([(g.word, g.required_count) for g in req.keywords])
    except TopicClipError as exc:
        raise HTTPException(status_code=422, detail={"type": "topic", "message": str(exc)})

    # 2. 复用同一套 SRT 解析器
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 3. 开始时间必须按输入顺序非递减，否则指出先倒退的那条输入序号
    try:
        check_starts_monotonic(cues)
    except TopicClipError as exc:
        raise HTTPException(status_code=422, detail={"type": "order", "message": str(exc)})

    # 4. 滑动窗口检索覆盖各词所需条数的最短连续字幕窗口
    result = find_topic_clip(cues, groups)

    if not result.found:
        # 整体不足：逐词给出全片命中与缺少条数 max(0, 所需 − 全片命中)
        word_views = [
            WordHitView(
                word=groups[k].word,
                required_count=groups[k].required_count,
                hit_count=result.totals[k],
                missing_count=max(0, groups[k].required_count - result.totals[k]),
            )
            for k in range(len(groups))
        ]
        return TopicClipResponse(
            found=False, cue_count=len(cues), keywords=word_views, clip=None
        )

    clip = result.clip
    assert clip is not None
    word_views = [
        WordHitView(
            word=stat.word,
            required_count=stat.required_count,
            hit_count=stat.hit_count,
            missing_count=max(0, stat.required_count - stat.hit_count),
        )
        for stat in clip.word_hits
    ]
    cue_views = [
        ClipCueView(
            cue_order=c.cue_order,
            index=c.index,
            start=format_timecode(c.start),
            end=format_timecode(c.end),
            text=c.text,
            hit_words=list(c.hit_words),
        )
        for c in clip.cues
    ]
    return TopicClipResponse(
        found=True,
        cue_count=len(cues),
        keywords=word_views,
        clip=ClipView(
            start=format_timecode(clip.start),
            end=format_timecode(clip.end),
            duration_ms=clip.end - clip.start,
            cue_order_from=clip.cue_order_from,
            cue_order_to=clip.cue_order_to,
            cue_count=clip.cue_order_to - clip.cue_order_from + 1,
            word_hits=word_views,
            cues=cue_views,
        ),
    )


@app.post("/api/bilingual-align", response_model=BilingualAlignResponse)
def bilingual_align(req: BilingualAlignRequest) -> BilingualAlignResponse | JSONResponse:
    # 1. 复用同一套 SRT 解析器分别解析左右两份字幕；
    #    任一侧解析失败返回 422，并在消息中指明侧别
    try:
        left_cues = parse_srt(req.left_srt)
    except SrtParseError as exc:
        raise HTTPException(
            status_code=422, detail={"type": "srt", "message": f"左侧字幕：{exc}"}
        )
    try:
        right_cues = parse_srt(req.right_srt)
    except SrtParseError as exc:
        raise HTTPException(
            status_code=422, detail={"type": "srt", "message": f"右侧字幕：{exc}"}
        )

    # 2. 单调动态规划：1:1 / 1:2 / 2:1 配对与单侧跳过，
    #    同分按操作序列优先 1:1、1:2、2:1、跳过左、跳过右
    result = align_bilingual(left_cues, right_cues)

    def cue_view(cues: list[Cue], pos: int) -> AlignCueView:
        cue = cues[pos]
        return AlignCueView(
            cue_order=pos + 1,
            index=cue.index,
            start=format_timecode(cue.start),
            end=format_timecode(cue.end),
            text=cue.text,
        )

    pair_views = [
        AlignPairView(
            step=g.step,
            kind=g.kind,
            left=[cue_view(left_cues, p) for p in range(g.left_from, g.left_to + 1)],
            right=[cue_view(right_cues, p) for p in range(g.right_from, g.right_to + 1)],
            left_start=format_timecode(left_cues[g.left_from].start),
            left_end=format_timecode(left_cues[g.left_to].end),
            right_start=format_timecode(right_cues[g.right_from].start),
            right_end=format_timecode(right_cues[g.right_to].end),
            start_diff_ms=g.start_diff_ms,
            end_diff_ms=g.end_diff_ms,
            merge_penalty_ms=g.merge_penalty_ms,
            cost=g.cost,
        )
        for g in result.groups
    ]

    def unmatched_views(
        cues: list[Cue], skipped: tuple[SkippedCue, ...]
    ) -> list[UnmatchedCueView]:
        return [
            UnmatchedCueView(
                step=s.step,
                cue_order=s.cue_index + 1,
                index=cues[s.cue_index].index,
                start=format_timecode(cues[s.cue_index].start),
                end=format_timecode(cues[s.cue_index].end),
                text=cues[s.cue_index].text,
                skip_cost_ms=s.skip_cost_ms,
            )
            for s in skipped
        ]

    return BilingualAlignResponse(
        left_count=len(left_cues),
        right_count=len(right_cues),
        pair_count=len(result.groups),
        unmatched_left_count=len(result.skipped_left),
        unmatched_right_count=len(result.skipped_right),
        merge_penalty_ms=MERGE_PENALTY_MS,
        skip_penalty_ms=SKIP_PENALTY_MS,
        pair_cost=result.pair_cost,
        skip_cost=result.skip_cost,
        total_cost=result.total_cost,
        pairs=pair_views,
        unmatched_left=unmatched_views(left_cues, result.skipped_left),
        unmatched_right=unmatched_views(right_cues, result.skipped_right),
    )


@app.post("/api/selection", response_model=SelectionResponse)
def selection(req: SelectionRequest) -> SelectionResponse | JSONResponse:
    # 1. 复用现有 SRT 解析器（时间轴不移动：结果 SRT 由原 Cue 的序号、时间、
    #    正文重新序列化，丢弃的条目整块删除）
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    n = len(cues)

    # 2. 汇总逐条优先级：同一输入序号重复设置拒绝生成
    priorities: dict[int, int] = {}
    for item in req.priorities:
        if item.cue_order in priorities:
            raise HTTPException(
                status_code=422,
                detail={
                    "type": "selection",
                    "message": f"序号 {item.cue_order} 的优先级被重复设置，请只填写一次。",
                },
            )
        priorities[item.cue_order] = item.priority

    required = list(req.required_orders)
    if len(set(required)) != len(required):
        dup = next(order for order in required if required.count(order) > 1)
        raise HTTPException(
            status_code=422,
            detail={
                "type": "selection",
                "message": f"必须保留序号 {dup} 重复填写，请只填写一次。",
            },
        )

    # 3. 序号越界等参数校验 + 动态规划择优；必保条目彼此不相容时返回冲突序号
    try:
        plan = select_cues(cues, priorities, required, req.min_gap_ms)
    except SelectionError as exc:
        raise HTTPException(
            status_code=422, detail={"type": "selection", "message": str(exc)}
        )

    if isinstance(plan, tuple):
        return SelectionResponse(
            compatible=False,
            cue_count=n,
            min_gap_ms=req.min_gap_ms,
            kept_count=0,
            dropped_count=0,
            total_priority=0,
            selected_srt="",
            cues=[],
            conflicts=[
                SelectionConflictView(first=pair.first, second=pair.second)
                for pair in plan
            ],
        )

    kept_set = set(plan.kept_orders)
    required_set = set(required)
    cue_views = [
        SelectionCueView(
            cue_order=pos + 1,
            index=cue.index,
            start=format_timecode(cue.start),
            end=format_timecode(cue.end),
            text=cue.text,
            priority=priorities.get(pos + 1, 1),
            kept=(pos + 1) in kept_set,
            required=(pos + 1) in required_set,
        )
        for pos, cue in enumerate(cues)
    ]
    kept_cues = [cues[order - 1] for order in plan.kept_orders]

    return SelectionResponse(
        compatible=True,
        cue_count=n,
        min_gap_ms=req.min_gap_ms,
        kept_count=len(plan.kept_orders),
        dropped_count=n - len(plan.kept_orders),
        total_priority=plan.total_priority,
        selected_srt=format_srt(kept_cues),
        cues=cue_views,
        conflicts=[],
    )


@app.post("/api/cut-rejoin", response_model=CutRejoinResponse)
def cut_rejoin(req: CutRejoinRequest) -> CutRejoinResponse | JSONResponse:
    # 1. 先校验删除区间：时间码合法、结束严格晚于开始；
    #    重叠或相接区间合并为升序不交的并集
    try:
        cuts = parse_cuts([(c.start, c.end) for c in req.cuts])
    except CutRejoinError as exc:
        raise HTTPException(status_code=422, detail={"type": "cuts", "message": str(exc)})

    # 2. 复用现有 SRT 解析器
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    # 3. 按保留时间轴裁切：跨删除区间的字幕拆成多个片段、完全落入删除区间
    #    的整条丢弃；保留片段按累计删除时长前移（片段时长不变），并从 1 连续编号
    results = rejoin_cues(cues, cuts)

    out_cues: list[Cue] = []
    cue_views: list[CutCueView] = []
    mapping_views: list[CutMappingView] = []
    for cue, result in zip(cues, results):
        new_orders: list[int] = []
        segment_views: list[CutSegmentView] = []
        for seg in result.segments:
            out_cues.append(
                Cue(index=str(seg.new_order), start=seg.start, end=seg.end, text=cue.text)
            )
            new_orders.append(seg.new_order)
            segment_views.append(
                CutSegmentView(
                    new_order=seg.new_order,
                    original_start=format_timecode(seg.original_start),
                    original_end=format_timecode(seg.original_end),
                    start=format_timecode(seg.start),
                    end=format_timecode(seg.end),
                )
            )
        cue_views.append(
            CutCueView(
                cue_order=result.cue_order,
                index=cue.index,
                original_start=format_timecode(cue.start),
                original_end=format_timecode(cue.end),
                status=result.status,
                segments=segment_views,
                text=cue.text,
            )
        )
        mapping_views.append(
            CutMappingView(
                cue_order=result.cue_order, index=cue.index, new_orders=new_orders
            )
        )

    return CutRejoinResponse(
        # 全部丢弃时输出空串而非仅含换行的空文档
        rejoined_srt=format_srt(out_cues) if out_cues else "",
        merged_cuts=[
            MergedCutView(
                start=format_timecode(c.start),
                end=format_timecode(c.end),
                duration_ms=c.end - c.start,
            )
            for c in cuts
        ],
        total_removed_ms=sum(c.end - c.start for c in cuts),
        cue_count=len(cues),
        kept_cue_count=sum(1 for r in results if r.segments),
        dropped_cue_count=sum(1 for r in results if not r.segments),
        segment_count=len(out_cues),
        cues=cue_views,
        mapping=mapping_views,
    )


@app.post("/api/cut-avoid", response_model=CutAvoidResponse)
def cut_avoid(req: CutAvoidRequest) -> CutAvoidResponse | JSONResponse:
    # 1. 复用现有 SRT 解析器
    try:
        cues = parse_srt(req.srt)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    n = len(cues)

    # 2. 切点校验（非负整数，去重升序）并在有限挪动范围内求最优避让；
    #    无解时按 HTTP 200、feasible=false 返回最先无法继续安排的字幕序号
    normalized_cuts = sorted(set(req.cut_points))
    try:
        plan = avoid_cuts(cues, req.cut_points, req.max_move_ms, req.min_gap_ms)
    except CutAvoidError as exc:
        if exc.blocked_order is None:
            # 切点本身非法（理论上 Pydantic 已兜底非负整数）
            raise HTTPException(
                status_code=422, detail={"type": "cuts", "message": str(exc)}
            )
        return CutAvoidResponse(
            feasible=False,
            blocked_order=exc.blocked_order,
            cut_points=normalized_cuts,
            max_move_ms=req.max_move_ms,
            min_gap_ms=req.min_gap_ms,
            cue_count=n,
            total_shift_ms=0,
            adjusted_srt="",
            cues=[],
        )

    # 3. 每条字幕时长不变（新结束 = 新起点 + 原时长），无损重写时间轴：
    #    序号、正文、定位参数、空行、BOM 与原有换行风格全部保持原样
    ranges = [
        (new_start, new_start + (cue.end - cue.start))
        for cue, new_start in zip(cues, plan.starts)
    ]
    try:
        result_srt = rewrite_srt(req.srt, ranges)
    except SrtParseError as exc:
        raise HTTPException(status_code=422, detail={"type": "srt", "message": str(exc)})

    cue_views = [
        CutAvoidCueView(
            cue_order=pos,
            index=cue.index,
            original_start=format_timecode(cue.start),
            original_end=format_timecode(cue.end),
            start=format_timecode(new_start),
            end=format_timecode(new_end),
            shift_ms=new_start - cue.start,
            text=cue.text,
        )
        for pos, (cue, (new_start, new_end)) in enumerate(zip(cues, ranges), start=1)
    ]

    return CutAvoidResponse(
        feasible=True,
        blocked_order=None,
        cut_points=list(plan.cuts),
        max_move_ms=req.max_move_ms,
        min_gap_ms=req.min_gap_ms,
        cue_count=n,
        total_shift_ms=plan.total_shift_ms,
        adjusted_srt=result_srt,
        cues=cue_views,
    )
