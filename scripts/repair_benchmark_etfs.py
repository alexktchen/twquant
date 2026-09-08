"""T-141：補回基準 ETF 在 2023 缺腿期間的行情。

用法：
    python scripts/repair_benchmark_etfs.py --check
    python scripts/repair_benchmark_etfs.py --write

缺陷（T-136 的遺留）：2023-02-20 ~ 2023-06-30 的 TWSE 缺腿同時帶走了 **1,260 個
非四碼代號**（ETF 與權證）。T-136 從 `staging/finmind_hist_price` 補回四碼普通股，
但那份 staging 的宇宙只有 3,007 檔四碼股，涵蓋不到 ETF。

**為什麼這件事重要**：`0050` 在該期間只有 26/89 列。它是 B 軌的判定基準，也是
所有「vs 大盤」比較的對象。缺口後的第一天，0050 的「日報酬」實際上是 60 幾天的
報酬，而策略是 1 日——配對比較的 Beta 與追蹤誤差因此被汙染。

**只抓實際用到的基準**（3 次請求）。其餘 1,257 檔權證與冷門 ETF 沒有任何策略或基準
會用到（策略宇宙是 `^[1-9][0-9]{3}$`），而 FinMind 免費層 600 次/小時的配額正被
T-116 的回補佔用，為它們燒 2 小時不划算。缺口記錄在 T-141 的完成註記裡。

token 只從 `.env` 或環境變數讀，**永不進命令列或輸出**（黃金規則 9）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import time

import polars as pl
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import API
from twquant.data.warehouse import Warehouse

# 實際被引用為基準的 ETF。新增基準時要一併加進來，否則它的缺口不會被修。
BENCHMARKS = ("0050", "0056", "006208")
# `--symbols` 可覆蓋預設清單：新增基準時不必改程式碼（例：櫃買指數型 006201、中型 100 0051）
# 預設涵蓋 2023 的缺腿期；`--from/--to` 可指向其他區間（如 0050 的 2025-06 分割停牌）
DEFAULT_LO, DEFAULT_HI = dt.date(2023, 1, 1), dt.date(2023, 8, 31)
_TOL = 1e-6
_SLEEP = 6.2  # 與 backfill_finmind.py 同：免費層 600 次/小時，留 3% 餘裕


def _token() -> str:
    tok = os.environ.get("FINMIND_TOKEN")
    if tok:
        return tok
    dotenv = pathlib.Path("D:/project/twquant/.env")
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if line.startswith("FINMIND_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("FINMIND_TOKEN 未設（環境變數或 D:/project/twquant/.env）")


def _fetch(sid: str, token: str, lo: dt.date, hi: dt.date) -> pl.DataFrame:
    r = requests.get(
        API,
        params={
            "dataset": "TaiwanStockPrice",
            "data_id": sid,
            "start_date": lo.isoformat(),
            "end_date": hi.isoformat(),
            "token": token,
        },
        timeout=120,
    )
    if r.status_code != 200:
        raise SystemExit(f"{sid}：HTTP {r.status_code} {r.text[:150]}")
    payload = r.json()
    if payload.get("status") != 200 or not payload.get("data"):
        raise SystemExit(f"{sid}：status {payload.get('status')} {str(payload.get('msg'))[:150]}")
    df = pl.DataFrame(payload["data"])
    return df.select(
        pl.col("date").str.to_date().alias("date"),
        pl.lit(sid).alias("stock_id"),
        pl.col("open").cast(pl.Float64),
        pl.col("max").cast(pl.Float64).alias("high"),
        pl.col("min").cast(pl.Float64).alias("low"),
        pl.col("close").cast(pl.Float64),
        pl.col("Trading_Volume").cast(pl.Int64).alias("volume"),
        pl.col("Trading_money").cast(pl.Float64).alias("amount"),
    ).filter(
        # 無成交日 FinMind 以 0 填價格；0 價在下游會變成除以零（T-140），一律丟棄
        (pl.col("open") > 0) & (pl.col("high") > 0) & (pl.col("low") > 0) & (pl.col("close") > 0)
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument(
        "--symbols",
        default="",
        help="逗號分隔的代號；空＝預設的三檔基準 ETF",
    )
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--from", dest="lo", type=dt.date.fromisoformat, default=DEFAULT_LO)
    ap.add_argument("--to", dest="hi", type=dt.date.fromisoformat, default=DEFAULT_HI)
    args = ap.parse_args()
    syms = tuple(x.strip() for x in args.symbols.split(",") if x.strip()) or BENCHMARKS
    lo, hi = args.lo, args.hi
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    cfg = ServingConfig(pg_schema="serving")
    token = _token()
    frames = []
    for i, sid in enumerate(syms):
        if i:
            time.sleep(_SLEEP)  # 與正在跑的 T-116 回補共用配額，不得超速
        df = _fetch(sid, token, lo, hi)
        print(f"{sid}：FinMind 取得 {df.height} 列（{df['date'].min()} ~ {df['date'].max()}）")
        frames.append(df)
    fm = pl.concat(frames)

    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT date, stock_id, open, high, low, close, volume, amount "
            f'FROM "{cfg.pg_schema}".prices_daily '
            "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s",
            (list(syms), lo, hi),
        )
        rows = cur.fetchall()
    pg = pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
        },
        orient="row",
    )
    print(f"PG 現有 {pg.height} 列（{lo} ~ {hi}）")

    # 一致性：重疊列逐欄比對。不符即 raise——映射錯了不能靜默流進去（黃金規則 5）
    ov = pg.join(fm, on=["date", "stock_id"], how="inner", suffix="_fm")
    bad = []
    for c in ("open", "high", "low", "close", "amount"):
        d = ov.filter(
            (pl.col(c) - pl.col(f"{c}_fm")).abs()
            > _TOL * pl.max_horizontal(pl.col(c).abs(), pl.lit(1.0))
        )
        if d.height:
            bad.append((c, d.height, d.select("date", "stock_id", c, f"{c}_fm").head(2).to_dicts()))
    if bad:
        for c, n, sample in bad:
            print(f"  ✗ {c}：{n} 列不符　例：{sample}")
        raise SystemExit("重疊列與 FinMind 不符，不得寫入。")
    print(f"  ✓ 重疊 {ov.height} 列全部相符")

    have = pg.select("date", "stock_id").with_columns(_h=pl.lit(True))
    add = (
        fm.join(have, on=["date", "stock_id"], how="left").filter(pl.col("_h").is_null()).drop("_h")
    )
    add = add.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("limit_up_price"),
        pl.lit(None, dtype=pl.Float64).alias("limit_down_price"),
        pl.col("date")
        .map_elements(
            lambda d: dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ), return_dtype=pl.Datetime
        )
        .cast(schemas.TW_DT)
        .alias("available_at"),
    )
    print(f"\n待補 {add.height} 列")
    for sid in BENCHMARKS:
        print(f"  {sid} 補 {add.filter(pl.col('stock_id') == sid).height} 列")
    if add.is_empty():
        print("無缺口，結束。")
        return

    out = schemas.validate(
        "prices_daily", add.select([c for c, _ in sch.spec_of("prices_daily").columns])
    )
    if args.check:
        print("\n--check 完成，未寫入。")
        return

    wh = Warehouse(args.lake)
    for d in sorted(out["date"].unique().to_list()):
        wh.write("clean", "prices_daily", out.filter(pl.col("date") == d), source="finmind_etf")
    with store.connect(cfg) as conn, conn.cursor() as cur:
        upsert(cur, cfg.pg_schema, sch.spec_of("prices_daily"), out)
        conn.commit()
        cur.execute(
            f'SELECT stock_id, COUNT(*) FROM "{cfg.pg_schema}".prices_daily '
            "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s GROUP BY stock_id ORDER BY 1",
            (list(BENCHMARKS), lo, hi),
        )
        after = cur.fetchall()
    print(f"\n已寫入湖與 PG：{out.height} 列")
    print("修補後各基準的列數：", [(s, n) for s, n in after])


if __name__ == "__main__":
    main()
