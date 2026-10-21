"""資料合約（SPEC-001 §1）——clean 層全表 pandera schema。

本檔是欄位定義的唯一真相來源（SPEC-000 文件優先權）。文件與此處衝突時以此為準。
單位遵守 SPEC-000：stock_id 字串保前導零、價格 float64 TWD、成交量 int64 股、
金額 float64 TWD、報酬率小數。available_at 一律 tz-aware Asia/Taipei（型別即強制）。
"""

from __future__ import annotations

import warnings

import pandera.polars as pa
import polars as pl

from twquant.errors import QualityGateError

# tz-aware 台北時間戳型別：available_at 用此型別，schema 層即強制帶時區。
TW_DT = pl.Datetime(time_unit="us", time_zone="Asia/Taipei")

# 每張表的 schema 版本（破壞性變更需新表名，見 SPEC-001）。
SCHEMA_VERSIONS: dict[str, int] = {}


def _nonneg(dtype: pl.DataType | type[pl.DataType], *, nullable: bool = False) -> pa.Column:
    return pa.Column(dtype, checks=pa.Check.ge(0), nullable=nullable)


def _col(dtype: pl.DataType | type[pl.DataType], *, nullable: bool = False) -> pa.Column:
    return pa.Column(dtype, nullable=nullable)


def _schema(name: str, version: int, columns: dict[str, pa.Column]) -> pa.DataFrameSchema:
    SCHEMA_VERSIONS[name] = version
    return pa.DataFrameSchema(columns, strict=True, name=name)


# ── clean 層資料表 ───────────────────────────────────────────────

PRICES_DAILY = _schema(
    "prices_daily",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "open": _nonneg(pl.Float64),
        "high": _nonneg(pl.Float64),
        "low": _nonneg(pl.Float64),
        "close": _nonneg(pl.Float64),
        "volume": _nonneg(pl.Int64),  # 股
        "amount": _nonneg(pl.Float64),  # TWD
        "limit_up_price": _col(pl.Float64, nullable=True),
        "limit_down_price": _col(pl.Float64, nullable=True),
        "available_at": _col(TW_DT),
    },
)

INSTITUTIONAL_DAILY = _schema(
    "institutional_daily",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "foreign_net": _col(pl.Int64),  # 股，可負
        "trust_net": _col(pl.Int64),
        "dealer_net": _col(pl.Int64),
        "available_at": _col(TW_DT),
    },
)

INST_HOLDING = _schema(
    "inst_holding",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "trust_holding_shares": _nonneg(pl.Int64),
        "foreign_holding_shares": _nonneg(pl.Int64),
        "trust_holding_pct": _col(pl.Float64, nullable=True),
        "estimated": _col(pl.Boolean),
        "available_at": _col(TW_DT),
    },
)

MARGIN_DAILY = _schema(
    "margin_daily",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "margin_balance": _nonneg(pl.Int64),  # 融資餘額（張或股，見清洗；此處股）
        "short_balance": _nonneg(pl.Int64),  # 融券餘額
        "available_at": _col(TW_DT),
    },
)

BROKER_TRADES = _schema(
    "broker_trades",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "broker_id": _col(pl.String),
        "buy_shares": _nonneg(pl.Int64),
        "sell_shares": _nonneg(pl.Int64),
        "avg_price": _col(pl.Float64, nullable=True),
        "available_at": _col(TW_DT),
    },
)

SHAREHOLDING_DISPERSION = _schema(
    "shareholding_dispersion",
    1,
    {
        "date": _col(pl.Date),  # 基準交易日（該週最後交易日）
        "stock_id": _col(pl.String),
        "level": _col(pl.Int32),  # 持股分級 1..15
        "holders": _nonneg(pl.Int64),
        "shares": _nonneg(pl.Int64),
        "available_at": _col(TW_DT),
    },
)

MONTHLY_REVENUE = _schema(
    "monthly_revenue",
    1,
    {
        "date": _col(pl.Date),  # 分區鍵 = 所屬月最後日曆日（= period_end）
        "year_month": _col(pl.String),  # "YYYY-MM"
        "stock_id": _col(pl.String),
        "period_end": _col(pl.Date),  # 所屬月最後日曆日
        "revenue": _col(pl.Float64),  # 元（清洗層由千元 ×1000）；金融/券商可為負，不設 nonneg
        "yoy": _col(pl.Float64, nullable=True),  # 小數（去年同月增減）
        "available_at": _col(TW_DT),
        "available_at_estimated": _col(pl.Boolean),
    },
)

