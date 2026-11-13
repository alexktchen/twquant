#!/usr/bin/env python3
"""
不用先設 PYTHONPATH 就能執行 autotrade 的指令列，在 PowerShell、cmd、bash 都是同一行。

用法（後面接的參數跟 `python -m autotrade` 完全一樣）：
  python tools/run.py check-config configs/tmf_sim.toml
  python tools/run.py notify-setup --config configs/tmf_sim.toml --env-file D:/project/twquant/.env
  python tools/run.py notify-test --config configs/tmf_sim.toml --env-file D:/project/twquant/.env
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autotrade.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
