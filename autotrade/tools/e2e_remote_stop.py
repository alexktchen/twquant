#!/usr/bin/env python3
"""
遠端緊急停止的端到端測試（A-011 AC22）：在**本機**資料庫上自動跑完整條路，每一步印「通過／失敗」。

  演練程式（另一個程序，買 1 口抱著） ← 指令檔 ← 中繼程式（另一個程序） ← 資料庫 ← 網站送出停止（網站自己的程式碼）
  演練程式停手、平倉 → 回報檔 → 中繼程式 → 資料庫 → 網站讀回來（網站自己的程式碼）

網站那一邊用 `frontend/functions/_lib/autotrade.ts` 的 requestStop／controlView（node 直接跑，跟正式環境是同一段程式）；
交易程式這一邊用真的 `python -m autotrade relay` 與 `python -m autotrade drill` 兩個程序。全程不碰任何券商。

只准連本機資料庫（連線字串不是 localhost／127.0.0.1 就拒絕），跑完把這次測試寫的資料刪掉。

用法（在 autotrade 資料夾裡）：
  python tools/e2e_remote_stop.py --ticks D:/project/twquant/data/research_ms/taifex_ticks/TMF/Daily_2026_11_12.csv --day 2026-11-12
連線字串預設讀環境變數 TWQUANT_PG_DSN（例：postgresql://twquant:twquant@localhost:5433/twquant）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FRONTEND = HERE.parent / "frontend"
LIB = (FRONTEND / "functions" / "_lib" / "autotrade.ts").as_uri()
PG = (FRONTEND / "node_modules" / "postgres" / "src" / "index.js").as_uri()
WHO = "e2e-remote-stop@example.invalid"
LOCAL = re.compile(r"@(localhost|127\.0\.0\.1)(:\d+)?/")

# 網站那一邊：node 跑網站自己的程式碼（requestStop、controlView），結果用 JSON 印出來
NODE = """
import postgres from '%(pg)s'
import { controlView, requestStop, taipeiToday } from '%(lib)s'
const db = postgres(process.env.E2E_DSN, { prepare: false, max: 1, fetch_types: false, idle_timeout: 5 })
const step = process.argv[2]
try {
  if (step === 'press') {
    const r = await requestStop(db, '%(who)s', Date.now())
    console.log(JSON.stringify(r.ok ? { ok: true, reused: r.reused, stop: JSON.parse(r.control.stop) } : r))
  } else if (step === 'read') {
    const [v] = await controlView(db, taipeiToday(Date.now()))
    console.log(JSON.stringify({ stop: JSON.parse(v.stop ?? 'null'), listeners: JSON.parse(v.listeners ?? '{}') }))
  } else if (step === 'cleanup') {
    const gone = await db`DELETE FROM web.autotrade_commands WHERE created_by = '%(who)s' RETURNING id`
    await db`DELETE FROM web.autotrade_listeners WHERE consumer = 'sim'`
    console.log(JSON.stringify({ deleted: gone.length }))
  }
} finally {
  await db.end({ timeout: 2 })
}
"""


def web(step: str, dsn: str, script: Path) -> dict[str, object]:
    """跑網站那一邊的一步，回傳它印出來的 JSON。"""
    env = {**os.environ, "E2E_DSN": dsn}
    out = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script), step],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
        cwd=FRONTEND,
    )
    if out.returncode != 0:
        raise RuntimeError(f"網站那一邊的 {step} 失敗：{out.stderr.strip()[-600:]}")
    lines = [line for line in out.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise RuntimeError(f"網站那一邊的 {step} 沒有輸出：{out.stdout[-300:]}")
    result: dict[str, object] = json.loads(lines[-1])
    return result


def wait_for(what: str, check: Callable[[], object], timeout_s: float) -> object:
    """每 0.5 秒看一次，直到 check 回傳有東西；逾時就 raise。"""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        got = check()
        if got:
            return got
        time.sleep(0.5)
    raise TimeoutError(f"{timeout_s:.0f} 秒內沒等到：{what}")


class Report:
    """每一步印「通過／失敗」，最後總結。"""

    def __init__(self) -> None:
        self.failed = 0

    def check(self, ok: bool, what: str, detail: str = "") -> None:
        mark = "通過" if ok else "失敗"
        print(f"[{mark}] {what}{'：' + detail if detail else ''}", flush=True)
        if not ok:
            self.failed += 1


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。全部通過回 0。"""
    parser = argparse.ArgumentParser(description="遠端緊急停止的端到端測試（只連本機資料庫）")
    parser.add_argument(
        "--ticks", type=Path, required=True, help="期交所 Daily_*.csv（演練拿它的價格）"
    )
    parser.add_argument("--day", required=True, help="那個檔是哪一天（YYYY-MM-DD）")
    parser.add_argument("--from", dest="start", default="09:00:00", help="從那一天的幾點開始播")
    parser.add_argument("--config", type=Path, default=HERE / "configs" / "tmf_sim.toml")
    parser.add_argument("--dsn-env", default="TWQUANT_PG_DSN", help="放本機連線字串的環境變數名稱")
    args = parser.parse_args(argv)
    dsn = os.environ.get(args.dsn_env, "")
    if not LOCAL.search(dsn):
        print(f"拒絕執行：{args.dsn_env} 不是本機資料庫（端到端測試只准連 localhost）")
        return 2
    if not (FRONTEND / "node_modules" / "postgres").exists():
        print("拒絕執行：frontend 還沒裝套件（在 frontend 資料夾跑 npm ci）")
        return 2

    report = Report()
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(HERE / "src"),
    }
    with tempfile.TemporaryDirectory(prefix="e2e_remote_stop_") as tmp:
        folder = Path(tmp)
        script = folder / "web.mjs"
        script.write_text(NODE % {"pg": PG, "lib": LIB, "who": WHO}, encoding="utf-8")
        relay_log = (folder / "relay.log").open("w", encoding="utf-8")
        drill_log = (folder / "drill.log").open("w", encoding="utf-8")
        relay = subprocess.Popen(
            [sys.executable, "-m", "autotrade", "relay", "--config", str(args.config), "--dir", str(folder),
             "--consumer", "sim", "--dsn-env", args.dsn_env],
            stdout=relay_log, stderr=subprocess.STDOUT, env=env, cwd=HERE,
        )  # fmt: skip
        drill = subprocess.Popen(
            [sys.executable, "-m", "autotrade", "drill", "--config", str(args.config), "--ticks", str(args.ticks),
             "--day", args.day, "--from", args.start, "--dir", str(folder), "--minutes", "3", "--speed", "5"],
            stdout=drill_log, stderr=subprocess.STDOUT, env=env, cwd=HERE,
        )  # fmt: skip
        try:
            # 1. 兩個程序都起來、演練程式買進 1 口、網站看得到它在聽
            wait_for("中繼程式寫出指令檔", lambda: (folder / "commands.json").exists(), 30)
            report.check(True, "中繼程式在跑，寫出了指令檔")
            wait_for(
                "演練程式買進 1 口",
                lambda: re.search(
                    r"BUY 1 口 \d", (folder / "drill.log").read_text(encoding="utf-8")
                ),
                40,
            )
            report.check(True, "演練程式買進 1 口")
            seen = wait_for(
                "網站看到模擬程式在聽",
                lambda: (web("read", args_dsn(args), script)["listeners"] or {}).get("sim"),  # type: ignore[union-attr]
                30,
            )
            report.check(
                isinstance(seen, dict) and seen.get("engine_age_s") is not None,
                "網站看到模擬程式的心跳",
                json.dumps(seen),
            )

            # 2. 網站送出停止；再按一次是沿用同一筆
            first = web("press", dsn, script)
            again = web("press", dsn, script)
            stop = first.get("stop") if isinstance(first.get("stop"), dict) else {}
            assert isinstance(stop, dict)
            report.check(
                first.get("ok") is True and first.get("reused") is False,
                "網站送出停止",
                f"第 {stop.get('id')} 號",
            )
            report.check(again.get("reused") is True, "再按一次沿用同一筆，不疊")

            # 3. 演練程式收到、停手、平倉；網站讀到「收到了」與「確認平掉」
            def done() -> object:
                got = web("read", dsn, script)["stop"]
                acks = got.get("acks", {}) if isinstance(got, dict) else {}
                sim = acks.get("sim") if isinstance(acks, dict) else None
                return sim if isinstance(sim, dict) and sim.get("done_code") else None

            sim = wait_for("網站讀到模擬程式確認平掉", done, 60)
            assert isinstance(sim, dict)
            report.check(
                sim.get("code") in ("flattening", "flat", "cancelling"),
                "網站讀到「收到了」",
                f"{sim.get('code')}：{sim.get('note')}",
            )
            report.check(
                sim.get("done_code") == "flat", "網站讀到「確認平掉」", str(sim.get("done_note"))
            )
            log = (folder / "drill.log").read_text(encoding="utf-8")
            report.check(
                "收到遠端緊急停止" in log and "緊急停止" in log, "演練程式記下了遠端停止與停手"
            )
            report.check(
                (folder / "commands.json").exists()
                and json.loads((folder / "commands.json").read_text(encoding="utf-8"))["commands"]
                == [],
                "回報送到之後，指令檔裡不再有這筆",
            )
        except (TimeoutError, RuntimeError, AssertionError, KeyError) as exc:
            report.check(False, "中途出錯", str(exc))
        finally:
            for proc in (drill, relay):
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            relay_log.close()
            drill_log.close()
            try:
                cleaned = web("cleanup", dsn, script)
                print(f"清掉這次測試寫的資料：{cleaned.get('deleted')} 筆停止指令")
            except RuntimeError as exc:
                report.check(False, "清理測試資料", str(exc))
            if report.failed:
                for name in ("relay.log", "drill.log"):
                    print(f"----- {name}（最後 30 行）-----")
                    print("\n".join((folder / name).read_text(encoding="utf-8").splitlines()[-30:]))
    print("全部通過" if report.failed == 0 else f"有 {report.failed} 項失敗")
    return 0 if report.failed == 0 else 1


def args_dsn(args: argparse.Namespace) -> str:
    """連線字串（只從環境變數讀，不印出來）。"""
    return os.environ.get(args.dsn_env, "")


if __name__ == "__main__":
    raise SystemExit(main())