STOCK_VALUATION = _schema(
    "stock_valuation",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        # 倍數（非率），不設 nonneg：PER 為負＝虧損公司，是 TWSE 慣例下有意義的值
        "per": _col(pl.Float64),
        "pbr": _col(pl.Float64),
        "dividend_yield": _col(pl.Float64),  # 小數（0.0289 = 2.89%），黃金規則 3
        "available_at": _col(TW_DT),
    },
)

FINANCIAL_STATEMENTS = _schema(
    "financial_statements",
    1,
    {
        # T-097：財報三表長表。date = 季底（所屬期間末日），**不是**公布日。
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        # income | balance | cashflow。損益表與資產負債表都有 EquityAttributableToOwnersOfParent
        # （一個是歸屬母公司淨利、一個是歸屬母公司權益），只能靠這欄區分，不能靠 type 名。
        "statement": _col(pl.String),
        "type": _col(
            pl.String
        ),  # FinMind 科目代碼（如 Revenue、TotalAssets）；_per 百分比列已在映射層剔除
        "value": _col(pl.Float64),  # 元；損益表為單季值、現金流量表為累計 YTD、資產負債表為時點值
        # PIT：法定申報期限 22:00（Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31），非實際公告日
        "available_at": _col(TW_DT),
        "available_at_estimated": _col(pl.Boolean),
    },
)

CORPORATE_EVENTS = _schema(
    "corporate_events",
    1,
    {
        "date": _col(pl.Date),  # 除權息/事件基準日
        "stock_id": _col(pl.String),
        "event_type": _col(pl.String),  # cash_dividend / stock_dividend / capital_reduction ...
        "adj_factor": _col(pl.Float64, nullable=True),  # 還原因子
        "ref_price": _col(pl.Float64, nullable=True),  # 參考價
        "available_at": _col(TW_DT),
    },
)

# ── 第 1 批免費籌碼（T-309／D-154）：只寫 PG（直寫表，見 serving/schema.py 的 DIRECT_PG_SPECS）──
_MARKETS = ("twse", "tpex")

# v1 在 T-309 之前從未有任何資料寫入，故就地增欄、不另開新表名（D-154 §二）。
DISPOSITION_STOCKS = _schema(
    "disposition_stocks",
    1,
    {
        "date": _col(pl.Date),  # 公告日
        "stock_id": _col(pl.String),  # 全部有價證券（含權證／ETF）
        "market": pa.Column(pl.String, checks=pa.Check.isin(_MARKETS)),
        "effective_start": _col(pl.Date),
        "effective_end": _col(pl.Date),
        # 第幾次處置：1／2；證交所「人工管制撮合」沒有次數 → null（不猜）
        "level": pa.Column(pl.Int64, checks=pa.Check.isin([1, 2]), nullable=True),
        # 「約每 N 分鐘撮合一次」的 N；內容沒寫 → null
        "match_minutes": pa.Column(pl.Int64, checks=pa.Check.gt(0), nullable=True),
        "reason": _col(pl.String, nullable=True),  # 證交所「處置條件」／櫃買「處置原因」
        "measure": _col(pl.String, nullable=True),  # 處置內容（去 HTML、全形轉半形）
        "available_at": _col(TW_DT),  # 公告日 18:00（SPEC-001）
    },
)

ATTENTION_STOCKS = _schema(
    "attention_stocks",
    1,
    {
        # 來源的「累計次數」刻意不存：證交所是查詢區間內的總次數（含之後的公告＝前視），
        # 櫃買是「當天公告幾檔」，兩者都不是這一列的事實（D-154 §二）。
        "date": _col(pl.Date),  # 公告日
        "stock_id": _col(pl.String),  # 全部有價證券
        "market": pa.Column(pl.String, checks=pa.Check.isin(_MARKETS)),
        "clauses": _col(pl.String, nullable=True),  # 觸及的款次，遞增逗號串如 "1,9"
        "info": _col(pl.String),  # 注意交易資訊（去 HTML、全形轉半形）
        "close": _nonneg(pl.Float64, nullable=True),  # 公告日收盤價（TWD）
        "available_at": _col(TW_DT),  # 公告日 18:00
    },
)

