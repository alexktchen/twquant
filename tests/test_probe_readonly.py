"""SPEC-016 T-334 AC-2：`scripts/probe/` 的盤點腳本必須唯讀——不寫 PG、不落地。

沿 tests/test_io_boundary.py 的 AST 掃描，但對象是盤點腳本：允許 requests（它的工作就是連網），
禁止 psycopg、供應層 import、內建 open() 與 DataFrame 落地方法。
"""

from __future__ import annotations

import ast
from pathlib import Path

_PROBE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "probe"
_BANNED_IMPORTS = {"psycopg", "sqlalchemy", "duckdb"}
_BANNED_INTERNAL = ("twquant.data.serving", "twquant.data.warehouse")
_BANNED_CALL_NAMES = {"open"}
_BANNED_CALL_ATTRS = {
    "write_parquet",
    "write_csv",
    "write_ipc",
    "to_parquet",
    "write_text",
    "write_bytes",
    "to_csv",
}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _BANNED_IMPORTS or alias.name.startswith(
                    _BANNED_INTERNAL
                ):
                    out.append(f"L{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in _BANNED_IMPORTS or module.startswith(_BANNED_INTERNAL):
                out.append(f"L{node.lineno}: from {module} import ...")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _BANNED_CALL_NAMES:
                out.append(f"L{node.lineno}: {fn.id}(...)")
            elif isinstance(fn, ast.Attribute) and fn.attr in _BANNED_CALL_ATTRS:
                out.append(f"L{node.lineno}: .{fn.attr}(...)")
    return out


def test_ac2_probe_scripts_are_readonly() -> None:
    files = sorted(_PROBE_DIR.glob("*.py"))
    assert any(f.name == "probe_fund_flow_sources.py" for f in files), "T-334 盤點腳本不存在"
    offenders = {f.name: v for f in files if (v := _violations(f))}
    assert not offenders, f"盤點腳本出現寫入：{offenders}"


def test_ac2_scanner_detects_write(tmp_path: Path) -> None:
    """反向測試：掃描器要抓得到寫入（防恆真）。"""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import psycopg\nfrom twquant.data.serving import store\nopen('x','w')\ndf.write_parquet('y')\n",
        encoding="utf-8",
    )
    v = _violations(bad)
    assert len(v) == 4, v
