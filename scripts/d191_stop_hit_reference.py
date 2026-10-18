"""D-191 §三-1（使用者選 C，2026-10-18）：同波動度的股票 20 日內碰到停損／大漲的歷史比例，分十組算一次、凍結成 json。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant uv run --all-groups python scripts/d191_stop_hit_reference.py \
        --out configs/stop_hit_reference.json --report docs/reports/d191-stop-hit-reference.md

定義逐字照 DECISIONS.md D-191 §三-1；算完不得改。只讀本機 PG，不寫 PG。決定性：同一份資料跑兩次輸出相同。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

import polars as pl
import psycopg

DEV_START = dt.date(2017, 9, 26)  # 生產模型分數起點
DEV_END = dt.date(2026, 9, 8)  # 第二保留期 2026-09-09 起（D-109），一天都不碰
HORIZON = 20
STOP = -0.15  # 使用者的單檔停損
SURGE = 0.20
GROUPS = 10
STOCK_RE = r"^[1-9][0-9]{3}$"
PICKS_FROM, PICKS_TO = dt.date(2026, 5, 4), dt.date(2026, 8, 11)  # 兩條線名單的描述區間（同 D-183）
LINES = {"CORE": "大盤精選", "QUIET": "潛伏飆股"}


def _read(
    dsn: str, sql: str, params: tuple[object, ...], schema: dict[str, pl.DataType]
) -> pl.DataFrame:
    with psycopg.connect(dsn, connect_timeout=30) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema=schema, orient="row")


def load(dsn: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """收盤、波動度、公司事件、兩條線名單。"""
    px = _read(
        dsn,
        "SELECT date, stock_id, close FROM serving.prices_daily "
        "WHERE stock_id ~ %s AND date BETWEEN %s AND %s AND close IS NOT NULL ORDER BY stock_id, date",
        (STOCK_RE, DEV_START, DEV_END),
        {"date": pl.Date(), "stock_id": pl.Utf8(), "close": pl.Float64()},
    )
    vol = _read(
        dsn,
        "SELECT date, stock_id, value FROM serving.factor_values "
        "WHERE factor = 'price.vol_20d' AND stock_id ~ %s AND date BETWEEN %s AND %s AND value IS NOT NULL",
        (STOCK_RE, DEV_START, DEV_END),
        {"date": pl.Date(), "stock_id": pl.Utf8(), "vol": pl.Float64()},
    )
    ev = _read(
        dsn,
        "SELECT DISTINCT date, stock_id FROM serving.corporate_events WHERE stock_id ~ %s AND date BETWEEN %s AND %s",
        (STOCK_RE, DEV_START, dt.date(2026, 12, 31)),
        {"date": pl.Date(), "stock_id": pl.Utf8()},
    )
    picks = _read(
        dsn,
        "SELECT date, line, stock_id FROM serving.daily_picks WHERE date BETWEEN %s AND %s AND line IN ('CORE','QUIET')",
        (PICKS_FROM, PICKS_TO),
        {"date": pl.Date(), "line": pl.Utf8(), "stock_id": pl.Utf8()},
    )
    return px, vol, ev, picks


def observations(px: pl.DataFrame, vol: pl.DataFrame, ev: pl.DataFrame) -> pl.DataFrame:
    """每一筆 (股, 訊號日)：往後 20 個有收盤的交易日的最低／最高／第 20 日收盤；窗內有公司事件的剔除。"""
    px = px.sort(["stock_id", "date"])
    # 事件累計數：到每個價格日（含）為止這檔有幾個事件；窗內事件數＝第 20 日的累計 − 訊號日的累計
    ev_cum = (
        ev.sort(["stock_id", "date"])
        .with_columns(pl.lit(1).alias("one"))
        .with_columns(pl.col("one").cum_sum().over("stock_id").alias("ev_cum"))
        .drop("one")
    )
    px = px.join_asof(ev_cum, on="date", by="stock_id", strategy="backward").with_columns(
        pl.col("ev_cum").fill_null(0)
    )
    rev_min = pl.col("close").reverse().rolling_min(HORIZON).reverse().shift(-1)
    rev_max = pl.col("close").reverse().rolling_max(HORIZON).reverse().shift(-1)
    obs = (
        px.with_columns(
            fwd_min=rev_min.over("stock_id"),
            fwd_max=rev_max.over("stock_id"),
            close_h=pl.col("close").shift(-HORIZON).over("stock_id"),
            end_date=pl.col("date").shift(-HORIZON).over("stock_id"),
            ev_end=pl.col("ev_cum").shift(-HORIZON).over("stock_id"),
        )
        .drop_nulls(["fwd_min", "fwd_max", "close_h", "end_date"])
        .filter(pl.col("end_date") <= DEV_END)
        .filter((pl.col("ev_end") - pl.col("ev_cum")) == 0)
        .filter(pl.col("close") > 0)
        .with_columns(
            down=(pl.col("fwd_min") / pl.col("close") - 1.0) <= STOP,
            up=(pl.col("fwd_max") / pl.col("close") - 1.0) >= SURGE,
            ret=pl.col("close_h") / pl.col("close") - 1.0,
        )
        .join(vol, on=["date", "stock_id"], how="inner")
        .select("date", "stock_id", "vol", "down", "up", "ret")
    )
    return obs


def bounds_of(obs: pl.DataFrame) -> list[float]:
    """全體觀察的 10%、20%…90% 分位（9 條界線）。"""
    out: list[float] = []
    for q in range(1, GROUPS):
        v = obs["vol"].quantile(q / GROUPS, interpolation="linear")
        if v is None:
            raise SystemExit("波動度分位算不出來（沒有觀察）")
        out.append(float(v))
    return out


def group_expr(bounds: list[float]) -> pl.Expr:
    """vol → 0..9：第 g 組＝ bounds[g-1] ≤ vol < bounds[g]（最低組無下界、最高組無上界）。"""
    expr = pl.lit(0)
    for b in bounds:
        expr = expr + (pl.col("vol") >= b).cast(pl.Int32)
    return expr.alias("group")


def summarize(obs: pl.DataFrame, bounds: list[float]) -> list[dict[str, object]]:
    g = obs.with_columns(group_expr(bounds))
    agg = (
        g.group_by("group")
        .agg(
            n=pl.len(),
            down15=pl.col("down").mean(),
            up20=pl.col("up").mean(),
            median_ret=pl.col("ret").median(),
            vol_median=pl.col("vol").median(),
        )
        .sort("group")
    )
    out: list[dict[str, object]] = []
    for r in agg.iter_rows(named=True):
        k = int(r["group"])
        out.append(
            {
                "group": k,
                "vol_lo": None if k == 0 else round(bounds[k - 1], 6),
                "vol_hi": None if k == GROUPS - 1 else round(bounds[k], 6),
                "vol_median": round(float(r["vol_median"]), 6),
                "n": int(r["n"]),
                "down15": round(float(r["down15"]), 4),
                "up20": round(float(r["up20"]), 4),
                "median_ret": round(float(r["median_ret"]), 4),
            }
        )
    return out


def picks_groups(
    picks: pl.DataFrame, vol: pl.DataFrame, bounds: list[float]
) -> dict[str, list[int]]:
    """兩條線名單各落在哪幾組（筆數，組 0..9）。只描述。"""
    j = picks.join(vol, on=["date", "stock_id"], how="inner").with_columns(group_expr(bounds))
    out: dict[str, list[int]] = {}
    for line in LINES:
        counts = [0] * GROUPS
        for r in j.filter(pl.col("line") == line).group_by("group").len().iter_rows(named=True):
            counts[int(r["group"])] = int(r["len"])
        out[line] = counts
    return out


def render(result: dict[str, object], picks_n: dict[str, int], generated_at: str) -> str:
    rows = result["groups"]
    assert isinstance(rows, list)
    pg = result["picks_by_group"]
    assert isinstance(pg, dict)
    lines = [
        "# D-191 §三-1：同波動股票 20 日內碰到停損／大漲的歷史比例（選項 C）",
        "",
        f"產生時間 {generated_at}；資料：本機 PG `prices_daily`、`factor_values(price.vol_20d)`、`corporate_events`。"
        f"訊號日 {DEV_START}～{DEV_END}（往後 20 個交易日也 ≤ {DEV_END}，不碰第二保留期）。",
        f"觀察 {result['n_obs']:,} 筆、{result['n_stocks']:,} 檔；窗內有除權息／分割／減資的筆數已剔除（{result['n_dropped_events']:,} 筆）。",
        "",
        "波動度＝近 20 日簡單日報酬的樣本標準差（不還原除權息）。界線＝全體觀察的十分位（絕對界線，不是每天相對排名）。",
        "",
        "| 組 | 波動度範圍（日） | 組中位數 | 樣本 | 20 日內碰 −15% | 20 日內碰 +20% | 20 日報酬中位數 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lo = "—" if r["vol_lo"] is None else f"{float(r['vol_lo']) * 100:.2f}%"
        hi = "—" if r["vol_hi"] is None else f"{float(r['vol_hi']) * 100:.2f}%"
        lines.append(
            f"| {int(r['group']) + 1} | {lo} ～ {hi} | {float(r['vol_median']) * 100:.2f}% | {int(r['n']):,} | "
            f"{float(r['down15']) * 100:.1f}% | {float(r['up20']) * 100:.1f}% | {float(r['median_ret']) * 100:+.2f}% |"
        )
    lines += [
        "",
        f"## 兩條線名單落在哪幾組（{PICKS_FROM}～{PICKS_TO}，只描述）",
        "",
        "| 線 | 筆數 | 第 1～10 組各幾筆 |",
        "|---|---|---|",
    ]
    for code, name in LINES.items():
        counts = pg.get(code, [])
        lines.append(
            f"| {name} | {picks_n.get(code, 0):,} | {' / '.join(str(c) for c in counts)} |"
        )
    lines += [
        "",
        "## 怎麼讀",
        "",
        "- 這不是預測模型，是把 D-132「下跌風險＝波動度換個說法」的結論變成一個查表的數字。",
        "- 名單上每檔用當天重算的波動度對界線找組，顯示那一組的歷史比例；高波動是雙向的，所以 −15% 與 +20% 一起看。",
        "- 定義凍結在 `configs/stop_hit_reference.json`（測試守 sha256）；要改只能另立登記重算。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        print("需要 TWQUANT_PG_DSN", file=sys.stderr)
        return 1
    px, vol, ev, picks = load(dsn)
    print(
        f"收盤 {px.height:,} 列、波動度 {vol.height:,} 列、事件 {ev.height:,} 列、名單 {picks.height:,} 列"
    )
    obs = observations(px, vol, ev)
    if obs.height == 0:
        raise SystemExit("沒有任何觀察，資料有問題")
    # 剔除筆數：有完整 20 日窗但窗內有事件的（為報告用，重算一次不含事件過濾的筆數）
    all_windows = observations(px, vol, ev.clear())
    bounds = bounds_of(obs)
    groups = summarize(obs, bounds)
    result: dict[str, object] = {
        "registered": "DECISIONS.md D-191 §三-1（2026-10-18，使用者選 C）",
        "dev_start": DEV_START.isoformat(),
        "dev_end": DEV_END.isoformat(),
        "horizon_days": HORIZON,
        "stop": STOP,
        "surge": SURGE,
        "vol_definition": "近 20 個交易日簡單日報酬的樣本標準差（ddof=1），收盤不還原除權息；同 factors/price.vol_20d",
        "bounds": [round(b, 6) for b in bounds],
        "n_obs": obs.height,
        "n_stocks": obs["stock_id"].n_unique(),
        "n_dropped_events": all_windows.height - obs.height,
        "groups": groups,
        "picks_by_group": picks_groups(picks, vol, bounds),
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    picks_n = {line: int(picks.filter(pl.col("line") == line).height) for line in LINES}
    generated_at = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    pathlib.Path(args.report).write_text(render(result, picks_n, generated_at), encoding="utf-8")
    for r in groups:
        print(r)
    print(f"寫入 {out} 與 {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
