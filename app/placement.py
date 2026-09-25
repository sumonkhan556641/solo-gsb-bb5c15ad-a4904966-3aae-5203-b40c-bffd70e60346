"""画面遮挡时段处理与字幕上下位置规划。

流程：
* 解析并校验画面上方 / 下方的遮挡时间段（起止必须为合法时间码且结束严格晚于开始）；
* 同侧时段先按开始时间排序并对重叠（含首尾相接）时段求并集；
* 统计并集时段与每条字幕时间区间的交集总毫秒数作为该侧遮挡时长；
* 以「总遮挡时长 + 相邻字幕上下换位代价」最小为目标做动态规划，
  总代价完全相同时按字幕顺序逐条优先选择下方；
* 导出带 ``line`` 位置设置（上方 ``line:10%``、下方 ``line:90%``）的 WebVTT。
"""
from __future__ import annotations

from dataclasses import dataclass

from .srt import Cue
from .timecode import format_timecode, format_vtt_timecode, parse_timecode

TOP = "top"
BOTTOM = "bottom"
_TOP_I = 0
_BOTTOM_I = 1


class PlacementError(ValueError):
    """遮挡时间段参数不合法。"""


@dataclass(frozen=True)
class Block:
    start: int
    end: int


@dataclass(frozen=True)
class CuePlacement:
    position: str
    top_occluded_ms: int
    bottom_occluded_ms: int

    @property
    def occluded_ms(self) -> int:
        return self.top_occluded_ms if self.position == TOP else self.bottom_occluded_ms

    @property
    def still_occluded(self) -> bool:
        return self.occluded_ms > 0


def parse_blocks(raw: list[tuple[str, str]], side: str) -> list[Block]:
    """解析一侧的遮挡时段并求并集；``side`` 仅用于错误提示（如“画面上方”）。"""
    blocks: list[Block] = []
    for pos, (start_text, end_text) in enumerate(raw, start=1):
        try:
            start_ms = parse_timecode(start_text)
        except ValueError as exc:
            raise PlacementError(
                f"{side}第 {pos} 个遮挡时段的开始时间无效：{exc}"
            ) from exc
        try:
            end_ms = parse_timecode(end_text)
        except ValueError as exc:
            raise PlacementError(
                f"{side}第 {pos} 个遮挡时段的结束时间无效：{exc}"
            ) from exc
        if end_ms <= start_ms:
            raise PlacementError(
                f"{side}第 {pos} 个遮挡时段无效：结束时间 {format_timecode(end_ms)}"
                f" 必须严格晚于开始时间 {format_timecode(start_ms)}。"
            )
        blocks.append(Block(start_ms, end_ms))
    return merge_blocks(blocks)


def merge_blocks(blocks: list[Block]) -> list[Block]:
    """同侧重叠（含首尾相接）遮挡时段求并集。"""
    ordered = sorted(blocks, key=lambda b: (b.start, b.end))
    merged: list[Block] = []
    for block in ordered:
        if merged and block.start <= merged[-1].end:
            prev = merged[-1]
            merged[-1] = Block(prev.start, max(prev.end, block.end))
        else:
            merged.append(block)
    return merged


def intersection_ms(start: int, end: int, blocks: list[Block]) -> int:
    """字幕区间 ``[start, end]`` 与已求并集的遮挡时段的交集总毫秒数。"""
    return sum(
        max(0, min(end, block.end) - max(start, block.start)) for block in blocks
    )


def occlusions_for_cues(
    starts: list[int], ends: list[int], blocks: list[Block]
) -> list[int]:
    """按字幕顺序批量计算每条字幕与已求并集遮挡时段的交集毫秒数。

    遮挡时段已合并排序；字幕时间顺序不做要求，按开始时间排序后归并扫描，
    结果再还原为原顺序。块的结束早于当前字幕开始即跳过。
    """
    result = [0] * len(starts)
    order = sorted(range(len(starts)), key=lambda i: starts[i])
    j = 0
    m = len(blocks)
    for idx in order:
        start, end = starts[idx], ends[idx]
        while j < m and blocks[j].end <= start:
            j += 1
        k = j
        total = 0
        while k < m and blocks[k].start < end:
            total += max(0, min(end, blocks[k].end) - max(start, blocks[k].start))
            k += 1
        result[idx] = total
    return result


