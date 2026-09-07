"""T-171：把 T-116 回補的長歷史籌碼／評價 staging 載入 PG（法人、融資券、本益比）。

**為什麼要另寫一支**，而不是直接用 `load_finmind_chip.py`：那支比對的對象是**資料湖**，
而主湖的法人資料只有 41 天（多數在 `lake_rebuild`）——41 天的重疊驗證不了映射。
本腳本改成對 **PG 的 2020+ 全量**比對，那才是權威集合（與行情載入器同一套做法）。

映射一律沿用 `sources/finmind.py` 的既有函數，不另寫一份：
`institutional_net`／`margin_balances`／`valuation_rows`。它們各自帶著自己的
防呆（未知投資人類別即 raise、缺欄即 raise、殖利率轉小數…），重寫等於把那些防呆丟掉。

**只寫 2020-01-02 之前**：那之後是既有權威資料，`upsert` 是 ON CONFLICT DO UPDATE，
寫進去會靜默蓋掉。與 `load_finmind_history.py` 同一條界線。

用法：
    python scripts/load_finmind_history_tables.py --table institutional --verify
    python scripts/load_finmind_history_tables.py --table institutional --load
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl

from twquant.config import ServingConfig, load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import (
    STATEMENT_DATASETS,
    institutional_net,
    margin_balances,
    statement_rows,
    valuation_rows,
)

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
STAGING_ROOT = pathlib.Path("D:/project/twquant/data/staging")
AUTHORITATIVE_FROM = dt.date(2020, 1, 2)
EARLIEST = dt.date(2015, 6, 1)  # D-083
# 恆等式：不一致列數佔比超過此值即拒絕載入（映射錯會是整片不一致，不是零星幾列）
_MAX_MISMATCH_RATE = 0.005


class Spec:
    """一張表的映射規格。`available_at` 的小時數沿用 `clean.py` 的慣例，不得另立一套。"""

    def __init__(
        self,
        staging: str,
        table: str,
        mapper: Callable[[Iterable[Mapping[str, Any]]], list[dict[str, Any]]],
        cols: tuple[str, ...],
        hour: int,
    ) -> None:
        self.staging, self.table, self.mapper, self.cols, self.hour = (
            staging,
            table,
            mapper,
            cols,
            hour,
        )


_SPECS = {
    # D-008：融資以 T-1 進訊號，故 available_at 用 21:00（比法人的 18:00 晚）
    "institutional": Spec(
        "finmind_hist_institutional",
        "institutional_daily",
        institutional_net,
        ("foreign_net", "trust_net", "dealer_net"),
        18,
    ),
    "margin": Spec(
        "finmind_hist_margin",
        "margin_daily",
        margin_balances,
        ("margin_balance", "short_balance"),
        21,
    ),
    "valuation": Spec(
        "finmind_hist_per", "stock_valuation", valuation_rows, ("per", "pbr", "dividend_yield"), 18
    ),
}


def _map_staging(spec: Spec, lo: dt.date, hi: dt.date) -> pl.DataFrame:
    """讀 staging 全部個股檔 → 套既有映射 → 裁到 [lo, hi] → 加 available_at → schema 驗證。"""
    d = STAGING_ROOT / spec.staging
    files = sorted(p for p in d.iterdir() if p.suffix == ".parquet")
    if not files:
        raise SystemExit(f"找不到 staging：{d}")
    out: list[dict[str, Any]] = []
    stubs = 0
    for f in files:
        raw = pl.read_parquet(f)
        if "date" not in raw.columns:
            stubs += 1  # 查無資料的股票只有 stock_id 一欄——計數，不當錯誤
            continue
        # `is_between` 的字串參數會被當成欄名，要用 pl.lit 包起來。
        # staging 的 date 有時是字串有時是 Date，故一律轉字串比較（ISO 字典序＝日期序）。
        rows = raw.filter(
            pl.col("date")
            .cast(pl.String)
            .is_between(pl.lit(lo.isoformat()), pl.lit(hi.isoformat()))
        )
        if rows.is_empty():
            continue
        out.extend(spec.mapper(rows.to_dicts()))
    if not out:
        raise SystemExit(f"{lo} ~ {hi} 映射後沒有任何列")
    print(f"  staging {len(files):,} 檔（查無資料 {stubs:,}）→ 映射後 {len(out):,} 列")
    frame = pl.DataFrame(out).with_columns(
        available_at=pl.col("date")
        .cast(pl.Datetime("us"))
        .dt.offset_by(f"{spec.hour}h")
        .dt.replace_time_zone(str(TZ))
    )
    order = ("date", "stock_id", *spec.cols, "available_at")
    return schemas.validate(spec.table, frame.select(order).sort("date", "stock_id"))


# ── 財報三表與月營收：形狀與上面三張不同，各自一條路徑 ────────────────────

_STMT_KEY = ("date", "stock_id", "statement", "type")
# 月營收於次月 10 日前公布（證交法）。available_at 用**法定期限**而非實際公告日
# ——保守估計，且與 `clean.py::clean_monthly_revenue` 的慣例一致，故標 estimated=True。
_REVENUE_DEADLINE_DAY = 10
_REVENUE_HOUR = 18


def _map_statements(lo: dt.date, hi: dt.date) -> pl.DataFrame:
    """三個 staging 目錄 → `financial_statements` 長表。映射用既有的 `statement_rows`。"""
    frames: list[pl.DataFrame] = []
    for statement in STATEMENT_DATASETS:
        d = STAGING_ROOT / f"finmind_hist_{statement}"
        rows: list[dict[str, Any]] = []
        files = sorted(d.glob("*.parquet"))
        for f in files:
            df = pl.read_parquet(f)
            if "date" not in df.columns:
                continue
            sel = df.filter(
                pl.col("date")
                .cast(pl.String)
                .is_between(pl.lit(lo.isoformat()), pl.lit(hi.isoformat()))
            )
            if not sel.is_empty():
                rows.extend(sel.to_dicts())
        mapped = statement_rows(rows, statement)
        print(f"  {statement:9s} {len(files):,} 檔 → 原始 {len(rows):,} → 映射 {len(mapped):,} 列")
        if mapped:
            frames.append(pl.DataFrame(mapped))
    if not frames:
        raise SystemExit(f"{lo} ~ {hi} 三表映射後沒有任何列")
    out = pl.concat(frames, how="diagonal").unique(subset=list(_STMT_KEY), keep="last")
    return schemas.validate("financial_statements", out.sort(*_STMT_KEY))


def _map_revenue(lo: dt.date, hi: dt.date) -> pl.DataFrame:
    """FinMind 月營收 → `monthly_revenue`。**yoy 要自己算**（來源只給金額）。

    來源的 `date` 是公布月的月初（2005-01-01 那列是 2004-12 的營收），真正的所屬月
    在 `revenue_year`／`revenue_month`。用後者推 `period_end`，不要拿 `date` 當月份
    ——差一個月的錯會讓整條營收因子錯位，而且不會有任何一關報錯。

    yoy 需要去年同月，故**先讀全期再算**，最後才裁到 [lo, hi]；先裁再算會讓
    區間開頭 12 個月的 yoy 全是 null。
    """
    d = STAGING_ROOT / "finmind_hist_revenue"
    frames = []
    for f in sorted(d.glob("*.parquet")):
        df = pl.read_parquet(f)
        if "revenue_year" not in df.columns:
            continue
        frames.append(
            df.select(
                pl.col("stock_id").cast(pl.String),
                pl.col("revenue_year").cast(pl.Int32),
                pl.col("revenue_month").cast(pl.Int32),
                pl.col("revenue").cast(pl.Float64),
            )
        )
    if not frames:
        raise SystemExit("找不到月營收 staging")
    raw = (
        pl.concat(frames)
        .filter(pl.col("revenue_month").is_between(1, 12))
        .unique(subset=["stock_id", "revenue_year", "revenue_month"], keep="last")
        .sort("stock_id", "revenue_year", "revenue_month")
    )
    # 去年同月：用 (year-1, month) 自我 join，不用 shift(12)——某檔中間缺月時
    # shift 會抓到錯的月份，而那不會報錯（同 T-139 的列位移問題）。
    prev = raw.select(
        "stock_id",
        (pl.col("revenue_year") + 1).alias("revenue_year"),
        "revenue_month",
        pl.col("revenue").alias("_prev"),
    )
    j = raw.join(prev, on=["stock_id", "revenue_year", "revenue_month"], how="left")
    out = j.with_columns(
        period_end=pl.date(pl.col("revenue_year"), pl.col("revenue_month"), 1).dt.month_end(),
        yoy=pl.when(pl.col("_prev") > 0.0)
        .then(pl.col("revenue") / pl.col("_prev") - 1.0)
        .otherwise(None),
    ).with_columns(
        date=pl.col("period_end"),
        year_month=pl.col("revenue_year").cast(pl.String)
        + "-"
        + pl.col("revenue_month").cast(pl.String).str.zfill(2),
        # 次月 10 日 18:00（法定期限）。月底 + 10 天必落在次月 10 或 11 日，
        # 故直接用 period_end 的次月 10 日。
        available_at=(
            pl.date(
                pl.when(pl.col("revenue_month") == 12)
                .then(pl.col("revenue_year") + 1)
                .otherwise(pl.col("revenue_year")),
                pl.when(pl.col("revenue_month") == 12)
                .then(1)
                .otherwise(pl.col("revenue_month") + 1),
                _REVENUE_DEADLINE_DAY,
            )
            .cast(pl.Datetime("us"))
            .dt.offset_by(f"{_REVENUE_HOUR}h")
            .dt.replace_time_zone(str(TZ))
        ),
        available_at_estimated=pl.lit(True),
    )
    out = out.filter(pl.col("date").is_between(lo, hi)).select(
        "date",
        "year_month",
        "stock_id",
        "period_end",
        "revenue",
        "yoy",
        "available_at",
        "available_at_estimated",
    )
    print(
        f"  月營收映射後 {out.height:,} 列（{out['stock_id'].n_unique():,} 檔，yoy 非空 {int(out['yoy'].is_not_null().sum()):,}）"
    )
    return schemas.validate("monthly_revenue", out.sort("date", "stock_id"))


_SPECIAL = {
    "statements": ("financial_statements", _STMT_KEY, ("value",), _map_statements),
    "revenue": ("monthly_revenue", ("date", "stock_id"), ("revenue", "yoy"), _map_revenue),
}


def verify_special(conn: object, schema: str, kind: str, lo: dt.date, hi: dt.date) -> bool:
    """財報／月營收對 PG 權威區間比對。**不寫入任何東西。**"""
    table, key, cols, mapper = _SPECIAL[kind]
    fm = mapper(lo, hi)
    sel = ", ".join((*key, *cols))
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f'SELECT {sel} FROM "{schema}"."{table}" WHERE date BETWEEN %s AND %s', (lo, hi)
        )
        rows = cur.fetchall()
        names = [d.name for d in cur.description]
    pg = pl.DataFrame(rows, schema=names, orient="row")
    print(f"  FinMind {fm.height:,} 列；PG {pg.height:,} 列")
    j = fm.join(pg, on=list(key), how="inner", suffix="_pg")
    print(f"  交集 {j.height:,} 列")
    if j.is_empty():
        print("  !! 交集為空，無法比對")
        return False
    ok = True
    for c in cols:
        both = j.filter(pl.col(c).is_not_null() & pl.col(f"{c}_pg").is_not_null())
        if both.is_empty():
            print(f"    {c:10s} （兩邊皆空，跳過）")
            continue
        rel = (
            (pl.col(c) - pl.col(f"{c}_pg")).abs() / pl.max_horizontal(pl.col(c).abs(), 1.0)
        ).alias("r")
        d = both.select(rel)["r"]
        bad = int((d > 1e-6).sum())
        rate = bad / both.height
        flag = "" if rate <= _MAX_MISMATCH_RATE else "   << 超過門檻"
        print(f"    {c:10s} 相對誤差 >1e-6 的列 {bad:,}/{both.height:,}（{rate:.3%}）{flag}")
        if rate > _MAX_MISMATCH_RATE:
            ok = False
    print("  判定：" + ("**通過**" if ok else "**未通過**——映射或來源有問題，不得載入"))
    return ok


def load_special(conn: object, schema: str, kind: str, lo: dt.date, hi: dt.date) -> None:
    table, _key, _cols, mapper = _SPECIAL[kind]
    if hi >= AUTHORITATIVE_FROM:
        raise SystemExit(f"迄日 {hi} 已進入權威區間——upsert 會靜默蓋掉既有資料")
    frame = mapper(lo, hi)
    sch.ensure_schema(conn, schema)
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        upsert(cur, schema, sch.spec_of(table), frame)
    conn.commit()  # type: ignore[attr-defined]
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(f'SELECT min(date), max(date), count(*) FROM "{schema}"."{table}"')
        print(f"  寫入 {frame.height:,} 列；{table} 現況 {cur.fetchone()}")


def _read_pg(conn: object, schema: str, spec: Spec, lo: dt.date, hi: dt.date) -> pl.DataFrame:
    cols = ", ".join(("date", "stock_id", *spec.cols))
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f'SELECT {cols} FROM "{schema}"."{spec.table}" WHERE date BETWEEN %s AND %s', (lo, hi)
        )
        rows = cur.fetchall()
    dtypes: dict[str, Any] = {"date": pl.Date, "stock_id": pl.String}
    for c in spec.cols:
        dtypes[c] = pl.Int64 if spec.table != "stock_valuation" else pl.Float64
    return pl.DataFrame(rows, schema=dtypes, orient="row")


def verify(conn: object, schema: str, spec: Spec, lo: dt.date, hi: dt.date) -> bool:
    """對 PG 的權威區間逐欄比對。**不寫入任何東西。**"""
    fm = _map_staging(spec, lo, hi)
    pg = _read_pg(conn, schema, spec, lo, hi)
    print(f"  FinMind {fm.height:,} 列；PG {pg.height:,} 列")
    j = fm.join(pg, on=["date", "stock_id"], how="inner", suffix="_pg")
    print(
        f"  交集 {j.height:,} 列；只在 FinMind {fm.height - j.height:,}、只在 PG {pg.height - j.height:,}"
    )
    if j.is_empty():
        print("  !! 交集為空，無法比對")
        return False
    ok = True
    for c in spec.cols:
        same = int(j.filter(pl.col(c) == pl.col(f"{c}_pg")).height)
        rate = 1.0 - same / j.height
        flag = "" if rate <= _MAX_MISMATCH_RATE else "   << 超過門檻"
        print(f"    {c:16s} 相同 {same:,}/{j.height:,}（不一致 {rate:.3%}）{flag}")
        if rate > _MAX_MISMATCH_RATE:
            ok = False
            bad = j.filter(pl.col(c) != pl.col(f"{c}_pg"))
            print("      不一致的日期分布：")
            per = bad.group_by(pl.col("date").dt.year().alias("y")).len().sort("y")
            print("      " + "、".join(f"{r['y']}:{r['len']:,}" for r in per.to_dicts()))
    print("  判定：" + ("**通過**" if ok else "**未通過**——映射或來源有問題，不得載入"))
    return ok


def load(conn: object, schema: str, spec: Spec, lo: dt.date, hi: dt.date) -> None:
    if lo < EARLIEST:
        raise SystemExit(f"起日 {lo} 早於 {EARLIEST}（D-083）")
    if hi >= AUTHORITATIVE_FROM:
        raise SystemExit(
            f"迄日 {hi} 已進入 {AUTHORITATIVE_FROM} 之後的權威區間"
            "——upsert 是 ON CONFLICT DO UPDATE，會靜默蓋掉既有資料"
        )
    frame = _map_staging(spec, lo, hi)
    sch.ensure_schema(conn, schema)
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        upsert(cur, schema, sch.spec_of(spec.table), frame)
    conn.commit()  # type: ignore[attr-defined]
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(f'SELECT min(date), max(date), count(*) FROM "{schema}"."{spec.table}"')
        print(f"  寫入 {frame.height:,} 列；{spec.table} 現況 {cur.fetchone()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=sorted({**_SPECS, **_SPECIAL}), required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true", help="對 PG 權威區間比對，不寫入")
    g.add_argument("--load", action="store_true", help="寫入 2020 之前的列")
    ap.add_argument("--from", dest="lo", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--to", dest="hi", type=dt.date.fromisoformat, default=None)
    args = ap.parse_args()
    special = args.table in _SPECIAL
    spec = None if special else _SPECS[args.table]
    # 驗證看權威區間、載入看長歷史區間——兩者的預設範圍本來就不同
    lo = args.lo or (AUTHORITATIVE_FROM if args.verify else EARLIEST)
    hi = args.hi or (
        dt.date(2026, 9, 4) if args.verify else AUTHORITATIVE_FROM - dt.timedelta(days=1)
    )

    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        if special:
            print(f"[{args.table}] → {_SPECIAL[args.table][0]}　{lo} ~ {hi}")
            if args.verify:
                raise SystemExit(
                    0 if verify_special(conn, cfg.pg_schema, args.table, lo, hi) else 1
                )
            load_special(conn, cfg.pg_schema, args.table, lo, hi)
            return
        assert spec is not None
        print(f"[{args.table}] {spec.staging} → {spec.table}　{lo} ~ {hi}")
        if args.verify:
            raise SystemExit(0 if verify(conn, cfg.pg_schema, spec, lo, hi) else 1)
        load(conn, cfg.pg_schema, spec, lo, hi)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
