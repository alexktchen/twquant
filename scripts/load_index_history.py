"""T-166：把 `index_daily`（TAIEX／TPEX）回補到 2015-06-01，並同步進 PG。

**為什麼卡住**：`price.rs_market_20d` 與 `price.industry_rs_20d` 要 `index_daily`，
而它只有 2020 起。`track_c1_breakout.yaml` 的四個訊號含 `rs_market_20d` 且
`require_all_factors: true`——所以 C1（以及共用同一組訊號的 B／M／F／R 系列）
在 2015~2019 會**整個宇宙空掉**。長窗口回測非補這張表不可。

**兩個來源，官方優先、FinMind 補洞**：

- `twquant backfill-index` 走 TWSE／TPEX 官方端點。實測 2015-06~2019-12 的 55 個月裡，
  TPEX 全部成功、**TWSE 有 11 個月（全部落在 2019）持續回非 JSON**，重試三次仍然失敗。
- FinMind 的 `TaiwanStockPrice`（`data_id="TAIEX"`）有完整的 1,128 天。
  **交叉驗證過才用**：與官方抓到的 907 天逐日比對，open／high／low／close
  **四欄全部 907/907 完全相同、最大相對誤差 0.000000%**。

故 TAIEX 缺的那 221 天用 FinMind 補，其餘沿用官方。

**只寫 2020-01-02 之前**：那之後是既有的權威資料，`serving rebuild` 會 truncate
整張表（實測主湖只有 1,211 個分區、`lake_rebuild` 只有 2020 起，兩個湖都不完整，
rebuild 任一個都會刪掉另一段）。故走 upsert，只補沒有的日期。

用法：
    python scripts/load_index_history.py --check
    python scripts/load_index_history.py --write
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl
import requests

from twquant.config import ServingConfig, load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.warehouse import Warehouse

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
_ENV = pathlib.Path("D:/project/twquant/.env")
LAKE = "D:/project/twquant/data"
API = "https://api.finmindtrade.com/api/v4/data"
LO, HI = dt.date(2015, 6, 1), dt.date(2019, 12, 31)
AUTHORITATIVE_FROM = dt.date(2020, 1, 2)
# FinMind 的 data_id → 我們的 index_id
_INDICES = {"TAIEX": "TAIEX", "TPEx": "TPEX"}
_AVAILABLE_HOUR = 18  # 盤後可得，與既有列一致（實測 18:00 台北）


def _token() -> str:
    tok = os.environ.get("FINMIND_TOKEN")
    if not tok and _ENV.exists():
        for line in _ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("FINMIND_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not tok:
        raise SystemExit("FINMIND_TOKEN 未設定")
    return tok


def _fetch(data_id: str, token: str) -> pl.DataFrame:
    r = requests.get(
        API,
        params={
            "dataset": "TaiwanStockPrice",
            "data_id": data_id,
            "start_date": LO.isoformat(),
            "end_date": HI.isoformat(),
            "token": token,
        },
        timeout=180,
    )
    payload = r.json()
    if payload.get("status") != 200:
        raise SystemExit(
            f"{data_id}：status {payload.get('status')} {str(payload.get('msg'))[:200]}"
        )
    rows = payload.get("data") or []
    if not rows:
        raise SystemExit(f"{data_id}：FinMind 回 0 列")
    return pl.DataFrame(rows).select(
        pl.col("date").str.to_date("%Y-%m-%d", strict=True).alias("date"),
        pl.lit(_INDICES[data_id]).alias("index_id"),
        pl.col("open").cast(pl.Float64),
        pl.col("max").cast(pl.Float64).alias("high"),
        pl.col("min").cast(pl.Float64).alias("low"),
        pl.col("close").cast(pl.Float64),
    )


def _official() -> pl.DataFrame:
    return (
        Warehouse(LAKE)
        .scan("index_daily")
        .filter(pl.col("date").is_between(LO, HI))
        .select("date", "index_id", "open", "high", "low", "close")
        .collect()
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只比對與報告，不寫")
    g.add_argument("--write", action="store_true", help="驗證通過才寫湖與 PG")
    args = ap.parse_args()

    token = _token()
    fm = pl.concat([_fetch(d, token) for d in _INDICES])
    off = _official()
    print(f"FinMind {fm.height:,} 列；官方（湖）{off.height:,} 列")

    # **先驗證再寫入**：兩個來源的重疊日必須一致，否則不得用 FinMind 補洞
    j = off.join(fm, on=["date", "index_id"], how="inner", suffix="_fm")
    print(f"\n重疊 {j.height:,} 列，逐欄比對：")
    ok = True
    for c in ("open", "high", "low", "close"):
        both = j.filter(pl.col(c).is_not_null() & pl.col(f"{c}_fm").is_not_null())
        if both.is_empty():
            print(f"  {c:6s} （官方無此欄，跳過——TPEX 只有收盤）")
            continue
        rel = ((pl.col(c) - pl.col(f"{c}_fm")).abs() / pl.col(c)).alias("r")
        d = both.select(rel)["r"]
        same = int((d == 0).sum())
        print(f"  {c:6s} 完全相同 {same:,}/{both.height:,}　最大相對誤差 {d.max():.6%}")
        if same != both.height:
            ok = False
    if not ok:
        raise SystemExit("重疊日不一致，拒絕用 FinMind 補洞")

    # 官方有的沿用官方；官方缺的才用 FinMind（來源優先序寫死，不看哪個好看）
    filled = pl.concat(
        [off, fm.join(off.select("date", "index_id"), on=["date", "index_id"], how="anti")]
    )
    filled = filled.sort("date", "index_id").with_columns(
        available_at=pl.col("date")
        .cast(pl.Datetime("us"))
        .dt.offset_by(f"{_AVAILABLE_HOUR}h")
        .dt.replace_time_zone(str(TZ))
    )
    print(
        f"\n合併後 {filled.height:,} 列（官方 {off.height:,} ＋ FinMind 補 {filled.height - off.height:,}）"
    )
    for idx in sorted(filled["index_id"].unique().to_list()):
        s = filled.filter(pl.col("index_id") == idx)
        print(f"  {idx:6s} {s.height:,} 列 {s['date'].min()} ~ {s['date'].max()}")

    if filled.filter(pl.col("date") >= AUTHORITATIVE_FROM).height:
        raise SystemExit(f"合併結果含 {AUTHORITATIVE_FROM} 之後的列——本腳本只補權威區間之前")

    frame = schemas.validate("index_daily", filled)
    if args.check:
        print("\n--check，未寫入。")
        return

    wh = Warehouse(LAKE)
    wh.write("clean", "index_daily", frame, source="finmind")
    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, sch.spec_of("index_daily"), frame)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT index_id, count(*), min(date), max(date) "
                f'FROM "{cfg.pg_schema}"."index_daily" GROUP BY index_id ORDER BY index_id'
            )
            print("\n寫入後 PG：")
            for r in cur.fetchall():
                print(f"  {r[0]:6s} {r[1]:,} 列 {r[2]} ~ {r[3]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
