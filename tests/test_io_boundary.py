"""IO 邊界靜態閘門（CLAUDE.md §4 / REVIEW H8）。

純運算層（factors/strategy/backtest/portfolio/insight）不得碰網路或直接寫檔——
把黃金規則 10 從「靠人審查」升級為「機器可攔」。此測試以 AST 掃描，避免誤判
註解或字串中的關鍵字。目前這些目錄尚未建立，測試會 collect 到 0 個檔案而通過；
一旦後續任務加入程式碼，違規即被攔下。
"""

from __future__ import annotations

import ast
from pathlib import Path

# 純運算層：禁止網路 IO 與直接寫檔
_PURE_DIRS = ["factors", "strategy", "backtest", "portfolio", "insight"]

# 禁止 import 的模組。insight 允許 anthropic（洞悉層是唯一能呼叫 LLM 的純運算例外）。
_BANNED_IMPORTS = {"requests", "httpx", "urllib3"}
_BANNED_IMPORTS_EXCEPT_INSIGHT = {"anthropic"}

# 禁止的呼叫：內建 open() 與 DataFrame 落地方法
_BANNED_CALL_NAMES = {"open"}
_BANNED_CALL_ATTRS = {"write_parquet", "write_csv", "write_ipc", "to_parquet"}

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "twquant"


def _pure_py_files() -> list[Path]:
    files: list[Path] = []
    for d in _PURE_DIRS:
        files.extend((_SRC_ROOT / d).rglob("*.py"))
    return files


def _banned_modules_for(path: Path) -> set[str]:
    banned = set(_BANNED_IMPORTS)
    if "insight" not in path.parts:
        banned |= _BANNED_IMPORTS_EXCEPT_INSIGHT
    return banned


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    banned_mods = _banned_modules_for(path)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in banned_mods:
                    out.append(f"L{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in banned_mods:
                out.append(f"L{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _BANNED_CALL_NAMES:
                out.append(f"L{node.lineno}: {fn.id}(...)")
            elif isinstance(fn, ast.Attribute) and fn.attr in _BANNED_CALL_ATTRS:
                out.append(f"L{node.lineno}: .{fn.attr}(...)")
    return out


def test_pure_layers_have_no_io() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _pure_py_files():
        v = _violations(path)
        if v:
            offenders[str(path.relative_to(_SRC_ROOT))] = v
    assert not offenders, f"純運算層出現 IO：{offenders}"


def test_scanner_detects_violation(tmp_path: Path) -> None:
    """反向測試：確認掃描器真的抓得到違規（防止測試變成恆真）。"""
    bad = tmp_path / "insight" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("import requests\nopen('x')\n", encoding="utf-8")
    v = _violations(bad)
    assert any("requests" in x for x in v)
    assert any("open" in x for x in v)
