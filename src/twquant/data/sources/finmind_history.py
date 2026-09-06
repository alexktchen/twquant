"""T-161：把 T-116 回補下來的 FinMind 長歷史原始檔清洗成 clean 層 schema。純函數、無 IO。

**為什麼需要獨立的清洗器**，而不是重用 `clean.py` 的那幾支：`clean_prices` 吃的是
TWSE MI_INDEX 的中文欄名原始表，FinMind 的欄名、型別、缺值編碼完全不同：

| FinMind | 這裡 | 差異 |
|---|---|---|
| `date` 是 **String** | `date: pl.Date` | 型別 |
| `max` / `min` | `high` / `low` | 欄名 |
| `Trading_Volume` / `Trading_money` | `volume` / `amount` | 欄名 |
| `spread` / `Trading_turnover` | （不要） | schema strict=True，多欄即拒 |
| 缺值編碼成 **0** | null → 剔除 | **本模組存在的主因** |

**0 當缺值是這批資料最大的地雷。** FinMind 免費層對「當天沒有這筆資料」回 0 而不是
null，而 `0` 通過每一道 null 檢查。已經咬過兩次：T-140 的 22,988 列 `inf`
（根因是 1,185 列 `close=0` 被當除數）、T-151 還留著的 727 列。抽 10 檔長壽股
53,223 列量到 OHLC 各有 8 列為 0（0.02%）——比例小，乘上 900 萬列就是約 1,800 顆毒種子。

故本模組把 **OHLC ≤ 0 一律視為「當天沒有有效報價」並剔除該列，且回報剔除筆數**
（黃金規則 5：不靜默）。`volume = 0` 不剔除——有報價但無成交是合法狀態。

`limit_up_price` / `limit_down_price` 留 null，與 `clean.py::clean_prices` 一致；
回測引擎在缺值時回退到 `limit_up_proxy(前收)`。**注意 `limit_up_proxy` 寫死 ×1.10，
而台股 2015-06-01 之前的漲跌幅上限是 7%**——用這批資料跑 2015 年之前的回測前
必須先修（T-162），否則引擎會放行當年不可能成交的價格。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.errors import DataError

# FinMind TaiwanStockPrice 的欄名 → clean 層欄名
_PRICE_RENAME = {
    "max": "high",
    "min": "low",
    "Trading_Volume": "volume",
    "Trading_money": "amount",
}
_OHLC = ("open", "high", "low", "close")
# 盤後 18:00 可得，與 `clean.py::clean_prices` 的 `_at(date, 18)` 逐字相同
# ——兩條路徑寫進同一張表，`available_at` 的語意不得有兩套。
_AVAILABLE_HOUR = 18


@dataclass(frozen=True)
class CleanResult:
    """清洗結果與**被丟掉了什麼**。丟棄一律計數回報，不得只回一張乾淨的表。"""

    frame: pl.DataFrame
    rows_in: int
    dropped_zero_price: int
    dropped_null: int
    dropped_duplicate: int

    @property
    def rows_out(self) -> int:
        """通過清洗的列數。"""
        return self.frame.height

    def summary(self) -> str:
        """一行摘要，供載入器逐批印出。"""
        return (
            f"{self.rows_in:,} → {self.rows_out:,} 列"
            f"（零價剔除 {self.dropped_zero_price:,}、"
            f"缺值剔除 {self.dropped_null:,}、"
            f"重複剔除 {self.dropped_duplicate:,}）"
        )


def clean_finmind_prices(raw: pl.DataFrame) -> CleanResult:
    """FinMind TaiwanStockPrice 原始 → `prices_daily` schema。

    `raw` 需含 date/stock_id/open/max/min/close/Trading_Volume/Trading_money，
    `date` 為 String（"YYYY-MM-DD"）或 Date 皆可。多餘欄位會被丟掉。

    輸出通過 `schemas.validate("prices_daily", ...)`，故欄序、型別、非負與非空
    都由 schema 把關而不是靠這裡的程式碼記得。
    """
    need = {"date", "stock_id", "open", "max", "min", "close", "Trading_Volume", "Trading_money"}
    missing = need - set(raw.columns)
    if missing:
        raise DataError(
            "prices_daily",
            None,
            f"FinMind 原始表缺欄位 {sorted(missing)}；實際欄位 {raw.columns}。"
            "查無資料的股票會落地成只有 stock_id 一欄的殘檔，呼叫端應先跳過那些檔",
        )
    if raw.is_empty():
        return CleanResult(schemas.validate("prices_daily", _empty()), 0, 0, 0, 0)

    n_in = raw.height
    out = raw.rename({k: v for k, v in _PRICE_RENAME.items() if k in raw.columns}).select(
        pl.col("date").cast(pl.Date) if raw["date"].dtype == pl.Date else _parse_date(),
        pl.col("stock_id").cast(pl.String),  # 黃金規則 4：一律字串，保留前導零
        *(pl.col(c).cast(pl.Float64) for c in _OHLC),
        pl.col("volume").cast(pl.Int64),
        pl.col("amount").cast(pl.Float64),
    )
    # FinMind 把「沒有這筆資料」編碼成 0。OHLC 為 0 代表當天沒有有效報價
    # ——不是「價格是零元」。轉 null 再一起剔除，並把筆數算出來。
    zeroed = out.with_columns(
        *(pl.when(pl.col(c) > 0.0).then(pl.col(c)).otherwise(None).alias(c) for c in _OHLC)
    )
    n_zero = int(
        zeroed.select(pl.any_horizontal(pl.col(c).is_null() for c in _OHLC)).to_series().sum()
    )
    kept = zeroed.drop_nulls([*_OHLC, "volume", "amount"])
    n_null = n_in - kept.height - n_zero if n_in - kept.height > n_zero else 0

    deduped = kept.unique(subset=["date", "stock_id"], keep="last", maintain_order=True)
    n_dup = kept.height - deduped.height

    final = deduped.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("limit_up_price"),
        pl.lit(None, dtype=pl.Float64).alias("limit_down_price"),
        available_at=pl.col("date")
        .cast(pl.Datetime("us"))
        .dt.offset_by(f"{_AVAILABLE_HOUR}h")
        .dt.replace_time_zone(str(TZ)),
    ).select(
        "date",
        "stock_id",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "limit_up_price",
        "limit_down_price",
        "available_at",
    )
    return CleanResult(
        frame=schemas.validate("prices_daily", final),
        rows_in=n_in,
        dropped_zero_price=n_zero,
        dropped_null=n_null,
        dropped_duplicate=n_dup,
    )


def _parse_date() -> pl.Expr:
    """FinMind 的 `date` 是 "YYYY-MM-DD" 字串。格式不符即 raise，不靜默轉 null。"""
    return pl.col("date").str.to_date("%Y-%m-%d", strict=True).alias("date")


def _empty() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
            "limit_up_price": pl.Float64,
            "limit_down_price": pl.Float64,
            "available_at": schemas.TW_DT,
        }
    )


def price_identity_violations(frame: pl.DataFrame, *, tolerance: float = 0.02) -> pl.DataFrame:
    """行情表的內部恆等式檢查：`low ≤ open,close ≤ high`，且 `amount ≈ volume × 均價`。

    回傳違規的列（空表＝全過）。**單位錯誤是這個檢查真正要抓的東西**：若
    `volume` 其實是「張」而非「股」，`amount / volume` 會是實際股價的 1,000 倍，
    而那不會觸發任何 schema 檢查（兩者都是合法的非負數）。

    `tolerance` 是均價與 (high+low)/2 的相對容差——日內成交分布不均屬正常，
    但差到數量級就是單位錯了。成交量為 0 的列不參與（分母為零）。
    """
    if frame.is_empty():
        return frame
    mid = (pl.col("high") + pl.col("low")) / 2.0
    vwap = pl.col("amount") / pl.col("volume")
    return frame.filter(
        (pl.col("low") > pl.col("open"))
        | (pl.col("low") > pl.col("close"))
        | (pl.col("high") < pl.col("open"))
        | (pl.col("high") < pl.col("close"))
        | ((pl.col("volume") > 0) & ((vwap - mid).abs() > mid * (1.0 + tolerance)))
    )


def trading_calendar(frames: list[pl.DataFrame]) -> list[dt.date]:
    """多檔的日期聯集＝全市場交易日曆。用於偵測個股缺列（T-139 的跨距防護需要它）。"""
    days: set[dt.date] = set()
    for f in frames:
        if "date" in f.columns and not f.is_empty():
            days |= set(f["date"].to_list())
    return sorted(days)
