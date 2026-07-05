"""端到端 smoke：套件可 import、CLI 可載入、check 指令存在。"""

from __future__ import annotations

import twquant
from twquant.cli import app, check


def test_version_available() -> None:
    assert isinstance(twquant.__version__, str)
    assert twquant.__version__


def test_cli_app_exists() -> None:
    # typer app 與 check 指令都應可載入（不實際執行子行程）
    assert app is not None
    assert callable(check)
