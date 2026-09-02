"""財報品質因子（T-097）：六個文獻公式 + size 控制。

| 因子 | 出處 | 公式 | 方向 |
|---|---|---|---|
| gross_profitability | Novy-Marx 2013 | TTM 營業毛利 ÷ 最新總資產 | + |
| roe | Fama–French 2015（RMW） | TTM 歸屬母公司淨利 ÷ 平均權益（最新與四季前） | + |
| accruals | Sloan 1996 | (TTM 淨利 − TTM 營業現金流) ÷ 平均總資產 | − |
| asset_growth | Cooper–Gulen–Schill 2008 | 總資產 ÷ 四季前總資產 − 1 | − |
| sue | Foster–Olsen–Shevlin 1984；Bernard–Thomas 1989 | (EPS_q − EPS_{q−4}) ÷ 前 8 個同比差的樣本標準差 | + |
| leverage | QMJ（Asness–Frazzini–Pedersen 2019）safety | 負債 ÷ 總資產 | − |
| log_market_cap | size 控制 | ln(收盤 × 股本 ÷ 面額) | − |

資料是 `financial_statements` 長表（FinMind 三表）。三個會算錯的地方：
1. **PIT**：財報以法定申報期限 22:00 為 available_at（D-008 截點 20:00 → 期限當天仍不可見，
   隔日才可見）。as-of 映射用「可見起日」而非 available_at 的日期部分。
2. **現金流量表是累計 YTD**：單季 = 本期 − 上期（Q1 照抄），且上期必須是同年前一季，否則 null。
3. **TTM 與四季前都要求季序連續**：缺一季即 null，不得用三季充數或跨過缺口比較。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.conventions import SIGNAL_CUTOFF_HOUR
from twquant.factors.fundamental import _asof_map, _daily_spine
from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_TABLES = ["financial_statements", "prices_daily"]
# 需要的科目：(statement, type) → 短名。損益表與資產負債表的 EquityAttributableToOwnersOfParent
# 是兩個不同科目（淨利 vs 權益），靠 statement 區分。
_ACCOUNTS: dict[tuple[str, str], str] = {
    ("income", "GrossProfit"): "gp",
    ("income", "EquityAttributableToOwnersOfParent"): "ni",
    ("income", "EPS"): "eps",
    ("balance", "TotalAssets"): "ta",
    ("balance", "Liabilities"): "liab",
    ("balance", "EquityAttributableToOwnersOfParent"): "eq",
    ("balance", "OrdinaryShare"): "cap",
    ("cashflow", "CashFlowsFromOperatingActivities"): "cfo_ytd",
}
_CUTOFF_HOUR = SIGNAL_CUTOFF_HOUR  # D-008 訊號截點
_SUE_PRIOR = 8  # 文獻定義：分母用前 8 季的同比差


def _qidx(col: str = "date") -> pl.Expr:
    """季序（連續整數）：year*4 + (month/3 − 1)，供「連續季」檢查。"""
    return pl.col(col).dt.year() * 4 + (pl.col(col).dt.month() // 3 - 1)


def _consecutive(n: int) -> pl.Expr:
    """本列與 n 列前的季序恰差 n（中間沒有缺季）。"""
    return (pl.col("_q") - pl.col("_q").shift(n).over("stock_id")) == n


def _lag(col: str, n: int) -> pl.Expr:
    """n 季前的值；季序不連續即 null。"""
    return pl.when(_consecutive(n)).then(pl.col(col).shift(n).over("stock_id")).otherwise(None)


def _ttm(col: str) -> pl.Expr:
    """近四季合計；四季須連續且皆非 null（缺一季即 null）。"""
    return pl.when(_consecutive(3)).then(
        pl.col(col)
        + pl.col(col).shift(1).over("stock_id")
        + pl.col(col).shift(2).over("stock_id")
        + pl.col(col).shift(3).over("stock_id")
    )


def _quarterly(view: PitView) -> pl.LazyFrame:
    """每檔每季一列的寬表（含可見起日 _avail），已算好 TTM／四季前／單季現金流。"""
    keys = pl.DataFrame(
        {
            "statement": [k[0] for k in _ACCOUNTS],
            "type": [k[1] for k in _ACCOUNTS],
            "_name": list(_ACCOUNTS.values()),
        }
    ).lazy()
    long = (
        view.table("financial_statements")
        .join(keys, on=["statement", "type"], how="inner")
        .select("stock_id", "date", "available_at", "_name", "value")
    )
    wide = (
        long.collect()
        .pivot(values="value", index=["stock_id", "date", "available_at"], on="_name")
        .lazy()
    )
    for name in _ACCOUNTS.values():  # 某檔缺某科目時補欄，讓後面的表達式不炸
        wide = wide.with_columns(
            pl.col(name)
            if name in wide.collect_schema().names()
            else pl.lit(None, pl.Float64).alias(name)
        )
    # 可見起日：available_at 在截點（20:00）之後 → 隔日才可見
    avail = (
        pl.when(pl.col("available_at").dt.hour() >= _CUTOFF_HOUR)
        .then(pl.col("available_at").dt.date().dt.offset_by("1d"))
        .otherwise(pl.col("available_at").dt.date())
    )
    q = (
        wide.with_columns(_q=_qidx(), _avail=avail)
        .sort(["stock_id", "date"])
        # 現金流量表 YTD → 單季：Q1 照抄；其餘 = 本期 − 上期，上期須為同年前一季
        .with_columns(
            cfo=pl.when(pl.col("date").dt.month() == 3)
            .then(pl.col("cfo_ytd"))
            .when(
                _consecutive(1)
                & (pl.col("date").dt.year() == pl.col("date").dt.year().shift(1).over("stock_id"))
            )
            .then(pl.col("cfo_ytd") - pl.col("cfo_ytd").shift(1).over("stock_id"))
            .otherwise(None)
        )
        .with_columns(
            gp_ttm=_ttm("gp"),
            ni_ttm=_ttm("ni"),
            cfo_ttm=_ttm("cfo"),
            ta_lag4=_lag("ta", 4),
            eq_lag4=_lag("eq", 4),
            eps_lag4=_lag("eps", 4),
        )
        .with_columns(_d=pl.col("eps") - pl.col("eps_lag4"))  # 同比差
    )
    # SUE 分母：前 8 個同比差的樣本標準差（全部須存在）
    prior = [pl.col("_d").shift(i).over("stock_id") for i in range(1, _SUE_PRIOR + 1)]
    ok8 = _consecutive(_SUE_PRIOR) & pl.all_horizontal([p.is_not_null() for p in prior])
    lst = pl.concat_list(prior)
    return q.with_columns(
        _sue_sd=pl.when(ok8).then(lst.list.std(ddof=1)).otherwise(None),
    )


def _map(view: PitView, expr: pl.Expr, name: str) -> pl.DataFrame:
    """把季頻因子值以可見起日 as-of 映射到每個交易日。"""
    # 刻意**不**在映射前 drop null：最新一季算不出來（缺季、分母 ≤ 0）就該是「無值」，
    # 若先 drop 再 as-of，會靜默回退到一年前那季的值，讓過期數字冒充當期。
    per_q = (
        _quarterly(view)
        .with_columns(expr.alias(name))
        .select("stock_id", "_avail", name)
        .sort(["stock_id", "_avail"])
    )
    return _asof_map(_daily_spine(view), per_q, name)


def _safe_div(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return pl.when(den > 0).then(num / den).otherwise(None)


@factor(
    name="fundamental.gross_profitability",
    category="fundamental",
    direction=1,
    tables=_TABLES,
    lookback_days=460,
    params={},
)
def gross_profitability(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Novy-Marx 2013：TTM 營業毛利 ÷ 最新總資產。越靠近損益表上方的獲利指標越少會計選擇。"""
    return _map(view, _safe_div(pl.col("gp_ttm"), pl.col("ta")), "value")


