"""
Split-flap message layout — mirrors public/board.html + vestaboard-text.js.

Pure Python (no TouchDesigner deps) so you can unit-test outside TD.
"""

from __future__ import annotations

import re
from typing import List, Sequence

COLS = 96
ROWS = 5

COLOR_BLOCKS = frozenset("🟥🟧🟨🟩🟦🟪⬜")
# Approximate Extended_Pictographic for emoji detection (best-effort without unicodedata age tables)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
)

SPOOL = list(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%&*()-+=:;',./?") + list(
    COLOR_BLOCKS
)

COLOR_MAP = {
    "🟥": "#e02424",
    "🟧": "#f97316",
    "🟨": "#eab308",
    "🟩": "#16a34a",
    "🟦": "#2563eb",
    "🟪": "#9333ea",
    "⬜": "#ffffff",
}


def is_color_block(ch: str) -> bool:
    return ch in COLOR_BLOCKS


def is_emoji_char(ch: str) -> bool:
    if not ch or ch == " " or is_color_block(ch):
        return False
    return bool(_EMOJI_RE.fullmatch(ch)) or bool(_EMOJI_RE.search(ch))


def normalize_grid_char(ch: str) -> str:
    if not ch or ch == " ":
        return ch or " "
    if is_color_block(ch) or is_emoji_char(ch):
        return ch
    upper = ch.upper()
    return upper if len(upper) == 1 else ch


def normalize_grid_line(line: str) -> List[str]:
    return [normalize_grid_char(c) for c in list(line or "")]


def wrap_message_to_rows(msg: str, cols: int = COLS, max_rows: int = 10**9) -> List[str]:
    result: List[str] = []
    for line in (msg or "").split("\n"):
        chars = list(line)
        if not chars:
            result.append("")
            if len(result) >= max_rows:
                return result[:max_rows]
            continue
        for i in range(0, len(chars), cols):
            result.append("".join(chars[i : i + cols]))
            if len(result) >= max_rows:
                return result[:max_rows]
    return result


def is_empty_row(line: str) -> bool:
    return not line or all(ch == " " for ch in list(line))


def center_line(line: str, cols: int = COLS) -> str:
    chars = list(line)
    end = len(chars)
    while end > 0 and chars[end - 1] == " ":
        end -= 1
    start = 0
    while start < end and chars[start] == " ":
        start += 1
    content = chars[start:end]
    if not content:
        return ""
    left_pad = (cols - len(content)) // 2
    out = ([" "] * left_pad) + content
    while len(out) < cols:
        out.append(" ")
    return "".join(out[:cols])


def layout_message_rows(
    msg: str, cols: int = COLS, rows: int = ROWS, autocenter: bool = False
) -> List[str]:
    wrapped = wrap_message_to_rows(msg, cols)
    if not autocenter:
        return [(wrapped[r] if r < len(wrapped) else "") for r in range(rows)]

    lines = list(wrapped)
    while lines and is_empty_row(lines[0]):
        lines.pop(0)
    while lines and is_empty_row(lines[-1]):
        lines.pop()
    if not lines:
        return [""] * rows
    centered = [center_line(line, cols) for line in lines]
    top_pad = (rows - len(centered)) // 2
    result = [""] * top_pad + centered
    while len(result) < rows:
        result.append("")
    return result[:rows]


def message_to_grid(
    msg: str, cols: int = COLS, rows: int = ROWS, autocenter: bool = False
) -> List[str]:
    """Return a flat list of length cols*rows (row-major)."""
    lines = layout_message_rows(msg, cols, rows, autocenter)
    grid: List[str] = []
    for r in range(rows):
        chars = normalize_grid_line(lines[r] if r < len(lines) else "")
        while len(chars) < cols:
            chars.append(" ")
        grid.extend(chars[:cols])
    return grid


def blank_grid(cols: int = COLS, rows: int = ROWS) -> List[str]:
    return [" "] * (cols * rows)


def grid_to_rows(grid: Sequence[str], cols: int = COLS, rows: int = ROWS) -> List[str]:
    out = []
    for r in range(rows):
        start = r * cols
        out.append("".join(grid[start : start + cols]))
    return out


def grid_to_table_text(grid: Sequence[str], cols: int = COLS, rows: int = ROWS) -> str:
    """Newline-joined rows for a Text DAT / Text TOP."""
    return "\n".join(grid_to_rows(grid, cols, rows))


def cell_color(ch: str) -> str:
    """Hex color for a cell character (text white, color blocks mapped)."""
    if is_color_block(ch):
        return COLOR_MAP.get(ch, "#ffffff")
    if ch == " " or not ch:
        return "#555555"
    return "#ffffff"
