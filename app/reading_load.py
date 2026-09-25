"""阅读负荷热区分析。

每条字幕的阅读速率 = 去掉 Unicode 空白（``str.isspace``，含换行、制表符、
全角空格等）后的字符数 ÷ 显示秒数。多条字幕时间区间重叠（按**半开**
时间轴 ``[start, end)`` 计算，首尾相接不算重叠）时，重叠区间内的合计
速率为各字幕速率之和。

以每个字幕起止时刻为事件点扫描时间轴，得到若干合计速率恒定的分段；
挑出合计速率**严格超过**阈值的分段，首尾相接的合并成热区，再保留
连续时长达到下限（毫秒）的热区。全程基于毫秒整数与
:class:`fractions.Fraction` 精确计算，不做整秒采样，因此不会漏掉毫秒级
峰值。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .srt import Cue


@dataclass(frozen=True)
class CueLoad:
    """一条字幕的字符数、显示时长与阅读速率。"""

    order: int
    char_count: int
    duration_ms: int
    rate: Fraction


@dataclass(frozen=True)
class ReadingZone:
    """一个阅读负荷热区。"""

    start: int
    end: int
    peak_rate: Fraction
    cue_orders: tuple[int, ...]


def count_display_chars(text: str) -> int:
    """统计去掉所有 Unicode 空白字符后的字符数。"""
    return sum(1 for ch in text if not ch.isspace())


def cue_loads(cues: list[Cue]) -> list[CueLoad]:
    """按输入顺序计算每条字幕的字符数与阅读速率。"""
    loads: list[CueLoad] = []
    for pos, cue in enumerate(cues, start=1):
        char_count = count_display_chars(cue.text)
        duration_ms = cue.end - cue.start
        rate = Fraction(char_count * 1000, duration_ms)  # 字 / 秒，避免除法取整
        loads.append(
            CueLoad(
                order=pos,
                char_count=char_count,
                duration_ms=duration_ms,
                rate=rate,
            )
        )
    return loads


def _segments(
    loads: list[CueLoad], cues: list[Cue]
) -> list[tuple[int, int, Fraction, tuple[int, ...]]]:
    """按起止事件扫描半开时间轴，输出 ``(开始, 结束, 合计速率, 参与序号)`` 分段。

    同一时刻先按旧集合结算 ``[上一时刻, 当前时刻)`` 的分段，再统一应用
    该时刻的结束 / 开始事件（故首尾相接的字幕不会被视作同时活跃）。
    """
    events: list[tuple[int, int, int, Fraction]] = []
    for cue, load in zip(cues, loads):
        # 结束事件排在开始事件之前：半开区间 [start, end) 在边界点结束先生效
        events.append((cue.start, 1, load.order, load.rate))
        events.append((cue.end, 0, load.order, load.rate))
    events.sort()

    segments: list[tuple[int, int, Fraction, tuple[int, ...]]] = []
    active: dict[int, Fraction] = {}
    prev_t = events[0][0]
    i = 0
    total = len(events)
    while i < total:
        t = events[i][0]
        if t > prev_t and active:
            total_rate = sum(active.values(), Fraction(0))
            segments.append(
                (prev_t, t, total_rate, tuple(sorted(active)))
            )
        # 应用该时刻的全部事件（结束在前）
        while i < total and events[i][0] == t:
            _, kind, order, rate = events[i]
            if kind == 0:
                del active[order]
            else:
                active[order] = rate
            i += 1
        prev_t = t
    return segments


def find_reading_zones(
    cues: list[Cue], rate_threshold: Fraction, min_duration_ms: int
) -> tuple[list[CueLoad], list[ReadingZone]]:
    """扫描时间轴找出阅读负荷热区。

    :param rate_threshold: 速率阈值（字 / 秒，正数），合计速率**严格超过**
        该值的时间段才算超阈值。
    :param min_duration_ms: 热区最短连续时长（非负整数毫秒），相接的超阈值
        分段合并为热区后再按此时长过滤。
    :returns: ``(逐条字幕负荷, 热区列表)``，热区按开始时间排序。
    """
    loads = cue_loads(cues)

    # 收集并合并相接的超阈值分段（事件分段两两相接，相邻段不会重叠）
    zones: list[ReadingZone] = []
    for seg_start, seg_end, total_rate, orders in _segments(loads, cues):
        if total_rate <= rate_threshold:
            continue
        if zones and seg_start == zones[-1].end:
            prev = zones[-1]
            zones[-1] = ReadingZone(
                start=prev.start,
                end=seg_end,
                peak_rate=max(prev.peak_rate, total_rate),
                cue_orders=tuple(
                    sorted(set(prev.cue_orders) | set(orders))
                ),
            )
        else:
            zones.append(
                ReadingZone(
                    start=seg_start,
                    end=seg_end,
                    peak_rate=total_rate,
                    cue_orders=orders,
                )
            )

    long_enough = [z for z in zones if z.end - z.start >= min_duration_ms]
    return loads, long_enough
