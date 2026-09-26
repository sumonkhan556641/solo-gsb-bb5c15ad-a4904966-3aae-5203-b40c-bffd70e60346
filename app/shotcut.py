"""镜头切点避让（在最大挪动量内平移字幕，使切点不落在字幕内部）。

剪辑镜头切换的瞬间（切点）若落在字幕显示区间内部，换镜时字幕还挂在屏幕上，
观感突兀。本模块在「每条字幕只在原起点前后最大挪动量内按整毫秒平移、时长与
正文不变、输入顺序不变」的前提下，为每条字幕选择新起点：

1. **可行约束**

   * 第 i 条新起点 ``x_i`` 为整数毫秒，且
     ``max(0, s_i − M) ≤ x_i ≤ s_i + M``（``s_i`` 为原起点，``M`` 为最大
     挪动量，**新起点不得小于零**）；
   * 切点只可与字幕边界重合（``x_i == c`` 或 ``x_i + d_i == c``），**不得
     落在字幕内部**（``x_i < c < x_i + d_i`` 禁止）；
   * **相邻字幕**（按输入顺序）满足最小间隔：
     ``x_i + d_i + G ≤ x_{i+1}``（``G`` 为最小间隔毫秒数，``G = 0`` 时
     首尾相接允许）。

2. **选解目标**：先最小化所有字幕「起点挪动绝对值之和」
   ``Σ |x_i − s_i|``；总挪动量相同时，取新起点序列 ``(x_1, …, x_n)``
   **字典序最小**的方案。

3. **无解判定**：约束只沿输入顺序向后耦合（第 i 条的可行位置只依赖第 i−1
   条），「前 k 条可安排」对 k 单调，故逐条前向可行性扫描找到的第一个使
   前缀不可行的 k，即「最先无法继续安排的字幕输入序号」。

实现要点（``n`` 条字幕、每条挪动域宽 ``W = 2M + 1 ≤ 4001``）：

* 每个切点 ``c`` 为第 i 条导出的禁止整点区间为 ``[c − d_i + 1, c − 1]``
  （边界整点 ``x == c``、``x == c − d_i`` 不置禁）；与某条字幕相关的切点
  只有 ``(low, high + d_i)`` 内的少数几个，二分定位后记录为下标跨度。
* 后向 DP 求出「第 i 条恰好落在 x 时，第 i～n 条的最小总代价表」
  ``A_i``，存为 ``array('q')``（全部表占 ``O(n·W)`` 空间，每项 8 字节）。
  相邻条目只含「不早于」关系，后继最优值即后缀最小值，用切片整体平移
  对齐后与 ``|x − s_i|``（V 形，两段 ``range`` 直接拼出）经 ``map(add)``
  批量相加，再把各禁止跨度整段填 ``INF``——内层全部是 C 速度。不可行
  状态以「``≥ INF``」统一表示（真实总代价上界 ``n × M`` 远小于 ``INF``，
  污染值参与后缀最小与求和仍保持 ``≥ INF``，且不会溢出 64 位整数），
  因此求和后无需逐元素截断。一旦某张表全 ``≥ INF``，更早的表必然同样
  不可行，直接判定无解并转前向扫描。
* 最优总代价 ``C* = min A_1``；正向贪心在「剩余预算」约束下逐条用
  ``array.index`` 直接定位字典序最小的新起点，整体时间 ``O(n·W)``。
"""
from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from itertools import accumulate, chain, islice, repeat
from operator import add
from typing import Iterator, Sequence

from .srt import Cue

# 真实总代价上界为 n × M（远小于该值）；64 位有符号整数可容
INF = 1 << 60

# 最大挪动量上界（毫秒），由接口字段 ge=0 / le=2000 兜底
MAX_SHIFT_LIMIT_MS = 2000

# 单元素 INF 数组，供整段填充时按长度复制
_INF_ROW = array("q", [INF])


class ShotCutError(ValueError):
    """镜头切点、最大挪动量或最小间隔等参数不合法。"""


