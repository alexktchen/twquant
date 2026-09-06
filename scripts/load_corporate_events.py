"""把已抓好的除權息結果載入 `corporate_events`（T-098／D-054）。

用法（在 worktree 目錄）：
    python scripts/load_corporate_events.py --check     # 只比對與報告，不寫
    python scripts/load_corporate_events.py --write     # 比對通過才寫湖與 PG

為什麼需要：回測與 ML 標籤都用未還原價，除息當天的機械跌幅被當成虧損。實測
2022-07 之後 77.7% 的事件落在 6~8 月，h=20 標籤在那三個月有 38.2% 的樣本換分位、
原「最差一成」有 17.9% 是除息假象（D-054）。

資料來源：`data/staging/finmind_dividend_result/`（2026-09-03 22:20 已抓完，不需再呼叫 API）。
還原因子一律 `after_price / before_price`；型別對映與「未知類別即 raise」見
`sources/finmind.py:dividend_rows`。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data import schemas
from twquant.data.clean import inconsistent_events
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import dividend_rows
from twquant.data.warehouse import Warehouse

TABLE = "corporate_events"
KEY = ["date", "stock_id"]
_FULL_WIDTH = 10  # 有資料的檔案有 10 欄；width < 10 是 backfill 寫的「無資料」佔位檔


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default="D:/project/twquant/data/staging/finmind_dividend_result")
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    # 台股單日漲跌幅上限。除權息當日的成交價必須落在「參考價 ± 漲跌幅」內，
    # 超出即代表該還原因子不可能是對的（複查實測 6 筆，其中 3 筆當日其實是漲停）。
    ap.add_argument("--max-deviation", type=float, default=0.10)
    ap.add_argument(
        "--drop-inconsistent",
        action="store_true",
        help="與行情矛盾的事件改為排除（逐筆列出）；不給此旗標則直接中止。",
    )
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")
    excluded: list[tuple[object, str]] = []

    staging = pathlib.Path(args.staging)
    if not staging.exists():
        raise SystemExit(f"staging 不存在：{staging}")

    raw: list[dict[str, object]] = []
    placeholders = 0
    for f in sorted(staging.glob("*.parquet")):
        df = pl.read_parquet(f)
        if df.width < _FULL_WIDTH:
            placeholders += 1
            continue
        raw.extend(df.to_dicts())
    print(f"staging：{len(raw):,} 列（另有 {placeholders} 個無資料佔位檔）", flush=True)

    mapped = dividend_rows(raw)  # 未知類別／價格 ≤ 0 會在這裡 raise
    out = schemas.validate(TABLE, pl.DataFrame(mapped))
    dup = out.height - out.select(KEY).unique().height
    if dup:
        raise SystemExit(f"(date, stock_id) 有 {dup} 筆重複——主鍵不成立，停止。")

    by_type = out.group_by("event_type").len().sort("len", descending=True)
    print(
        f"對映後 {out.height:,} 筆、{out['stock_id'].n_unique():,} 檔、"
        f"{out['date'].min()} ~ {out['date'].max()}",
        flush=True,
    )
    print(by_type)
    print(
        f"adj_factor {out['adj_factor'].min():.4f} ~ {out['adj_factor'].max():.4f}；"
        f">1（增資價高於市價）{out.filter(pl.col('adj_factor') > 1.0).height} 筆",
        flush=True,
    )

    # 與行情交叉驗證：假事件會讓累積因子憑空縮小，把該股連續 h 個訊號日推到
    # 橫斷面前段——與 D-054 方向相反、性質相同的「公司行動日曆」洩漏。
    cfg0 = ServingConfig(pg_schema="serving")
    with store.connect(cfg0) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT date, stock_id, open, close FROM "{cfg0.pg_schema}"."prices_daily"')
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
    bad = inconsistent_events(out, px, max_deviation=args.max_deviation)
    if bad.height:
        print(
            "",
            f"與行情矛盾 {bad.height} 筆"
            f"（|open ÷ (adj_factor × 前收) − 1| > {args.max_deviation:.0%}）：",
            flush=True,
        )
        print(
            bad.select(
                "date", "stock_id", "event_type", "adj_factor", "open", "prev_close", "deviation"
            )
        )
        if not args.drop_inconsistent:
            raise SystemExit(
                "還原因子與行情矛盾——不得靜默載入（黃金規則 5）。逐筆確認後，"
                "以 --drop-inconsistent 排除，或修正來源對映。"
            )
        out = out.join(bad.select("date", "stock_id"), on=["date", "stock_id"], how="anti")
        print(f"已排除 → 保留 {out.height:,} 筆", flush=True)
        excluded = [(r["date"], r["stock_id"]) for r in bad.to_dicts()]

    if args.check:
        print("\n--check 完成，未寫入。", flush=True)
        return

    wh = Warehouse(args.lake)
    wh.write("clean", TABLE, out, source="finmind")
    cfg = ServingConfig(pg_schema="serving")
    spec = sch.spec_of(TABLE)
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            # upsert 不會刪列：先前載入過的矛盾事件必須明確移除，否則它們留在表中
            # 繼續污染累積因子（--drop-inconsistent 的語意是「不要這些事件」）。
            for day, sid in excluded:
                cur.execute(
                    f'DELETE FROM "{cfg.pg_schema}"."{TABLE}" WHERE date = %s AND stock_id = %s',
                    (day, sid),
                )
            upsert(cur, cfg.pg_schema, spec, out.select([c for c, _ in spec.columns]))
        conn.commit()
    print(f"\n已寫入湖與 PG：{out.height:,} 筆", flush=True)


if __name__ == "__main__":
    main()
