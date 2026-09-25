"""重叠字幕择优保留。

从输入 SRT 的字幕序列中选出一个子序列（原时间轴不移动），使任意相邻的两条
保留字幕都满足「前条结束 + 最小间隔 ≤ 后条开始」（首尾相接且间隔不足时视为
冲突），在必须保留条目约束下：

1. **最大化保留条目的优先级总和**（每条优先级为正整数，未设置为 1）；
2. 总和相同时**保留条数更多**者优先；
3. 仍相同时取**输入序号序列字典序更小**的方案。

实现要点是把三级目标编码进同一个大整数做加权区间调度：

* 第 i 条（0 基）被选中时贡献字典序位权 ``2 ** (n - 1 - i)``——保持集合下
  「保留序号序列字典序小」恰等价于 0/1 指示串字典序大；
* 每条再贡献 ``2 ** n``，使低位（取值不超过 ``2 ** n - 1``）不可能翻转条数
  比较；
* 再叠加 ``(n + 1) * 2 ** n`` 乘以优先级——条数项与字典序项合计不超过
  ``(n + 1) * 2 ** n - 1``，不可能翻转优先级比较。

这样目标退化为普通的**最大权区间调度**：按输入顺序处理、以结束时间维护
Fenwick 前缀最大值即可在 ``O(n log n)`` 内完成。

必须保留的条目把序列切成若干段（首段左边界、末段右边界、中间前后必保条目），
段与段之间的边界相容性只取决于相邻的必保条目；必保条目本身的贡献是常数，
各段独立求最优后合并即为全局最优（目标函数对段相加，段间没有取舍耦合）。
相邻必保条目不相容时无法构造任何可行解，:func:`select_cues` 原样返回这些
冲突序号对，不抛出异常；参数越界 / 重复则抛出 :class:`SelectionError`。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .srt import Cue

# Fenwick 回溯标签：无前驱（子序列直接以该条开头，或末段选中空集）
_BASE_TAG = -2


class SelectionError(ValueError):
    """优先级或必须保留序号等参数不合法。"""


@dataclass(frozen=True)
class ConflictPair:
    """一对相邻（按输入序号）且彼此不相容的必须保留条目（序号从 1 开始）。"""

    first: int
    second: int


@dataclass(frozen=True)
class SelectionPlan:
    """择优结果。

    ``kept_orders`` 为保留条目的输入序号（从 1 开始，升序）；
    ``total_priority`` 为保留条目优先级之和。
    """

    kept_orders: tuple[int, ...]
    total_priority: int


def select_cues(
    cues: list[Cue],
    priorities: dict[int, int],
    required_orders: list[int],
    min_gap_ms: int,
) -> SelectionPlan | tuple[ConflictPair, ...]:
    """在间隔与必须保留约束下求优先级总和最大的保留子序列。

    ``priorities`` 的键为字幕输入序号（从 1 开始，与 SRT 自身序号无关），
    值为正整数，未出现者按 1 计；``required_orders`` 为必须保留的输入序号。
    必须保留条目彼此不相容时，返回全部相邻冲突对 :class:`ConflictPair`。
    """
    n = len(cues)
    if min_gap_ms < 0:
        raise SelectionError("最小间隔毫秒数必须是非负整数。")

    for order, priority in priorities.items():
        if not 1 <= order <= n:
            raise SelectionError(
                f"优先级序号 {order} 超出范围：字幕共 {n} 条，序号须在 1～{n} 之间。"
            )
        if priority < 1:
            raise SelectionError(f"序号 {order} 的优先级必须是正整数。")

    required = sorted(set(required_orders))
    for order in required:
        if not 1 <= order <= n:
            raise SelectionError(
                f"必须保留序号 {order} 超出范围：字幕共 {n} 条，序号须在 1～{n} 之间。"
            )

    starts = [cue.start for cue in cues]
    ends = [cue.end for cue in cues]

    def compatible(prev_order: int, next_order: int) -> bool:
        return ends[prev_order - 1] + min_gap_ms <= starts[next_order - 1]

    # 相邻必保条目不相容即不存在可行子序列；逐对返回冲突序号
    conflicts = tuple(
        ConflictPair(first=a, second=b)
        for a, b in zip(required, required[1:])
        if not compatible(a, b)
    )
    if conflicts:
        return conflicts

    # 三级目标的整数编码（位权只对非必保条目计入；必保贡献为跨方案常数）
    bit_base = 1 << n
    count_unit = bit_base            # 每条的条数项 = 2**n
    priority_unit = (n + 1) * bit_base

    def solve_segment(l: int, r: int, left_order: int | None) -> tuple[int, ...]:
        """求位置区间 ``[l, r)`` 内可选条目的最优子集（输入序号，1 基升序）。

        子序列首条须与左边界必保条目 ``left_order``（1 基；``None`` 表示
        左边界在负无穷）相容；右边界相容性由调用方在选末态时判定。
        """
        if l >= r:
            return ()

        boundary_end = ends[left_order - 1] if left_order is not None else None

        # 结束时间压缩坐标：Fenwick 维护“结束时间 ≤ 阈值”的最优前驱状态
        end_values = sorted({ends[p] for p in range(l, r)})
        size = len(end_values)
        bit_value: list[int] = [0] * (size + 1)
        bit_tag: list[int] = [-1] * (size + 1)  # -1: 该节点尚无状态
        # 每个可达位置 p 的父标签：_BASE_TAG（无前驱）或更早的位置
        parent: dict[int, int] = {}
        state_value: dict[int, int] = {}

        def query(threshold: int) -> tuple[int, int]:
            """结束时间 ≤ threshold 的最优 (权值, 标签)；没有则 (0, 基态)。"""
            k = bisect_right(end_values, threshold)
            best_value = 0
            best_tag = _BASE_TAG
            while k > 0:
                tag = bit_tag[k]
                if tag != -1 and bit_value[k] > best_value:
                    best_value = bit_value[k]
                    best_tag = tag
                k -= k & -k
            return best_value, best_tag

        def update(rank0: int, value: int, tag: int) -> None:
            k = rank0 + 1
            while k <= size:
                if value > bit_value[k]:
                    bit_value[k] = value
                    bit_tag[k] = tag
                k += k & -k

        final_value = 0
        final_tag: int = _BASE_TAG

        for p in range(l, r):
            # 前驱状态：左边界必保条目之后、且结束 + 间隔 ≤ 本条开始
            threshold = starts[p] - min_gap_ms
            if boundary_end is not None and boundary_end > threshold:
                continue  # 无法接在左边界必保条目之后，本条不可达
            base_value, base_tag = query(threshold)
            value = (
                base_value
                + priority_unit * priorities.get(p + 1, 1)
                + count_unit
                + (1 << (n - 1 - p))
            )
            parent[p] = base_tag
            state_value[p] = value
            update(bisect_right(end_values, ends[p]) - 1, value, p)

        # 末态：可选空集（权值 0），也可结束于与右边界相容的任一状态
        right_required = r < n  # r 为下一个必保条目位置（0 基）
        for p, value in state_value.items():
            if right_required and ends[p] + min_gap_ms > starts[r]:
                continue
            if value > final_value:
                final_value = value
                final_tag = p

        if final_tag == _BASE_TAG:
            return ()
        chosen: list[int] = []
        tag: int = final_tag
        while tag != _BASE_TAG:
            chosen.append(tag + 1)  # 0 基位置转回输入序号
            tag = parent[tag]
        chosen.reverse()
        return tuple(chosen)

    # 必保条目把序列切段：(-inf, r1), (r1, r2), ..., (rk, +inf)
    kept: list[int] = []
    l = 0
    left_order: int | None = None
    for order in required:
        r = order - 1  # 0 基必保位置；段区间为 [l, r)
        kept.extend(solve_segment(l, r, left_order))
        kept.append(order)
        l = r + 1
        left_order = order
    kept.extend(solve_segment(l, n, left_order))

    total_priority = sum(priorities.get(order, 1) for order in kept)
    return SelectionPlan(kept_orders=tuple(kept), total_priority=total_priority)
