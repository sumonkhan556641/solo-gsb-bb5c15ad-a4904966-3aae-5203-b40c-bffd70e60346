"""字幕专名统一：别名 → 标准写法的最长匹配替换。

规则按输入顺序给出。扫描时**只处理字幕正文的各个物理行**（序号行与时间轴
行不参与），**区分大小写**，在每一行内：

- 从左到右只对原文匹配一次，已命中并替换的文本不再参与后续匹配（替换出来的
  标准写法也不会再次被扫描）；
- 同一位置（同样的起点）可命中多个别名时，优先选择字符数最长的别名；
- 长度相同则按规则的输入顺序取靠前者。

替换通过直接改写原始物理行实现，序号、时间轴、行尾排版参数、空行、原有换行
（CRLF/LF/CR）以及开头 BOM 全部保持原样。
"""
from __future__ import annotations

from dataclasses import dataclass

from .srt import BOM, split_line_eol, split_physical_lines


class TermError(ValueError):
    """专名规则不合法（空别名，或同一别名指向不同标准写法）。"""


@dataclass(frozen=True)
class TermRule:
    """一条专名规则：``aliases`` 中每个别名都替换为 ``standard``。"""

    standard: str
    aliases: tuple[str, ...]
    order: int


@dataclass(frozen=True)
class TermHit:
    """一次别名命中。

    ``cue_order`` 为字幕在输入 SRT 中从 1 开始的出现顺序号（与 SRT 自身序号
    无关）；``line_no`` 为该字幕正文内从 1 开始的物理行号；``column`` 为别名
    在该物理正文中从 0 开始的字符位置（按 Unicode 码位计数，常规中英文与
    JavaScript 字符串下标一致；含 emoji 等增补平面字符时与 JS 的 UTF-16
    下标相差代理对数量）。
    """

    cue_order: int
    line_no: int
    column: int
    source: str
    standard: str


def build_rules(rows: list[tuple[str, list[str]]]) -> list[TermRule]:
    """由 ``(标准写法, [别名, ...])`` 构造规则并做跨规则校验。

    - 空别名（空串或仅含空白）拒绝；
    - 同一别名在不同规则中指向不同标准写法时拒绝；
    - 同一规则内重复别名按首次出现保留（同长度同分本就按输入顺序）。
    """
    rules: list[TermRule] = []
    alias_targets: dict[str, str] = {}
    for order, (standard, aliases) in enumerate(rows, start=1):
        if not standard.strip():
            raise TermError(f"第 {order} 条规则的标准写法不能为空。")
        # dict 兼作去重与输入顺序保留（等长同分按规则顺序，规则内别名顺序也稳定）
        seen: dict[str, None] = {}
        for alias in aliases:
            if not alias.strip():
                raise TermError(f"第 {order} 条规则“{standard}”包含空别名。")
            previous = alias_targets.get(alias)
            if previous is not None and previous != standard:
                raise TermError(
                    f"别名“{alias}”同时指向“{previous}”与“{standard}”，"
                    "同一别名只能对应一个标准写法。"
                )
            if alias not in seen:
                seen[alias] = None
                alias_targets[alias] = standard
        rules.append(TermRule(standard=standard, aliases=tuple(seen), order=order))
    return rules


def build_alias_index(rules: list[TermRule]) -> dict[str, list[tuple[str, TermRule]]]:
    """按别名首字符分组的候选表，组内保持“规则顺序 → 规则内别名顺序”。"""
    index: dict[str, list[tuple[str, TermRule]]] = {}
    for rule in rules:
        for alias in rule.aliases:
            if alias:
                index.setdefault(alias[0], []).append((alias, rule))
    return index


def scan_line_with_index(
    line: str, index: dict[str, list[tuple[str, TermRule]]]
) -> tuple[str, list[tuple[int, str, str]]]:
    """同 :func:`scan_line`，但复用预先构造的首字符候选表。"""
    out: list[str] = []
    hits: list[tuple[int, str, str]] = []
    pos = 0
    length = len(line)
    while pos < length:
        best_rule: TermRule | None = None
        best_alias: str | None = None
        best_len = 0
        # 只需比较首字符与当前字符相同的别名；候选表顺序即规则输入顺序，
        # 所以等长时先入候选的规则天然胜出，这里仅在“严格更长”时替换
        for alias, rule in index.get(line[pos], ()):
            if len(alias) <= best_len:
                continue
            if line.startswith(alias, pos):
                best_rule = rule
                best_alias = alias
                best_len = len(alias)
        if best_rule is None:
            out.append(line[pos])
            pos += 1
        else:
            assert best_alias is not None
            out.append(best_rule.standard)
            hits.append((pos, best_alias, best_rule.standard))
            # 命中的别名片段（以及刚写入的替换文本）整体跳过，不再匹配
            pos += best_len
    return "".join(out), hits


def scan_line(line: str, rules: list[TermRule]) -> tuple[str, list[tuple[int, str, str]]]:
    """扫描一行正文，返回 ``(替换后的行, [(column, 别名, 标准写法), ...])``。

    从左到右一次扫描原文：每个位置取**最长别名**，等长按规则输入顺序；命中后
    整段别名连同其替换文本一起跳过，不再参与匹配。
    """
    return scan_line_with_index(line, build_alias_index(rules))


def apply_terms(
    content: str,
    rules: list[TermRule],
    body_ranges: list[tuple[int, int]],
) -> tuple[str, list[TermHit]]:
    """在原始 SRT 上只改正文命中片段，其余字节级内容保持原样。

    ``body_ranges`` 与 :func:`app.srt.parse_document` 给出的正文物理行区间
    一一对应（按字幕输入顺序）。无任何命中时，返回值与 ``content`` 完全相同
    （含开头 BOM 数量与换行风格）。
    """
    # 未剥 BOM 的切片无法与行号对应：解析器已先剥离并统计开头 BOM，这里按同一
    # 方式切出物理行，重写后再把原 BOM 补回
    bom_count = 0
    while bom_count < len(content) and content[bom_count] == BOM:
        bom_count += 1
    lines = split_physical_lines(content[bom_count:])

    # 候选索引只构造一次，跨所有正文物理行复用
    alias_index = build_alias_index(rules)
    all_hits: list[TermHit] = []
    changed = False
    for cue_order, (start_line, end_line) in enumerate(body_ranges, start=1):
        for line_index in range(start_line, end_line):
            body, eol = split_line_eol(lines[line_index])
            new_body, raw_hits = scan_line_with_index(body, alias_index)
            if not raw_hits:
                continue
            changed = True
            lines[line_index] = new_body + eol
            all_hits.extend(
                TermHit(
                    cue_order=cue_order,
                    line_no=line_index - start_line + 1,
                    column=column,
                    source=alias,
                    standard=standard,
                )
                for column, alias, standard in raw_hits
            )

    if not changed:
        return content, []
    # 与 srt.rewrite_srt 一致：原文件开头无论几个 BOM，重写输出最多保留一个；
    # 无命中时直接原样返回，不走这里（逐字符一致优先）
    return (BOM if bom_count else "") + "".join(lines), all_hits
