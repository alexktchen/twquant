"""T-161：把 T-116 回補的長歷史行情清洗後載入 PG。**先驗證，再寫入。**

兩個模式，順序不得顛倒：

    # ① 重疊驗證（不寫入任何東西）——用 2020+ 的重疊區與現有 TWSE 資料逐欄比對
    python scripts/load_finmind_history.py --verify-overlap --from 2022-01-01 --to 2022-12-31

    # ② 載入（只寫 2020-01-02 之前的列，逐年一批）
    python scripts/load_finmind_history.py --load --from 2015-06-01 --to 2019-12-31

**為什麼要有 ①**：現有 `prices_daily` 的 2020+ 來自 TWSE／TPEX 官方原始檔，
而且已經被 T-136（2023 缺腿）、T-141（基準 ETF）、T-151（未還原公司行動）修補過
——那是權威版本。FinMind 在同一段期間也有資料，於是重疊區就是**現成的答案卷**：
清洗器若正確，兩邊的 OHLC 與成交量應該對得起來。對不起來就不該載，
而不是載完再去猜為什麼因子怪怪的。

**為什麼 ② 只寫 2020 之前**：不覆蓋權威資料。`upsert` 是 ON CONFLICT DO UPDATE，
把 FinMind 的 2020+ 寫進去會靜默蓋掉上述三次修補的成果。要寫也得是另一個決定。

範圍上界依 **D-083** 定在 2015-06-01：`limit_up_proxy` 寫死 ×1.10，而台股
2015-06-01 之前的漲跌幅上限是 7%（T-162）。腳本會擋下更早的起日。
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl

from twquant.config import ServingConfig, load_config
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind_history import (
    clean_finmind_prices,
    price_identity_violations,
)

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
SRC = pathlib.Path("D:/project/twquant/data/staging/finmind_hist_price")

# D-083：長歷史起點。更早的資料留在 staging，但不載入——見 T-162。
EARLIEST_ALLOWED = dt.date(2015, 6, 1)
# 現有 prices_daily 的起點。這之後是 TWSE 官方＋三次修補的權威版本，不覆蓋。
AUTHORITATIVE_FROM = dt.date(2020, 1, 2)
_COMPARE_COLS = ("open", "high", "low", "close", "volume", "amount")


def _load_staging(lo: dt.date, hi: dt.date) -> tuple[pl.DataFrame, dict[str, int]]:
    """讀所有個股檔、清洗、串成一張表。回傳 (frame, 剔除統計)。"""
    files = sorted(SRC.glob("*.parquet"))
    if not files:
        raise SystemExit(f"找不到回補的行情檔：{SRC}")
    frames: list[pl.DataFrame] = []
    stats = {"stub": 0, "zero": 0, "null": 0, "dup": 0, "rows_in": 0}
    for f in files:
        raw = pl.read_parquet(f)
        if "date" not in raw.columns:
            # 查無資料的股票會落地成只有 stock_id 一欄的殘檔（實測 336 檔）——計數，不當錯誤
            stats["stub"] += 1
            continue
        r = clean_finmind_prices(raw)
        stats["rows_in"] += r.rows_in
        stats["zero"] += r.dropped_zero_price
        stats["null"] += r.dropped_null
        stats["dup"] += r.dropped_duplicate
        part = r.frame.filter(pl.col("date").is_between(lo, hi))
        if part.height:
            frames.append(part)
    if not frames:
        raise SystemExit(f"{lo} ~ {hi} 清洗後沒有任何列")
    return pl.concat(frames).sort("date", "stock_id"), stats


def _read_pg_prices(conn: object, schema: str, lo: dt.date, hi: dt.date) -> pl.DataFrame:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f"SELECT date, stock_id, open, high, low, close, volume, amount "
            f'FROM "{schema}"."prices_daily" WHERE date BETWEEN %s AND %s',
            (lo, hi),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
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


def verify_overlap(conn: object, schema: str, lo: dt.date, hi: dt.date) -> bool:
    """把 FinMind 清洗結果與現有權威資料逐欄比對。回傳是否通過。**不寫入任何東西。**"""
    fm, stats = _load_staging(lo, hi)
    pg = _read_pg_prices(conn, schema, lo, hi)
    print(f"\nFinMind 清洗後 {fm.height:,} 列（{fm['stock_id'].n_unique():,} 檔）")
    print(f"PG 現有       {pg.height:,} 列（{pg['stock_id'].n_unique():,} 檔）")
    print(
        f"清洗剔除：殘檔 {stats['stub']} 檔、零價 {stats['zero']:,} 列、"
        f"缺值 {stats['null']:,} 列、重複 {stats['dup']:,} 列"
    )

    bad = price_identity_violations(fm)
    print(f"恆等式違規（low≤open,close≤high 且 amount≈volume×均價）：{bad.height:,} 列")
    if bad.height:
        print(bad.head(3))

    j = fm.join(pg, on=["date", "stock_id"], how="inner", suffix="_pg")
    only_fm = fm.height - j.height
    only_pg = pg.height - j.height
    print(f"\n交集 {j.height:,} 列；只在 FinMind {only_fm:,} 列、只在 PG {only_pg:,} 列")
    if j.is_empty():
        print("!! 交集為空，無法比對")
        return False

    print(f"\n{'欄位':8s} {'完全相同':>12s} {'相對誤差 >0.1%':>14s} {'最大相對誤差':>14s}")
    ok = True
    for c in _COMPARE_COLS:
        a, b = pl.col(c).cast(pl.Float64), pl.col(f"{c}_pg").cast(pl.Float64)
        rel = ((a - b).abs() / pl.when(b.abs() > 0).then(b.abs()).otherwise(None)).alias("_rel")
        d = j.select(same=(a == b), rel=rel)
        n_same = int(d["same"].sum())
        n_off = int((d["rel"] > 0.001).sum())
        mx = d["rel"].max()
        share = n_same / j.height
        print(
            f"{c:8s} {n_same:12,} {n_off:14,} "
            f"{(f'{float(mx):.3%}' if mx is not None else '—'):>14s}   ({share:.2%} 相同)"
        )
        # 浮點與四捨五入差異可接受；**超過千分之一的列數占比**才是問題
        if n_off / j.height > 0.01:
            ok = False
    print(
        "\n判定："
        + ("**通過** —— 清洗器與權威資料一致，可以載入" if ok else "**未通過** —— 不得載入")
    )
    return ok


def load(conn: object, schema: str, cfg: ServingConfig, lo: dt.date, hi: dt.date) -> None:
    """逐年一批寫入。每批印出剔除統計與實際寫入列數。"""
    if lo < EARLIEST_ALLOWED:
        raise SystemExit(
            f"起日 {lo} 早於 {EARLIEST_ALLOWED}（D-083）。"
            "2015-06-01 之前台股的漲跌幅上限是 7%，而 limit_up_proxy 寫死 ×1.10"
            "（T-162 未修）——引擎會放行當年不可能成交的價格。要載更早的資料請先做 T-162"
        )
    if hi >= AUTHORITATIVE_FROM:
        raise SystemExit(
            f"迄日 {hi} 已進入 {AUTHORITATIVE_FROM} 之後的權威區間。"
            "該區間的 prices_daily 來自 TWSE／TPEX 官方原始檔，且已被 T-136／T-141／T-151 "
            "修補過；upsert 是 ON CONFLICT DO UPDATE，寫進去會靜默蓋掉那三次修補。"
            "要覆蓋須另立裁決"
        )
    sch.ensure_schema(conn, cfg.pg_schema)
    spec = sch.spec_of("prices_daily")
    t0 = time.perf_counter()
    total = 0
    for year in range(lo.year, hi.year + 1):
        y_lo = max(lo, dt.date(year, 1, 1))
        y_hi = min(hi, dt.date(year, 12, 31))
        frame, stats = _load_staging(y_lo, y_hi)
        bad = price_identity_violations(frame)
        if bad.height:
            # 單位錯誤會讓整年的因子失真，且 schema 攔不住——停下來，不要寫進去
            raise SystemExit(
                f"{year} 有 {bad.height:,} 列違反行情恆等式，拒絕寫入。首列：{bad.row(0, named=True)}"
            )
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            upsert(cur, schema, spec, frame)
        conn.commit()  # type: ignore[attr-defined]
        total += frame.height
        el = (time.perf_counter() - t0) / 60
        print(
            f"  {year}：{frame.height:,} 列寫入（{frame['stock_id'].n_unique():,} 檔、"
            f"{frame['date'].n_unique():,} 交易日）｜"
            f"零價剔除 {stats['zero']:,}、重複 {stats['dup']:,}｜已 {el:.1f} 分",
            flush=True,
        )
    print(f"\n完成：共寫入 {total:,} 列，耗時 {(time.perf_counter() - t0) / 60:.1f} 分")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--to", dest="hi", type=dt.date.fromisoformat, required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify-overlap", action="store_true", help="與現有 PG 資料比對，不寫入")
    g.add_argument("--load", action="store_true", help="寫入 PG（只允許權威區間之前）")
    args = ap.parse_args()

    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        if args.verify_overlap:
            ok = verify_overlap(conn, cfg.pg_schema, args.lo, args.hi)
            raise SystemExit(0 if ok else 1)
        load(conn, cfg.pg_schema, cfg, args.lo, args.hi)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
