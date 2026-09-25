"""双语字幕配对（单调动态规划）。

按输入顺序把左右两份字幕配成一组组对照：

1. 允许五种操作：1:1、1:2（左一右二）、2:1（左二右一）配对，以及跳过左、
   跳过右（单侧字幕不配对）；操作只向前推进、不交叉，保持两侧输入顺序
   （单调配对）；
2. 配对代价 = 两组时间跨度（组内首条开始 → 末条结束）的起止差绝对值之和，
   即 ``|左组开始 − 右组开始| + |左组结束 − 右组结束|``；合并时每多一条
   （1:2 / 2:1 即多一条）加 ``MERGE_PENALTY_MS`` 毫秒；
3. 跳过单侧一条字幕加 ``SKIP_PENALTY_MS`` 毫秒；
4. 总代价相同时，按从头到尾的操作序列依次优先选择 1:1、1:2、2:1、
   跳过左、跳过右（即操作优先级序列字典序最小者）。

动态规划从后往前填表：``cost[i][j]`` 表示左字幕第 i 条起、右字幕第 j 条起
（均为 0 基）这段后缀的最小代价。同一格出发、操作相同必到达同一后继格，
因此逐格按「(总代价, 操作优先级)」取最小，即等价于全局「总代价相同取操作
序列字典序最小」，每格只需保存最优代价与所选操作，O(n·m) 求解。
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass

from .srt import Cue

MERGE_PENALTY_MS = 500  # 配对组内每多一条字幕（1:2 / 2:1）的合并代价
SKIP_PENALTY_MS = 3000  # 单侧跳过一条字幕的代价

# 操作优先级（同分裁决，数值越小越优先）
_OP_PAIR_11 = 0
_OP_PAIR_12 = 1
_OP_PAIR_21 = 2
_OP_SKIP_LEFT = 3
_OP_SKIP_RIGHT = 4


@dataclass(frozen=True)
class AlignGroup:
    """一次配对：左侧连续若干条与右侧连续若干条字幕（1:1 / 1:2 / 2:1）。

    ``left_from`` / ``left_to``、``right_from`` / ``right_to`` 为两侧字幕
    的 0 基下标闭区间；``step`` 为该操作在完整操作序列中从 1 开始的位置；
    ``cost`` = ``start_diff_ms`` + ``end_diff_ms`` + ``merge_penalty_ms``。
    """

    step: int
    kind: str  # "1:1" / "1:2" / "2:1"
    left_from: int
    left_to: int
    right_from: int
    right_to: int
    start_diff_ms: int
    end_diff_ms: int
    merge_penalty_ms: int
    cost: int


@dataclass(frozen=True)
class SkippedCue:
    """一条被跳过的单侧字幕；``cue_index`` 为该侧 0 基下标。"""

    step: int
    cue_index: int
    skip_cost_ms: int


@dataclass(frozen=True)
class AlignResult:
    groups: tuple[AlignGroup, ...]
    skipped_left: tuple[SkippedCue, ...]
    skipped_right: tuple[SkippedCue, ...]
    pair_cost: int  # 各配对组代价之和
    skip_cost: int  # 各跳过代价之和 = SKIP_PENALTY_MS × 跳过条数
    total_cost: int


def align_bilingual(left: list[Cue], right: list[Cue]) -> AlignResult:
    """把左右两份字幕（各自保持输入顺序）做单调配对，返回总代价最小的方案。

    组跨度取组内首条开始 → 末条结束；总代价相同按操作序列字典序（1:1、
    1:2、2:1、跳过左、跳过右）取最小者。
    """
    n, m = len(left), len(right)
    ls = [cue.start for cue in left]
    le = [cue.end for cue in left]
    rs = [cue.start for cue in right]
    re_ = [cue.end for cue in right]

    width = m + 1
    cost = [array("q", [0]) * width for _ in range(n + 1)]
    choice = [bytearray(width) for _ in range(n + 1)]

    for i in range(n, -1, -1):
        row = cost[i]
        pick = choice[i]
        nxt1 = cost[i + 1] if i < n else None
        nxt2 = cost[i + 2] if i + 1 < n else None
        li_s = ls[i] if i < n else 0
        li_e = le[i] if i < n else 0
        li1_e = le[i + 1] if i + 1 < n else 0
        for j in range(m, -1, -1):
            if i == n and j == m:
                continue
            # 按优先级顺序评估各操作，仅在严格更小时替换，
            # 代价相同即保留优先级更高的操作
            best_cost = -1
            best_op = _OP_SKIP_RIGHT
            if i < n and j < m:
                best_cost = abs(li_s - rs[j]) + abs(li_e - re_[j]) + nxt1[j + 1]
                best_op = _OP_PAIR_11
            if i < n and j + 1 < m:
                cand = (
                    abs(li_s - rs[j])
                    + abs(li_e - re_[j + 1])
                    + MERGE_PENALTY_MS
                    + nxt1[j + 2]
                )
                if best_cost < 0 or cand < best_cost:
                    best_cost, best_op = cand, _OP_PAIR_12
            if i + 1 < n and j < m:
                cand = (
                    abs(li_s - rs[j])
                    + abs(li1_e - re_[j])
                    + MERGE_PENALTY_MS
                    + nxt2[j + 1]
                )
                if best_cost < 0 or cand < best_cost:
                    best_cost, best_op = cand, _OP_PAIR_21
            if i < n:
                cand = SKIP_PENALTY_MS + nxt1[j]
                if best_cost < 0 or cand < best_cost:
                    best_cost, best_op = cand, _OP_SKIP_LEFT
            if j < m:
                cand = SKIP_PENALTY_MS + row[j + 1]
                if best_cost < 0 or cand < best_cost:
                    best_cost, best_op = cand, _OP_SKIP_RIGHT
            row[j] = best_cost
            pick[j] = best_op

    # 从 (0, 0) 沿各格记录的最优操作回放出完整操作序列
    groups: list[AlignGroup] = []
    skipped_left: list[SkippedCue] = []
    skipped_right: list[SkippedCue] = []
    i = j = 0
    step = 0
    while i < n or j < m:
        step += 1
        op = choice[i][j]
        if op == _OP_PAIR_11:
            start_diff = abs(ls[i] - rs[j])
            end_diff = abs(le[i] - re_[j])
            groups.append(
                AlignGroup(
                    step=step, kind="1:1",
                    left_from=i, left_to=i, right_from=j, right_to=j,
                    start_diff_ms=start_diff, end_diff_ms=end_diff,
                    merge_penalty_ms=0, cost=start_diff + end_diff,
                )
            )
            i += 1
            j += 1
        elif op == _OP_PAIR_12:
            start_diff = abs(ls[i] - rs[j])
            end_diff = abs(le[i] - re_[j + 1])
            groups.append(
                AlignGroup(
                    step=step, kind="1:2",
                    left_from=i, left_to=i, right_from=j, right_to=j + 1,
                    start_diff_ms=start_diff, end_diff_ms=end_diff,
                    merge_penalty_ms=MERGE_PENALTY_MS,
                    cost=start_diff + end_diff + MERGE_PENALTY_MS,
                )
            )
            i += 1
            j += 2
        elif op == _OP_PAIR_21:
            start_diff = abs(ls[i] - rs[j])
            end_diff = abs(le[i + 1] - re_[j])
            groups.append(
                AlignGroup(
                    step=step, kind="2:1",
                    left_from=i, left_to=i + 1, right_from=j, right_to=j,
                    start_diff_ms=start_diff, end_diff_ms=end_diff,
                    merge_penalty_ms=MERGE_PENALTY_MS,
                    cost=start_diff + end_diff + MERGE_PENALTY_MS,
                )
            )
            i += 2
            j += 1
        elif op == _OP_SKIP_LEFT:
            skipped_left.append(SkippedCue(step=step, cue_index=i, skip_cost_ms=SKIP_PENALTY_MS))
            i += 1
        else:
            skipped_right.append(SkippedCue(step=step, cue_index=j, skip_cost_ms=SKIP_PENALTY_MS))
            j += 1

    pair_cost = sum(g.cost for g in groups)
    skip_cost = SKIP_PENALTY_MS * (len(skipped_left) + len(skipped_right))
    return AlignResult(
        groups=tuple(groups),
        skipped_left=tuple(skipped_left),
        skipped_right=tuple(skipped_right),
        pair_cost=pair_cost,
        skip_cost=skip_cost,
        total_cost=pair_cost + skip_cost,
    )
