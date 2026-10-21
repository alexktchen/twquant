"""D-203（2026-10-21）：主力大賣是不是籌碼鬆動，定義逐字照 DECISIONS.md D-203 §二～§四。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant uv run --all-groups python scripts/d203_broker_sell_check.py \
        --report <絕對路徑>/docs/reports/d203-broker-sell-check.md [--json <絕對路徑>]

只讀本機 PG，不寫 PG。主力淨買超的數學一律呼叫 `factors.broker` 的共用函數，分段兩趟同
`scripts/backfill_broker_factors.py`（隔日沖統計要全期累計，見那支腳本的說明）。決定性：固定 seed、同一份資料跑兩次相同。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import polars as pl

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from backfill_broker_factors import _segments  # noqa: E402

import twquant.factors.builtins  # noqa: E402, F401  # 匯入觸發因子註冊
from twquant.config import ServingConfig, load_config  # noqa: E402
from twquant.factors.broker import (  # noqa: E402
    clean_daily,
    concentration_values,
    cumulate_events,
    daytrade_events,
)
from twquant.factors.registry import get  # noqa: E402

BROKER_START = dt.date(2022, 1, 1)  # broker_trades 最早 2022-01-03；隔日沖統計從頭累計
SIGNAL_START = dt.date(2022, 3, 1)
DEV_END = dt.date(2026, 9, 8)  # 第 t+20 個交易日不得晚於此（D-109：09-09 起是第二保留期）
WINDOW = 5  # 近 5 日主力淨買超 ÷ 近 5 日成交量；融資也看近 5 日
HORIZON = 20
TAIL = 0.10  # 最低／最高 10%
N_BINS = 5  # 配對用五分位
DROP_10, DROP_15 = -0.10, -0.15
SEED = 20261021
BOOT = 10_000
SEGMENT_MONTHS = 6  # 同 T-271：半年一段，峰值約 1.6 GB
RET_GATE, HIT_GATE = -0.005, 0.02
MIN_ROWS, MIN_DAYS = 10_000, 300
YEARS = (2022, 2023, 2024, 2025, 2026)
MIN_NEG_YEARS = 4
LIST_MIN = 300
SIGNAL_FACTOR = "chip.broker_concentration_20d"  # 只借它登記的隔日沖門檻（params）；窗長用 WINDOW
MATCH = {"price.vol_20d": "vol", "price.mom_20d": "mom", "volume.adv_20d": "adv"}
MODELS = {
    "CORE": "lgbm_lw_net_h20_20260225_83890036",
    "QUIET": "lgbm_quietA20_tu_h20_20260430_655df6d7",
}
LINE_NAME = {"CORE": "大盤精選", "QUIET": "潛伏飆股", "ALL": "合併"}
STOCK_RE = r"^[1-9][0-9]{3}$"
OUTCOMES = ("ret", "hit10", "hit15")
CONFIGS = ROOT / "configs"


# ── 主力淨買超面板（分段兩趟，數學全在 factors.broker） ─────────────────────────────


BOUNDED = ("broker_trades", "prices_daily")  # 只給 available_at 都不早於 date 的表加上界


def bounded_source(conn: Any, sch: str, since: dt.date, until: dt.date) -> Any:
    """讀 date ∈ [since, until] 的 PgTableSource（D-203 §九 修正一）。

    原本每段 `since=a` 不設上界，第一段就把 2022 起整張 broker_trades（約 5,800 萬列）讀進記憶體，
    超過 28 GB 被 OOM。這兩張表 available_at 皆不早於 date（2026-10-21 查過全表為 0 筆），
    `view(until)` 的截點是 until 20:00，所以 date > until 的列本來就會被 PIT 濾掉；加上界只省記憶體，結果不變。
    """
    from twquant.data.serving.pg_view import PgTableSource

    class _Bounded(PgTableSource):
        def scan(self, table: str, layer: str = "clean") -> pl.LazyFrame:
            if table not in BOUNDED:
                return super().scan(table, layer)
            df = pl.read_database(
                f'SELECT * FROM "{sch}"."{table}" WHERE date >= %(a)s AND date <= %(b)s',
                conn,
                execute_options={"params": {"a": since, "b": until}},
                infer_schema_length=None,
            )
            if df.is_empty():
                raise ValueError(f"{sch}.{table} 在 {since}～{until} 沒有資料")
            return df.with_columns(
                pl.col("available_at").dt.convert_time_zone("Asia/Taipei")
            ).lazy()

    return _Bounded(conn, sch, since=since)


def build_panel(
    view_of: Callable[[dt.date, dt.date], Any],
    segs: Sequence[tuple[dt.date, dt.date]],
    params: dict[str, object],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """回 (clean_daily 面板, 成交量)。第一趟逐段算事件 → 全期累計；第二趟逐段算面板並注入全期 state。"""
    events = [daytrade_events(view_of(a, b)).collect() for a, b in segs]
    state = cumulate_events(pl.concat(events).lazy()).collect().lazy()
    panels: list[pl.DataFrame] = []
    vols: list[pl.DataFrame] = []
    for a, b in segs:
        v = view_of(a, b)
        panels.append(clean_daily(v, params, state=state).collect())
        vols.append(v.table("prices_daily").select("date", "stock_id", "volume").collect())
    daily = pl.concat(panels).unique(subset=["date", "stock_id"], keep="last")
    vol = pl.concat(vols).unique(subset=["date", "stock_id"], keep="last")
    return daily, vol


def signal_values(
    daily: pl.DataFrame, vol: pl.DataFrame, start: dt.date, end: dt.date
) -> pl.DataFrame:
    """近 WINDOW 日主力淨買超 ÷ 近 WINDOW 日成交量：(date, stock_id, value)，四碼普通股。"""
    return (
        concentration_values(daily.lazy(), vol.lazy(), WINDOW)
        .filter(pl.col("stock_id").str.contains(STOCK_RE) & pl.col("date").is_between(start, end))
        .collect()
    )


# ── 結果、分組、配對 ─────────────────────────────────────────────────────────────


def outcomes(
    px: pl.DataFrame, events: pl.DataFrame, end: dt.date, horizon: int = HORIZON
) -> pl.DataFrame:
    """以 t+1 開盤為基準：ret＝t+horizon 收盤 ÷ t+1 開盤 − 1；hit＝t+1～t+horizon 最低收盤跌破門檻（0／1）。

    t+1～t+horizon 有任何公司事件的整筆剔除；第 t+horizon 個交易日晚於 end 的不算。
    """
    ev_cum = (
        events.select("date", "stock_id")
        .unique()
        .sort(["stock_id", "date"])
        .with_columns(ev_cum=pl.int_range(1, pl.len() + 1).over("stock_id"))
    )
    g = "stock_id"
    fwd_min = pl.col("close").reverse().rolling_min(horizon).reverse().shift(-1)
    base = (
        px.sort([g, "date"])
        .join_asof(ev_cum, on="date", by=g, strategy="backward")
        .with_columns(pl.col("ev_cum").fill_null(0))
        .with_columns(
            open1=pl.col("open").shift(-1).over(g),
            fwd_min=fwd_min.over(g),
            close_h=pl.col("close").shift(-horizon).over(g),
            end_date=pl.col("date").shift(-horizon).over(g),
            ev_end=pl.col("ev_cum").shift(-horizon).over(g),
        )
        .drop_nulls(["open1", "fwd_min", "close_h", "end_date"])
        .filter(
            (pl.col("end_date") <= end)
            & ((pl.col("ev_end") - pl.col("ev_cum")) == 0)
            & (pl.col("open1") > 0)
        )
    )
    dd = pl.col("fwd_min") / pl.col("open1") - 1.0
    return base.select(
        "date",
        "stock_id",
        ret=pl.col("close_h") / pl.col("open1") - 1.0,
        hit10=(dd <= DROP_10).cast(pl.Float64),
        hit15=(dd <= DROP_15).cast(pl.Float64),
    ).sort(["date", "stock_id"])


def margin_up(margin: pl.DataFrame, window: int = WINDOW) -> pl.DataFrame:
    """融資餘額比 window 個交易日前多：(date, stock_id, margin_up)。"""
    return (
        margin.sort(["stock_id", "date"])
        .with_columns(
            margin_up=(
                pl.col("margin_balance") - pl.col("margin_balance").shift(window).over("stock_id")
            )
            > 0
        )
        .drop_nulls("margin_up")
        .select("date", "stock_id", "margin_up")
    )


def label(sig: pl.DataFrame, match: pl.DataFrame) -> pl.DataFrame:
    """當天可比較的股票（訊號值＋三個配對變數都有）上切：最低 10% 且 < 0＝主力大賣、最高 10% 且 > 0＝主力大買、五分位格子。"""
    cols = list(MATCH.values())
    df = sig.join(match, on=["date", "stock_id"], how="inner").drop_nulls(["value", *cols])
    n = pl.len().over("date")

    def q(c: str) -> pl.Expr:
        return (
            (pl.col(c).rank("average").over("date") * N_BINS / n)
            .ceil()
            .clip(1, N_BINS)
            .cast(pl.Int32)
        )

    pct = pl.col("value").rank("average").over("date") / n
    return (
        df.with_columns(
            sell=(pct <= TAIL) & (pl.col("value") < 0),
            buy=(pct > 1 - TAIL) & (pl.col("value") > 0),
            cell=q("vol") * 100 + q("mom") * 10 + q("adv"),
        )
        .select("date", "stock_id", "value", "sell", "buy", "cell")
        .sort(["date", "stock_id"])
    )


def matched_excess(
    df: pl.DataFrame,
    flag: str,
    keys: Sequence[str] = ("date", "cell"),
    outs: Sequence[str] = OUTCOMES,
) -> tuple[pl.DataFrame, int]:
    """被標的每筆減同 keys 裡沒被標的平均 → ex_<結果>；回 (配對成功的列, 沒有對照而剔掉的筆數)。"""
    k = list(keys)
    ctrl = (
        df.filter(~pl.col(flag))
        .group_by(k)
        .agg(*[pl.col(o).mean().alias(f"c_{o}") for o in outs], n_ctrl=pl.len())
    )
    fl = df.filter(pl.col(flag)).join(ctrl, on=k, how="left")
    m = fl.filter(pl.col("n_ctrl").is_not_null()).with_columns(
        *[(pl.col(o) - pl.col(f"c_{o}")).alias(f"ex_{o}") for o in outs]
    )
    return m.sort(["date", "stock_id", *[c for c in k if c != "date"]]), fl.height - m.height


def month_bootstrap(
    m: pl.DataFrame, cols: Sequence[str], seed: int = SEED, n: int = BOOT
) -> dict[str, tuple[float, float, float]]:
    """整月一起抽：每欄回 (全部列平均, 2.5%, 97.5%)。各欄共用同一組抽樣。"""
    if m.is_empty():
        raise ValueError("month_bootstrap：沒有任何列，區間算不了")
    g = (
        m.with_columns(month=pl.col("date").dt.truncate("1mo"))
        .group_by("month")
        .agg(*[pl.col(c).sum() for c in cols], _n=pl.len())
        .sort("month")
    )
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, g.height, size=(n, g.height))
    cnt = g["_n"].to_numpy().astype(float)
    denom = cnt[idx].sum(axis=1)
    out: dict[str, tuple[float, float, float]] = {}
    for c in cols:
        s = g[c].to_numpy().astype(float)
        lo, hi = np.quantile(s[idx].sum(axis=1) / denom, [0.025, 0.975])
        out[c] = (float(s.sum() / cnt.sum()), float(lo), float(hi))
    return out


def by_year(m: pl.DataFrame, cols: Sequence[str] = ("ex_ret", "ex_hit10")) -> pl.DataFrame:
    """依訊號日年份：筆數與各欄平均。"""
    return (
        m.group_by(year=pl.col("date").dt.year())
        .agg(pl.len().alias("n"), *[pl.col(c).mean() for c in cols])
        .sort("year")
    )


# ── 判讀（D-203 §三） ───────────────────────────────────────────────────────────


@dataclass
class Summary:
    n_rows: int
    n_days: int
    ret: tuple[float, float, float]  # (平均, 下界, 上界)
    hit10: tuple[float, float, float]
    years: dict[int, float]  # 年份 → ret 超額平均
    list_n: int
    list_ret: float | None
    list_hit10: float | None


def gates(s: Summary) -> dict[str, bool]:
    """樣本是否足夠與 A～D 四個關卡。"""
    neg_years = sum(1 for y in YEARS if s.years.get(y, 0.0) < 0)
    return {
        "enough": s.n_rows >= MIN_ROWS and s.n_days >= MIN_DAYS,
        "A": s.ret[0] <= RET_GATE and s.ret[2] < 0,
        "B": s.hit10[0] >= HIT_GATE and s.hit10[1] > 0,
        "C": neg_years >= MIN_NEG_YEARS,
        "D": s.list_n >= LIST_MIN
        and s.list_ret is not None
        and s.list_hit10 is not None
        and s.list_ret < 0
        and s.list_hit10 > 0,
    }


def verdict(g: dict[str, bool]) -> str:
    """四選一：樣本不足／鬆動成立／全市場成立、名單內不成立／不成立。"""
    if not g["enough"]:
        return "樣本不足"
    if g["A"] and g["B"] and g["C"]:
        return "鬆動成立" if g["D"] else "全市場成立、名單內不成立"
    return "不成立"


NEXT_STEP = {
    "樣本不足": "不下結論；資料累積後照同一份定義重跑。",
    "鬆動成立": "寫上線提案給使用者另行裁決：籌碼面加「主力近 5 日淨賣超」事實與扣分規則；雲端要重開「券商買賣超」步驟、broker_trades 至少留 5 天。",
    "全市場成立、名單內不成立": "不進名單評分；要不要放在個股頁當資訊另行裁決。",
    "不成立": "籌碼面不加主力賣超當扣分；D-141 §六 加本條證據；不換定義重試。",
}


# ── 讀本機 PG ───────────────────────────────────────────────────────────────────


def _read(conn: Any, sql: str, params: tuple[object, ...], schema: dict[str, Any]) -> pl.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema=schema, orient="row")


def load_prices(conn: Any, sch: str) -> pl.DataFrame:
    return _read(
        conn,
        f'SELECT date, stock_id, open, close FROM "{sch}".prices_daily '
        "WHERE stock_id ~ %s AND date BETWEEN %s AND %s AND close IS NOT NULL",
        (STOCK_RE, SIGNAL_START, DEV_END),
        {"date": pl.Date, "stock_id": pl.Utf8, "open": pl.Float64, "close": pl.Float64},
    )


def load_events(conn: Any, sch: str) -> pl.DataFrame:
    return _read(
        conn,
        f'SELECT DISTINCT date, stock_id FROM "{sch}".corporate_events '
        "WHERE stock_id ~ %s AND date BETWEEN %s AND %s",
        (STOCK_RE, SIGNAL_START, DEV_END),
        {"date": pl.Date, "stock_id": pl.Utf8},
    )


def load_match(conn: Any, sch: str) -> pl.DataFrame:
    """三個配對因子的原值（SQL 端轉寬，一列一股一日）。"""
    cols = ", ".join(
        f"max(value) FILTER (WHERE factor = '{f}') AS {short}" for f, short in MATCH.items()
    )
    return _read(
        conn,
        f'SELECT date, stock_id, {cols} FROM "{sch}".factor_values '
        "WHERE factor = ANY(%s) AND stock_id ~ %s AND date BETWEEN %s AND %s GROUP BY date, stock_id",
        (list(MATCH), STOCK_RE, SIGNAL_START, DEV_END),
        {"date": pl.Date, "stock_id": pl.Utf8, **{s: pl.Float64 for s in MATCH.values()}},
    )


def load_margin(conn: Any, sch: str) -> pl.DataFrame:
    return _read(
        conn,
        f'SELECT date, stock_id, margin_balance FROM "{sch}".margin_daily '
        "WHERE stock_id ~ %s AND date BETWEEN %s AND %s AND margin_balance IS NOT NULL",
        (STOCK_RE, SIGNAL_START - dt.timedelta(days=30), DEV_END),
        {"date": pl.Date, "stock_id": pl.Utf8, "margin_balance": pl.Float64},
    )


def load_lists(conn: Any, sch: str) -> pl.DataFrame:
    """兩條線每天前 15（同 D-192 的名單代理）：date, stock_id, line。"""
    frames = []
    for line, mid in MODELS.items():
        df = _read(
            conn,
            "SELECT date, stock_id FROM ("
            "  SELECT date, stock_id, ROW_NUMBER() OVER (PARTITION BY date ORDER BY score DESC) AS rank"
            f'  FROM "{sch}".ml_scores WHERE model_id = %s AND stock_id ~ %s AND date BETWEEN %s AND %s'
            "  AND score IS NOT NULL"
            ") t WHERE rank <= 15",
            (mid, STOCK_RE, SIGNAL_START, DEV_END),
            {"date": pl.Date, "stock_id": pl.Utf8},
        )
        frames.append(df.with_columns(line=pl.lit(line)))
    return pl.concat(frames)


# ── 報告 ────────────────────────────────────────────────────────────────────────


def pp(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.2f} pp"


def ci(t: tuple[float, float, float]) -> str:
    return f"{t[0] * 100:+.2f} pp（{t[1] * 100:+.2f}～{t[2] * 100:+.2f}）"


def render(res: dict[str, Any], generated_at: str) -> str:
    s = res["sample"]
    lines = [
        "# D-203：主力大賣是不是籌碼鬆動",
        "",
        f"產生時間 {generated_at}。定義與門檻見 DECISIONS.md D-203 §二～§三，一字未改。"
        f"主力＝富邦 DJ 前 15 大券商（排除隔日沖）；主力大賣＝近 {WINDOW} 日淨賣超占成交量在當天最低 {TAIL:.0%} 且 < 0；"
        f"對照＝同一天同波動×漲幅×成交額五分位格子裡沒被標的股票；結果以 t+1 開盤為基準、看 {HORIZON} 個交易日。",
        "",
        "## 樣本",
        "",
        "| 項目 | 數字 |",
        "|---|---|",
        f"| 可比較的股票-日 | {s['eligible']:,} |",
        f"| 有結果的股票-日（窗內無事件、t+{HORIZON} ≤ {DEV_END}） | {s['with_outcome']:,} |",
        f"| 主力大賣（有結果） | {s['flagged']:,} |",
        f"| 配對成功 | {s['matched']:,}（沒有對照剔掉 {s['dropped']:,}） |",
        f"| 訊號日／月 | {s['days']:,}／{s['months']:,} |",
        "",
        "## 主結果：主力大賣 − 同格子對照",
        "",
        "| 結果 | 主力大賣平均 | 對照平均 | 超額（95% 區間，整月抽） |",
        "|---|---|---|---|",
    ]
    for o, name in (
        ("ret", f"{HORIZON} 日報酬"),
        ("hit10", "跌 10% 比例"),
        ("hit15", "跌 15% 比例（只報）"),
    ):
        r = res["main"][o]
        lines.append(f"| {name} | {r['flag']:.2%} | {r['ctrl']:.2%} | {ci(tuple(r['ex']))} |")
    g = res["gates"]
    yes = {True: "**過**", False: "否"}
    lines += [
        "",
        "## 關卡（D-203 §三）",
        "",
        "| 關卡 | 條件 | 實際 | 過？ |",
        "|---|---|---|---|",
        f"| 樣本 | ≥ {MIN_ROWS:,} 筆且 ≥ {MIN_DAYS} 個訊號日 | {s['matched']:,} 筆／{s['days']:,} 日 | {yes[g['enough']]} |",
        f"| A 報酬 | 超額 ≤ {RET_GATE * 100:+.1f} pp 且上界 < 0 | {ci(tuple(res['main']['ret']['ex']))} | {yes[g['A']]} |",
        f"| B 大跌 | 超額 ≥ {HIT_GATE * 100:+.1f} pp 且下界 > 0 | {ci(tuple(res['main']['hit10']['ex']))} | {yes[g['B']]} |",
        f"| C 穩定 | 報酬超額 < 0 的年份 ≥ {MIN_NEG_YEARS} | {res['neg_years']} 年 | {yes[g['C']]} |",
        f"| D 名單內 | ≥ {LIST_MIN} 筆、報酬差 < 0 且跌 10% 差 > 0 | {res['list']['ALL']['n']:,} 筆、"
        f"{pp(res['list']['ALL']['ret'])}、{pp(res['list']['ALL']['hit10'])} | {yes[g['D']]} |",
        "",
        "## 分年（C）",
        "",
        "| 年 | 筆數 | 報酬超額 | 跌 10% 超額 |",
        "|---|---|---|---|",
    ]
    for y in res["years"]:
        lines.append(f"| {y['year']} | {y['n']:,} | {pp(y['ex_ret'])} | {pp(y['ex_hit10'])} |")
    lines += [
        "",
        "## 名單內（D）：被標的名單股 − 同一天同一條線沒被標的",
        "",
        "| 組 | 被標筆數 | 報酬差 | 跌 10% 差 |",
        "|---|---|---|---|",
    ]
    for line in ("ALL", "CORE", "QUIET"):
        r = res["list"][line]
        lines.append(f"| {LINE_NAME[line]} | {r['n']:,} | {pp(r['ret'])} | {pp(r['hit10'])} |")
    lines += [
        "",
        "## 只報（不影響結論）",
        "",
        "| 組 | 筆數 | 報酬超額（95% 區間） | 跌 10% 超額（95% 區間） |",
        "|---|---|---|---|",
    ]
    for key, name in (
        ("margin_up", "主力大賣且融資近 5 日增加"),
        ("buy", "鏡像：主力大買（最高 10% 且 > 0）"),
        ("unmatched", "不配對：主力大賣 − 同一天全部沒被標的"),
    ):
        r = res["extra"][key]
        if r["n"] == 0:
            lines.append(f"| {name} | 0 | — | — |")
            continue
        lines.append(f"| {name} | {r['n']:,} | {ci(tuple(r['ret']))} | {ci(tuple(r['hit10']))} |")
    lines += [
        "",
        "## 判讀",
        "",
        f"**{res['verdict']}**——{NEXT_STEP[res['verdict']]}",
        "",
        "## 已知限制（D-203 §七）",
        "",
        "- 「主力」＝前 15 大券商的總公司合計，不是分公司；混著當沖與散戶單。",
        "- 分段計算時，每段最後一個交易日的隔日沖事件漏算（與已物化的分點因子同一份程式、同一個行為）。",
        "- 配對只控制波動、漲幅、成交額；產業、市值沒控制。",
        "- 2022-03～2026-08 只有約四年半，含一次大空頭與一段大多頭。",
        "- 名單代理的分數在兩個模型各自訓練期內是樣本內。",
        "",
    ]
    return "\n".join(lines)


def _excess_block(m: pl.DataFrame) -> dict[str, Any]:
    if m.is_empty():
        return {"n": 0, "ret": None, "hit10": None}
    b = month_bootstrap(m, ["ex_ret", "ex_hit10"])
    return {"n": m.height, "ret": list(b["ex_ret"]), "hit10": list(b["ex_hit10"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", required=True, help="報告 markdown 的絕對路徑")
    ap.add_argument("--json", default=None, help="另存完整數字（絕對路徑）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    from twquant.data.serving import store

    cfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    sch = cfg.pg_schema
    params: dict[str, object] = dict(get(SIGNAL_FACTOR).params)
    segs = _segments(BROKER_START, DEV_END, SEGMENT_MONTHS)
    conn = store.connect(cfg)
    try:
        daily, vol = build_panel(lambda a, b: bounded_source(conn, sch, a, b).view(b), segs, params)
        print(f"主力面板 {daily.height:,} 列（{len(segs)} 段）", flush=True)
        sig = signal_values(daily, vol, SIGNAL_START, DEV_END)
        del daily, vol
        match = load_match(conn, sch)
        out = outcomes(load_prices(conn, sch), load_events(conn, sch), DEV_END)
        mup = margin_up(load_margin(conn, sch))
        lists = load_lists(conn, sch)
    finally:
        conn.close()

    lab = label(sig, match)
    frame = lab.join(out, on=["date", "stock_id"], how="inner")
    m, dropped = matched_excess(frame, "sell")
    if m.is_empty():
        raise SystemExit(
            "沒有任何配對成功的主力大賣——檢查 broker_trades／factor_values 是否涵蓋 2022-03 起"
        )
    boot = month_bootstrap(m, [f"ex_{o}" for o in OUTCOMES])
    years = by_year(m)
    year_ret = {int(r["year"]): float(r["ex_ret"]) for r in years.iter_rows(named=True)}

    in_list = lists.join(frame, on=["date", "stock_id"], how="inner")
    lst: dict[str, dict[str, Any]] = {}
    for line in ("ALL", "CORE", "QUIET"):
        sub = in_list if line == "ALL" else in_list.filter(pl.col("line") == line)
        lm, _ = matched_excess(sub, "sell", keys=("date", "line"))
        lst[line] = {
            "n": lm.height,
            "ret": None if lm.is_empty() else float(lm["ex_ret"].mean()),  # type: ignore[arg-type]
            "hit10": None if lm.is_empty() else float(lm["ex_hit10"].mean()),  # type: ignore[arg-type]
        }

    summary = Summary(
        n_rows=m.height,
        n_days=m["date"].n_unique(),
        ret=boot["ex_ret"],
        hit10=boot["ex_hit10"],
        years=year_ret,
        list_n=lst["ALL"]["n"],
        list_ret=lst["ALL"]["ret"],
        list_hit10=lst["ALL"]["hit10"],
    )
    g = gates(summary)
    mm_up = m.join(mup, on=["date", "stock_id"], how="left").filter(
        pl.col("margin_up").fill_null(False)
    )
    buy_m, _ = matched_excess(frame, "buy")
    unm, _ = matched_excess(frame, "sell", keys=("date",))
    res: dict[str, Any] = {
        "sample": {
            "eligible": lab.height,
            "with_outcome": frame.height,
            "flagged": int(frame["sell"].sum()),
            "matched": m.height,
            "dropped": dropped,
            "days": summary.n_days,
            "months": m["date"].dt.truncate("1mo").n_unique(),
        },
        "main": {
            o: {
                "flag": float(m[o].mean()),  # type: ignore[arg-type]
                "ctrl": float(m[f"c_{o}"].mean()),  # type: ignore[arg-type]
                "ex": list(boot[f"ex_{o}"]),
            }
            for o in OUTCOMES
        },
        "years": years.to_dicts(),
        "neg_years": sum(1 for y in YEARS if year_ret.get(y, 0.0) < 0),
        "list": lst,
        "extra": {
            "margin_up": _excess_block(mm_up),
            "buy": _excess_block(buy_m),
            "unmatched": _excess_block(unm),
        },
        "gates": g,
        "summary": asdict(summary),
        "verdict": verdict(g),
    }
    generated_at = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    pathlib.Path(args.report).write_text(render(res, generated_at), encoding="utf-8")
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    print(f"{res['verdict']}：{NEXT_STEP[res['verdict']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