DAYTRADE_DAILY = _schema(
    "daytrade_daily",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "daytrade_shares": _nonneg(pl.Int64),  # 當日沖銷成交股數（股）
        "daytrade_buy_amount": _nonneg(pl.Float64),  # 當沖買進成交金額（TWD）
        "daytrade_sell_amount": _nonneg(pl.Float64),  # 當沖賣出成交金額（TWD）
        # 暫停「先賣後買」當沖（FinMind BuyAfterSale='Y'）
        "sell_first_suspended": _col(pl.Boolean),
        "available_at": _col(TW_DT),  # 同日 21:00（比照 margin_daily，D-008）
    },
)

SBL_SHORT_DAILY = _schema(
    "sbl_short_daily",
    1,
    {
        # 借券賣出，全部是**股**（FinMind TaiwanDailyShortSaleBalances；不是張，不 ×1000）
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "sbl_prev_balance": _nonneg(pl.Int64),  # 前日餘額
        "sbl_sell": _nonneg(pl.Int64),  # 當日賣出
        "sbl_return": _nonneg(pl.Int64),  # 當日還券
        "sbl_adjust": _col(pl.Int64),  # 當日調整（可負）
        "sbl_balance": _nonneg(pl.Int64),  # 當日餘額＝前日＋賣出－還券＋調整
        "sbl_next_quota": _col(pl.Int64, nullable=True),  # 次一營業日可限額
        "sbl_covering": _col(pl.Int64, nullable=True),  # FinMind SBLShortSalesShortCovering
        "available_at": _col(TW_DT),  # 同日 21:00
    },
)

TAIFEX_INSTITUTIONAL = _schema(
    "taifex_institutional",
    1,
    {
        "date": _col(pl.Date),
        "product": _col(pl.String),  # TX / MTX ...
        "side": _col(pl.String),  # foreign / trust / dealer
        "net_oi": _col(pl.Int64),  # 淨未平倉口數，可負
        "available_at": _col(TW_DT),
    },
)

STOCK_META = _schema(
    "stock_meta",
    1,
    {
        "stock_id": _col(pl.String),
        "name": _col(pl.String, nullable=True),
        "market": _col(pl.String),  # TWSE / TPEX
        "industry": _col(pl.String, nullable=True),
        "list_date": _col(pl.Date, nullable=True),
        "delist_date": _col(pl.Date, nullable=True),
    },
)

INDEX_DAILY = _schema(
    "index_daily",
    1,
    {
        "date": _col(pl.Date),
        "index_id": _col(pl.String),  # TAIEX（加權）/ TPEX（櫃買）
        "open": _col(pl.Float64, nullable=True),  # 櫃買僅收盤，OHLC 留 null
        "high": _col(pl.Float64, nullable=True),
        "low": _col(pl.Float64, nullable=True),
        "close": _nonneg(pl.Float64),  # 指數點數
        "available_at": _col(TW_DT),
    },
)

TRADING_CALENDAR = _schema(
    "trading_calendar",
    1,
    {
        "date": _col(pl.Date),  # 一列一交易日
    },
)

MARKET_HOLIDAYS = _schema(
    "market_holidays",
    1,
    {
        # 參考/維度表（無 date 欄 → 單一 _all 分區；官方年度公布，非 PIT 事實，無 available_at）。
        # 用 holiday_date 而非 date，避免 warehouse 依 date 逐日分區（整年一檔即可）。
        "holiday_date": _col(pl.Date),  # 該註記所屬日期
        "year": _col(pl.Int32),  # 西元年（民國換算）
        "name": _col(pl.String),  # 假日/註記名稱
        "weekday": _col(pl.String, nullable=True),  # 原始星期字（四/五/六…）
        "description": _col(pl.String, nullable=True),
        "closed": _col(pl.Boolean),  # True=休市；False=僅為交易日註記（開始/最後交易日）
    },
)

INGEST_LOG = _schema(
    "ingest_log",
    1,
    {
        "source": _col(pl.String),
        "table": _col(pl.String),
        "date": _col(pl.Date, nullable=True),
        "attempt_ts": _col(TW_DT),
        "stage": _col(pl.String),  # raw_landed / clean_validated
        "status": _col(pl.String),  # success / failed
    },
)