def plan_positions(
    cues: list[Cue],
    top_blocks: list[Block],
    bottom_blocks: list[Block],
    switch_cost_ms: int,
) -> list[CuePlacement]:
    """动态规划为每条字幕选择上 / 下位置。

    目标 = 各字幕所选侧遮挡时长之和 + 相邻字幕位置不同时的换位代价之和。

    同分优先下方（按字幕顺序逐条词典序）通过两遍 DP 实现：``suffix[i][pos]``
    为第 i 条固定在 pos 时第 i..n-1 条的最小代价（含本条遮挡与到第 i+1 条的
    换位代价）；正向回溯第 i 条时，只要「已选前缀代价 + 与前一条的衔接换位
    + suffix[i][pos]」等于全局最优，该位置就可行，候选顺序下方在前故同分先
    选下方。越早的字幕越先定，等价于按字幕顺序逐条优先下方。
    """
    n = len(cues)
    starts = [cue.start for cue in cues]
    ends = [cue.end for cue in cues]
    top_occ = occlusions_for_cues(starts, ends, top_blocks)
    bottom_occ = occlusions_for_cues(starts, ends, bottom_blocks)
    occ = list(zip(top_occ, bottom_occ))

    # suffix[i][pos]：第 i 条固定取 pos 时，第 i..n-1 条的最小代价
    suffix = [[0, 0] for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        suffix[i][_TOP_I] = occ[i][0] + min(
            suffix[i + 1][_TOP_I],
            switch_cost_ms + suffix[i + 1][_BOTTOM_I],
        )
        suffix[i][_BOTTOM_I] = occ[i][1] + min(
            switch_cost_ms + suffix[i + 1][_TOP_I],
            suffix[i + 1][_BOTTOM_I],
        )

    optimum = min(suffix[0])

    # 正向回溯：prefix_cost 为第 0..i-1 条已定选择的实际代价；第 i 条候选位置
    # 的最优完整代价 = 前缀 + 衔接换位 + suffix[i][pos]（已含本条遮挡与后续最优段）
    positions_i: list[int] = []
    prefix_cost = 0
    prev: int | None = None
    for i in range(n):
        candidates = []
        for pos in (_BOTTOM_I, _TOP_I):  # 同分时遍先后方先中选
            link = 0 if prev is None else (0 if pos == prev else switch_cost_ms)
            value = prefix_cost + link + suffix[i][pos]
            if value == optimum:
                candidates.append(pos)
        chosen = candidates[0]
        positions_i.append(chosen)
        link = 0 if prev is None else (0 if chosen == prev else switch_cost_ms)
        prefix_cost += link + occ[i][chosen]
        prev = chosen

    return [
        CuePlacement(
            position=TOP if pos == _TOP_I else BOTTOM,
            top_occluded_ms=occ[i][0],
            bottom_occluded_ms=occ[i][1],
        )
        for i, pos in enumerate(positions_i)
    ]


def count_switches(placements: list[CuePlacement]) -> int:
    """相邻字幕位置不同（发生上下换位）的次数。"""
    return sum(
        1
        for prev, cur in zip(placements, placements[1:])
        if prev.position != cur.position
    )


def export_vtt(cues: list[Cue], placements: list[CuePlacement]) -> str:
    """序列化为带 line 位置设置的 WebVTT（上方 line:10%，下方 line:90%）。"""
    blocks: list[str] = []
    for cue, placement in zip(cues, placements):
        line_pos = "10%" if placement.position == TOP else "90%"
        blocks.append(
            f"{cue.index}\n"
            f"{format_vtt_timecode(cue.start)} --> {format_vtt_timecode(cue.end)}"
            f" line:{line_pos}\n"
            f"{cue.text}"
        )
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"