@dataclass(frozen=True)
class AvoidPlan:
    """避让规划结果。

    可行时 ``starts`` 为逐条新起点（毫秒，与输入字幕一一对应），
    ``total_shift_ms`` 为起点挪动绝对值之和；无解时 ``starts`` 为空、
    ``first_unschedulable_order`` 为最先无法继续安排的字幕输入序号
    （从 1 开始）。
    """

    feasible: bool
    starts: tuple[int, ...]
    total_shift_ms: int
    first_unschedulable_order: int | None


def _gather_window(
    values: Sequence[int], j0: int, size: int, below: int, above: int
) -> Iterator[int]:
    """产出 ``out[k] = values[j0 + k]``（``k`` 取 ``0..size−1``），越界取边界值。

    ``j0 + k < 0`` 时取 ``below``（约束比定义域更松），
    ``j0 + k >= len(values)`` 时取 ``above``（域内没有满足约束的位置）；
    三段用 ``repeat`` / ``islice`` 惰性拼接，不复制中间列表。
    """
    neg = min(size, max(0, -j0))
    start = max(j0, 0)
    end = min(j0 + size, len(values))
    mid = max(0, end - start)
    return chain(
        repeat(below, neg),
        islice(values, start, start + mid),
        repeat(above, size - neg - mid),
    )


def _forbidden_spans(
    low: int, high: int, duration: int, cuts: Sequence[int]
) -> list[tuple[int, int]]:
    """挪动区间内被切点禁止的起点下标跨度（含端点，相对于 ``low``）。

    起点 ``x`` 时字幕占半开区间 ``[x, x + duration)``；切点 ``c`` 落在内部
    当且仅当 ``x < c < x + duration``，即整点 ``x ∈ [c − duration + 1,
    c − 1]`` 禁止；边界重合（``x == c`` 或 ``x + duration == c``）允许。
    只有 ``c ∈ (low, high + duration)`` 的切点才可能禁止本区间内的起点。
    """
    spans: list[tuple[int, int]] = []
    for j in range(bisect_right(cuts, low), bisect_left(cuts, high + duration)):
        c = cuts[j]
        a = max(c - duration + 1, low) - low
        b = min(c - 1, high) - low
        if a <= b:
            spans.append((a, b))
    return spans


def _first_unschedulable(
    durations: list[int],
    lows: list[int],
    sizes: list[int],
    spans: list[list[tuple[int, int]]],
    min_gap_ms: int,
) -> int:
    """前向可行性扫描，返回首个使前缀不可行的字幕输入序号（从 1 开始）。

    调用方已通过后向 DP 确认整体无解；「前 k−1 条可安排、前 k 条不可安排」
    的第一个 k 即最先无法继续安排的字幕。
    """

    def valid_positions(i: int) -> list[int]:
        """第 i 条各起点位置是否可行（1 可行 / 0 被切点禁止）。"""
        mask = bytearray(b"\x01" * sizes[i])
        for a, b in spans[i]:
            mask[a : b + 1] = b"\x00" * (b - a + 1)
        return list(mask)

    prev = valid_positions(0)
    if not any(prev):
        return 1
    for i in range(1, len(spans)):
        prefix_any = list(accumulate(prev, max))
        step = durations[i - 1] + min_gap_ms
        # x − step < 下界时没有任何前驱；x − step ≥ 上界时所有前驱都可衔接
        j0 = lows[i] - step - lows[i - 1]
        reachable = _gather_window(prefix_any, j0, sizes[i], 0, prefix_any[-1])
        cur = [m & r for m, r in zip(valid_positions(i), reachable)]
        if not any(cur):
            return i + 1
        prev = cur
    raise AssertionError("整体无解时必存在首个不可行前缀。")  # pragma: no cover


