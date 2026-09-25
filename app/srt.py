"""SRT 解析与无损重写。

解析时仅提取校验所需的序号/时间码/文本；校正输出不走整块重建，而是通过
:func:`rewrite_srt` 直接替换原始文本中各时间轴行的起止时间码，序号、正文、
箭头空格、``X1:``/``Position`` 等定位参数、空行、原有换行（CRLF/LF/CR）以及
开头的 BOM 一律保持原样。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .timecode import TIMECODE_PATTERN, format_timecode, parse_timecode

BOM = "﻿"


class SrtParseError(ValueError):
    """SRT 内容无法解析。"""


@dataclass
class Cue:
    index: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class TimingRef:
    """一条时间轴行在物理行序列中的位置及两个时间码的匹配区间。"""

    line_no: int
    start_span: tuple[int, int]
    end_span: tuple[int, int]


@dataclass(frozen=True)
class BodyRange:
    """一条字幕正文在物理行序列中的半开行区间 ``[start_line, end_line)``。

    与 :class:`TimingRef` 一样，行号针对剥离开头 BOM 后、由
    :func:`split_physical_lines` 切出的物理行序列。
    """

    start_line: int
    end_line: int


@dataclass(frozen=True)
class ParsedDocument:
    cues: list[Cue]
    timing_refs: list[TimingRef]
    body_ranges: list[BodyRange]
    bom_count: int

    @property
    def has_bom(self) -> bool:
        return self.bom_count > 0


# 时间码行尾可能携带 X1:/Position 等排版信息，这里只取箭头两侧的时间码
_CUE_TIMING = re.compile(
    rf"^\s*({TIMECODE_PATTERN})\s*-->\s*({TIMECODE_PATTERN})(?:\s|$)"
)
_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_EOL_RE = re.compile(r"\r\n|\r|\n")


def split_physical_lines(text: str) -> list[str]:
    """按物理行拆分，保留每行原有的行尾（``\\r\\n`` / ``\\r`` / ``\\n``）。"""
    lines: list[str] = []
    pos = 0
    for match in _EOL_RE.finditer(text):
        lines.append(text[pos : match.end()])
        pos = match.end()
    if pos < len(text):
        lines.append(text[pos:])
    return lines


def _strip_eol(line: str) -> str:
    return _EOL_RE.sub("", line)


def _parse_document(content: str) -> ParsedDocument:
    """逐物理行扫描 SRT，同时记录每条时间轴行的位置供无损重写。"""
    if content is None or not content.strip():
        raise SrtParseError("内容为空。")

    # 统计并剥离开头连续的 BOM；重写时最多补回一个
    bom_count = 0
    while bom_count < len(content) and content[bom_count] == BOM:
        bom_count += 1
    text = content[bom_count:]

    lines = split_physical_lines(text)
    total = len(lines)
    cues: list[Cue] = []
    timing_refs: list[TimingRef] = []
    body_ranges: list[BodyRange] = []
    auto_index = 0
    block_no = 0

    i = 0
    while i < total:
        # 跳过块间空行（空串或仅含空白的行）
        if not _strip_eol(lines[i]).strip():
            i += 1
            continue

        block_no += 1
        block: list[str] = []
        while i < total and _strip_eol(lines[i]).strip():
            block.append(_strip_eol(lines[i]))
            i += 1

        # 兼容省略序号行的 SRT：首行是纯数字且下一行是时间轴才视作序号
        index: str | None = None
        cursor = 0
        if _INDEX_RE.match(block[0]) and len(block) >= 2 and _CUE_TIMING.match(block[1]):
            index = block[0].strip()
            cursor = 1
        elif _CUE_TIMING.match(block[0]):
            cursor = 0
        else:
            raise SrtParseError(
                f"第 {block_no} 个字幕块缺少有效的时间轴行，"
                "结构应为“序号 / 开始 --> 结束 / 字幕文本”。"
            )

        timing_line = block[cursor]
        timing = _CUE_TIMING.match(timing_line)
        assert timing is not None
        try:
            start = parse_timecode(timing.group(1))
            end = parse_timecode(timing.group(2))
        except ValueError as exc:
            raise SrtParseError(f"第 {block_no} 个字幕块的时间码无效：{exc}") from exc

        if end <= start:
            raise SrtParseError(
                f"第 {block_no} 个字幕块的结束时间必须严格晚于开始时间。"
            )

        body = "\n".join(block[cursor + 1 :])
        if not body.strip():
            raise SrtParseError(f"第 {block_no} 个字幕块缺少文本内容。")

        auto_index += 1
        cues.append(Cue(index=index or str(auto_index), start=start, end=end, text=body))

        # 时间轴行在整个物理行序列中的序号；同一块内它前面都是非空行，
        # 因此回退“正文行数 + 可选序号行”即可定位
        body_line_count = len(block) - cursor - 1
        timing_line_no = i - 1 - body_line_count
        timing_refs.append(
            TimingRef(
                line_no=timing_line_no,
                start_span=timing.span(1),
                end_span=timing.span(2),
            )
        )
        body_ranges.append(
            BodyRange(start_line=timing_line_no + 1, end_line=timing_line_no + 1 + body_line_count)
        )

    if not cues:
        raise SrtParseError("未解析到任何字幕，请粘贴有效的 SRT 内容。")
    return ParsedDocument(
        cues=cues, timing_refs=timing_refs, body_ranges=body_ranges, bom_count=bom_count
    )


def parse_document(content: str) -> ParsedDocument:
    """逐物理行解析 SRT，返回字幕、时间轴行引用、正文行区间与 BOM 信息。"""
    return _parse_document(content)


def parse_srt(content: str) -> list[Cue]:
    """把 SRT 文本解析为 Cue 列表；块结构为“序号 / 时间轴 / 一行或多行文本”。"""
    return _parse_document(content).cues


def split_line_eol(line: str) -> tuple[str, str]:
    """把单个物理行拆成 ``(正文, 行尾)``；行尾为 ``\\r\\n`` / ``\\r`` / ``\\n`` 或空串。"""
    match = _EOL_RE.search(line)
    if match is None:
        return line, ""
    return line[: match.start()], line[match.start() :]


def rewrite_srt(content: str, ranges: list[tuple[int, int]]) -> str:
    """无损重写：仅替换各时间轴行的起止时间码，其余字节级内容保持原样。

    ``ranges`` 顺序与 :func:`parse_srt` 解析出的字幕一一对应，元素为
    ``(新开始毫秒, 新结束毫秒)``。
    """
    doc = _parse_document(content)
    if len(ranges) != len(doc.cues):
        raise ValueError("时间轴替换数量与解析出的字幕条数不一致。")

    lines = split_physical_lines(content[doc.bom_count :])
    for ref, (new_start, new_end) in zip(doc.timing_refs, ranges):
        line = lines[ref.line_no]
        start_a, start_b = ref.start_span
        end_a, end_b = ref.end_span
        lines[ref.line_no] = (
            line[:start_a]
            + format_timecode(new_start)
            + line[start_b:end_a]
            + format_timecode(new_end)
            + line[end_b:]
        )

    rewritten = "".join(lines)
    # 原文件开头无论出现几个 BOM，输出最多保留一个
    return (BOM if doc.has_bom else "") + rewritten


def format_srt(cues: list[Cue]) -> str:
    """把 Cue 列表序列化为标准 SRT 文本（CRLF 统一为 \\n）。"""
    blocks = [
        f"{cue.index}\n"
        f"{format_timecode(cue.start)} --> {format_timecode(cue.end)}\n"
        f"{cue.text}"
        for cue in cues
    ]
    return "\n\n".join(blocks) + "\n"
