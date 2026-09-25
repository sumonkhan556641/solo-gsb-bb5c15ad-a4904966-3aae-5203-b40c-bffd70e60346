"""锚点校验与分段线性时间映射。

规则：
* 相邻锚点之间对字幕时间做线性插值；
* 早于第一个锚点 / 晚于最后一个锚点的时间，使用最近一个区段的斜率外推；
* 锚点两侧时间都必须严格递增，否则拒绝生成并给出原因。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass

from .timecode import parse_timecode


class AnchorError(ValueError):
    """锚点不满足生成校正映射的条件。"""


@dataclass(frozen=True)
class Anchor:
    original_ms: int
    corrected_ms: int
    original_text: str
    corrected_text: str


def build_anchors(pairs: list[tuple[str, str]]) -> list[Anchor]:
    """把“原时间 / 正确时间”文本对转为毫秒锚点并严格校验递增性。"""
    if len(pairs) < 2:
        raise AnchorError(
            f"至少需要 2 组锚点才能确定分段线性映射（当前只有 {len(pairs)} 组）。"
        )

    anchors: list[Anchor] = []
    for pos, (original, corrected) in enumerate(pairs, start=1):
        try:
            original_ms = parse_timecode(original)
        except ValueError as exc:
            raise AnchorError(f"第 {pos} 组锚点的原时间无效：{exc}") from exc
        try:
            corrected_ms = parse_timecode(corrected)
        except ValueError as exc:
            raise AnchorError(f"第 {pos} 组锚点的正确时间无效：{exc}") from exc

        anchors.append(
            Anchor(
                original_ms=original_ms,
                corrected_ms=corrected_ms,
                original_text=str(original).strip(),
                corrected_text=str(corrected).strip(),
            )
        )

    # 两侧都必须严格递增；不自动排序，直接拒绝并说明原因
    for i in range(1, len(anchors)):
        prev, cur = anchors[i - 1], anchors[i]
        if cur.original_ms <= prev.original_ms:
            raise AnchorError(
                f"第 {i} 与第 {i + 1} 组锚点的原时间未严格递增："
                f"{prev.original_text} → {cur.original_text}。"
                "请按原时间从小到大排列锚点，且时间不得相等。"
            )
        if cur.corrected_ms <= prev.corrected_ms:
            raise AnchorError(
                f"第 {i} 与第 {i + 1} 组锚点的正确时间未严格递增："
                f"{prev.corrected_text} → {cur.corrected_text}。"
                "正确时间也必须随原时间严格递增且不能相等，请核对人工锚点。"
            )

    return anchors


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


@dataclass
class PiecewiseMapping:
    """分段线性映射：锚点间插值，锚点范围外用最近区段斜率外推。"""

    anchors: list[Anchor]

    def __post_init__(self) -> None:
        self._xs = [a.original_ms for a in self.anchors]
        self._ys = [a.corrected_ms for a in self.anchors]

    @staticmethod
    def _linear(ms: int, x0: int, x1: int, y0: int, y1: int) -> int:
        return _round_half_up(y0 + (ms - x0) * (y1 - y0) / (x1 - x0))

    def map_ms(self, ms: int) -> int:
        """把原时间轴上的毫秒值映射到校正后的毫秒值。"""
        xs, ys = self._xs, self._ys

        # 左端外推：沿用第一区段斜率
        if ms <= xs[0]:
            return self._linear(ms, xs[0], xs[1], ys[0], ys[1])

        # 右端外推：沿用最后区段斜率
        if ms >= xs[-1]:
            return self._linear(ms, xs[-2], xs[-1], ys[-2], ys[-1])

        # 锚点之间：找到所属区段并线性插值
        i = bisect_right(xs, ms) - 1
        if ms == xs[i]:
            return ys[i]
        return self._linear(ms, xs[i], xs[i + 1], ys[i], ys[i + 1])
