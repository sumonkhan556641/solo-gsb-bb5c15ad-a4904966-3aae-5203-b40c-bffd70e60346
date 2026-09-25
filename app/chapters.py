"""字幕章节规划。

按输入顺序把字幕切成 K 个非空连续章节：

1. 仅允许在「前一条结束时间不晚于后一条开始时间」的相邻字幕之间切分
   （时间重叠或倒序的相邻字幕之间不可切）；
2. 每章时长 = 章内首条开始时间 → 末条结束时间；以整段（首条开始 → 末条
   结束）总时长 / K 为目标时长，最小化各章时长与目标偏差的平方和，加上
   各切分处 ``max(0, P − 停顿间隔)`` 的平方和（停顿间隔 = 前条结束到后条
   开始的毫秒数，间隔达到 P 即不再惩罚）；
3. 总代价完全相同时取切分点序列更靠前（字典序更小）的方案；
4. 无法切出 K 章时（字幕条数不足 K，或合法切分点不足 K−1 个）抛出
   :class:`ChapterError` 并给出明确原因。

代价全程用整数精确计算：把每章偏差写成 ``(时长·K − 总时长)²``、停顿惩罚
写成 ``max(0, P − 间隔)²·K²``，两者之和恰为总代价的 K² 倍，动态规划
不引入浮点误差。
"""
from __future__ import annotations

from dataclasses import dataclass

from .srt import Cue


class ChapterError(ValueError):
    """无法按要求切出 K 个章节。"""


@dataclass(frozen=True)
class Chapter:
    """一个章节：连续的一段字幕。

    ``cue_order_from`` / ``cue_order_to`` 为字幕在输入 SRT 中从 1 开始的
    出现顺序号（闭区间）；``start`` / ``end`` 分别为章内首条的开始时间与
    末条的结束时间（毫秒）。
    """

    order: int
    start: int
    end: int
    cue_order_from: int
    cue_order_to: int


@dataclass(frozen=True)
class ChapterSplit:
    """一个切分点：位于输入顺序第 ``after_cue_order`` 条字幕之后。"""

    after_cue_order: int
    gap_ms: int
    pause_shortfall_ms: int


@dataclass(frozen=True)
class ChapterPlan:
    chapters: tuple[Chapter, ...]
    splits: tuple[ChapterSplit, ...]
    total_duration_ms: int
    duration_cost_num: int  # 各章 (时长·K − 总时长)² 之和 = 偏差平方和的 K² 倍
    pause_cost: int  # 各切分处 max(0, P − 间隔)² 之和


def plan_chapters(
    cues: list[Cue], chapter_count: int, preferred_pause_ms: int
) -> ChapterPlan:
    """把 ``cues``（保持输入顺序）切成 ``chapter_count`` 个非空连续章节。

    动态规划最小化「偏差平方和 + 停顿惩罚平方和」的 K² 倍（整数），
    同分取切分点序列字典序最小者；无法切出所需章节数时抛出
    :class:`ChapterError`。
    """
    n = len(cues)
    k = chapter_count
    if k < 1:
        raise ChapterError("章节数必须为正整数。")
    if k > n:
        raise ChapterError(
            f"字幕共 {n} 条，少于章节数 {k}：每章至少一条字幕，"
            f"最多只能切出 {n} 章，请减少章节数。"
        )

    starts = [cue.start for cue in cues]
    ends = [cue.end for cue in cues]

    # 可切分点：第 j 条（0 基）字幕之前，即第 j−1 条结束不晚于第 j 条开始
    feasible = [j for j in range(1, n) if ends[j - 1] <= starts[j]]
    need = k - 1
    if len(feasible) < need:
        if feasible:
            positions = "、".join(str(j) for j in feasible[:10])
            if len(feasible) > 10:
                positions += " 等"
            where = f"可切位置仅在第 {positions} 条字幕之后；"
        else:
            where = "不存在满足条件的切分位置；"
        raise ChapterError(
            f"切出 {k} 章需要 {need} 个切分点，但全片仅 {len(feasible)} 处相邻字幕"
            f"满足“前条结束不晚于后条开始”，{where}"
            "其余相邻字幕时间重叠或倒序。请减少章节数，或先校正字幕时间。"
        )

    total = ends[-1] - starts[0]
    p = preferred_pause_ms
    kk = k * k

    # prev[i] = (代价, 切分点序列)：前 i 条字幕恰好切成 layer 章的最优解。
    # 切分点记录第 2..layer 章首条字幕的 0 基下标，天然按位置递增，
    # 元组比较即“切分点序列靠前优先”
    prev: dict[int, tuple[int, tuple[int, ...]]] = {}
    for i in range(1, n - k + 2):  # 1 章：给后面 k−1 章至少各留 1 条
        dev = (ends[i - 1] - starts[0]) * k - total
        prev[i] = (dev * dev, ())

    for layer in range(2, k + 1):
        cur: dict[int, tuple[int, tuple[int, ...]]] = {}
        # 前 i 条切 layer 章：i 至少 layer，且给剩余 k−layer 章各留 1 条
        for i in range(layer, n - (k - layer) + 1):
            best: tuple[int, tuple[int, ...]] | None = None
            for j in range(layer - 1, i):
                entry = prev.get(j)
                if entry is None or ends[j - 1] > starts[j]:
                    continue
                base_cost, base_seq = entry
                gap = starts[j] - ends[j - 1]
                short = p - gap
                penalty = short * short if short > 0 else 0
                dev = (ends[i - 1] - starts[j]) * k - total
                candidate = (
                    base_cost + dev * dev + penalty * kk,
                    base_seq + (j,),
                )
                if best is None or candidate < best:
                    best = candidate
            if best is not None:
                cur[i] = best
        prev = cur

    _, splits = prev[n]
    bounds = (0, *splits, n)
    chapters = tuple(
        Chapter(
            order=no,
            start=starts[bounds[no - 1]],
            end=ends[bounds[no] - 1],
            cue_order_from=bounds[no - 1] + 1,
            cue_order_to=bounds[no],
        )
        for no in range(1, k + 1)
    )
    split_views = tuple(
        ChapterSplit(
            after_cue_order=j,
            gap_ms=starts[j] - ends[j - 1],
            pause_shortfall_ms=max(0, p - (starts[j] - ends[j - 1])),
        )
        for j in splits
    )
    return ChapterPlan(
        chapters=chapters,
        splits=split_views,
        total_duration_ms=total,
        duration_cost_num=sum(
            ((ch.end - ch.start) * k - total) ** 2 for ch in chapters
        ),
        pause_cost=sum(sp.pause_shortfall_ms**2 for sp in split_views),
    )
