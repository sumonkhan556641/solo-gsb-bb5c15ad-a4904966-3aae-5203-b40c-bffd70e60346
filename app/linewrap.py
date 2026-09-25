"""字幕智能断行。

对每条字幕正文：

1. 原有换行与连续空白（含制表符、全角空格等 Unicode 空白）统一折叠为单个
   半角空格，并去掉首尾空白；
2. 可断位置 = 空格，或汉字 / 中文标点与任意字符相邻处；**英文单词内部不拆**；
3. 显示列宽：东亚宽（``W``）/ 全角（``F``）字符计 2 列，规范组合类非 0 的
   组合字符（combining mark）计 0 列，其余可见字符计 1 列；空格计 1 列；
4. 禁止行首为闭标点（如 ``）。」』”，、``）、行尾为开标点（如 ``（「『“《``）；
5. 动态规划在「行数 ≤ 上限、每行宽度 ≤ 最大列数」的可行断法中最小化
   各行剩余列数的平方和；总代价相同时按断点序列字典序靠前者
   （第一个断点尽量靠前，再依次比较后续断点）；
6. 排不下的字幕不改正文，只返回其输入顺序号与原因。

重写直接在原始物理行上进行：只替换成功字幕的正文行，序号、时间轴、
``X1:``/``Position`` 等定位参数、块间空行、原有换行（CRLF/LF/CR）以及开头
BOM 均保持原样（与 :mod:`app.terms` 同一约定：无任何改动时逐字符原样返回，
有改动时多个开头 BOM 最多保留一个）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .srt import BOM, parse_document, split_line_eol, split_physical_lines

# ---------------------------------------------------------------------------
# 字符分类
# ---------------------------------------------------------------------------

# 汉字（含部首 / 兼容表意文字 / 各扩展区；BMP 外按码位区间判定）
_HAN_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2EFF),  # CJK 部首补充
    (0x2F00, 0x2FDF),  # 康熙部首
    (0x3400, 0x4DBF),  # CJK 统一表意文字扩展 A
    (0x4E00, 0x9FFF),  # CJK 统一表意文字
    (0xF900, 0xFAFF),  # CJK 兼容表意文字
    (0x20000, 0x2A6DF),  # 扩展 B
    (0x2A700, 0x2EBEF),  # 扩展 C / D / E / F
    (0x30000, 0x323AF),  # 扩展 G / H
)

# 中文标点所在区块：CJK 符号与标点、全角 ASCII 变体（FF01–FF60）
_CJK_PUNCT_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),
    (0xFF00, 0xFF60),
)

# 分散在一般标点区、中文排版常用的标点（间隔号 / 破折号 / 弯引号 / 省略号）
_CJK_PUNCT_EXTRA: frozenset[str] = frozenset(
    "·—―‘’“”…"
)

# 开标点：不得出现在行尾
_OPEN_PUNCT: frozenset[str] = frozenset(
    "([{（［｛【《〈「『〔〖〘〝｟"
    "‘“'\"＇＂"
)

# 闭标点：不得出现在行首（含中点、省略号、破折号等“后置类”避头字符）
_CLOSE_PUNCT: frozenset[str] = frozenset(
    ")]}）］｝】》〉」』〕〗〙〞｠"
    "’”'\"＇％"
    "，。、；：！？．・…·—―～､｡･"
    ",.;:!?%"
)

_WS_RE = re.compile(r"\s+")


def _in_ranges(ch: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in ranges)


def is_han(ch: str) -> bool:
    """是否为汉字（CJK 表意文字 / 部首）。"""
    return _in_ranges(ch, _HAN_RANGES)


def is_cjk_punct(ch: str) -> bool:
    """是否为中文标点（CJK 标点区块、全角标点或常用中文标点）。"""
    return ch in _CJK_PUNCT_EXTRA or _in_ranges(ch, _CJK_PUNCT_RANGES)


def is_cjk_char(ch: str) -> bool:
    """汉字或中文标点——其相邻处即可断行。"""
    return is_han(ch) or is_cjk_punct(ch)


def char_display_width(ch: str) -> int:
    """单个字符的显示列数：组合字符 0、东亚宽 / 全角 2、其余 1。"""
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def normalize_text(text: str) -> str:
    """把原有换行与连续空白折叠为单个半角空格，并去除首尾空白。"""
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# 原子切分
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Atom:
    """不可拆片段：单个汉字 / 中文标点，或一个英文单词（含其内部标点）。"""

    text: str
    width: int
    cjk: bool


def _atom_width(text: str) -> int:
    return sum(char_display_width(ch) for ch in text)


def build_atoms(text: str) -> tuple[list[Atom], list[bool]]:
    """把归一化正文切成原子序列。

    :returns: ``(atoms, gap_space)``；``gap_space[i]`` 表示原子 ``i`` 与
        ``i+1`` 之间是否隔着一个（折叠后的）空格。归一化后空格不可能连续、
        也不会出现在首尾。
    """
    # 先按 (kind, payload) 扫描：c=单个 CJK 字符，w=英文连续片段，s=空格
    pieces: list[tuple[str, str]] = []
    run: list[str] = []

    def flush_run() -> None:
        if run:
            pieces.append(("w", "".join(run)))
            run.clear()

    for ch in text:
        if ch == " ":
            flush_run()
            pieces.append(("s", " "))
        elif unicodedata.combining(ch):
            # 组合字符附加到前一个原子上（英文单词同样不拆）；
            # 出现在开头 / 空格之后时并入下一个连续片段
            if pieces and pieces[-1][0] != "s":
                kind, value = pieces[-1]
                pieces[-1] = (kind, value + ch)
            else:
                run.append(ch)
        elif is_cjk_char(ch):
            flush_run()
            pieces.append(("c", ch))
        else:
            run.append(ch)
    flush_run()

    atoms: list[Atom] = []
    gap_space: list[bool] = []
    pending_space = False
    for kind, value in pieces:
        if kind == "s":
            pending_space = True
            continue
        if atoms:
            gap_space.append(pending_space)
        atoms.append(
            Atom(text=value, width=_atom_width(value), cjk=kind == "c")
        )
        pending_space = False
    return atoms, gap_space


# ---------------------------------------------------------------------------
# 动态规划断行
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WrappedLine:
    text: str
    width: int


@dataclass(frozen=True)
class WrapPlan:
    lines: tuple[WrappedLine, ...]
    breaks: tuple[int, ...]


@dataclass(frozen=True)
class WrapFailure:
    reason: str


def _join_line(atoms: list[Atom], gap_space: list[bool], lo: int, hi: int) -> str:
    """拼接原子 ``[lo, hi)`` 为一行：未断开的空格保留。"""
    parts = [atoms[lo].text]
    for i in range(lo + 1, hi):
        if gap_space[i - 1]:
            parts.append(" ")
        parts.append(atoms[i].text)
    return "".join(parts)


def _greedy_line_count(
    atoms: list[Atom],
    gap_space: list[bool],
    max_columns: int,
) -> int:
    """列宽约束下的贪心最少行数（忽略标点禁则，仅用于失败原因）。

    原子化后任意间隙都可断（连续非 CJK 字符已并入同一英文单词原子），
    因此这里只需贪心塞满；单词超宽已由上层提前判出。在空格处换行时
    该空格不进入任何一行。
    """
    line_count = 1
    used = atoms[0].width
    for i in range(1, len(atoms)):
        added = atoms[i].width + (1 if gap_space[i - 1] else 0)
        if used + added > max_columns:
            line_count += 1
            used = atoms[i].width
        else:
            used += added
    return line_count


def plan_wrap(text: str, max_columns: int, max_lines: int) -> WrapPlan | WrapFailure:
    """对单条字幕正文做智能断行。

    返回 :class:`WrapPlan`，或在列数 / 行数 / 标点限制无法满足时返回
    :class:`WrapFailure`（原因文本含具体说明，供接口原样回传）。
    """
    normalized = normalize_text(text)
    atoms, gap_space = build_atoms(normalized)
    n = len(atoms)
    if n == 0:
        # 正常经由 SRT 解析器不会发生（空正文已被拒绝），这里仅作防御
        return WrapFailure("正文为空（仅含空白）。")

    # 归一化结果与原文一致时，单行排得下即原样返回（不“为断而断”）：
    # 原本单行且无需改动的字幕因此保持 changed=False
    identity = normalized == text

    # 可断位置：空格两侧，或相邻两侧至少一边是汉字 / 中文标点
    breakable = [
        gap_space[i] or atoms[i].cjk or atoms[i + 1].cjk
        for i in range(n - 1)
    ]

    # 失败原因 1：单个不可拆原子本身超宽（超长英文单词等）
    for atom in atoms:
        if atom.width > max_columns:
            return WrapFailure(
                f"存在宽度 {atom.width} 列、不可断开的片段"
                f"（如超长英文单词），超过每行最大 {max_columns} 列。"
            )

    # 失败原因 2：放宽标点限制仍超过行数上限
    greedy_lines = _greedy_line_count(atoms, gap_space, max_columns)
    if greedy_lines > max_lines:
        return WrapFailure(
            f"在每行最多 {max_columns} 列时至少需要约 {greedy_lines} 行，"
            f"超过每条最多行数 {max_lines} 的限制。"
        )

    # 前缀宽度（含原子之间未断开的空格），便于 O(1) 求段宽
    prefix = [0]
    for i, atom in enumerate(atoms):
        width = atom.width + (1 if i > 0 and gap_space[i - 1] else 0)
        prefix.append(prefix[-1] + width)

    def segment_width(lo: int, hi: int) -> int:
        """原子区间 [lo, hi) 的宽度，并扣除被排除在段首之外的那个空格。"""
        width = prefix[hi] - prefix[lo]
        if lo > 0 and gap_space[lo - 1]:
            width -= 1
        return width

    # dp：按行数分层。prev[i] = 用恰好 k-1 行排好前 i 个原子时的
    # (代价, 断点序列)；断点记录第 2..k 行的首原子下标，天然按位置递增，
    # 元组比较即“断点序列字典序靠前优先”
    prev: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    best: tuple[int, tuple[int, ...]] | None = None

    for line_no in range(1, max_lines + 1):
        cur: dict[int, tuple[int, tuple[int, ...]]] = {}
        for start, (base_cost, base_breaks) in prev.items():
            if start >= n:
                continue
            for end in range(start + 1, n + 1):
                width = segment_width(start, end)
                if width > max_columns:
                    break
                # 后续行的起点必须是可断位置，且行首不得为闭标点
                if end < n:
                    if not breakable[end - 1]:
                        continue
                    if atoms[end].text[0] in _CLOSE_PUNCT:
                        continue
                # 行尾不得为开标点
                if atoms[end - 1].text[-1] in _OPEN_PUNCT:
                    continue
                new_breaks = base_breaks + ((start,) if start > 0 else ())
                candidate = (
                    base_cost + (max_columns - width) ** 2,
                    new_breaks,
                )
                old = cur.get(end)
                if old is None or candidate < old:
                    cur[end] = candidate
        full = cur.get(n)
        if full is not None and (best is None or full < best):
            best = full
        prev = cur
        if not prev:
            break

    if best is None:
        # 失败原因 3：列宽 / 行数都够，但行首行尾标点限制使断法不存在
        return WrapFailure(
            "无法在不违反“行首不得为闭标点、行尾不得为开标点”"
            "限制的条件下完成断行。"
        )

    _, breaks = best
    if identity and not breaks:
        return WrapPlan(
            lines=(WrappedLine(text=text, width=segment_width(0, n)),),
            breaks=(),
        )
    bounds = (0, *breaks, n)
    lines = tuple(
        WrappedLine(
            text=_join_line(atoms, gap_space, bounds[k], bounds[k + 1]),
            width=segment_width(bounds[k], bounds[k + 1]),
        )
        for k in range(len(bounds) - 1)
    )
    return WrapPlan(lines=lines, breaks=breaks)


# ---------------------------------------------------------------------------
# SRT 正文无损重写
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CueWrapResult:
    """一条字幕的断行结果。

    ``cue_order`` 为字幕在输入 SRT 中从 1 开始的出现顺序号；失败时
    ``success=False``、``lines`` 为空、``reason`` 给出中文原因。
    """

    cue_order: int
    index: str
    success: bool
    original_text: str
    lines: tuple[WrappedLine, ...]
    reason: str | None
    changed: bool


def _default_eol(content: str) -> str:
    """文件级默认行尾：优先沿用该文件已经使用的换行风格。"""
    if "\r\n" in content:
        return "\r\n"
    if "\n" in content:
        return "\n"
    if "\r" in content:
        return "\r"
    return "\n"


def wrap_document(
    content: str, max_columns: int, max_lines: int
) -> tuple[str, list[CueWrapResult]]:
    """对整个 SRT 做智能断行，返回 ``(新 SRT, 逐条结果)``。

    只替换成功且正文确实发生变化的字幕正文物理行；失败字幕与未变化字幕
    原样保留。序号、时间轴、行尾定位参数、块间空行、换行风格与开头 BOM
    的处理约定见模块文档字符串。
    """
    doc = parse_document(content)

    # 与 terms.apply_terms 相同的 BOM 处理：解析时剥离并计数，重写后至多补回一个
    bom_count = 0
    while bom_count < len(content) and content[bom_count] == BOM:
        bom_count += 1
    lines = split_physical_lines(content[bom_count:])
    default_eol = _default_eol(content)

    results: list[CueWrapResult] = []
    # (起始物理行, 结束物理行, 新物理行)；从后向前应用以免行号位移
    splices: list[tuple[int, int, list[str]]] = []
    changed_any = False

    for cue_order, (cue, body_range) in enumerate(
        zip(doc.cues, doc.body_ranges), start=1
    ):
        start_line, end_line = body_range.start_line, body_range.end_line
        eols = [
            split_line_eol(lines[idx])[1] for idx in range(start_line, end_line)
        ]
        plan = plan_wrap(cue.text, max_columns, max_lines)

        if isinstance(plan, WrapFailure):
            results.append(
                CueWrapResult(
                    cue_order=cue_order,
                    index=cue.index,
                    success=False,
                    original_text=cue.text,
                    lines=(),
                    reason=plan.reason,
                    changed=False,
                )
            )
            continue

        line_texts = tuple(line.text for line in plan.lines)
        new_text = "\n".join(line_texts)
        changed = new_text != cue.text
        if changed:
            new_physical: list[str] = []
            line_count = len(line_texts)
            old_count = len(eols)
            for pos, line_text in enumerate(line_texts):
                if pos < line_count - 1:
                    # 新增的内部换行：先用原正文同位置行尾；新增行多于原行时
                    # 沿用原正文最后一个内部行尾，再退到文件级默认行尾
                    if pos <= old_count - 2:
                        eol = eols[pos]
                    elif old_count >= 2:
                        eol = eols[old_count - 2]
                    else:
                        eol = default_eol
                else:
                    # 末行行尾保持原正文末行行尾（块间空行结构因此不动）
                    eol = eols[old_count - 1]
                new_physical.append(line_text + eol)
            splices.append((start_line, end_line, new_physical))
            changed_any = True

        results.append(
            CueWrapResult(
                cue_order=cue_order,
                index=cue.index,
                success=True,
                original_text=cue.text,
                lines=plan.lines,
                reason=None,
                changed=changed,
            )
        )

    if not changed_any:
        return content, results

    for start_line, end_line, new_physical in reversed(splices):
        lines[start_line:end_line] = new_physical
    return (BOM if bom_count else "") + "".join(lines), results
