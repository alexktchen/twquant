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


def _month_end(year: int, month: int) -> dt.date:
    """該月最後一個日曆日。"""
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return nxt - dt.timedelta(days=1)


def clean_monthly_revenue(raw: pl.DataFrame, roc_year: int, month: int) -> pl.DataFrame:
    """MOPS t21sc03 原始 → monthly_revenue。營收千元→元；yoy 取去年同月增減%/100。

    available_at 用「次月 10 日 22:00」估算並標 estimated=True（順延規則待交易日曆
    T-003，見 SPEC-001 §1 / REVIEW M25）。
    """
    year = roc_year + 1911
    period_end = _month_end(year, month)
    pub_year, pub_month = (year + 1, 1) if month == 12 else (year, month + 1)
    available_at = dt.datetime(pub_year, pub_month, 10, 22, 0, tzinfo=TZ)

    out = raw.select(
        pl.lit(period_end, dtype=pl.Date).alias("date"),
        pl.lit(f"{year}-{month:02d}").alias("year_month"),
        pl.col("公司代號").str.strip_chars().alias("stock_id"),
        pl.lit(period_end, dtype=pl.Date).alias("period_end"),
        (_float("當月營收") * 1000.0).alias("revenue"),  # 千元 → 元
        # yoy 自算（當月/去年當月−1）：來源「去年同月增減」欄在 HTML 有錯位風險，
        # 而當月營收與去年當月營收兩欄穩定，自算更可靠。
        pl.when(_float("去年當月營收") > 0)
        .then(_float("當月營收") / _float("去年當月營收") - 1.0)
        .otherwise(None)
        .alias("yoy"),
        pl.lit(available_at, dtype=schemas.TW_DT).alias("available_at"),
        pl.lit(True).alias("available_at_estimated"),
    )
    out = out.drop_nulls(["revenue"])
    return schemas.validate("monthly_revenue", out)


def _roc_date(col: str) -> pl.Expr:
    """民國日期字串 "115/07/01" → Date（無法解析回 null，供 drop_nulls 丟總計列）。"""
    parts = pl.col(col).str.strip_chars().str.split("/")
    return pl.date(
        parts.list.get(0, null_on_oob=True).cast(pl.Int32, strict=False) + 1911,
        parts.list.get(1, null_on_oob=True).cast(pl.Int32, strict=False),
        parts.list.get(2, null_on_oob=True).cast(pl.Int32, strict=False),
    )


def _index_avail_at(hour: int) -> pl.Expr:
    """由已建好的 date 欄組出可得時點（收盤後，tz-aware 台北）。"""
    return (
        pl.col("date")
        .dt.combine(dt.time(hour, 0))
        .dt.replace_time_zone("Asia/Taipei")
        .cast(schemas.TW_DT)
    )


def clean_index_twse(raw: pl.DataFrame) -> pl.DataFrame:
    """TWSE 加權指數月批（c0=日期,c1..c4=開高低收）→ index_daily（index_id=TAIEX）。"""
    out = raw.select(
        _roc_date("c0").alias("date"),
        pl.lit("TAIEX").alias("index_id"),
        _float("c1").alias("open"),
        _float("c2").alias("high"),
        _float("c3").alias("low"),
        _float("c4").alias("close"),
    )
    out = out.drop_nulls(["date", "close"]).filter(pl.col("close") > 0)
    out = out.with_columns(_index_avail_at(18).alias("available_at"))
    return schemas.validate("index_daily", out)


def clean_index_tpex(raw: pl.DataFrame) -> pl.DataFrame:
    """TPEx 櫃買指數月批（c0=日期,c4=收盤指數）→ index_daily（index_id=TPEX，僅收盤）。"""
    out = raw.select(
        _roc_date("c0").alias("date"),
        pl.lit("TPEX").alias("index_id"),
        pl.lit(None, dtype=pl.Float64).alias("open"),
        pl.lit(None, dtype=pl.Float64).alias("high"),
        pl.lit(None, dtype=pl.Float64).alias("low"),
        _float("c4").alias("close"),
    )
    out = out.drop_nulls(["date", "close"]).filter(pl.col("close") > 0)
    out = out.with_columns(_index_avail_at(18).alias("available_at"))
    return schemas.validate("index_daily", out)


