"""删段后字幕接轴（按删除区间裁切字幕并前移补齐）。

从视频中删掉若干时间区间后，字幕需要同步「接轴」：

1. **校验并合并删除区间**：起止必须为合法时间码且结束严格晚于开始；
   重叠（``下一段开始 < 上一段结束``）或**相接**（``下一段开始 == 上一段
   结束``）的区间合并为一个，合并后按开始时间升序且两两不相交。
2. **按保留时间轴裁切每条字幕**：字幕与删除区间都按**半开区间**
   ``[start, end)`` 处理，字幕区间减去删除区间并集后剩下的极大连续段即
   保留片段。边界恰好相接不算重叠：字幕结束恰为某删除区间的开始、或字幕
   开始恰为某删除区间的结束时，该字幕**完整保留**；字幕开始恰为删除开始
   （或结束恰为删除结束）时，该端点落入删除区间，相应端被裁掉。
3. **拆分与丢弃**：跨删除区间的字幕拆成多个片段（正文随片段各自保留）；
   完全落入删除区间（没有任何保留片段）的字幕整条丢弃。
4. **按累计删除时长前移**：保留片段内部不含任何删除区间，片段前后端点
   的前移量相同（均为该片段之前被删除的总时长），故片段时长不变、整体
   平移；删除起点为零时，片头内容被删除，后续内容自然前移到 0。
5. **连续编号输出**：所有保留片段按原字幕输入顺序、片段时间顺序重新从
   1 连续编号，输出标准 SRT，并给出每条原字幕（输入序号）到新序号的
   对应表（一条原字幕可对应多个新序号，丢弃的对应空列表）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .srt import Cue
from .timecode import format_timecode, parse_timecode

# 字幕裁切状态
KEPT = "kept"        # 完整保留（未触及任何删除区间）
TRIMMED = "trimmed"  # 被裁切但仍为单个片段
SPLIT = "split"      # 跨删除区间，拆成多个片段
DROPPED = "dropped"  # 完全落入删除区间，整条丢弃


class CutRejoinError(ValueError):
    """删除区间参数不合法。"""


@dataclass(frozen=True)
class Cut:
    """一个已校验的删除区间（半开区间 ``[start, end)``，毫秒）。"""

    start: int
    end: int


@dataclass(frozen=True)
class SegmentResult:
    """一个保留片段：原时间轴区间、前移后的新时间轴区间与连续新序号。"""

    new_order: int
    original_start: int
    original_end: int
    start: int
    end: int


@dataclass(frozen=True)
class CueResult:
    """一条字幕的裁切结果（``cue_order`` 为输入序号，从 1 开始）。"""

    cue_order: int
    status: str
    segments: tuple[SegmentResult, ...]


def parse_cuts(raw: list[tuple[str, str]]) -> list[Cut]:
    """解析删除区间并求并集（重叠或相接都合并），返回升序不交的区间列表。"""
    cuts: list[Cut] = []
    for pos, (start_text, end_text) in enumerate(raw, start=1):
        try:
            start_ms = parse_timecode(start_text)
        except ValueError as exc:
            raise CutRejoinError(
                f"第 {pos} 个删除区间的开始时间无效：{exc}"
            ) from exc
        try:
            end_ms = parse_timecode(end_text)
        except ValueError as exc:
            raise CutRejoinError(
                f"第 {pos} 个删除区间的结束时间无效：{exc}"
            ) from exc
        if end_ms <= start_ms:
            raise CutRejoinError(
                f"第 {pos} 个删除区间无效：结束时间 {format_timecode(end_ms)}"
                f" 必须严格晚于开始时间 {format_timecode(start_ms)}。"
            )
        cuts.append(Cut(start_ms, end_ms))
    return merge_cuts(cuts)


def merge_cuts(cuts: list[Cut]) -> list[Cut]:
    """重叠（含首尾相接）的删除区间求并集，结果按开始时间升序且两两不交。"""
    ordered = sorted(cuts, key=lambda c: (c.start, c.end))
    merged: list[Cut] = []
    for cut in ordered:
        if merged and cut.start <= merged[-1].end:
            prev = merged[-1]
            merged[-1] = Cut(prev.start, max(prev.end, cut.end))
        else:
            merged.append(cut)
    return merged


def removed_before(cuts: list[Cut], t: int) -> int:
    """时刻 ``t`` 之前被删除的总毫秒数（即该时刻的前移量）。

    ``cuts`` 须为已合并（升序不交）的删除区间；``t`` 恰好等于某区间结束
    时该区间完整计入，恰好等于某区间开始时该区间不计入（半开区间）。
    调用方保证 ``t`` 不在任何删除区间内部（保留片段的端点即如此）。
    """
    total = 0
    for cut in cuts:
        if cut.end <= t:
            total += cut.end - cut.start
        else:
            break
    return total


def cut_cue(start: int, end: int, cuts: list[Cut]) -> list[tuple[int, int]]:
    """字幕区间 ``[start, end)`` 减去删除区间并集，返回保留片段（原时间轴）。

    片段按时间升序、两两不交；完全落入删除区间时返回空列表。
    """
    segments: list[tuple[int, int]] = []
    cursor = start
    for cut in cuts:
        if cut.end <= cursor:
            continue  # 删除区间在当前位置之前（相接不算重叠）
        if cut.start >= end:
            break  # 之后的删除区间不再影响本字幕（相接不算重叠）
        if cut.start > cursor:
            segments.append((cursor, cut.start))
        cursor = max(cursor, cut.end)
        if cursor >= end:
            break
    if cursor < end:
        segments.append((cursor, end))
    return segments


def rejoin_cues(cues: list[Cue], cuts: list[Cut]) -> list[CueResult]:
    """逐条裁切并按累计删除时长前移，返回每条字幕的状态与保留片段。

    保留片段按输入顺序、片段时间顺序从 1 连续编号（``new_order``）；
    片段内部不含删除区间，前后端点前移量相同，片段时长保持不变。
    """
    results: list[CueResult] = []
    new_order = 0
    for pos, cue in enumerate(cues, start=1):
        segments: list[SegmentResult] = []
        for seg_start, seg_end in cut_cue(cue.start, cue.end, cuts):
            shift = removed_before(cuts, seg_start)
            new_order += 1
            segments.append(
                SegmentResult(
                    new_order=new_order,
                    original_start=seg_start,
                    original_end=seg_end,
                    start=seg_start - shift,
                    end=seg_end - shift,
                )
            )
        if not segments:
            status = DROPPED
        elif len(segments) > 1:
            status = SPLIT
        elif segments[0].original_start == cue.start and segments[0].original_end == cue.end:
            status = KEPT
        else:
            status = TRIMMED
        results.append(CueResult(cue_order=pos, status=status, segments=tuple(segments)))
    return results
