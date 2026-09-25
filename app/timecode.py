"""SRT 时间码解析与格式化，内部统一使用毫秒整数。"""
from __future__ import annotations

import re

# HH:MM:SS,mmm；毫秒分隔符同时接受逗号与句点，毫秒允许 1~3 位
_TIMECODE_RE = re.compile(r"^\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*$")

# 供 SRT 时间轴行复用的时间码片段
TIMECODE_PATTERN = r"\d+:\d{1,2}:\d{1,2}[,.]\d{1,3}"


def parse_timecode(value: str) -> int:
    """把 ``HH:MM:SS,mmm`` 解析为毫秒。"""
    if value is None or not str(value).strip():
        raise ValueError("时间不能为空，格式应为 HH:MM:SS,mmm，例如 00:01:23,456。")

    match = _TIMECODE_RE.match(str(value))
    if match is None:
        raise ValueError(
            f"无法识别 {str(value).strip()!r}，格式应为 HH:MM:SS,mmm，例如 00:01:23,456。"
        )

    hours, minutes, seconds, millis = match.groups()
    minutes_i, seconds_i = int(minutes), int(seconds)
    if minutes_i >= 60 or seconds_i >= 60:
        raise ValueError(f"{str(value).strip()!r} 中的分钟或秒超出了 0–59 的范围。")

    milliseconds = int(millis.ljust(3, "0"))
    return ((int(hours) * 60 + minutes_i) * 60 + seconds_i) * 1000 + milliseconds


def format_timecode(ms: int) -> str:
    """把毫秒格式化为 SRT 时间码 ``HH:MM:SS,mmm``（负值按 0 处理）。"""
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, milliseconds = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def format_signed_timecode(ms: int) -> str:
    """格式化毫秒，允许负号显示（用于提示外推算出的负时间）。"""
    if ms >= 0:
        return format_timecode(ms)
    return "-" + format_timecode(-ms)


def format_vtt_timecode(ms: int) -> str:
    """把毫秒格式化为 WebVTT 时间码 ``HH:MM:SS.mmm``（负值按 0 处理）。"""
    return format_timecode(ms).replace(",", ".")