STRUCTURAL_BREAKS = _schema(
    "structural_breaks",
    1,
    {
        "date": _col(pl.Date),
        "name": _col(pl.String),
        "description": _col(pl.String),
    },
)

ML_SCORES = _schema(
    "ml_scores",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "model_id": _col(pl.String),  # lgbm_h{h}_{train_end}_{cfg_hash8}（SPEC-010 §4）
        "score": _col(pl.Float64),  # 模型原始輸出
        "rank_pct": _col(pl.Float64, nullable=True),  # 同日橫斷面百分位 (0,1]
        "available_at": _col(TW_DT),  # 推論寫入時刻（PIT 邊界）
    },
)

NEWS_FEATURES = _schema(
    "news_features",
    1,
    {
        "date": _col(pl.Date),
        "stock_id": _col(pl.String),
        "available_at": _col(TW_DT),  # date 的 cutoff_hour（configs/news_factors.yaml）；PIT 邊界
        "count_1d": _col(pl.Float64, nullable=True),
        "attention_5d": _col(pl.Float64, nullable=True),
        "attention_20d": _col(pl.Float64, nullable=True),
        "novelty": _col(pl.Float64, nullable=True),
        "sent_1d": _col(pl.Float64, nullable=True),
        "sent_5d": _col(pl.Float64, nullable=True),
        "sent_shift": _col(pl.Float64, nullable=True),
        "sector_heat": _col(pl.Float64, nullable=True),
        "mops_count_5d": _col(pl.Float64, nullable=True),
        "mops_novelty": _col(pl.Float64, nullable=True),
        "topic_01": _col(pl.Float64, nullable=True),
        "topic_02": _col(pl.Float64, nullable=True),
        "topic_03": _col(pl.Float64, nullable=True),
        "topic_04": _col(pl.Float64, nullable=True),
        "topic_05": _col(pl.Float64, nullable=True),
        "topic_06": _col(pl.Float64, nullable=True),
        "topic_07": _col(pl.Float64, nullable=True),
        "topic_08": _col(pl.Float64, nullable=True),
        "topic_09": _col(pl.Float64, nullable=True),
        "topic_10": _col(pl.Float64, nullable=True),
        "topic_11": _col(pl.Float64, nullable=True),
        "topic_12": _col(pl.Float64, nullable=True),
        "topic_13": _col(pl.Float64, nullable=True),
        "topic_14": _col(pl.Float64, nullable=True),
        "topic_15": _col(pl.Float64, nullable=True),
        "topic_16": _col(pl.Float64, nullable=True),
        "topic_version": _col(pl.String, nullable=True),
    },
)

# ── 投信／ETF 資金流（SPEC-016／T-335）：只寫 PG（見 serving/schema.py 的 FUND_FLOW_SPECS）──

ETF_META = _schema(
    "etf_meta",
    1,
    {
        "etf_id": _col(pl.String),  # 保留前導零，如 "0050"、"00981A"
        "name": _col(pl.String),
        "issuer": _col(pl.String),  # 經理公司簡稱
        "fund_type": _col(pl.String),  # TWSE t187ap47_L 的「基金類型」
        "is_active": _col(pl.Boolean),  # 主動式 ETF
        "index_name": _col(pl.String, nullable=True),  # 追蹤指數；主動式為 null
        "listed_date": _col(pl.Date),
        "delisted_date": _col(pl.Date, nullable=True),
        "units_tracked": _col(
            pl.Boolean
        ),  # False＝來源沒有它的單位數（原因在 etf_meta.yaml 的 note）
        "available_at": _col(TW_DT),  # 上市日 18:00
    },
)

ETF_UNITS_DAILY = _schema(
    "etf_units_daily",
    1,
    {
        "date": _col(pl.Date),
        "etf_id": _col(pl.String),
        "units": _nonneg(pl.Int64),  # 已發行受益權單位數（FinMind NumberOfSharesIssued）
        # 與前一交易日相減（自算，T-334）；前一交易日缺值、分割／反分割窗內 → null（不是 0）
        "units_delta": _col(pl.Int64, nullable=True),
        "nav": _nonneg(pl.Float64, nullable=True),  # 每單位淨值 TWD；歷史沒有來源 → null
        "available_at": _col(TW_DT),  # 同日 21:00（外資持股統計同表）
    },
)

