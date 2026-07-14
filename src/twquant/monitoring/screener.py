"""策略選股池（SPEC-007 §6 / T-040）——主力買賣超研究的高勝率條件常態化為每日池。

規則屬程式碼（下方 _CONDS/_SORT 以池 key 對應）、閾值屬 configs/screener.yaml
（黃金規則 8）。指標全部只用 ≤ as_of 的資料；主力淨買超來自 broker_trades 日聚合
（富邦DJ top-15±，單位：股）。純函數（daily_metrics/assign_pools/add_streaks/
build_payload）不碰 PG 可單元測試；compute 負責 PG 讀取。描述性選股池、非交易訊號。
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import polars as pl

from twquant.config import ScreenerConfig, ServingConfig, load_config
from twquant.monitoring import snapshot as snap

_SID4 = r"^[1-9]\d{3}$"  # 四碼普通股（排除 ETF/權證等）
# 池 key → (排序欄, 遞減?)：成員依主指標排序後截斷
_SORT: dict[str, tuple[str, bool]] = {
    "main_accum": ("accum_ratio", True),
    "chip_revenue": ("conc", True),
    "trend_revenue": ("yoy", True),
    "triple": ("conc", True),
    "main_distrib": ("accum_ratio", False),
}


def screener_cfg() -> ScreenerConfig:
    """每個請求重新載入 configs/screener.yaml（不快取；改閾值即時生效）。"""
    return load_config(snap.CONFIGS_DIR / "screener.yaml", ScreenerConfig)


def daily_metrics(
    prices: pl.DataFrame, broker: pl.DataFrame, fund: pl.DataFrame, cfg: ScreenerConfig
) -> pl.DataFrame:
    """日頻輸入 → 每 (date, stock_id) 指標與宇宙百分位（AC-7/AC-8）。

    輸入欄位：prices=(date, stock_id, close, volume, amount)、broker=(date, stock_id, net)、
    fund=(date, stock_id, yoy)。宇宙＝四碼普通股 ∩ amt 窗均額 ≥ 下限；
    百分位＝當日宇宙內 rank(average)/count（缺值列不參與排名）。
    """
    w = cfg.windows
    df = (
        prices.filter(pl.col("stock_id").str.contains(_SID4))
        .join(broker, on=["date", "stock_id"], how="left")
        .join(fund, on=["date", "stock_id"], how="left")
        .sort(["stock_id", "date"])
        # YoY 為月頻慢變數且因子物化可落後行情日：取 ≤ 當日的最近已物化值前向沿用（PIT 安全）
        .with_columns(pl.col("net").fill_null(0), pl.col("yoy").forward_fill().over("stock_id"))
    )
    df = df.with_columns(
        _mn_c=pl.col("net").rolling_sum(w.conc).over("stock_id"),
        _vol_c=pl.col("volume").rolling_sum(w.conc).over("stock_id"),
        _mn_a=pl.col("net").rolling_sum(w.accum).over("stock_id"),
        _vol_a=pl.col("volume").rolling_sum(w.accum).over("stock_id"),
        ma_fast=pl.col("close").rolling_mean(w.ma_fast).over("stock_id"),
        ma_slow=pl.col("close").rolling_mean(w.ma_slow).over("stock_id"),
        amt_avg=pl.col("amount").rolling_mean(w.amt).over("stock_id"),
        prev_close=pl.col("close").shift(1).over("stock_id"),
    )
    df = df.with_columns(
        conc=pl.when(pl.col("_vol_c") > 0).then(pl.col("_mn_c") / pl.col("_vol_c")).otherwise(None),
        accum_ratio=pl.when(pl.col("_vol_a") > 0)
        .then(pl.col("_mn_a") / pl.col("_vol_a"))
        .otherwise(None),
    )
    df = df.filter(pl.col("amt_avg") >= cfg.liquidity_floor_twd)
    df = df.with_columns(
        pct_conc=pl.col("conc").rank("average").over("date") / pl.col("conc").count().over("date"),
        pct_accum=pl.col("accum_ratio").rank("average").over("date")
        / pl.col("accum_ratio").count().over("date"),
    )
    return df.drop("_mn_c", "_vol_c", "_mn_a", "_vol_a")


def assign_pools(metrics: pl.DataFrame, cfg: ScreenerConfig) -> pl.DataFrame:
    """加上每池布林欄 in_<key>（AC-9）。指標缺值（窗未滿/無 YoY）一律不入池。"""
    trend = (pl.col("close") > pl.col("ma_fast")) & (pl.col("ma_fast") > pl.col("ma_slow"))
    yoy_ok = pl.col("yoy") >= cfg.yoy_min
    conds: dict[str, pl.Expr] = {
        "main_accum": pl.col("pct_accum") >= cfg.pct_hi,
        "chip_revenue": (pl.col("pct_conc") >= cfg.pct_hi) & yoy_ok,
        "trend_revenue": trend & yoy_ok,
        "triple": (pl.col("pct_conc") >= cfg.pct_hi) & trend & yoy_ok,
        "main_distrib": pl.col("pct_accum") <= cfg.pct_lo,
    }
    return metrics.with_columns(
        [cond.fill_null(value=False).alias(f"in_{k}") for k, cond in conds.items()]
    )


def add_streaks(flags: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """每池加 streak_<key>＝截至該日的連續在池天數，中斷歸零（AC-10）。"""
    df = flags.sort(["stock_id", "date"])
    for k in keys:
        flag = pl.col(f"in_{k}")
        df = df.with_columns(_blk=(~flag).cast(pl.Int32).cum_sum().over("stock_id"))
        df = df.with_columns(
            (flag.cast(pl.Int32).cum_sum().over(["stock_id", "_blk"]) * flag.cast(pl.Int32)).alias(
                f"streak_{k}"
            )
        ).drop("_blk")
    return df


def _round(v: object, nd: int) -> float | None:
    return None if v is None else round(float(v), nd)  # type: ignore[arg-type]


def _member(r: dict[str, Any], meta: dict[str, str], key: str) -> dict[str, Any]:
    prev = r["prev_close"]
    chg = None if prev in (None, 0) else float(r["close"]) / float(prev) - 1
    return {
        "stock_id": r["stock_id"],
        "name": meta.get(str(r["stock_id"])),
        "close": _round(r["close"], 2),
        "chg": _round(chg, 4),
        "yoy": _round(r["yoy"], 3),
        "conc": _round(r["conc"], 4),
        "accum_ratio": _round(r["accum_ratio"], 4),
        "streak": int(r[f"streak_{key}"]),
    }


def build_payload(
    flags: pl.DataFrame, cfg: ScreenerConfig, meta: dict[str, str], n: int
) -> dict[str, Any]:
    """最新日成員 + 連續在池天數 → API payload（AC-11）。"""
    if flags.is_empty():
        return {"as_of": None, "source": "pg", "universe_n": 0, "pools": []}
    keys = [p.key for p in cfg.pools]
    dates = flags["date"].unique().sort()
    recent = flags.filter(pl.col("date").is_in(dates.tail(cfg.streak_days)))
    last = add_streaks(recent, keys).filter(pl.col("date") == dates[-1])
    pools: list[dict[str, Any]] = []
    for p in cfg.pools:
        col, desc = _SORT[p.key]
        mem = last.filter(pl.col(f"in_{p.key}")).sort(col, descending=desc, nulls_last=True)
        members = [_member(r, meta, p.key) for r in mem.head(n).iter_rows(named=True)]
        pools.append(
            {
                "key": p.key,
                "name": p.name,
                "rule": p.rule,
                "note": p.note,
                "total": mem.height,
                "members": members,
            }
        )
    return {
        "as_of": dates[-1].isoformat(),
        "source": "pg",
        "universe_n": last.height,
        "pools": pools,
    }


# ── PG 讀取（monitoring 層允許 IO；psycopg 延後 import，比照 stock_api）─────────


def _span_days(cfg: ScreenerConfig) -> int:
    """回看窗（交易日）換算日曆日（週末/假日餘裕）。"""
    need = max(cfg.windows.accum, cfg.windows.ma_slow) + cfg.streak_days + 5
    return math.ceil(need * 1.6) + 14


def _fetch(
    scfg: ServingConfig,
    cfg: ScreenerConfig,
    as_of: dt.date | None = None,
    floor_from: dt.date | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, str]]:
    """一次抓齊：宇宙價量、主力日聚合、YoY 因子值、名稱表。回看窗由配置推導。

    as_of 給定時只取 ≤ as_of 的資料（歷史回算）；None＝最新行情日。
    floor_from 給定時下限改由它推導（快照回補整段時用），否則由 as_of/最新日推導。
    """
    from twquant.data.serving import store

    sch = scfg.pg_schema
    with store.connect(scfg) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT max(date) FROM "{sch}".prices_daily WHERE date <= %s', (as_of or dt.date.max,)
        )
        row = cur.fetchone()
        latest = row[0] if row else None
        if latest is None:
            empty = pl.DataFrame()
            return empty, empty, empty, {}
        floor = (floor_from or latest) - dt.timedelta(days=_span_days(cfg))
        cur.execute(
            f'SELECT date, stock_id, close, volume, amount FROM "{sch}".prices_daily '
            "WHERE date >= %s AND date <= %s AND stock_id ~ %s",
            (floor, latest, _SID4),
        )
        px = cur.fetchall()
        cur.execute(
            "SELECT date, stock_id, "
            "SUM(COALESCE(buy_shares, 0) - COALESCE(sell_shares, 0)) AS net "
            f'FROM "{sch}".broker_trades WHERE date >= %s AND date <= %s GROUP BY 1, 2',
            (floor, latest),
        )
        bk = cur.fetchall()
        cur.execute(
            f'SELECT date, stock_id, value FROM "{sch}".factor_values '
            "WHERE factor = 'fundamental.revenue_yoy' AND date >= %s AND date <= %s",
            (floor, latest),
        )
        fd = cur.fetchall()
        cur.execute(f'SELECT stock_id, name FROM "{sch}".stock_meta')
        meta = {str(r[0]): str(r[1]) for r in cur.fetchall() if r[1]}
    prices = pl.DataFrame(
        px,
        schema={
            "date": pl.Date,
            "stock_id": pl.Utf8,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
        },
        orient="row",
    )
    broker = pl.DataFrame(
        bk, schema={"date": pl.Date, "stock_id": pl.Utf8, "net": pl.Int64}, orient="row"
    )
    fund = pl.DataFrame(
        fd, schema={"date": pl.Date, "stock_id": pl.Utf8, "yoy": pl.Float64}, orient="row"
    )
    return prices, broker, fund, meta


def compute(
    wh: object, scfg: ServingConfig, n: int = 30, as_of: dt.date | None = None
) -> dict[str, Any]:
    """PG 抓輸入 → 指標 → 池歸屬 → payload。PG 不可用讓例外上拋（端點轉 503）。

    as_of 給定＝以該日（含）以前資料回算歷史池（非交易日自動貼齊前一交易日）。
    """
    cfg = screener_cfg()
    prices, broker, fund, meta = _fetch(scfg, cfg, as_of)
    if prices.is_empty():
        return {"as_of": None, "source": "pg", "universe_n": 0, "pools": []}
    flags = assign_pools(daily_metrics(prices, broker, fund, cfg), cfg)
    return build_payload(flags, cfg, meta, n)


# ── 每日快照與回溯（T-041：screener_daily 表；寫入走 CLI，API 唯讀）─────────────


def snapshot_frame(
    flags: pl.DataFrame, cfg: ScreenerConfig, d0: dt.date, d1: dt.date
) -> pl.DataFrame:
    """flags（含回看窗）→ [d0, d1] 內每日每池成員的快照列（AC-14）。

    streak 以整個 flags 窗計、封頂 streak_days——與即時 payload（只回看
    streak_days 日）語義一致。欄位順序＝SCREENER_DAILY_SPEC（不含 created_at）。
    """
    keys = [p.key for p in cfg.pools]
    st = add_streaks(flags, keys).with_columns(
        chg=pl.when(pl.col("prev_close") > 0)
        .then(pl.col("close") / pl.col("prev_close") - 1)
        .otherwise(None),
        universe_n=pl.len().over("date").cast(pl.Int32),
    )
    st = st.filter((pl.col("date") >= d0) & (pl.col("date") <= d1))
    parts = [
        st.filter(pl.col(f"in_{k}")).select(
            "date",
            pool=pl.lit(k),
            stock_id=pl.col("stock_id"),
            close=pl.col("close"),
            chg=pl.col("chg"),
            yoy=pl.col("yoy"),
            conc=pl.col("conc"),
            accum_ratio=pl.col("accum_ratio"),
            streak=pl.min_horizontal(pl.col(f"streak_{k}"), pl.lit(cfg.streak_days)).cast(pl.Int32),
            universe_n=pl.col("universe_n"),
        )
        for k in keys
    ]
    return pl.concat(parts)


def write_snapshot(cur: Any, schema: str, frame: pl.DataFrame) -> None:
    """快照列 upsert（ON CONFLICT DO NOTHING＝首錄保留、冪等，AC-12）。

    比照 chip_ingest：不 import psycopg，只在呼叫端傳入的 cursor 上操作。
    """
    cols = (
        "date",
        "pool",
        "stock_id",
        "close",
        "chg",
        "yoy",
        "conc",
        "accum_ratio",
        "streak",
        "universe_n",
    )
    collist = ", ".join(f'"{c}"' for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    cur.executemany(
        f'INSERT INTO "{schema}".screener_daily ({collist}, created_at) '
        f"VALUES ({ph}, now()) ON CONFLICT (date, pool, stock_id) DO NOTHING",
        list(frame.select(cols).iter_rows()),
    )


def snapshot(
    scfg: ServingConfig, from_: dt.date | None = None, to: dt.date | None = None
) -> dict[str, Any]:
    """把 [from_, to] 的每日池成員存入 serving.screener_daily（省略＝最新交易日）。

    單次抓取整段回算後批次寫入（回補整年也只抓一次）；建表冪等。
    """
    from twquant.data.serving import schema as sch
    from twquant.data.serving import store

    if from_ is not None and to is not None and from_ > to:
        raise ValueError(f"快照區間顛倒：{from_} > {to}")
    cfg = screener_cfg()
    prices, broker, fund, _meta = _fetch(scfg, cfg, as_of=to, floor_from=from_)
    if prices.is_empty():
        return {"from": None, "to": None, "rows": 0}
    flags = assign_pools(daily_metrics(prices, broker, fund, cfg), cfg)
    if flags.is_empty():
        return {"from": None, "to": None, "rows": 0}
    dmax = flags["date"].max()
    d1: dt.date = dmax if to is None else min(to, dmax)  # type: ignore[assignment, type-var]
    d0: dt.date = d1 if from_ is None else from_
    frame = snapshot_frame(flags, cfg, d0, d1)
    with store.connect(scfg) as conn:
        sch.ensure_schema(conn, scfg.pg_schema)
        with conn.cursor() as cur:
            write_snapshot(cur, scfg.pg_schema, frame)
            cur.execute(
                f'SELECT count(*) FROM "{scfg.pg_schema}".screener_daily '
                "WHERE date >= %s AND date <= %s",
                (d0, d1),
            )
            row = cur.fetchone()
        conn.commit()
    return {"from": d0.isoformat(), "to": d1.isoformat(), "rows": int(row[0]) if row else 0}


def payload_from_rows(
    rows: list[dict[str, Any]], cfg: ScreenerConfig, meta: dict[str, str], n: int, day: dt.date
) -> dict[str, Any]:
    """screener_daily 某日列 → API payload（純函數，AC-13）。排序/截斷同即時路徑。"""
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_pool.setdefault(str(r["pool"]), []).append(r)
    pools: list[dict[str, Any]] = []
    for p in cfg.pools:
        mem = by_pool.get(p.key, [])
        col, desc = _SORT[p.key]
        # None 一律排最後；遞減以負號實現（排序欄皆為 float）
        mem.sort(
            key=lambda r: (
                r[col] is None,
                (-float(r[col]) if desc else float(r[col])) if r[col] is not None else 0.0,
            )
        )
        members = [
            {
                "stock_id": str(r["stock_id"]),
                "name": meta.get(str(r["stock_id"])),
                "close": _round(r["close"], 2),
                "chg": _round(r["chg"], 4),
                "yoy": _round(r["yoy"], 3),
                "conc": _round(r["conc"], 4),
                "accum_ratio": _round(r["accum_ratio"], 4),
                "streak": int(r["streak"]),
            }
            for r in mem[:n]
        ]
        pools.append(
            {
                "key": p.key,
                "name": p.name,
                "rule": p.rule,
                "note": p.note,
                "total": len(mem),
                "members": members,
            }
        )
    universe = max((int(r["universe_n"]) for r in rows if r["universe_n"] is not None), default=0)
    return {
        "as_of": day.isoformat(),
        "source": "pg",
        "universe_n": universe,
        "pools": pools,
    }


def read_snapshot(
    scfg: ServingConfig, cfg: ScreenerConfig, day: dt.date, n: int
) -> dict[str, Any] | None:
    """讀某日快照 → payload；該日無快照（或表尚未建立）回 None（呼叫端改即時回算）。"""
    import psycopg
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    sch = scfg.pg_schema
    try:
        with store.connect(scfg) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f'SELECT * FROM "{sch}".screener_daily WHERE date = %s', (day,))
            rows = list(cur.fetchall())
            cur.execute(f'SELECT stock_id, name FROM "{sch}".stock_meta')
            meta = {str(r["stock_id"]): str(r["name"]) for r in cur.fetchall() if r["name"]}
    except psycopg.errors.UndefinedTable:
        return None
    if not rows:
        return None
    return payload_from_rows(rows, cfg, meta, n, day)


def payload_for(wh: object, scfg: ServingConfig, n: int, date_str: str | None) -> dict[str, Any]:
    """端點分派：date 省略＝最新即時；給定＝先讀快照（snapshot=true）、無則回算。"""
    from twquant.errors import DataError

    if date_str is None:
        out = compute(wh, scfg, n)
        out["snapshot"] = False
        return out
    try:
        day = dt.date.fromisoformat(date_str)
    except ValueError as exc:
        raise DataError("screener_daily", None, f"日期格式錯誤：{date_str!r}") from exc
    snap_payload = read_snapshot(scfg, screener_cfg(), day, n)
    if snap_payload is not None:
        snap_payload["snapshot"] = True
        return snap_payload
    out = compute(wh, scfg, n, as_of=day)
    out["snapshot"] = False
    return out