def plan_avoidance(
    cues: list[Cue],
    cut_points_ms: Sequence[int],
    max_shift_ms: int,
    min_gap_ms: int,
) -> AvoidPlan:
    """在切点避让、最小间隔与挪动范围约束下求总挪动量最小的平移方案。

    ``cut_points_ms`` 为镜头切点（非负整数毫秒，自动排序去重）；
    ``max_shift_ms`` 为每条字幕起点允许的前后最大挪动量（0～2000 毫秒）；
    ``min_gap_ms`` 为相邻字幕最小间隔（非负整数毫秒）。无解时返回的
    :class:`AvoidPlan` 带最先无法继续安排的字幕输入序号。
    """
    if not 0 <= max_shift_ms <= MAX_SHIFT_LIMIT_MS:
        raise ShotCutError(
            f"最大挪动量必须是 0～{MAX_SHIFT_LIMIT_MS} 毫秒之间的整数。"
        )
    if min_gap_ms < 0:
        raise ShotCutError("最小字幕间隔必须是非负整数毫秒。")
    for pos, c in enumerate(cut_points_ms, start=1):
        if c < 0:
            raise ShotCutError(f"第 {pos} 个镜头切点必须是非负整数毫秒。")

    cuts = sorted(set(cut_points_ms))

    n = len(cues)
    starts = [cue.start for cue in cues]
    durations = [cue.end - cue.start for cue in cues]
    lows = [max(0, s - max_shift_ms) for s in starts]
    highs = [s + max_shift_ms for s in starts]
    sizes = [highs[i] - lows[i] + 1 for i in range(n)]
    spans = [
        _forbidden_spans(lows[i], highs[i], durations[i], cuts) for i in range(n)
    ]

    # 后向 DP：tables[i][k] = 第 i 条恰好落在 lows[i] + k 时，
    # 第 i～n 条的最小总挪动代价（不可行时 ≥ INF）
    tables: list[array] = [None] * n  # type: ignore[list-item]
    nxt: array | None = None
    for i in range(n - 1, -1, -1):
        size = sizes[i]
        dec = starts[i] - lows[i]
        inc = highs[i] - starts[i]
        if nxt is None:
            row = array("q", chain(range(dec, -1, -1), range(1, inc + 1)))
        else:
            suffix_min = list(accumulate(reversed(nxt), min))[::-1]
            if suffix_min[0] >= INF:
                # 后缀 i+1..n 已不可行，更早的表必然同样不可行，整体无解
                # （suffix_min[0] 即 min(tables[i+1])，无需再单独扫描）
                break
            # 下一条起点须 ≥ x + step：早于下一条下界时约束等价于其下界；
            # 晚于下一条上界则无处可放
            j0 = lows[i] + durations[i] + min_gap_ms - lows[i + 1]
            aligned = _gather_window(suffix_min, j0, size, suffix_min[0], INF)
            # |x − s_i| 在定义域上呈 V 形，两段 range 惰性拼出
            costs = chain(range(dec, -1, -1), range(1, inc + 1))
            row = array("q", map(add, aligned, costs))
        for a, b in spans[i]:
            row[a : b + 1] = _INF_ROW * (b - a + 1)
        tables[i] = row
        nxt = row

    total = min(tables[0]) if tables[0] is not None else INF
    if total >= INF:
        order = _first_unschedulable(durations, lows, sizes, spans, min_gap_ms)
        return AvoidPlan(
            feasible=False, starts=(), total_shift_ms=0,
            first_unschedulable_order=order,
        )

    # 正向贪心：剩余预算 = 后缀最小代价，逐条取预算允许的最小新起点，
    # 即同分方案中新起点序列字典序最小者
    chosen: list[int] = []
    remaining = total
    bound = lows[0]
    for i in range(n):
        table = tables[i]
        k = table.index(remaining, max(0, bound - lows[i]))
        x = lows[i] + k
        chosen.append(x)
        remaining -= abs(x - starts[i])
        bound = x + durations[i] + min_gap_ms

    return AvoidPlan(
        feasible=True,
        starts=tuple(chosen),
        total_shift_ms=total,
        first_unschedulable_order=None,
    )