ETF_HOLDINGS_DAILY = _schema(
    "etf_holdings_daily",
    1,
    {
        "date": _col(pl.Date),  # 持股基準日（不是公布日、不是 PCF 適用日）
        "etf_id": _col(pl.String),
        "stock_id": _col(pl.String),
        "shares": _nonneg(pl.Int64),  # 股
        "weight": pa.Column(pl.Float64, checks=[pa.Check.ge(0.0), pa.Check.le(1.0)]),  # 小數
        "available_at": _col(TW_DT),  # max(基準日 18:00, 來源公布時點)
    },
)

INDEX_REBALANCE_EVENTS = _schema(
    "index_rebalance_events",
    1,
    {
        "etf_id": _col(pl.String),  # 由哪一檔指數型 ETF 的持股推導
        "stock_id": _col(pl.String),
        "action": pa.Column(pl.String, checks=pa.Check.isin(["add", "remove"])),
        "effective_date": _col(pl.Date),  # 新清單第一個出現（add）／消失（remove）的基準日
        "prev_base_date": _col(
            pl.Date
        ),  # 前一份清單的基準日：生效日落在 (prev_base_date, effective_date]
        "announce_date": _col(pl.Date, nullable=True),  # 由持股推導拿不到 → null（T-334）
        "source": _col(pl.String),  # holdings_diff
        "available_at": _col(TW_DT),  # 推導來源那份清單的 available_at
    },
)

# 表名 → schema 的註冊表（唯一入口）
SCHEMAS: dict[str, pa.DataFrameSchema] = {
    "prices_daily": PRICES_DAILY,
    "institutional_daily": INSTITUTIONAL_DAILY,
    "inst_holding": INST_HOLDING,
    "margin_daily": MARGIN_DAILY,
    "broker_trades": BROKER_TRADES,
    "shareholding_dispersion": SHAREHOLDING_DISPERSION,
    "monthly_revenue": MONTHLY_REVENUE,
    "index_daily": INDEX_DAILY,
    "stock_valuation": STOCK_VALUATION,
    "financial_statements": FINANCIAL_STATEMENTS,
    "corporate_events": CORPORATE_EVENTS,
    "disposition_stocks": DISPOSITION_STOCKS,
    "attention_stocks": ATTENTION_STOCKS,
    "daytrade_daily": DAYTRADE_DAILY,
    "sbl_short_daily": SBL_SHORT_DAILY,
    "taifex_institutional": TAIFEX_INSTITUTIONAL,
    "stock_meta": STOCK_META,
    "trading_calendar": TRADING_CALENDAR,
    "market_holidays": MARKET_HOLIDAYS,
    "ingest_log": INGEST_LOG,
    "structural_breaks": STRUCTURAL_BREAKS,
    "ml_scores": ML_SCORES,
    "news_features": NEWS_FEATURES,
    "etf_meta": ETF_META,
    "etf_units_daily": ETF_UNITS_DAILY,
    "etf_holdings_daily": ETF_HOLDINGS_DAILY,
    "index_rebalance_events": INDEX_REBALANCE_EVENTS,
}


def get_schema(table: str) -> pa.DataFrameSchema:
    """取表 schema；未知表名 raise。"""
    try:
        return SCHEMAS[table]
    except KeyError as exc:
        raise QualityGateError("schema", f"未知資料表：{table}") from exc


def validate(table: str, df: pl.DataFrame) -> pl.DataFrame:
    """對 df 跑該表 schema 驗證；失敗 raise QualityGateError（含 pandera 細節）。"""
    schema = get_schema(table)
    # pandera 1.x 在 polars 1.42+ 內部用了已棄用的 concat(how='horizontal')；
    # 精準抑制此第三方棄用，避免在 filterwarnings=error 下被誤判為 check 失敗。
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="the default behavior of", category=DeprecationWarning
        )
        try:
            result = schema.validate(df, lazy=True)
        except pa.errors.SchemaErrors as exc:
            raise QualityGateError(f"schema:{table}", str(exc.failure_cases)) from exc
        except pa.errors.SchemaError as exc:
            raise QualityGateError(f"schema:{table}", str(exc)) from exc
    return result


def schema_version(table: str) -> int:
    """取表 schema 版本。"""
    return SCHEMA_VERSIONS[table]
