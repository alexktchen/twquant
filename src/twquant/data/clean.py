"""清洗管線（SPEC-001 §3）：raw 原始欄 → clean 層 typed 資料。

職責：欄名統一、單位統一（張→股）、型別轉換、千分位逗號去除、佔位符（--、空白）
轉 null、加 available_at（tz-aware 台北）與 schema_version。輸出通過 pandera 驗證。
數值一律 strict=False 轉型：無法解析（"--" 等）自動成 null。
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from twquant.conventions import SHARES_PER_LOT, TZ
from twquant.data import schemas


def _at(date: dt.date, hour: int) -> dt.datetime:
    """該表資料的可得時點（tz-aware 台北）。"""
    return dt.datetime(date.year, date.month, date.day, hour, 0, tzinfo=TZ)


def _num_expr(col: str) -> pl.Expr:
    """去千分位逗號後的字串欄（供後續 cast）。"""
    return pl.col(col).str.strip_chars().str.replace_all(",", "")


def _int(col: str) -> pl.Expr:
    return _num_expr(col).cast(pl.Int64, strict=False)


def _float(col: str) -> pl.Expr:
    return _num_expr(col).cast(pl.Float64, strict=False)


def clean_prices(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """MI_INDEX 原始 → prices_daily。limit_up/down 需前收，此處留 null（T-018 前補）。"""
    at = _at(date, 18)
    out = raw.select(
        pl.lit(date, dtype=pl.Date).alias("date"),
        pl.col("證券代號").str.strip_chars().alias("stock_id"),
        _float("開盤價").alias("open"),
        _float("最高價").alias("high"),
        _float("最低價").alias("low"),
        _float("收盤價").alias("close"),
        _int("成交股數").alias("volume"),  # 已是股
        _float("成交金額").alias("amount"),
        pl.lit(None, dtype=pl.Float64).alias("limit_up_price"),
        pl.lit(None, dtype=pl.Float64).alias("limit_down_price"),
        pl.lit(at, dtype=schemas.TW_DT).alias("available_at"),
    )
    # 無成交（價格 "--" → null）的列丟棄：OHLC 為必填非負
    out = out.drop_nulls(["open", "high", "low", "close", "volume", "amount"])
    return schemas.validate("prices_daily", out)


def clean_institutional(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """T86 原始 → institutional_daily。外資 = 外陸資(不含自營) + 外資自營商。"""
    at = _at(date, 18)
    out = raw.select(
        pl.lit(date, dtype=pl.Date).alias("date"),
        pl.col("證券代號").str.strip_chars().alias("stock_id"),
        (_int("外陸資買賣超股數(不含外資自營商)") + _int("外資自營商買賣超股數")).alias(
            "foreign_net"
        ),
        _int("投信買賣超股數").alias("trust_net"),
        _int("自營商買賣超股數").alias("dealer_net"),
        pl.lit(at, dtype=schemas.TW_DT).alias("available_at"),
    )
    out = out.drop_nulls(["foreign_net", "trust_net", "dealer_net"])
    return schemas.validate("institutional_daily", out)


def clean_margin(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """MI_MARGN 原始 → margin_daily。融資融券餘額單位為張，×1000 轉股（SPEC-000）。"""
    at = _at(date, 21)
    out = raw.select(
        pl.lit(date, dtype=pl.Date).alias("date"),
        pl.col("代號").str.strip_chars().alias("stock_id"),
        (_int("今日餘額") * SHARES_PER_LOT).alias("margin_balance"),  # 融資，張→股
        (_int("今日餘額_2") * SHARES_PER_LOT).alias("short_balance"),  # 融券，張→股
        pl.lit(at, dtype=schemas.TW_DT).alias("available_at"),
    )
    # 只留正規股票代號（濾掉合計/空白列）
    out = out.filter(pl.col("stock_id").str.contains(r"^[0-9]{4,6}[A-Z]?$"))
    out = out.drop_nulls(["margin_balance", "short_balance"])
    return schemas.validate("margin_daily", out)


def clean_tpex_prices(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """TPEX dailyQuotes 原始 → prices_daily（上櫃）。含權證等，過濾為正規股票代號。"""
    at = _at(date, 18)
    out = raw.select(
        pl.lit(date, dtype=pl.Date).alias("date"),
        pl.col("代號").str.strip_chars().alias("stock_id"),
        _float("開盤").alias("open"),
        _float("最高").alias("high"),
        _float("最低").alias("low"),
        _float("收盤").alias("close"),
        _int("成交股數").alias("volume"),
        _float("成交金額(元)").alias("amount"),
        pl.lit(None, dtype=pl.Float64).alias("limit_up_price"),
        pl.lit(None, dtype=pl.Float64).alias("limit_down_price"),
        pl.lit(at, dtype=schemas.TW_DT).alias("available_at"),
    )
    out = out.filter(pl.col("stock_id").str.contains(r"^[0-9]{4,6}[A-Z]?$"))
    out = out.drop_nulls(["open", "high", "low", "close", "volume", "amount"])
    return schemas.validate("prices_daily", out)


# 表名 → (clean 函數) 註冊表（TWSE）
CLEANERS = {
    "prices_daily": clean_prices,
    "institutional_daily": clean_institutional,
    "margin_daily": clean_margin,
}

# TPEX 專用清洗（上櫃）
TPEX_CLEANERS = {
    "prices_daily": clean_tpex_prices,
}
