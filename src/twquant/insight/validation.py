"""洞悉分析的數字驗證（PLAN §5、D-081d）。

rationale 裡出現的數字，必須能在**被引用的證據**文字中找到。D-081(d) 裁定這一版
只記錄不拒絕：回傳對不上的數字清單，由呼叫端存進 artifact，兩週後看拒絕率再決定
是否升級為硬性驗證。純函數，不讀檔、不連網。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 數字前不得緊接識別字元，避免把 ev_1、run_2 這類引用代號當成數值主張。
_NUMBER = re.compile(r"(?<![0-9A-Za-z_])\d[\d,]*(?:\.\d+)?")


def _normalise(value: str) -> str:
    """去掉千分位並收斂小數尾零，讓 1,234,567 與 1234567 視為同一個數。"""
    plain = value.replace(",", "")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    return plain or "0"


def numbers_in(text: str) -> tuple[str, ...]:
    """Extract the numeric claims from a piece of text, normalised for comparison."""
    return tuple(_normalise(match.group()) for match in _NUMBER.finditer(text))


def unverified_numbers(rationale: str, evidence_texts: Iterable[str]) -> tuple[str, ...]:
    """Return each number absent from every cited evidence text, in order, deduplicated."""
    known: set[str] = set()
    for text in evidence_texts:
        known.update(numbers_in(text))
    missing: list[str] = []
    for number in numbers_in(rationale):
        if number not in known and number not in missing:
            missing.append(number)
    return tuple(missing)
