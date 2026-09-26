"""镜头切点避让（在有限挪动范围内整体平移字幕，避开切点）。

视频剪辑中每个「镜头切点」是一个毫秒时刻：字幕可以与切点**首尾相接**
（切点恰为字幕开始或结束），但切点不得落在字幕显示区间**内部**。输入
SRT 后按以下规则重新安排每条字幕的开始时间：

1. **时长与正文不变**：每条字幕只做整体平移，新区间为
   ``[new_start, new_start + 原时长)``；序号、正文等由调用方经
   :func:`app.srt.rewrite_srt` 无损保留。
2. **挪动范围**：新开始时间只能取整毫秒，且满足
   ``max(0, 原开始 − 最大挪动量) ≤ 新开始 ≤ 原开始 + 最大挪动量``
   （新开始不得小于零，故新结束也必为正）。
3. **切点不内部**：对每个切点 ``c``，``new_start < c < new_start + 时长``
   不成立；等价于新开始不得落入整数禁区间
   ``[c − 时长 + 1, c − 1]``，而 ``new_start == c``（切点在开始）与
   ``new_start + 时长 == c``（切点在结束）都是允许的边界重合。
4. **相邻间隔**：相邻字幕按输入顺序满足
   ``前条新结束 + 最小间隔 ≤ 后条新开始``（首尾相接且间隔为 0 允许）。

在全部可行安排中**最小化各条开始挪动绝对值之和**
``Σ|new_start − 原开始|``；总挪动量相同时取**新起点序列字典序最小**
（第一个不同的起点尽量小，再依次比较后续）。实现为按字幕顺序的动态规划：
每条字幕的候选起点窗口至多 ``2·M+1`` 个整毫秒（M ≤ 2000），用切点禁区间
在窗口上做掩码，后向 DP 行的后缀最小值承接相邻间隔约束，自后而前求
``bwd[i][s]``（第 i 条起点取 s 时，i 及之后各条的最小总挪动量）；总最优
为 ``min bwd[0]``，再自前而后在每个窗口里取满足前缀约束且
``bwd[i][s]`` 恰等于剩余预算的最小 s，即得字典序最小的最优序列。
逐行 DP 用 NumPy 向量化构建（掩码差分 + 后缀最小值）。

**无解**时调用 :func:`first_blocked_order` 可得到「最先无法继续安排」的
字幕输入序号（从 1 开始）：按最早可行起点贪心安排前序字幕后，第 k 条在
其窗口与相邻间隔约束内不存在任何不含切点的起点；取最早起点给后续留出
最大空间，故贪心失败即前 k 条整体无解，k 也就是最小的失败前缀长度。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import numpy as np

from .srt import Cue

# int64 哨兵：真实总挪动量不超过 条数 × 2000，远小于此值
INF = np.int64(10**15)


class CutAvoidError(ValueError):
    """镜头切点等参数不合法。"""

    def __init__(self, message: str, blocked_order: int | None = None) -> None:
        super().__init__(message)
        self.blocked_order = blocked_order


@dataclass(frozen=True)
class AvoidPlan:
    """避让结果：各条新起点（毫秒，与输入字幕同序）、去重排序后的切点与总挪动量。"""

    starts: tuple[int, ...]
    cuts: tuple[int, ...]
    total_shift_ms: int


def normalize_cuts(raw: list[int]) -> tuple[int, ...]:
    """校验镜头切点（非负整数毫秒），返回去重后按毫秒升序排列的切点元组。"""
    values: list[int] = []
    for pos, value in enumerate(raw, start=1):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CutAvoidError(f"第 {pos} 个镜头切点必须是非负整数毫秒。")
        values.append(value)
    return tuple(sorted(set(values)))


def _first_allowed(lo: int, hi: int, duration: int, cuts: tuple[int, ...]) -> int | None:
    """返回 ``[lo, hi]`` 内最小的、不使任一切点落入字幕内部的整数起点。

    切点 ``c`` 排除的起点区间为 ``[c−时长+1, c−1]``；起点恰为 ``c``
    （切点在开始）或 ``c−时长``（切点在结束）都允许。区间不相交（切点为
    孤立时刻、时长固定），按升序逐个跳过覆盖当前位置的区间即可。
    """
    start = lo
    idx = bisect_right(cuts, start)  # 第一个严格大于 start 的切点
    while idx < len(cuts):
        cut = cuts[idx]
        forbidden_from = cut - duration + 1
        if forbidden_from > start:
            break  # 该切点及之后切点的禁区都在 start 之后，start 可行
        if cut - 1 >= start:
            start = cut  # 跳过整个禁区，落在切点（开始边界，允许）
        idx += 1
    return start if start <= hi else None


def first_blocked_order(
    cues: list[Cue], cuts: tuple[int, ...], max_move_ms: int, min_gap_ms: int
) -> int | None:
    """按最早可行起点贪心安排，返回最先无法安排的字幕输入序号（从 1 开始）。

    每条总选满足窗口与相邻间隔约束的最小可行起点（给后续留出最大空间）；
    全部可安排时返回 ``None``。
    """
    prev_end: int | None = None
    for pos, cue in enumerate(cues, start=1):
        duration = cue.end - cue.start
        lo = max(0, cue.start - max_move_ms)
        hi = cue.start + max_move_ms
        if prev_end is not None:
            lo = max(lo, prev_end + min_gap_ms)
        if lo > hi:
            return pos
        start = _first_allowed(lo, hi, duration, cuts)
        if start is None:
            return pos
        prev_end = start + duration
    return None


def _forbidden_mask(
    lo: int, hi: int, duration: int, cuts: tuple[int, ...]
) -> np.ndarray:
    """候选窗口 ``[lo, hi]`` 上各整毫秒起点是否落入某切点的禁区间（布尔数组）。

    切点 ``c`` 映射到窗口内的禁区为 ``[max(lo, c−时长+1), min(hi, c−1)]``。
    在窗口坐标上做一次差分累加即可标记全部区间：在每个
    ``c−时长+1`` 处 +1、每个 ``c`` 处 −1，前缀和大于 0 即在某禁区内部
    （起点恰为切点 c 时差分已先 −1，故切点位置不被禁用）。
    """
    size = hi - lo + 1
    diff = np.zeros(size + 1, dtype=np.int64)
    idx = bisect_right(cuts, lo)
    limit = hi + duration - 1
    while idx < len(cuts) and cuts[idx] <= limit:
        cut = cuts[idx]
        a = max(lo, cut - duration + 1)
        b = min(hi, cut - 1)
        if a <= b:
            diff[a - lo] += 1
            diff[b - lo + 1] -= 1
        idx += 1
    return np.cumsum(diff[:-1], dtype=np.int64) > 0


def _build_last_row(
    lo: int, hi: int, center: int, duration: int, cuts: tuple[int, ...]
) -> np.ndarray:
    """最后一条字幕的后向 DP 行：可行起点代价为 |s − 原起点|，禁用格为 INF。"""
    row = np.abs(np.arange(lo, hi + 1, dtype=np.int64) - center)
    row[_forbidden_mask(lo, hi, duration, cuts)] = INF
    return row


def _build_row(
    lo: int,
    hi: int,
    center: int,
    duration: int,
    cuts: tuple[int, ...],
    next_row: np.ndarray,
    next_lo: int,
    min_gap_ms: int,
) -> np.ndarray:
    """第 i 条的后向 DP 行：自身挪动量 + 满足间隔约束的后续最小挪动量。"""
    starts = np.arange(lo, hi + 1, dtype=np.int64)
    # 起点 s 时下一条可行起点须 ≥ s + 时长 + 最小间隔，对其 DP 行取后缀最小值
    thresholds = starts + duration + min_gap_ms - next_lo
    suffix = np.minimum.accumulate(next_row[::-1])[::-1]
    best = np.full(starts.shape, INF, dtype=np.int64)
    inside = (thresholds >= 0) & (thresholds < len(next_row))
    best[inside] = suffix[thresholds[inside]]
    best[thresholds < 0] = suffix[0]

    # 仅在后续可行的格子累加自身代价（INF 不参与加法，杜绝哨兵溢出），
    # 切点禁区间随后整体置为 INF
    feasible = best < INF
    row = np.full(starts.shape, INF, dtype=np.int64)
    row[feasible] = best[feasible] + np.abs(starts[feasible] - center)
    row[_forbidden_mask(lo, hi, duration, cuts)] = INF
    return row


def avoid_cuts(
    cues: list[Cue],
    raw_cuts: list[int],
    max_move_ms: int,
    min_gap_ms: int,
) -> AvoidPlan:
    """求切点避让最优安排；前 k 条整体无解时抛出 :class:`CutAvoidError`。"""
    cuts = normalize_cuts(raw_cuts)

    blocked = first_blocked_order(cues, cuts, max_move_ms, min_gap_ms)
    if blocked is not None:
        raise CutAvoidError(
            f"第 {blocked} 条字幕在最大挪动量内无法避开全部镜头切点"
            "并满足相邻字幕最小间隔，没有可行的安排。",
            blocked_order=blocked,
        )

    n = len(cues)
    lows = [max(0, cue.start - max_move_ms) for cue in cues]
    highs = [cue.start + max_move_ms for cue in cues]

    # 自后而前构建后向 DP：rows[i][s − lows[i]] = 第 i 条起点取 s 时的最优尾代价
    last = cues[-1]
    rows: list[np.ndarray] = [np.empty(0, dtype=np.int64)] * n
    rows[n - 1] = _build_last_row(
        lows[n - 1], highs[n - 1], last.start, last.end - last.start, cuts
    )
    for i in range(n - 2, -1, -1):
        cue = cues[i]
        rows[i] = _build_row(
            lows[i],
            highs[i],
            cue.start,
            cue.end - cue.start,
            cuts,
            rows[i + 1],
            lows[i + 1],
            min_gap_ms,
        )

    # 总最优 = 首条尾代价最小值；自前而后取剩余预算恰好匹配的最小起点，
    # 得到总挪动量最小、同分新起点序列字典序最小的安排
    total = int(rows[0].min())
    starts: list[int] = []
    spent = 0
    prev_end: int | None = None
    for i, cue in enumerate(cues):
        lo, hi = lows[i], highs[i]
        row = rows[i]
        if prev_end is not None:
            gap_idx = prev_end + min_gap_ms - lo
            if gap_idx > 0:
                row = row[gap_idx:]
                offset = gap_idx
            else:
                offset = 0
        else:
            offset = 0
        matches = np.flatnonzero(row == total - spent)
        if matches.size == 0:  # 已通过贪心可行性检查，理论上不可达
            raise CutAvoidError(f"第 {i + 1} 条字幕回溯最优安排失败。")
        chosen = lo + offset + int(matches[0])
        starts.append(chosen)
        spent += abs(chosen - cue.start)
        prev_end = chosen + (cue.end - cue.start)

    return AvoidPlan(starts=tuple(starts), cuts=cuts, total_shift_ms=total)
