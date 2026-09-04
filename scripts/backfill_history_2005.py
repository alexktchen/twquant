"""一次性：把全部來源回補到 2005（T-116）。逐資料集串跑，可中斷續傳。

為什麼要新的 staging 目錄：既有 `staging/finmind_*` 是用 `--start 2020-01-01` 抓的，
而 `backfill_finmind.py` 對「檔案已存在」直接跳過——寫回同一個目錄只會補到新股票，
既有 2,064 檔仍是 2020 起的短歷史。故一律寫到 `finmind_hist_*`。

宇宙用 `universe_historical.txt`（TaiwanStockInfo ∪ TaiwanStockDelisting，3,007 檔），
不是 `prices_daily` 的 2,056 檔——後者只含活到 2020 年的公司，用它回補等於把
2005~2020 整段變成存活者樣本（實測漏 951 檔，其中 563 檔在 2020 年前已下市）。

各資料集的實際起點不同（實測：行情 2005-01-03、法人 2012-05-02），來源有多少就抓多少，
不足的年份留空，由後續清洗層據實反映，不得補值。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
STAGING = pathlib.Path("D:/project/twquant/data/staging")
UNIVERSE = STAGING / "universe_historical.txt"
START, END = "2005-01-01", "2026-09-04"

# 順序＝重要性：行情是面板本體，先抓完它就能開始下游工作。
DATASETS = [
    ("TaiwanStockPrice", "finmind_hist_price"),
    ("TaiwanStockPER", "finmind_hist_per"),
    ("TaiwanStockInstitutionalInvestorsBuySell", "finmind_hist_institutional"),
    ("TaiwanStockMarginPurchaseShortSale", "finmind_hist_margin"),
    ("TaiwanStockMonthRevenue", "finmind_hist_revenue"),
    ("TaiwanStockFinancialStatements", "finmind_hist_income"),
    ("TaiwanStockBalanceSheet", "finmind_hist_balance"),
    ("TaiwanStockCashFlowsStatement", "finmind_hist_cashflow"),
]


def _token_env() -> dict[str, str]:
    """把 FINMIND_TOKEN 備妥給子程序。

    token 只從環境變數或 `.env` 讀，**不放進命令列**（命令列會出現在程序清單），
    也永不輸出（黃金規則 9）。
    """
    env = dict(os.environ)
    if env.get("FINMIND_TOKEN"):
        return env
    dotenv = pathlib.Path("D:/project/twquant/.env")
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if line.startswith("FINMIND_TOKEN="):
                env["FINMIND_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                return env
    raise SystemExit("FINMIND_TOKEN 未設（環境變數或 D:/project/twquant/.env）")


def main() -> None:
    if not UNIVERSE.exists():
        raise SystemExit(f"宇宙清單不存在：{UNIVERSE}")
    n = sum(
        1
        for ln in UNIVERSE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    )
    print(f"宇宙 {n:,} 檔 × {len(DATASETS)} 個資料集 = {n * len(DATASETS):,} 次請求", flush=True)
    print(
        f"免費層 600 次/小時（6.2 秒/請求）→ 約 {n * len(DATASETS) * 6.2 / 3600:.1f} 小時\n",
        flush=True,
    )
    env = _token_env()
    t0 = time.perf_counter()
    for i, (dataset, sub) in enumerate(DATASETS, 1):
        out = STAGING / sub
        print(
            f"\n{'=' * 70}\n[{i}/{len(DATASETS)}] {dataset} → {sub}"
            f"（已 {(time.perf_counter() - t0) / 3600:.1f}h）\n{'=' * 70}",
            flush=True,
        )
        r = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "backfill_finmind.py"),
                dataset,
                str(out),
                "--universe-file",
                str(UNIVERSE),
                "--start",
                START,
                "--end",
                END,
            ],
            cwd=str(REPO),
            env=env,
        )
        if r.returncode != 0:
            # 不靜默續跑：某個資料集失敗代表來源或額度有問題，續跑會把後面也拖垮，
            # 且腳本本身可續傳，修好後重跑即可從中斷處接上。
            raise SystemExit(f"{dataset} 失敗（exit {r.returncode}）——修好後重跑本腳本即可續傳。")
    print(f"\n全部完成，耗時 {(time.perf_counter() - t0) / 3600:.1f} 小時", flush=True)


if __name__ == "__main__":
    main()