@factor(
    name="fundamental.roe",
    category="fundamental",
    direction=1,
    tables=_TABLES,
    lookback_days=460,
    params={},
)
def roe(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """TTM 歸屬母公司淨利 ÷ 平均權益（最新與四季前）。"""
    avg_eq = (pl.col("eq") + pl.col("eq_lag4")) / 2
    return _map(view, _safe_div(pl.col("ni_ttm"), avg_eq), "value")


@factor(
    name="fundamental.accruals",
    category="fundamental",
    direction=-1,
    tables=_TABLES,
    lookback_days=460,
    params={},
)
def accruals(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Sloan 1996：(TTM 淨利 − TTM 營業現金流) ÷ 平均總資產。沒有現金支撐的盈餘會反轉（負向）。"""
    avg_ta = (pl.col("ta") + pl.col("ta_lag4")) / 2
    return _map(view, _safe_div(pl.col("ni_ttm") - pl.col("cfo_ttm"), avg_ta), "value")


@factor(
    name="fundamental.asset_growth",
    category="fundamental",
    direction=-1,
    tables=_TABLES,
    lookback_days=460,
    params={},
)
def asset_growth(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Cooper–Gulen–Schill 2008：總資產 ÷ 四季前 − 1（負向：擴張過快的公司後續報酬低）。"""
    return _map(view, _safe_div(pl.col("ta"), pl.col("ta_lag4")) - 1.0, "value")


@factor(
    name="fundamental.sue",
    category="fundamental",
    direction=1,
    tables=_TABLES,
    lookback_days=850,
    params={},
)
def sue(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """標準化盈餘驚奇：(EPS_q − EPS_{q−4}) ÷ 前 8 個同比差的樣本標準差；分母為 0 或不足 8 個 → null。"""
    return _map(view, _safe_div(pl.col("_d"), pl.col("_sue_sd")), "value")


@factor(
    name="fundamental.leverage",
    category="fundamental",
    direction=-1,
    tables=_TABLES,
    lookback_days=460,
    params={},
)
def leverage(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """負債 ÷ 總資產（QMJ 的 safety 子項之一；負向）。"""
    return _map(view, _safe_div(pl.col("liab"), pl.col("ta")), "value")


@factor(
    name="fundamental.log_market_cap",
    category="fundamental",
    direction=-1,
    tables=_TABLES,
    lookback_days=460,
    params={"par_value": 10.0},
)
def log_market_cap(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """ln(當日收盤 × 股本 ÷ 面額)。股本取最新可見的資產負債表；面額為參數（台股普通股 10 元）。"""
    raw = params.get("par_value", 10.0)
    par = float(raw) if isinstance(raw, int | float | str) else 10.0
    shares = _map(view, _safe_div(pl.col("cap"), pl.lit(par)), "shares").rename({"value": "shares"})
    close = view.table("prices_daily").select("date", "stock_id", "close").collect()
    return (
        shares.join(close, on=["date", "stock_id"], how="inner")
        .filter((pl.col("close") > 0) & (pl.col("shares") > 0))
        .with_columns((pl.col("close") * pl.col("shares")).log().alias("value"))
        .select("date", "stock_id", "value")
        .sort(["date", "stock_id"])
    )
