"""清洗管線（SPEC-001 §3）：raw 原始欄 → clean 層 typed 資料。

職責：欄名統一、單位統一（張→股）、型別轉換、千分位逗號去除、佔位符（--、空白）
轉 null、加 available_at（tz-aware 台北）與 schema_version。輸出通過 pandera 驗證。
數值一律 strict=False 轉型：無法解析（"--" 等）自動成 null。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import polars as pl

from twquant.conventions import SHARES_PER_LOT, TZ
from twquant.data import schemas
from twquant.errors import DataError, QualityGateError


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


# T86 的內部恆等式。實測 2026-08-31 全 1,335 列成立（七條全過）。
#
# 為什麼需要它（T-102）：2022-01~2026-09 有 41 個**分散**的日子、共 508 列，欄位整體
# 位移 3 格（湖的 foreign_net = 外陸資＋投信、trust_net = 自營商賣出(自行)、
# dealer_net = 自行買賣超）。2026-09-05 重抓 2026-08-31 的原始回應並以現行程式碼剖析，
# 與 FinMind **1,331 檔逐列相符、0 差異**——**剖析器是對的**，出事的是上游當時給了
# fields 與 data 不對應的回應。日期分散而非連續，也指向暫時性故障而非 schema 變更。
#
# 既然不能靠修剖析器，唯一的防線就是資料自己的一致性：位移會同時破壞下列每一條。
_T86_IDENTITIES: tuple[tuple[str, tuple[tuple[int, str], ...]], ...] = (
    (
        "三大法人買賣超股數",
        (
            (1, "外陸資買賣超股數(不含外資自營商)"),
            (1, "外資自營商買賣超股數"),
            (1, "投信買賣超股數"),
            (1, "自營商買賣超股數"),
        ),
    ),
    (
        "自營商買賣超股數",
        ((1, "自營商買賣超股數(自行買賣)"), (1, "自營商買賣超股數(避險)")),
    ),
    (
        "外陸資買賣超股數(不含外資自營商)",
        ((1, "外陸資買進股數(不含外資自營商)"), (-1, "外陸資賣出股數(不含外資自營商)")),
    ),
    ("外資自營商買賣超股數", ((1, "外資自營商買進股數"), (-1, "外資自營商賣出股數"))),
    ("投信買賣超股數", ((1, "投信買進股數"), (-1, "投信賣出股數"))),
    (
        "自營商買賣超股數(自行買賣)",
        ((1, "自營商買進股數(自行買賣)"), (-1, "自營商賣出股數(自行買賣)")),
    ),
    ("自營商買賣超股數(避險)", ((1, "自營商買進股數(避險)"), (-1, "自營商賣出股數(避險)"))),
)


def assert_t86_consistent(raw: pl.DataFrame, date: dt.date) -> None:
    """T86 原始表的自我一致性檢查（T-102）。任一恆等式被違反即 raise。

    連一條都驗不了時同樣 raise：上游若改欄名，一個安靜地什麼都不檢查的閘門
    比沒有閘門更危險，因為它讓人以為有防護（黃金規則 5）。
    """
    cols = set(raw.columns)
    checked = 0
    violations: list[tuple[str, int, list[str]]] = []
    for lhs, terms in _T86_IDENTITIES:
        if lhs not in cols or any(c not in cols for _, c in terms):
            continue
        checked += 1
        rhs = terms[0][0] * _int(terms[0][1])
        for sign, col in terms[1:]:
            rhs = rhs + sign * _int(col)
        bad = raw.filter(_int(lhs).is_not_null() & rhs.is_not_null() & (_int(lhs) != rhs))
        if bad.height:
            ids = bad["證券代號"].str.strip_chars().to_list() if "證券代號" in cols else []
            violations.append((lhs, bad.height, ids[:5]))
    if checked == 0:
        raise DataError(
            "institutional_daily",
            date,
            "T86 原始表湊不出任何一條內部恆等式（欄名可能已變更）——"
            f"現有欄位：{sorted(cols)}。閘門不得在無法檢查時靜默放行。",
        )
    if violations:
        detail = "；".join(
            f"{lhs} 有 {n} 列不符（如 {', '.join(ids)}）" for lhs, n, ids in violations
        )
        raise DataError(
            "institutional_daily",
            date,
            f"T86 內部恆等式不成立：{detail}。"
            "已知成因是上游回傳的 fields 與 data 不對應，造成欄位整體位移而**不報錯**"
            "（T-102：2022-01~2026-09 曾有 41 日共 508 列如此）。此日資料不可用。",
        )


def clean_institutional(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """T86 原始 → institutional_daily。外資 = 外陸資(不含自營) + 外資自營商。"""
    assert_t86_consistent(raw, date)
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


# ── T-313：個股本益比／殖利率／淨值比（證交所 BWIBBU_d、櫃買 peQryDate）────────────────
# 「不計算」標記：證交所 "-"、櫃買 "N/A"。櫃買附註：「當每股稅後純益為 0 或負數時，則不計算本益比」；
# 新上市、還沒有財報的股票淨值比也不計算（實測 7835 @ 2026-07-24、6287 @ 2025-06-20）。
# 存 **0.0**——這是 stock_valuation 既有的慣例，不是新發明：FinMind 全史 477 萬列同樣以 0 表示「不適用」
# （2026-09-18 兩市 423 個標記與 FinMind 的 0 逐檔對上，D-157），下游 `fundamental._valuation`
# 以 positive_only 把 ≤0 轉 null、洞悉層把 0 當缺值。只認各市場自己的那一個標記；其他非數字一律 raise。
_VALUATION_NOT_COMPUTED = 0.0
_VALUATION_HOUR = 18  # 同 FinMind 歷史（scripts/load_finmind_history_tables.py 的 valuation 規格）


def _strict_float(
    raw: pl.DataFrame, col: str, id_col: str, marker: str | None, date: dt.date
) -> pl.Series:
    """字串欄 → Float64：千分位逗號去掉；`marker` → 0.0；其他解析不了或非有限值 → raise（不轉 null）。"""
    text = raw[col].str.strip_chars().str.replace_all(",", "")
    parsed = text.cast(pl.Float64, strict=False)
    if marker is None:
        marked = pl.repeat(False, raw.height, dtype=pl.Boolean, eager=True)
    else:
        marked = (text == marker).fill_null(False)
    bad = raw.filter((parsed.is_null() | ~parsed.is_finite()).fill_null(True) & ~marked)
    if bad.height:
        sample = bad.select(id_col, col).head(5).rows()
        raise DataError(
            "stock_valuation",
            date,
            f"{col} 有 {bad.height} 列不是數字（只認 {marker!r} 為「不計算」）：{sample}",
        )
    return pl.select(
        pl.when(marked).then(pl.lit(_VALUATION_NOT_COMPUTED)).otherwise(parsed)
    ).to_series()


def _clean_valuation(raw: pl.DataFrame, date: dt.date, id_col: str, marker: str) -> pl.DataFrame:
    table = "stock_valuation"
    missing = [c for c in (id_col, "本益比", "股價淨值比", "殖利率(%)") if c not in raw.columns]
    if missing:
        raise DataError(table, date, f"回應缺欄位 {missing}（來源改版？）；欄位：{raw.columns}")
    ids = raw[id_col].str.strip_chars()
    if ids.is_null().any() or (ids == "").any() or ids.is_duplicated().any():
        raise DataError(table, date, f"{id_col} 有空白或重複：{ids.filter(ids.is_duplicated())}")
    # 殖利率是率 → 小數（黃金規則 3）；實測從來沒有「不計算」標記，沒股利是 "0.00"。
    # 用 Python 真除法：polars 除以純量實作成「乘倒數」（0.94×0.01＝0.0094，0.94÷100＝0.009399999999999999），
    # 最後一位會與 FinMind 歷史（`finmind.valuation_rows` 的 v / 100.0）不同——同一個數字存成兩個值。
    pct = _strict_float(raw, "殖利率(%)", id_col, None, date).to_list()
    out = pl.DataFrame(
        {
            "date": pl.repeat(date, raw.height, dtype=pl.Date, eager=True),
            "stock_id": ids,
            "per": _strict_float(raw, "本益比", id_col, marker, date),
            "pbr": _strict_float(raw, "股價淨值比", id_col, marker, date),
            "dividend_yield": pl.Series([v / 100.0 for v in pct], dtype=pl.Float64),
            "available_at": pl.repeat(
                _at(date, _VALUATION_HOUR), raw.height, dtype=schemas.TW_DT, eager=True
            ),
        }
    )
    # 新表的驗證失敗只算這一張表失敗：run_ingest 只接 DataError，QualityGateError 會讓同一輪
    # 排在後面的來源（例如櫃買行情）一起沒抓。
    try:
        return schemas.validate(table, out)
    except QualityGateError as exc:
        raise DataError(table, date, str(exc)) from exc


def clean_valuation(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """證交所 BWIBBU_d 原始 → stock_valuation（上市）。本益比／淨值比 "-"＝不計算 → 0.0。"""
    return _clean_valuation(raw, date, "證券代號", "-")


def clean_tpex_valuation(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """櫃買 peQryDate 原始 → stock_valuation（上櫃）。本益比／淨值比 "N/A"＝不計算 → 0.0。"""
    return _clean_valuation(raw, date, "股票代號", "N/A")


# 櫃買三大法人買賣明細的七組欄位（去重後的欄名，依頁面順序）：每組「買進／賣出／買賣超股數」。
# 外資＝外資及陸資合計（＝不含自營＋外資自營商，與上市 T86 的 foreign_net 同義）；自營商＝自營商合計。
_TPEX_INSTI_NET = {
    "foreign_ex_dealer": "買賣超股數",
    "foreign_dealer": "買賣超股數_2",
    "foreign": "買賣超股數_3",
    "trust": "買賣超股數_4",
    "dealer_self": "買賣超股數_5",
    "dealer_hedge": "買賣超股數_6",
    "dealer": "買賣超股數_7",
}
_TPEX_INSTI_TOTAL = "三大法人買賣超股數合計"


def clean_tpex_institutional(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """櫃買 insti/dailyTrade 原始 → institutional_daily（上櫃）。

    欄名全是重複的「買進／賣出／買賣超股數」，只能依位置對應；所以先驗三條加總
    （外資合計＝不含自營＋外資自營商、自營合計＝自行買賣＋避險、三大合計＝外資＋投信＋自營），
    任何一列不符整天 raise——欄位位移不會報錯，只會給出一整天錯的數字（同上市 T86，T-102）。
    """
    need = [*_TPEX_INSTI_NET.values(), _TPEX_INSTI_TOTAL, "代號"]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        raise DataError(
            "institutional_daily", date, f"櫃買法人表缺欄位 {missing}；現有 {raw.columns}"
        )
    n = {k: _int(v) for k, v in _TPEX_INSTI_NET.items()}
    checks = {
        "外資合計": n["foreign"] - (n["foreign_ex_dealer"] + n["foreign_dealer"]),
        "自營合計": n["dealer"] - (n["dealer_self"] + n["dealer_hedge"]),
        "三大合計": _int(_TPEX_INSTI_TOTAL) - (n["foreign"] + n["trust"] + n["dealer"]),
    }
    for name, diff in checks.items():
        bad = raw.filter(diff.is_null() | (diff != 0))
        if bad.height:
            ids = bad["代號"].str.strip_chars().to_list()[:5]
            raise DataError(
                "institutional_daily",
                date,
                f"櫃買法人表「{name}」加總不符 {bad.height} 列（如 {ids}），欄位可能位移",
            )
    out = raw.select(
        pl.lit(date, dtype=pl.Date).alias("date"),
        pl.col("代號").str.strip_chars().alias("stock_id"),
        n["foreign"].alias("foreign_net"),
        n["trust"].alias("trust_net"),
        n["dealer"].alias("dealer_net"),
        pl.lit(_at(date, 18), dtype=schemas.TW_DT).alias("available_at"),
    ).filter(pl.col("stock_id").str.contains(r"^[0-9]{4,6}[A-Z]?$"))
    return schemas.validate("institutional_daily", out)


def clean_tpex_margin(raw: pl.DataFrame, date: dt.date) -> pl.DataFrame:
    """櫃買 margin/balance 原始 → margin_daily（上櫃）。資餘額／券餘額單位為張，×1000 轉股（SPEC-000）。"""
    for col in ("代號", "資餘額", "券餘額"):
        if col not in raw.columns:
            raise DataError(
                "margin_daily", date, f"櫃買融資融券表缺欄位「{col}」；現有 {raw.columns}"
            )
    out = raw.select(
        pl.lit(date, dtype=pl.Date).alias("date"),
        pl.col("代號").str.strip_chars().alias("stock_id"),
        (_int("資餘額") * SHARES_PER_LOT).alias("margin_balance"),
        (_int("券餘額") * SHARES_PER_LOT).alias("short_balance"),
        pl.lit(_at(date, 21), dtype=schemas.TW_DT).alias("available_at"),
    ).filter(pl.col("stock_id").str.contains(r"^[0-9]{4,6}[A-Z]?$"))
    bad = out.filter(pl.col("margin_balance").is_null() | pl.col("short_balance").is_null())
    if bad.height:
        raise DataError(
            "margin_daily",
            date,
            f"櫃買融資融券餘額有 {bad.height} 列不是數字（如 {bad['stock_id'].to_list()[:5]}）",
        )
    return schemas.validate("margin_daily", out)


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

    覆蓋範圍：id=1-5 是全市場（含四碼普通股約 2,950 檔與 ETF／受益證券；T-386 實測），
    代號保留前導零與字尾字母。
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
    # T-117：中文名不得含替換字元 U+FFFD。這是解碼失敗留下的痕跡，只影響顯示但會出現在
    # 每一份對外報表；閘門放在這裡是因為它是唯一能同時看到 stock_id 與 name 的地方。
    broken = out.filter(pl.col("name").str.contains("\ufffd", literal=True))
    if broken.height:
        ids = broken["stock_id"].to_list()[:8]
        raise DataError(
            "stock_meta",
            None,
            f"中文名含替換字元 U+FFFD 的有 {broken.height} 檔（如 {', '.join(ids)}）"
            "——來源解碼失敗。標準 Big5 缺「碁」等字，須用 cp950（T-117）。",
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


def clean_market_holidays(raw: pl.DataFrame) -> pl.DataFrame:
    """TWSE 官方開休市日曆原始 → market_holidays（參考表，無 date 分區、無 available_at）。

    Date 為民國封裝碼（"1150101"＝民國 115=2026 年 1 月 1 日）：固定 7 碼（ROC 年 3＋月 2＋
    日 2），長度不符即來源格式變動 → raise（黃金規則 5：不靜默套錯解析）。closed 由名稱判定：
    名稱含「開始交易」或「最後交易」者為交易日註記（closed=False），其餘為休市（closed=True）
    ——此端點夾帶「農曆春節前最後交易日」等純交易日資訊列，全當休市會誤刪真實交易日。
    """
    d = pl.col("Date").str.strip_chars()
    bad = raw.filter(d.str.len_chars() != 7).height
    if bad:
        raise DataError("market_holidays", None, f"官方日曆 Date 非 7 碼民國格式（{bad} 筆）")
    year = d.str.slice(0, 3).cast(pl.Int32, strict=False) + 1911  # 民國 3 碼 → 西元年
    # 組西元封裝碼 YYYYMMDD 後 strptime（strict=False）：月/日超範圍或非數字 → null，交由下方
    # guard raise DataError；不用 pl.date——它對超範圍值會拋 polars ComputeError，繞過 DataError
    # 與 ingest_log 失敗記錄（違反黃金規則 5：失敗要走結構化路徑）。
    holiday_date = (year.cast(pl.Utf8) + d.str.slice(3, 4)).str.strptime(
        pl.Date, "%Y%m%d", strict=False
    )
    is_trading = pl.col("Name").str.contains("開始交易", literal=True) | pl.col(
        "Name"
    ).str.contains("最後交易", literal=True)
    out = raw.select(
        holiday_date.alias("holiday_date"),
        year.alias("year"),
        pl.col("Name").str.strip_chars().alias("name"),
        pl.col("Weekday").str.strip_chars().alias("weekday"),
        pl.col("Description").str.strip_chars().alias("description"),
        (~is_trading).alias("closed"),
    )
    if out.filter(pl.col("holiday_date").is_null()).height:
        raise DataError("market_holidays", None, "官方日曆有無法解析的日期（月/日超範圍？）")
    out = out.unique(subset=["holiday_date"], keep="first", maintain_order=True)
    return schemas.validate("market_holidays", out)


# 表名 → (clean 函數) 註冊表（TWSE）
CLEANERS = {
    "prices_daily": clean_prices,
    "institutional_daily": clean_institutional,
    "margin_daily": clean_margin,
    "stock_valuation": clean_valuation,
}

# TPEX 專用清洗（上櫃）
TPEX_CLEANERS = {
    "prices_daily": clean_tpex_prices,
    "stock_valuation": clean_tpex_valuation,
    "institutional_daily": clean_tpex_institutional,
    "margin_daily": clean_tpex_margin,
}

# TDCC 專用清洗（集保戶股權分散）
TDCC_CLEANERS = {
    "shareholding_dispersion": clean_shareholding_dispersion,
}


def reconcile_stock_meta(
    existing: pl.DataFrame,
    *,
    present_ids: Sequence[str],
    info: Sequence[Mapping[str, object]],
    delistings: Sequence[Mapping[str, object]],
) -> pl.DataFrame:
    """把「有行情但不在 stock_meta」的股票補進來，並補上 delist_date（T-111 / D-053）。

    `clean_stock_meta` 的來源（ISIN）只列現存上市櫃公司，故已下市者整批缺席；
    依賴 stock_meta 的三個因子（含 pin 特徵 `price.residual_reversal_5d`）會**靜默**略過它們，
    而缺的正是後來下市的左尾樣本。

    規則：
    - **既有列不覆蓋**（名稱／市場／產業／上市日都保留），只補原本永遠是 null 的 `delist_date`。
    - `present_ids`（行情裡真的出現過的股票）中不在 `existing` 者，用 `info` 補列。
    - `market` 是必填欄位，`info` 沒給市場別的股票**不補列**（不得偽造），由呼叫端回報缺口。
    """
    inf = {str(r["stock_id"]): r for r in info}
    dl = {str(r["stock_id"]): r["delist_date"] for r in delistings}
    have = set(existing["stock_id"].to_list()) if existing.height else set()

    added: list[dict[str, object]] = []
    for sid in present_ids:
        if sid in have:
            continue
        rec = inf.get(sid)
        if rec is None or rec.get("market") is None:
            continue  # 缺市場別（必填）→ 不補列
        added.append(
            {
                "stock_id": sid,
                "name": rec.get("name"),
                "market": rec["market"],
                "industry": rec.get("industry"),
                "list_date": None,
                "delist_date": dl.get(sid),
            }
        )

    cols = ["stock_id", "name", "market", "industry", "list_date", "delist_date"]
    out = (
        existing.select(cols)
        if existing.height
        else pl.DataFrame(schema={c: pl.String for c in cols})
    )
    if added:
        new = pl.DataFrame(added).with_columns(
            pl.col("list_date").cast(pl.Date), pl.col("delist_date").cast(pl.Date)
        )
        out = pl.concat([out.select(cols), new.select(cols)], how="vertical_relaxed")
    # 既有列補 delist_date（原本一律 null）
    if dl:
        m = pl.DataFrame({"stock_id": list(dl), "_dl": [dl[k] for k in dl]}).with_columns(
            pl.col("_dl").cast(pl.Date)
        )
        out = (
            out.join(m, on="stock_id", how="left")
            .with_columns(delist_date=pl.coalesce("delist_date", "_dl"))
            .drop("_dl")
        )
    out = out.unique(subset=["stock_id"], keep="first").sort("stock_id")
    return schemas.validate("stock_meta", out)


# adj_factor 經 after/before 的浮點往返後乘回前收，末位誤差約 1e-16 相對量級。
_BAND_FLOAT_TOL = 1e-9


def inconsistent_events(
    events: pl.DataFrame, prices: pl.DataFrame, *, max_deviation: float
) -> pl.DataFrame:
    """找出還原因子與行情互相矛盾的除權息事件（T-098 / D-054 複查）。

    除權息當日的成交價必須落在「參考價 ± 漲跌幅」之內；若 `open` 相對
    `adj_factor × 前收` 的偏離超過 `max_deviation`（＝台股漲跌幅 10%），
    那個因子就不可能是對的。實測 6 筆如此，其中 3 筆在宣稱的除權日其實是**漲停**。

    危害方向與 D-054 相反但性質相同：假事件讓該股的累積還原因子憑空縮小，
    每個跨過該日的標籤窗被灌水 `1/adj_factor`（實測最大 +47.8%），
    使它連續 h 個訊號日排在橫斷面前段——同樣是「模型學到公司行動日曆」。

    除權息日**無行情列**者（颱風休市、停牌，約 5.75%）無從比對，不視為矛盾。
    """
    px = (
        prices.sort("stock_id", "date")
        .with_columns(prev_close=pl.col("close").shift(1).over("stock_id"))
        .select("date", "stock_id", "open", "prev_close")
    )
    return (
        events.join(px, on=["date", "stock_id"], how="inner")
        .filter(
            pl.col("prev_close").is_not_null()
            & (pl.col("prev_close") > 0.0)
            & (pl.col("open") > 0.0)
        )
        .with_columns(
            deviation=pl.col("open") / (pl.col("adj_factor") * pl.col("prev_close")) - 1.0
        )
        # 漲跌停價**恰好**落在帶寬邊界上（實測 6791 於 2026-04-16 自參考價 120.0 漲停到
        # 132.0，偏離正好 +10.0%），而 adj_factor 是 after/before 的浮點往返值，
        # 乘回去會有末位誤差。不留容差會把合法的漲跌停誤判為資料錯誤。
        .filter(pl.col("deviation").abs() > max_deviation * (1.0 + _BAND_FLOAT_TOL))
        .sort("date", "stock_id")
    )
