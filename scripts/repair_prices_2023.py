"""T-136：從 T-116 的 staging 價格回補修補 `prices_daily` 缺失的 TWSE 腿。

用法：
    python scripts/repair_prices_2023.py --check    # 只比對與報告，不寫
    python scripts/repair_prices_2023.py --write    # 比對通過才寫湖與 PG

缺陷（D-064 缺陷 B）：`prices_daily` 在 **2023-03-09 ~ 2023-05-23 共 51 個交易日**
只有上櫃（TPEX）的列，上市（TWSE）整腿缺席——TWSE 四碼普通股由 ~975 檔掉到 5 檔，
台積電、台泥在該期間 0 列。ETF 同步由 232 掉到 86，也是同一條腿。

後果不只那 51 天：D-029 的衛生濾網（`max_gap_sessions: 5`）會正確地把這些股票判為
資料事件並在復市後再排除 `hygiene_lookback`（60 列），於是 **2023-03 ~ 2023-08 整整
半年，所有回測的宇宙不含台積電與大多數權值股**——而 2023 正是 `ml_net_h20_m`
賺 +40% 的那一年。

修補原則（沿用 `load_finmind_chip.py` 的先例）：
- **不覆寫既有列**。重疊的 (date, stock_id) 只做一致性比對，任何欄位不符即 raise
  ——映射錯了不能靜默流進去（黃金規則 5）。只補 PG 沒有的 (date, stock_id)。
- FinMind 的 `Trading_Volume` 已是**股**、`Trading_money` 是 TWD，與 schema 同單位
  （抽驗 2330 2023-03-01：23,624,972,884 / 45,971,058 = 514 元/股，與收盤 522 同量級）。
- 無成交日 FinMind 以 0 填 OHLC。0 價會在下游變成除以零（見 memory 的 traps），
  故一律丟棄，與 `clean_prices` 丟棄 "--" 的行為一致。
- `available_at` 用 `date 18:00 Asia/Taipei`，與 `clean_prices` 的 `_at(date, 18)` 相同。
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.warehouse import Warehouse

STAGING = pathlib.Path("D:/project/twquant/data/staging/finmind_hist_price")
LO, HI = (
    dt.date(2023, 1, 1),
    dt.date(2023, 8, 31),
)  # 涵蓋全期掃描找到的三段缺腿，前後留正常日供比對與參照
_PRICE_COLS = ("open", "high", "low", "close")
_TOL = 1e-6
# PG 當日四碼普通股數 ÷ staging 當日數；低於此即判為「整腿缺席」。
# 0.7 是刻意寬鬆的：正常日實測比值約 0.85（PG 宇宙本就小於 staging 的 3,007 檔歷史宇宙），
# 缺腿日約 0.38，兩者差距很大，門檻落在中間任何值結果都相同。
_BROKEN_RATIO = 0.7


def _staging_frame() -> pl.DataFrame:
    frames = []
    for f in sorted(STAGING.glob("*.parquet")):
        df = pl.read_parquet(f)
        if "close" not in df.columns:  # 空股票的佔位檔只有 stock_id
            continue
        s = df.select(
            pl.col("date").str.to_date().alias("date"),
            pl.col("stock_id").cast(pl.String),
            pl.col("open").cast(pl.Float64),
            pl.col("max").cast(pl.Float64).alias("high"),
            pl.col("min").cast(pl.Float64).alias("low"),
            pl.col("close").cast(pl.Float64),
            pl.col("Trading_Volume").cast(pl.Int64).alias("volume"),
            pl.col("Trading_money").cast(pl.Float64).alias("amount"),
        ).filter((pl.col("date") >= LO) & (pl.col("date") <= HI))
        if s.height:
            frames.append(s)
    out = pl.concat(frames)
    # 無成交日 FinMind 以 0 填價格；0 價在下游會變成除以零，一律丟棄
    before = out.height
    out = out.filter(
        (pl.col("open") > 0) & (pl.col("high") > 0) & (pl.col("low") > 0) & (pl.col("close") > 0)
    )
    print(f"staging：{before:,} 列 → 丟棄 0 價後 {out.height:,} 列", flush=True)
    return out.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("limit_up_price"),
        pl.lit(None, dtype=pl.Float64).alias("limit_down_price"),
        pl.col("date")
        .map_elements(
            lambda d: dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ), return_dtype=pl.Datetime
        )
        .cast(schemas.TW_DT)
        .alias("available_at"),
    )


def _pg_frame(cfg: ServingConfig) -> pl.DataFrame:
    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT date, stock_id, open, high, low, close, volume, amount "
            f'FROM "{cfg.pg_schema}".prices_daily WHERE date BETWEEN %s AND %s',
            (LO, HI),
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    cfg = ServingConfig(pg_schema="serving")
    stg = _staging_frame()
    pg = _pg_frame(cfg)
    print(f"PG 現有 {LO}~{HI}：{pg.height:,} 列、{pg['date'].n_unique()} 個日期", flush=True)

    # ── 一致性：重疊的 (date, stock_id) 每個價量欄位都要相符 ──────────
    ov = pg.join(stg, on=["date", "stock_id"], how="inner", suffix="_stg")
    print(f"重疊 {ov.height:,} 列，逐欄比對中…", flush=True)
    bad = []
    for c in (*_PRICE_COLS, "amount"):
        d = ov.filter(
            (pl.col(c) - pl.col(f"{c}_stg")).abs()
            > _TOL * pl.max_horizontal(pl.col(c).abs(), pl.lit(1.0))
        )
        if d.height:
            bad.append(
                (c, d.height, d.select("date", "stock_id", c, f"{c}_stg").head(3).to_dicts())
            )
    dv = ov.filter(pl.col("volume") != pl.col("volume_stg"))
    if dv.height:
        bad.append(
            (
                "volume",
                dv.height,
                dv.select("date", "stock_id", "volume", "volume_stg").head(3).to_dicts(),
            )
        )
    if bad:
        for c, n, sample in bad:
            print(f"  ✗ {c}：{n:,} 列不符　例：{sample}", flush=True)
        raise SystemExit("重疊列的值與 staging 不符——映射可能有誤，不得寫入（黃金規則 5）。")
    print(f"  ✓ 重疊 {ov.height:,} 列全部相符（價格容差 {_TOL}、成交股數需完全相等）", flush=True)

    # ── 只補 PG 沒有的 (date, stock_id)，且**只在壞掉的日子** ──────────
    #
    # 壞日的判準：PG 當日的四碼普通股數遠少於 **staging 當日**的數量。
    #
    # 不能用 PG 自己的中位數當基準——實測 62 天裡有 51 天是壞的，中位數本身就是壞的值，
    # 自我參照的統計在缺口佔多數時直接失效（第一版就是這樣回傳空集合）。
    # staging 是獨立來源，拿它當分母才有意義。
    #
    # 為什麼要限定日子：staging 的宇宙是 3,007 檔（含已下市），PG 的價格宇宙較小，
    # 故正常日也會有 ~280 列「可補」。補它們是對的方向（存活者偏誤），但那是**另一件事**：
    # 會改變沒壞的那些天的宇宙，讓重跑的差異無法歸因。分開做，見 T-137。
    _C4 = r"^[1-9][0-9]{3}$"
    pg_n = pg.filter(pl.col("stock_id").str.contains(_C4)).group_by("date").len()
    st_n = stg.filter(pl.col("stock_id").str.contains(_C4)).group_by("date").len()
    cmp_ = (
        pg_n.join(st_n, on="date", suffix="_stg")
        .with_columns(ratio=pl.col("len") / pl.col("len_stg"))
        .sort("date")
    )
    broken = set(cmp_.filter(pl.col("ratio") < _BROKEN_RATIO)["date"].to_list())
    print("")
    print(
        f"四碼普通股 PG/staging 比值：中位數 {cmp_['ratio'].median():.2f}、最低 {cmp_['ratio'].min():.2f}"
    )
    print(f"判為缺腿（比值 < {_BROKEN_RATIO}）的日子 {len(broken)} 天", end="")
    if broken:
        print(f"（{min(broken)} ~ {max(broken)}）")
    else:
        print("　→ 無缺口")
        return
    print(
        f"其餘 {cmp_.height - len(broken)} 天不在本次修補範圍（可補列數屬存活者偏誤，另立 T-137）"
    )

    # 只補「窗口前後正常日 PG 本來就有」的股票。
    #
    # staging 的宇宙是 3,007 檔（含已下市、興櫃型代號），比 PG 的價格宇宙大。不設這道限制，
    # 修補窗口會多出 294 檔**只存在於那 50 天**的幽靈股，宇宙在接縫處由 1,767 跳到 2,058。
    # 而且衛生濾網攔不住：那些股票在窗口前沒有任何列，`shift(1)` 為 null、
    # 缺口 `fill_null(0)` 後判為 false，於是會被當成正常股放進宇宙。
    # 補齊歷史宇宙是對的方向，但那是 T-137，要整段期間一起做，不能只在這 50 天做。
    normal = sorted(set(pg["date"].to_list()) - broken)
    known = set(pg.filter(pl.col("date").is_in(normal))["stock_id"].unique().to_list())
    have = pg.select("date", "stock_id").with_columns(_h=pl.lit(True))
    add = (
        stg.filter(pl.col("date").is_in(list(broken)))
        .filter(pl.col("stock_id").is_in(list(known)))
        .join(have, on=["date", "stock_id"], how="left")
        .filter(pl.col("_h").is_null())
        .drop("_h")
    )
    print(f"窗口前後正常日 PG 已知的股票 {len(known):,} 檔；只補這些，避免製造幽靈股")
    print(
        f"\n待補 {add.height:,} 列、{add['stock_id'].n_unique():,} 檔、{add['date'].n_unique()} 個日期"
    )
    if add.is_empty():
        print("無缺口，結束。")
        return
    by_day = add.group_by("date").len().sort("date")
    print("逐日補列數（前 8 天）：", by_day.head(8).to_dicts())
    for sid in ("2330", "1101", "2317"):
        n = add.filter(pl.col("stock_id") == sid).height
        print(f"  {sid} 補 {n} 列")

    out = schemas.validate(
        "prices_daily", add.select([c for c, _ in sch.spec_of("prices_daily").columns])
    )
    if args.check:
        print("\n--check 完成，未寫入。", flush=True)
        return

    wh = Warehouse(args.lake)
    days = sorted(out["date"].unique().to_list())
    for d in days:
        # 寫成獨立 source：`_write_partition` 只替換同名檔，故與既有 part-pg.parquet 並存，
        # scan 時 union。因為只補「兩邊都沒有」的列，union 不會產生重複。
        wh.write("clean", "prices_daily", out.filter(pl.col("date") == d), source="finmind_repair")
    with store.connect(cfg) as conn, conn.cursor() as cur:
        upsert(cur, cfg.pg_schema, sch.spec_of("prices_daily"), out)
        conn.commit()
    print(f"\n已寫入湖與 PG：{out.height:,} 列", flush=True)

    # ── 寫後驗證：湖不得出現重複 (date, stock_id)，PG 的缺腿須消失 ──────
    lake = (
        wh.scan("prices_daily")
        .filter(pl.col("date").is_in(days))
        .select("date", "stock_id")
        .collect()
    )
    if lake.height != lake.unique().height:
        raise SystemExit(
            f"湖在修補日出現重複 (date, stock_id)：{lake.height:,} 列但只有 "
            f"{lake.unique().height:,} 個相異鍵——分區檔可能重疊，請檢查 "
            "clean/prices_daily/date=*/ 下的 part-*.parquet"
        )
    print(f"  ✓ 湖回讀 {lake.height:,} 列、無重複鍵", flush=True)

    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT MIN(n), MAX(n) FROM (SELECT COUNT(*) n FROM "{cfg.pg_schema}".prices_daily '
            "WHERE date = ANY(%s) AND stock_id ~ '^[1-9][0-9]{3}$' GROUP BY date) t",
            (days,),
        )
        lo_n, hi_n = cur.fetchone()
        cur.execute(
            f'SELECT COUNT(*) FROM "{cfg.pg_schema}".prices_daily '
            "WHERE stock_id = '2330' AND date = ANY(%s)",
            (days,),
        )
        n2330 = cur.fetchone()[0]
    print(f"  ✓ PG 修補日的四碼普通股數：{lo_n} ~ {hi_n}（修補前約 795）")
    print(f"  ✓ 2330 在修補日有 {n2330} 列（修補前 0，共 {len(days)} 天）")
    if n2330 == 0 or lo_n < 1500:
        raise SystemExit("修補後仍缺腿——寫入未生效，請檢查。")


if __name__ == "__main__":
    main()