def clean_shareholding_dispersion(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """TDCC 集保戶股權分散表（id=1-5）原始 → shareholding_dispersion。

    基準日取自「資料日期」欄（非傳入 date；opendata 為單一全市場週快照，date 僅為
    clean 介面一致而保留）。available_at = 公布日（基準日之後第一個週六）12:00
    （SPEC-001 §1）。持股分級只留 1..15：16=差異數、17=合計 為對帳/衍生列，schema 僅
    收 1..15，明確剔除（非靜默）；占集保庫存數比例% 欄不入 schema、捨棄。

    覆蓋範圍：id=1-5 目前為 6 碼證券（ETF/受益證券/債券）；四碼普通股的集保分散表僅
    TDCC 互動查詢站提供，屬另一個未來 SourcePort（D-017 可插拔設計即為此準備）。
    """
    d = pl.col("資料日期").str.strptime(pl.Date, "%Y%m%d")
    # 基準日（週五）之後第一個週六（含當日）：polars weekday ISO 週一=1..週日=7，週六=6
    to_sat = (6 - d.dt.weekday()) % 7
    available_at = (
        (d.cast(pl.Datetime("us")) + pl.duration(days=to_sat, hours=12))
        .dt.replace_time_zone("Asia/Taipei")
        .cast(schemas.TW_DT)
    )
    out = raw.select(
        d.alias("date"),
        pl.col("證券代號").str.strip_chars().alias("stock_id"),
        pl.col("持股分級").cast(pl.Int32, strict=False).alias("level"),
        _int("人數").alias("holders"),
        _int("股數").alias("shares"),
        available_at.alias("available_at"),
    )
    out = out.filter(pl.col("stock_id").str.contains(r"^[0-9]{4,6}[A-Z]?$"))
    out = out.filter(pl.col("level").is_between(1, 15))  # 剔除 16=差異數、17=合計
    out = out.drop_nulls(["level", "holders", "shares"])
    return schemas.validate("shareholding_dispersion", out)


def clean_stock_meta(raw: pl.DataFrame, market: str) -> pl.DataFrame:
    """TWSE ISIN 原始 → stock_meta。code_name 以全形空格切為代號/中文名；產業別=族群。

    market 由來源市場給定（TWSE/TPEX）。緩變維度表（無 date 欄 → 單一 _all 分區）。
    """
    parts = pl.col("code_name").str.strip_chars().str.split_exact("　", 1)
    industry = pl.col("industry").str.strip_chars()
    out = raw.select(
        parts.struct.field("field_0").str.strip_chars().alias("stock_id"),
        parts.struct.field("field_1").str.strip_chars().alias("name"),
        pl.lit(market).alias("market"),
        pl.when(industry == "").then(None).otherwise(industry).alias("industry"),
        pl.col("list_date")
        .str.strip_chars()
        .str.strptime(pl.Date, "%Y/%m/%d", strict=False)
        .alias("list_date"),
        pl.lit(None, dtype=pl.Date).alias("delist_date"),
    )
    # 上市日不合理（<1911，例如來源改回民國 3 碼年被誤解析為西元 115 年）→ 視為缺值，
    # 避免靜默寫入錯誤日期（ISIN 現況為西元 yyyy/mm/dd，此為防未來格式變動的防呆）。
    out = out.with_columns(
        pl.when(pl.col("list_date").dt.year() >= 1911)
        .then(pl.col("list_date"))
        .otherwise(None)
        .alias("list_date")
    )
    # 只留 4 碼證券（普通股 / ETF / TDR）；排除 6 碼權證等雜訊，聚焦 app 會顯示的標的
    out = out.filter(pl.col("stock_id").str.contains(r"^[0-9]{4}[A-Z]?$"))
    out = out.unique(subset=["stock_id"], keep="first")
    return schemas.validate("stock_meta", out)


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

# TDCC 專用清洗（集保戶股權分散）
TDCC_CLEANERS = {
    "shareholding_dispersion": clean_shareholding_dispersion,
}
