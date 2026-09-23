"""D-148：第二保留期開啟日的判讀——**2027-03-01 之前拒絕執行**。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/holdout2_tail.py \
        --out D:/project/twquant/data/holdout2

這支腳本是 2026-09-23（保留期開始兩週、任何績效數字之前）寫定的。開啟日照原樣執行，
不得修改；要修改就是另一個判讀程序，須另立裁決。

順序是刻意的，任何一步不過就停：
1. 開啟條件（D-109 §二）：日期 ≥ 2027-03-01、保留期累積 ≥ 120 個交易日
2. 凍結核對：策略與成本的行為指紋、模型檔的 sha256 與 `configs/holdout2.yaml` 逐項相同
3. 回測：保留期起點 ~ 最後一個交易日，資本與研究期相同
4. 只報數字（D-146／D-148 §三：不設通過線，部署由使用者決定）

**只能跑一次**（D-109）。結果包寫到 --out；若該目錄已有結果包就拒絕覆寫——
第二次執行不會得到新資訊，只會製造「挑一個比較好看的版本」的機會。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
CONFIGS = REPO / "configs"
MODELS = pathlib.Path("D:/project/twquant/data/models")


def _sessions_since(start: dt.date) -> tuple[int, dt.date]:
    """保留期起點以來的交易日數與最後一個交易日（以 PG 的 prices_daily 為準）。"""
    import psycopg

    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("需要 TWQUANT_PG_DSN")
    with psycopg.connect(dsn, connect_timeout=30) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(distinct date), max(date) from serving.prices_daily where date >= %s",
            (start,),
        )
        n, last = cur.fetchone()  # type: ignore[misc]
    return int(n), last


def main() -> None:
    sys.path.insert(0, str(REPO / "src"))
    from twquant.backtest.costs import CostConfig
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.reporting.holdout import (
        HoldoutRegistration,
        config_fingerprint,
        file_sha256,
        refuse_before_opening,
    )
    from twquant.strategy.config import load_strategy

    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--out", required=True, help="結果包目錄（必須不存在或為空）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    reg = load_config(CONFIGS / "holdout2.yaml", HoldoutRegistration)
    n, last = _sessions_since(reg.holdout_start)
    refuse_before_opening(reg, now_tw().date(), n)  # ① 開啟條件

    out = pathlib.Path(args.out)
    if out.exists() and any(out.glob("*.json")):
        raise SystemExit(f"{out} 已有結果包——第二保留期只能開一次（D-109），拒絕覆寫")
    out.mkdir(parents=True, exist_ok=True)

    costs = load_config(CONFIGS / "costs.yaml", CostConfig)
    for e in reg.entries:  # ② 凍結核對
        cfg = load_strategy(str(CONFIGS / "strategies" / f"{e.strategy}.yaml"))
        checks = {
            "策略指紋": config_fingerprint(cfg) == e.strategy_fingerprint,
            "成本指紋": config_fingerprint(costs) == e.costs_fingerprint,
            "模型檔 sha256": file_sha256((MODELS / e.model_id / "model.txt").read_bytes())
            == e.model_sha256,
        }
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            raise SystemExit(f"{e.code}：{bad} 與 D-148 登記不符——這已經不是登記的那個東西")
        print(f"✓ {e.code}：策略、成本、模型三項指紋與登記相同")

    print(f"\n保留期 {reg.holdout_start} ~ {last}，共 {n} 個交易日\n")
    for e in reg.entries:  # ③ 回測
        dest = out / f"{e.code}_holdout2.json"
        cmd = [
            sys.executable, "-m", "twquant", "backtest",
            "--strategy", str(CONFIGS / "strategies" / f"{e.strategy}.yaml"),
            "--start", reg.holdout_start.isoformat(), "--end", last.isoformat(),
            "--capital", str(int(e.capital_twd)), "--costs", "costs.yaml", "--out", str(dest),
        ]  # fmt: skip
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            raise SystemExit(f"{e.code} 回測失敗 exit {r.returncode}")

    # ④ 只報數字：沿用研究期同一套算法（d146_report 的欄位），但不套研究期的日期上界
    report = [sys.executable, str(REPO / "scripts" / "d146_report.py"), "--bundles", str(out)]
    env = {**os.environ, "TWQUANT_HOLDOUT_OPEN": reg.registration}
    subprocess.run(report, cwd=REPO, env=env, check=True)
    (out / "registration.json").write_text(
        json.dumps({"registration": reg.registration, "opened_at": now_tw().isoformat()}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
