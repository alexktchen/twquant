"""T-170：把 2014 起的除權息載入 `corporate_events`（先驗證，只寫 2020 前）。

**為什麼不用 `load_corporate_events.py`**：那支比對的是資料湖（主湖此表為空，等於沒驗證），
而且會整批寫、動到 2020 之後的權威區間。本腳本照今晚其他表的作法：
對 PG 的重疊區逐欄驗證 → 只寫 2020-01-02 之前。

**為什麼要重抓**：`attach_adjustment` 原本只擋「事件表全空」（T-169 已改成擋涵蓋範圍）。
長歷史載到 2015-06 後，`corporate_events` 仍只有 2019 起——2015-06~2018-12 的
**886 個交易日對應 0 筆事件**，那幾年的 ML 標籤會靜默退回價格報酬。
FinMind 的 `TaiwanStockDividendResult` 其實有 2014 起（2330 實測 2014-07-14），
舊 staging 只是用 2019 當起日抓的。

**與行情矛盾的事件逐筆排除**：除權息當日的開盤價必須落在「參考價 × 漲跌幅」內，
超出即代表該還原因子不可能對。實測 10 筆（多為 stock_dividend），全部列出後排除
——不是靜默丟棄（黃金規則 5）。

用法：
    python scripts/load_corporate_events_history.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl

from twquant.config import ServingConfig, load_config
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import dividend_rows

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
STAGING = pathlib.Path("D:/project/twquant/data/staging/finmind_dividend_2014")
AUTHORITATIVE_FROM = dt.date(2020, 1, 2)
# 台股單日漲跌幅上限。開盤價偏離「adj × 前收」超過此值 → 還原因子不可能是對的。
MAX_DEVIATION = 0.10
_MIN_STAGING_WIDTH = 5  # 少於此欄數即「該檔無資料」的佔位檔


def main() -> None:
    raw: list[dict[str, object]] = []
    placeholders = 0
    for f in sorted(STAGING.glob("*.parquet")):
        df = pl.read_parquet(f)
        if df.width < _MIN_STAGING_WIDTH:
            placeholders += 1
            continue
        raw.extend(df.to_dicts())
    print(f"staging {len(raw):,} 列（無資料佔位 {placeholders}）")
    out = schemas.validate("corporate_events", pl.DataFrame(dividend_rows(raw)))
    print(
        f"對映後 {out.height:,} 筆、{out['stock_id'].n_unique():,} 檔、"
        f"{out['date'].min()} ~ {out['date'].max()}"
    )

    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT date, stock_id, open, close FROM "{cfg.pg_schema}"."prices_daily"')
            px = pl.DataFrame(
                cur.fetchall(),
                schema={
                    "date": pl.Date,
                    "stock_id": pl.String,
                    "open": pl.Float64,
                    "close": pl.Float64,
                },
                orient="row",
            )
        prev = px.sort("stock_id", "date").with_columns(
            _prev=pl.col("close").shift(1).over("stock_id", order_by="date")
        )
        chk = (
            out.join(
                prev.select("date", "stock_id", "open", "_prev"),
                on=["date", "stock_id"],
                how="left",
            )
            .drop_nulls("_prev")
            .with_columns(
                dev=(pl.col("open") / (pl.col("adj_factor") * pl.col("_prev")) - 1.0).abs()
            )
        )
        bad = chk.filter(pl.col("dev") > MAX_DEVIATION)
        print(
            f"\n與行情矛盾 {bad.height} 筆（|open ÷ (adj × 前收) − 1| > {MAX_DEVIATION:.0%}），逐筆排除："
        )
        for r in bad.select(
            "date", "stock_id", "event_type", "adj_factor", "open", "_prev", "dev"
        ).to_dicts():
            print(
                f"  {r['date']} {r['stock_id']} {r['event_type']:24s} adj={r['adj_factor']:.4f} "
                f"open={r['open']} 前收={r['_prev']} 偏差={r['dev']:.1%}"
            )
        keep = out.join(bad.select("date", "stock_id"), on=["date", "stock_id"], how="anti")

        with conn.cursor() as cur:
            cur.execute(
                f'SELECT date, stock_id, event_type, adj_factor FROM "{cfg.pg_schema}"."corporate_events"'
            )
            pg = pl.DataFrame(
                cur.fetchall(),
                schema={
                    "date": pl.Date,
                    "stock_id": pl.String,
                    "event_type": pl.String,
                    "adj_factor": pl.Float64,
                },
                orient="row",
            )
        j = keep.join(pg, on=["date", "stock_id"], how="inner", suffix="_pg")
        print(f"\n與 PG 重疊 {j.height:,} 筆")
        if j.height:
            same_t = int(j.filter(pl.col("event_type") == pl.col("event_type_pg")).height)
            d = j.select((pl.col("adj_factor") - pl.col("adj_factor_pg")).abs().alias("r"))["r"]
            off = int((d > 1e-9).sum())
            print(
                f"  event_type 相同 {same_t:,}/{j.height:,}；adj_factor 不同 {off:,}（{off / j.height:.3%}）"
            )
            if off / j.height > 0.005 or same_t / j.height < 0.995:
                raise SystemExit("重疊不一致，拒絕載入")

        new = keep.filter(pl.col("date") < AUTHORITATIVE_FROM)
        print(
            f"\n待寫（{AUTHORITATIVE_FROM} 前）{new.height:,} 筆、"
            f"{new['date'].min()} ~ {new['date'].max()}、{new['stock_id'].n_unique():,} 檔"
        )
        print(new.group_by("event_type").len().sort("len", descending=True))
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, sch.spec_of("corporate_events"), new)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT min(date), max(date), count(*) FROM "{cfg.pg_schema}"."corporate_events"'
            )
            print(f"\n寫入後 corporate_events：{cur.fetchone()}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
