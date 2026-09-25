"""字幕主题短片段检索。

给定 SRT 与 1～8 组「关键词 + 所需出现字幕条数」，沿输入顺序寻找覆盖
各词所需条数的**最短连续字幕窗口**：

1. 关键词先去首尾空白，再做 Unicode ``casefold`` 比较（``Straße`` 与
   ``STRASSE`` 折叠后同为 ``strasse``）；折叠后为空词、或与更早输入的
   词组折叠后重复，均抛出 :class:`TopicClipError`；
2. 每条字幕正文同样 ``casefold`` 后做子串判定；**同一条字幕内每个词
   至多计一次**（出现多次也只 +1）；
3. 字幕开始时间必须按输入顺序**非递减**，否则指出先倒退的那条输入序号；
4. 滑动窗口：右边界扩张到可行后逐次收缩左边界，每次仍可行就记录——
   记录到的每个左边界都对应「该起点最早变得可行的右边界」，故全局
   最优解必在候选集合内；
5. 片段起点取窗口首条的开始时间，终点取窗口内**最晚结束**（结束时间
   不要求有序，用单调队列维护窗口最大值）；
6. 选解顺序：片段时长（最晚结束 − 首条开始）最短，其次起点最早，
   再其次窗口起始输入序号最早；
7. 全片任一关键词的命中条数不足时必然无解（反之全片窗口一定可行），
   返回无解与逐词缺少条数 ``max(0, 所需 − 全片命中)``。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .srt import Cue
from .timecode import format_timecode


class TopicClipError(ValueError):
    """关键词不合法，或字幕开始时间未按输入顺序非递减。"""


@dataclass(frozen=True)
class TopicKeyword:
    """一组检索条件：``word`` 为去首尾空白后的原写法（回显用），
    ``folded`` 为 casefold 后的比较形式。"""

    word: str
    folded: str
    required_count: int


@dataclass(frozen=True)
class WordHitStat:
    """窗口内单个关键词的命中字幕条数（每条字幕至多 1）。"""

    word: str
    required_count: int
    hit_count: int


@dataclass(frozen=True)
class WindowCue:
    """窗口内的一条字幕及其命中的关键词原写法。"""

    cue_order: int
    index: str
    start: int
    end: int
    text: str
    hit_words: tuple[str, ...]


@dataclass(frozen=True)
class TopicClip:
    """检索到的最短片段：连续字幕窗口 [cue_order_from, cue_order_to]。"""

    start: int
    end: int
    cue_order_from: int
    cue_order_to: int
    word_hits: tuple[WordHitStat, ...]
    cues: tuple[WindowCue, ...]


@dataclass(frozen=True)
class TopicClipResult:
    """检索结果；``found`` 为假时 ``clip`` 为 None，``totals`` 给出
    全片逐词命中条数，供调用方计算缺少条数。"""

    found: bool
    clip: TopicClip | None
    totals: tuple[int, ...]


def build_keyword_groups(
    groups: list[tuple[str, int]],
) -> tuple[TopicKeyword, ...]:
    """校验并构造关键词组：1～8 组、所需条数为正整数、折叠后非空且不重复。"""
    if not 1 <= len(groups) <= 8:
        raise TopicClipError("关键词必须为 1～8 组。")

    result: list[TopicKeyword] = []
    seen: dict[str, int] = {}  # 折叠形式 -> 最早输入的组号（1 基）
    for pos, (raw_word, required_count) in enumerate(groups, start=1):
        if not isinstance(raw_word, str):
            raise TopicClipError(f"第 {pos} 组关键词必须是字符串。")
        if not isinstance(required_count, int) or isinstance(required_count, bool):
            raise TopicClipError(f"第 {pos} 组关键词所需字幕条数必须是整数。")
        if required_count < 1:
            raise TopicClipError(f"第 {pos} 组关键词所需字幕条数必须为正整数。")

        word = raw_word.strip()
        folded = word.casefold()
        if not folded:
            raise TopicClipError(
                f"第 {pos} 组关键词为空（空串或仅含空白），请填写实际要检索的词。"
            )
        earlier = seen.get(folded)
        if earlier is not None:
            raise TopicClipError(
                f"第 {pos} 组关键词 {word!r} 折叠后与第 {earlier} 组重复"
                "（按 Unicode casefold 比较，不区分大小写），请删除重复词组。"
            )
        seen[folded] = pos
        result.append(TopicKeyword(word=word, folded=folded,
                                   required_count=required_count))
    return tuple(result)


def check_starts_monotonic(cues: list[Cue]) -> None:
    """校验字幕开始时间按输入顺序非递减；倒退时指出后一条的输入序号。"""
    for i in range(1, len(cues)):
        if cues[i].start < cues[i - 1].start:
            raise TopicClipError(
                f"字幕开始时间必须按输入顺序非递减：第 {i + 1} 条字幕"
                f"（SRT 序号 {cues[i].index}，开始 {format_timecode(cues[i].start)}）"
                f"早于第 {i} 条（SRT 序号 {cues[i - 1].index}，"
                f"开始 {format_timecode(cues[i - 1].start)}），请先调整第 {i + 1} 条。"
            )


def find_topic_clip(
    cues: list[Cue], groups: tuple[TopicKeyword, ...]
) -> TopicClipResult:
    """在 ``cues`` 上检索覆盖各词所需条数的最短连续字幕窗口。

    调用前应先通过 :func:`build_keyword_groups` 与
    :func:`check_starts_monotonic` 校验。
    """
    n = len(cues)
    m = len(groups)

    # 每条字幕逐词命中向量：折叠后子串包含即命中，同一条字幕每词至多一次
    folded_texts = [cue.text.casefold() for cue in cues]
    hits = [[g.folded in text for g in groups] for text in folded_texts]
    totals = tuple(
        sum(1 for row in hits if row[k]) for k in range(m)
    )
    # 任一关键词全片不足则必无解；反之全片窗口必可行
    if any(totals[k] < groups[k].required_count for k in range(m)):
        return TopicClipResult(found=False, clip=None, totals=totals)

    needs = [g.required_count for g in groups]
    counts = [0] * m
    satisfied = 0
    left = 0
    # 单调递减队列：队首下标始终是当前窗口内 end 最大的字幕
    end_queue: deque[int] = deque()
    # 最优候选键：(片段时长, 起点毫秒, 窗口起始输入序号)；另记录左右下标
    best_key: tuple[int, int, int] | None = None
    best_lr: tuple[int, int] | None = None

    for r in range(n):
        for k in range(m):
            if hits[r][k]:
                before = counts[k]
                counts[k] = before + 1
                if before < needs[k] <= before + 1:
                    satisfied += 1

        while end_queue and cues[end_queue[-1]].end <= cues[r].end:
            end_queue.pop()
        end_queue.append(r)

        # 可行后逐次收缩左边界：记录到的 (left, r) 恰为「left 起点最早
        # 变可行的右边界」，全局最优必在其中
        while satisfied == m and left <= r:
            window_end = cues[end_queue[0]].end
            key = (window_end - cues[left].start, cues[left].start, left + 1)
            if best_key is None or key < best_key:
                best_key = key
                best_lr = (left, r)

            for k in range(m):
                if hits[left][k]:
                    before = counts[k]
                    counts[k] = before - 1
                    if before >= needs[k] > before - 1:
                        satisfied -= 1
            if end_queue[0] == left:
                end_queue.popleft()
            left += 1

    assert best_lr is not None  # 全片充足时必有可行窗口
    l, r = best_lr
    window_end = max(cues[i].end for i in range(l, r + 1))
    word_hits = tuple(
        WordHitStat(
            word=groups[k].word,
            required_count=needs[k],
            hit_count=sum(1 for i in range(l, r + 1) if hits[i][k]),
        )
        for k in range(m)
    )
    window_cues = tuple(
        WindowCue(
            cue_order=i + 1,
            index=cues[i].index,
            start=cues[i].start,
            end=cues[i].end,
            text=cues[i].text,
            hit_words=tuple(groups[k].word for k in range(m) if hits[i][k]),
        )
        for i in range(l, r + 1)
    )
    clip = TopicClip(
        start=cues[l].start,
        end=window_end,
        cue_order_from=l + 1,
        cue_order_to=r + 1,
        word_hits=word_hits,
        cues=window_cues,
    )
    return TopicClipResult(found=True, clip=clip, totals=totals)
