"""供應層 PG schema：資料表規格與 DDL（SPEC-009 §2）。

T-031 範圍：六張鏡射表（欄位比照 data/schemas.py）+ sync_state 簿記表。
factor_values / broker_* 等隨 T-032 / T-035 加入。DDL 皆冪等（IF NOT EXISTS）。
`TableSpec` 是欄位/主鍵的單一真相——DDL 與 sync（COPY/upsert 欄位順序）都由它衍生，
避免兩處漂移。本模組**不** import psycopg——只產生 SQL 並在呼叫端連線上執行。

型別對應：Date→date、String→text、Int64→bigint、Float64→double precision、
tz-aware Datetime→timestamptz。stock_id 一律 text（保前導零）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from twquant.errors import DataError

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _check_ident(name: str) -> None:
    """schema 名只允許小寫字母/數字/底線；擋 SQL 注入（雖來自受信配置，仍防呆）。"""
    if not _IDENT.match(name):
        raise ValueError(f"非法 schema 識別字：{name!r}（僅允許小寫字母/數字/底線）")


@dataclass(frozen=True)
class TableSpec:
    """一張 PG 表的欄位、主鍵與分區型態。"""

    name: str
    columns: tuple[tuple[str, str], ...]  # (欄名, PG 型別)
    pk: tuple[str, ...]
    snapshot: bool = False  # True＝無 date 分區（stock_meta，每次全量重載）
    indexes: tuple[tuple[str, ...], ...] = ()  # 額外索引，每項一組欄位（如 (("date",),)）

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c for c, _ in self.columns)

    def ddl(self, schema: str) -> str:
        cols = ", ".join(f"{c} {t}" for c, t in self.columns)
        pk = ", ".join(self.pk)
        return f'CREATE TABLE IF NOT EXISTS "{schema}"."{self.name}" ({cols}, PRIMARY KEY ({pk}))'


MIRROR_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "prices_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("open", "double precision"),
            ("high", "double precision"),
            ("low", "double precision"),
            ("close", "double precision"),
            ("volume", "bigint"),
            ("amount", "double precision"),
            ("limit_up_price", "double precision"),
            ("limit_down_price", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "institutional_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("foreign_net", "bigint"),
            ("trust_net", "bigint"),
            ("dealer_net", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "margin_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("margin_balance", "bigint"),
            ("short_balance", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "broker_trades",  # 富邦DJ 券商(母公司)買賣超 top-15±（D-019）；broker_id=券商名、avg_price=null
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("broker_id", "text"),
            ("buy_shares", "bigint"),
            ("sell_shares", "bigint"),
            ("avg_price", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date", "broker_id"),
        indexes=(("date",),),
    ),
    TableSpec(
        "monthly_revenue",
        (
            ("date", "date"),
            ("year_month", "text"),
            ("stock_id", "text"),
            ("period_end", "date"),
            ("revenue", "double precision"),
            ("yoy", "double precision"),
            ("available_at", "timestamptz"),
            ("available_at_estimated", "boolean"),
        ),
        pk=("stock_id", "year_month"),
    ),
    TableSpec(
        "shareholding_dispersion",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("level", "integer"),
            ("holders", "bigint"),
            ("shares", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date", "level"),
    ),
    TableSpec(
        # T-010／D-044：FinMind 評價資料。dividend_yield 存小數（黃金規則 3）；
        # PER 可為負（虧損公司），故不加非負約束。
        "stock_valuation",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("per", "double precision"),
            ("pbr", "double precision"),
            ("dividend_yield", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("date", "stock_id"),
        indexes=(("date",),),
    ),
    TableSpec(
        # T-097：財報三表長表（FinMind 免費層）。PK 含 statement——損益表與資產負債表
        # 有同名科目 EquityAttributableToOwnersOfParent（淨利 vs 權益）。
        "financial_statements",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("statement", "text"),
            ("type", "text"),
            ("value", "double precision"),
            ("available_at", "timestamptz"),
            ("available_at_estimated", "boolean"),
        ),
        pk=("date", "stock_id", "statement", "type"),
        indexes=(("date",), ("stock_id",)),
    ),
    TableSpec(
        # T-082：先前遺漏於鏡射清單，導致 serving sync 不同步指數；而因子物化在 D-026
        # 之後改讀 PG，故 price.rs_market_20d 只能在 PG 恰好有的 52 天算出來
        # （Phase 1 模型中 split=0、gain=0.00%）。TPEx 僅提供收盤，OHL 可為 null。
        "index_daily",
        (
            ("date", "date"),
            ("index_id", "text"),
            ("open", "double precision"),
            ("high", "double precision"),
            ("low", "double precision"),
            ("close", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("date", "index_id"),
        indexes=(("date",),),
    ),
    TableSpec(
        # T-098／D-054：除權息還原因子。因子與標籤讀它算總報酬；回測引擎**不**讀它
        # （成交要用可成交的真實價格），改以現金入帳處理（T-113）。
        "corporate_events",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("event_type", "text"),
            ("adj_factor", "double precision"),
            ("ref_price", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("date", "stock_id"),
        indexes=(("stock_id",),),
    ),
    TableSpec(
        "stock_meta",
        (
            ("stock_id", "text"),
            ("name", "text"),
            ("market", "text"),
            ("industry", "text"),
            ("list_date", "date"),
            ("delist_date", "date"),
        ),
        pk=("stock_id",),
        snapshot=True,
    ),
)

FACTOR_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        # ML 排序分數（SPEC-010 §5）：`ml score` 直寫；薄因子 ml.* 經 PitView 讀取
        "ml_scores",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("model_id", "text"),
            ("score", "double precision"),
            ("rank_pct", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("model_id", "stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        # 新聞因子特徵（T-414／SPEC-018 §5）：`news features` 直寫；薄因子 news.* 經 PitView 讀取。
        # available_at ＝ date 的 cutoff_hour（configs/news_factors.yaml）；topic_01…16 的中心版本記在 topic_version
        "news_features",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("available_at", "timestamptz"),
            ("count_1d", "double precision"),
            ("attention_5d", "double precision"),
            ("attention_20d", "double precision"),
            ("novelty", "double precision"),
            ("sent_1d", "double precision"),
            ("sent_5d", "double precision"),
            ("sent_shift", "double precision"),
            ("sector_heat", "double precision"),
            ("mops_count_5d", "double precision"),
            ("mops_novelty", "double precision"),
            ("topic_01", "double precision"),
            ("topic_02", "double precision"),
            ("topic_03", "double precision"),
            ("topic_04", "double precision"),
            ("topic_05", "double precision"),
            ("topic_06", "double precision"),
            ("topic_07", "double precision"),
            ("topic_08", "double precision"),
            ("topic_09", "double precision"),
            ("topic_10", "double precision"),
            ("topic_11", "double precision"),
            ("topic_12", "double precision"),
            ("topic_13", "double precision"),
            ("topic_14", "double precision"),
            ("topic_15", "double precision"),
            ("topic_16", "double precision"),
            ("topic_version", "text"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "factor_values",
        (
            ("factor", "text"),
            ("stock_id", "text"),
            ("date", "date"),
            ("value", "double precision"),
            ("z", "double precision"),
            ("pct", "double precision"),
        ),
        pk=("factor", "stock_id", "date"),
        indexes=(("factor", "date"),),
    ),
    TableSpec(
        "factor_cross_stats",
        (
            ("factor", "text"),
            ("date", "date"),
            ("mean", "double precision"),
            ("std", "double precision"),
            ("n_universe", "integer"),
        ),
        pk=("factor", "date"),
    ),
    TableSpec(
        "composite_daily",
        (
            ("stock_id", "text"),
            ("date", "date"),
            ("signed_z_mean", "double precision"),
            ("pct", "double precision"),
        ),
        pk=("stock_id", "date"),
    ),
)

SYNC_STATE_SPEC = TableSpec(
    "sync_state",
    (
        ("table_name", "text"),
        ("last_date", "date"),
        ("last_synced_at", "timestamptz"),
        ("row_count", "bigint"),
    ),
    pk=("table_name",),
)

# 富邦DJ 分點回補的抓取帳本（D-025）：一列＝一次「已 commit」的 (日, 股) 抓取（含空結果
# n_rows=0）；stock_id 可為 chip_ingest.SENTINEL 標記整日非交易。簿記表（比照 sync_state）：
# 不屬鏡射/因子表，sync/rebuild 不觸碰。
FUBON_FETCH_LOG_SPEC = TableSpec(
    "fubon_fetch_log",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("n_rows", "integer"),
        ("fetched_at", "timestamptz"),
    ),
    pk=("date", "stock_id"),
)

# 策略選股池每日快照（SPEC-007 §6 / T-041）：一列＝某日某池一名成員，記錄「當日實際
# 顯示」的池組成供回溯——之後改配置閾值不重寫歷史（首錄保留）。衍生表：由 CLI
# `screener-snapshot` 寫入，非鏡射/因子表，sync/rebuild 不觸碰。
SCREENER_DAILY_SPEC = TableSpec(
    "screener_daily",
    (
        ("date", "date"),
        ("pool", "text"),
        ("stock_id", "text"),
        ("close", "double precision"),
        ("chg", "double precision"),
        ("yoy", "double precision"),
        ("conc", "double precision"),
        ("accum_ratio", "double precision"),
        ("streak", "integer"),
        ("universe_n", "integer"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "pool", "stock_id"),
)

# 券商母公司買賣超日聚合（T-055/D-030）：broker_trades 的 (date, stock) 彙總。雲端只留
# 10 日明細，250 日折線改讀本表；由 `serving broker-flow`（或雲端 pipeline 尾波）刷新。
# 衍生表：非鏡射/因子表，sync/rebuild 不觸碰。
BROKER_FLOW_DAILY_SPEC = TableSpec(
    "broker_flow_daily",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("buy_shares", "bigint"),
        ("sell_shares", "bigint"),
        ("net_shares", "bigint"),
        ("n_brokers", "integer"),
        ("available_at", "timestamptz"),
    ),
    pk=("stock_id", "date"),
    indexes=(("date",),),
)

# 第 1 批免費籌碼（T-309／D-154）：**直寫 PG** 的四張表（使用者裁決：資料只在 PG，不寫資料湖）。
# 刻意**不**放進 MIRROR_SPECS：`serving rebuild` 會 TRUNCATE 鏡射表再從資料湖重灌，
# 而資料湖裡沒有這四張——放進去，一次 rebuild 就清空。欄位與 data/schemas.py 同名同序（測試守著）。
DIRECT_PG_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "daytrade_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("daytrade_shares", "bigint"),
            ("daytrade_buy_amount", "double precision"),
            ("daytrade_sell_amount", "double precision"),
            ("sell_first_suspended", "boolean"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "sbl_short_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("sbl_prev_balance", "bigint"),
            ("sbl_sell", "bigint"),
            ("sbl_return", "bigint"),
            ("sbl_adjust", "bigint"),
            ("sbl_balance", "bigint"),
            ("sbl_next_quota", "bigint"),
            ("sbl_covering", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "disposition_stocks",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("market", "text"),
            ("effective_start", "date"),
            ("effective_end", "date"),
            ("level", "bigint"),
            ("match_minutes", "bigint"),
            ("reason", "text"),
            ("measure", "text"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "attention_stocks",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("market", "text"),
            ("clauses", "text"),
            ("info", "text"),
            ("close", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
)

# 當沖「首次抓到的值」（T-310，D-154 §三 A）：每日 pipeline 在 T+1 抓 T 日當沖時另存一份（先到先存，
# ON CONFLICT DO NOTHING），之後 T+2／T+3 重抓只覆寫 daytrade_daily。兩者一比就是「訊號當下用到的數字」
# 與定案值的差——任何當沖因子進研究之前要先看這個（D-154 §三）。量測用的簿記表：只由每日「籌碼」步驟寫、
# 不在鏡射清單、也不在 DIRECT_PG_SPECS（那是 T-309 的四張資料表）。fetched_at＝抓取時點。
DAYTRADE_FIRST_PUB_SPEC = TableSpec(
    "daytrade_first_pub",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("daytrade_shares", "bigint"),
        ("daytrade_buy_amount", "double precision"),
        ("daytrade_sell_amount", "double precision"),
        ("sell_first_suspended", "boolean"),
        ("available_at", "timestamptz"),
        ("fetched_at", "timestamptz"),
    ),
    pk=("stock_id", "date"),
    indexes=(("date",),),
)

# 每日直寫擷取（T-310）要建的表：四張資料表＋首次公布值簿記表。
DIRECT_INGEST_SPECS: tuple[TableSpec, ...] = (*DIRECT_PG_SPECS, DAYTRADE_FIRST_PUB_SPEC)

# 每日名單（T-332／D-170）：每條線當天收盤後的前 n 名，永久保留（約 30 列／日）。
# 大盤精選＝若當天換股的目標名單（score 為空：名單由規則排名而非單一分數）；
# 潛伏飆股＝補位候選序，score＝模型機率。衍生表：sync/rebuild/prune 都不碰。
DAILY_PICKS_SPEC = TableSpec(
    "daily_picks",
    (
        ("date", "date"),
        ("line", "text"),
        ("rank", "integer"),
        ("stock_id", "text"),
        ("score", "double precision"),
        ("model_id", "text"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "line", "stock_id"),
)

# 名單績效（D-176）：每條線一列，存網站要的整份 JSON（模擬帳戶淨值、逐筆明細）。每天由 daily_picks＋行情
# 從頭重算後整列覆蓋，可隨時重建；網站 `/api/scorecard` 原樣讀出。sync/rebuild/prune 都不碰。
SCORECARD_SPEC = TableSpec(
    "scorecard",
    (
        ("line", "text"),
        ("as_of", "date"),
        ("payload", "jsonb"),
        ("created_at", "timestamptz"),
    ),
    pk=("line",),
    snapshot=True,  # 沒有 date 分區：每次整份重算
)

# 族群（D-177）：每檔一個族群（stock_groups.py），網站每日名單旁邊顯示、族群資金照它分組。
# 整份重抓重算（每 refresh_days 天），可隨時重建；sync/rebuild/prune 都不碰。
# 表上沒有的股票（重抓之間新上市的）網站顯示官方產業別、族群資金不算它。
STOCK_GROUP_SPEC = TableSpec(
    "stock_group",
    (
        ("stock_id", "text"),
        ("grp", "text"),  # 族群：拆段後的段（散熱）、產業鏈（被動元件）或官方產業別（玻璃陶瓷）
        ("chain", "text"),  # 挑中的產業鏈；用官方產業別時 NULL
        ("segments", "text[]"),  # 這檔在這條鏈上掛的段，例：{電阻器}
        ("also", "text[]"),  # 還掛了哪些別的鏈
        ("updated_at", "timestamptz"),
    ),
    pk=("stock_id",),
    snapshot=True,
    indexes=(("grp",),),
)

# 族群資金（D-177 修訂）：每天每個族群的成交比重、資金倍數、漲跌家數（reporting/group_flow.py）。
# 每天由行情＋族群表算出、整天覆蓋；members 是族群排行列出的成員（jsonb）。sync/rebuild 不碰，雲端照 windows 修剪。
GROUP_FLOW_SPEC = TableSpec(
    "group_flow_daily",
    (
        ("date", "date"),
        ("grp", "text"),
        ("n", "bigint"),  # 當天有成交的成員
        ("up", "bigint"),
        ("down", "bigint"),
        ("ret", "double precision"),  # 成員當天漲跌的等權平均
        ("amount", "double precision"),  # TWD
        ("share", "double precision"),  # 佔全市場成交金額
        ("share_avg", "double precision"),  # 前 avg_sessions 日平均比重
        ("ratio", "double precision"),  # 資金倍數＝share／share_avg
        ("hot", "boolean"),  # 資金流入（門檻見 configs/stock_groups.yaml flow）
        ("members", "jsonb"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "grp"),
    indexes=(("grp", "date"),),
)

# 「為什麼選它」（D-180／T-360）：名單上每一檔的白話理由（每日「名單理由」步驟算、整天覆蓋）。
# 一天幾十列、全史保留（不進 windows）——雲端因子只留 2 天，過了就算不回來，所以要存下來。
PICK_REASONS_SPEC = TableSpec(
    "pick_reasons",
    (
        ("date", "date"),
        ("line", "text"),
        ("stock_id", "text"),
        ("model_id", "text"),
        ("reasons", "jsonb"),  # [{f: 因子, t: 白話, s: +1 加分／−1 扣分／0 前提}]
        ("created_at", "timestamptz"),
    ),
    pk=("date", "line", "stock_id"),
)

# 歷史同區間參考（D-179／T-359）：每條線 × 期間 × 分數區間一列，本機 scripts/score_reference.py 算一次、整表覆蓋。
# 小表、全史保留（不進 windows）；網站 /api/picks 每條線附上，每檔依分數對到區間。
SCORE_REFERENCE_SPEC = TableSpec(
    "score_reference",
    (
        ("line", "text"),
        ("period", "text"),  # dev｜unseen
        ("bucket", "text"),  # 區間鍵；不分區間的線＝ALL
        ("lo", "double precision"),  # 區間 [lo, hi)；ALL 為 NULL
        ("hi", "double precision"),
        ("n", "bigint"),
        ("mean", "double precision"),
        ("median", "double precision"),
        ("win_rate", "double precision"),
        ("big_loss_rate", "double precision"),
        ("p10", "double precision"),
        ("p90", "double precision"),
        ("best", "double precision"),
        ("worst", "double precision"),
        ("signals_from", "date"),
        ("signals_to", "date"),
        ("label", "text"),  # 期間的中文說明
        ("registration", "text"),
        ("generated_at", "timestamptz"),
    ),
    pk=("line", "period", "bucket"),
)

# 歷史相似情境（D-182／T-364）：情境庫（本機 scripts/similar_library.py --publish 建，整表覆蓋）與每天名單的比對結果。
# 情境庫是 zlib 壓縮的欄位切片（reporting/similar.encode_library），一列一份；比對結果全史保留、不進 windows。
SIMILAR_LIBRARY_SPEC = TableSpec(
    "similar_library",
    (
        ("part", "text"),  # meta｜<欄位>.<序號>
        ("payload", "bytea"),
        ("registration", "text"),
        ("created_at", "timestamptz"),
    ),
    pk=("part",),
)
PICK_SIMILAR_SPEC = TableSpec(
    "pick_similar",
    (
        ("date", "date"),
        ("line", "text"),
        ("stock_id", "text"),
        ("n", "integer"),
        ("mean", "double precision"),
        ("median", "double precision"),
        ("win_rate", "double precision"),
        ("big_loss_rate", "double precision"),
        ("p10", "double precision"),
        ("p90", "double precision"),
        ("best", "double precision"),
        ("worst", "double precision"),
        ("span_from", "date"),  # 鄰居的公布日範圍
        ("span_to", "date"),
        ("examples", "jsonb"),  # [{d: 公布日, sid: 代號, r: 20 天報酬}]，最像的在前
        ("registration", "text"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "line", "stock_id"),
)

# 投信／ETF 資金流（SPEC-016／T-335）：直寫 PG、資料湖沒有它們。刻意不放進 MIRROR_SPECS
# （rebuild 會清空）也不放進 DIRECT_PG_SPECS（那是 T-309 第 1 批籌碼，每日「籌碼」步驟逐張有來源）。
# 欄位與 data/schemas.py 同名同序（tests/test_fund_flow_units.py 守著）。
FUND_FLOW_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "etf_meta",
        (
            ("etf_id", "text"),
            ("name", "text"),
            ("issuer", "text"),
            ("fund_type", "text"),
            ("is_active", "boolean"),
            ("index_name", "text"),
            ("listed_date", "date"),
            ("delisted_date", "date"),
            ("units_tracked", "boolean"),
            ("available_at", "timestamptz"),
        ),
        pk=("etf_id",),
        snapshot=True,  # 無 date：整份由 configs/etf_meta.yaml 重載
    ),
    TableSpec(
        "etf_units_daily",
        (
            ("date", "date"),
            ("etf_id", "text"),
            ("units", "bigint"),
            ("units_delta", "bigint"),
            ("nav", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("etf_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "etf_holdings_daily",
        (
            ("date", "date"),
            ("etf_id", "text"),
            ("stock_id", "text"),
            ("shares", "bigint"),
            ("weight", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("etf_id", "date", "stock_id"),
        indexes=(("date",), ("stock_id", "date")),
    ),
    TableSpec(
        "index_rebalance_events",
        (
            ("etf_id", "text"),
            ("stock_id", "text"),
            ("action", "text"),
            ("effective_date", "date"),
            ("prev_base_date", "date"),
            ("announce_date", "date"),
            ("source", "text"),
            ("available_at", "timestamptz"),
        ),
        pk=("etf_id", "effective_date", "stock_id", "action"),
        snapshot=True,  # 由 etf_holdings_daily 整份重算
        indexes=(("stock_id", "effective_date"),),
    ),
)

# MOPS 重大訊息主旨（T-371／D-187）：直寫 PG、資料湖沒有。每日「重大訊息」步驟抓當天、前一天與回看窗內的缺日；
# 欄位與 data/sources/material_news.SCHEMA 同名同序（tests/test_material_news.py 守著）。只存主旨不存全文。
# 不叫 material_news：那是本機事件研究（2026-09-28）留下的全文凍結快照，欄位與主鍵都不同，不互相覆蓋。
MOPS_ANNOUNCEMENTS_SPEC = TableSpec(
    "mops_announcements",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("market", "text"),
        ("name", "text"),
        ("subject", "text"),
        ("clause", "text"),
        ("fact_date", "date"),
        ("mops_seq", "bigint"),
        ("available_at", "timestamptz"),
        ("content", "text"),  # 說明全文（2026-10-17 加，最後一欄：舊表 ADD COLUMN 即對齊）
    ),
    # MOPS 自己的編號不唯一（同公司同日不同公告都可能是 1），一則公告的身分是 (代號, 發言時間, 主旨)
    pk=("stock_id", "available_at", "subject"),
    indexes=(("date",),),
)

# 洞悉證據包（T-373／D-187）：每日 pipeline 分析完把 data/insights/*.json 整包寫進來（pack 欄 jsonb），
# 雲端 runner 每次都是新機器，檔案留不住；網站個股頁從這裡讀。date＝as_of 的日期（雲端滾動窗用）。
INSIGHT_PACKS_SPEC = TableSpec(
    "insight_packs",
    (
        ("insight_run_id", "text"),
        ("recommendation_run_id", "text"),
        ("stock_id", "text"),
        ("date", "date"),
        ("as_of", "timestamptz"),
        ("has_analysis", "boolean"),
        ("stance", "text"),
        ("recommended_bucket", "text"),
        ("pack", "jsonb"),
        ("created_at", "timestamptz"),
    ),
    pk=("insight_run_id",),
    indexes=(("stock_id", "as_of"), ("date",)),
)

# 大盤體檢與籌碼（T-374／D-188）：每個交易日一列，首頁戰情室讀；欄位與 reporting/market_daily.MarketDaily 同名
# （tests/test_market_daily.py 守著）。金額是 TWD；法人買賣超＝股數 × 當天收盤；融資餘額是來源單位（千元）。
MARKET_DAILY_SPEC = TableSpec(
    "market_daily",
    (
        ("date", "date"),
        ("taiex_close", "double precision"),
        ("taiex_chg", "double precision"),
        ("otc_close", "double precision"),
        ("otc_chg", "double precision"),
        ("amount", "double precision"),
        ("amount_avg", "double precision"),
        ("amount_ratio", "double precision"),
        ("up", "bigint"),
        ("down", "bigint"),
        ("flat", "bigint"),
        ("limit_up", "bigint"),
        ("limit_down", "bigint"),
        ("new_high", "bigint"),
        ("new_low", "bigint"),
        ("foreign_net", "double precision"),
        ("trust_net", "double precision"),
        ("dealer_net", "double precision"),
        ("foreign_streak", "bigint"),
        ("margin_balance", "double precision"),
        ("margin_delta", "double precision"),
        ("short_balance", "double precision"),
        ("created_at", "timestamptz"),
    ),
    pk=("date",),
)

# 大盤體檢分上市、上櫃（T-398／D-199）：每個交易日每個市場（tse／otc）一列，家數與成交金額的定義同 market_daily；
# 欄位與 reporting/market_daily.MarketBreadth 同名（tests/test_market_daily.py 守著）。另開一張表，不在 market_daily 後面加欄。
MARKET_BREADTH_SPEC = TableSpec(
    "market_breadth_daily",
    (
        ("date", "date"),
        ("market", "text"),
        ("up", "bigint"),
        ("down", "bigint"),
        ("flat", "bigint"),
        ("limit_up", "bigint"),
        ("limit_down", "bigint"),
        ("new_high", "bigint"),
        ("new_low", "bigint"),
        ("amount", "double precision"),
        ("amount_avg", "double precision"),
        ("amount_ratio", "double precision"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "market"),
)

# 期交所盤後統計（T-375／D-188）：三大法人臺股期貨未平倉淨額、選擇權買賣權比；每個交易日一列。
# 欄位與 data/sources/taifex_daily.SCHEMA 同名同序（tests/test_taifex_daily.py 守著）。
TAIFEX_DAILY_SPEC = TableSpec(
    "taifex_daily",
    (
        ("date", "date"),
        ("fut_dealer_net_oi", "bigint"),
        ("fut_trust_net_oi", "bigint"),
        ("fut_foreign_net_oi", "bigint"),
        ("fut_foreign_long_oi", "bigint"),
        ("fut_foreign_short_oi", "bigint"),
        ("pc_vol_ratio", "double precision"),
        ("pc_oi_ratio", "double precision"),
        ("put_oi", "bigint"),
        ("call_oi", "bigint"),
        ("available_at", "timestamptz"),
    ),
    pk=("date",),
)

# 法說會行事曆（T-376／D-188）：MOPS 月表，當月＋下月，每天覆蓋；欄位與 data/sources/conferences.SCHEMA 同名同序。
# 本機另一張研究用的 investor_conferences（2026-09-28 快照、以 row_key 為鍵）與它分開。
CONFERENCE_CALENDAR_SPEC = TableSpec(
    "conference_calendar",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("conf_time", "text"),
        ("market", "text"),
        ("name", "text"),
        ("location", "text"),
        ("summary", "text"),
        ("available_at", "timestamptz"),
    ),
    pk=("date", "stock_id", "conf_time"),
    indexes=(("date",),),
)

# 晨報（T-377／D-188）：每天 06:30 一列；規則版一句話一定有，模型版（headline／bullets／risk_note）拿不到就空並寫原因。
# inputs 是晨報引用的全部數字（jsonb），讓每句話都能對回來源。
_BRIEF_COLUMNS: tuple[tuple[str, str], ...] = (
    ("date", "date"),
    ("generated_at", "timestamptz"),
    ("rule_text", "text"),
    ("headline", "text"),
    ("bullets", "jsonb"),
    ("risk_note", "text"),
    ("model_id", "text"),
    ("absent_reason", "text"),
    ("checks", "jsonb"),  # 數字檢查丟掉了哪些句子
    ("facts", "jsonb"),  # 給模型的事實句（晨報的每個數字都出自這裡）
    ("inputs", "jsonb"),
)
MORNING_BRIEF_SPEC = TableSpec("morning_brief", _BRIEF_COLUMNS, pk=("date",))
# 盤後快訊（2026-10-19，使用者：「這邊台灣晚上時間也要跑一次」）：同一套欄位另一張表，不動線上晨報表的主鍵
EVENING_BRIEF_SPEC = TableSpec("evening_brief", _BRIEF_COLUMNS, pk=("date",))

# 除權除息預告（2026-10-17）：首頁行事曆用；證交所 TWT48U＋櫃買 tpex_exright_prepost，每天整份 upsert。
# 欄位與 data/sources/dividend_calendar.SCHEMA 同名同序。還原權值仍用 corporate_events（除權息後的結果）。
DIVIDEND_CALENDAR_SPEC = TableSpec(
    "dividend_calendar",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("market", "text"),
        ("name", "text"),
        ("kind", "text"),
        ("cash_dividend", "double precision"),
        ("stock_ratio", "double precision"),
        ("rights_ratio", "double precision"),
        ("rights_price", "double precision"),
        ("available_at", "timestamptz"),
    ),
    pk=("date", "stock_id"),
    indexes=(("date",),),
)

# 內部人持股轉讓事前申報（T-384，2026-10-18）：證交所 t187ap12_L＋櫃買 mopsfin_t187ap12_O，每天整份 upsert 累積成歷史。
# 給人看（個股頁行事曆、名單小標），不進因子、不進模型。欄位與 data/sources/insider_transfer.SCHEMA 同名同序；
# 與 2026-09-28 事件研究直寫的同名表欄位相同，所以不用重建。
INSIDER_TRANSFER_SPEC = TableSpec(
    "insider_transfer_notices",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("row_key", "text"),
        ("market", "text"),
        ("company_name", "text"),
        ("role", "text"),
        ("person", "text"),
        ("method", "text"),
        ("planned_shares", "double precision"),
        ("daily_max_shares", "double precision"),
        ("transferee", "text"),
        ("held_own", "double precision"),
        ("held_trust", "double precision"),
        ("after_own", "double precision"),
        ("after_trust", "double precision"),
        ("period_start", "date"),
        ("period_end", "date"),
        ("available_at", "timestamptz"),
    ),
    pk=("date", "stock_id", "row_key"),
    indexes=(("stock_id", "date"),),
)

# 權證（T-385，2026-10-18）：條款只在變了才多一版（valid_from，不是 date，所以不能進 cloud.windows）；
# 日行情每天一份（雲端只留 2 天，摘要算完就用不到）；摘要每檔四碼標的一列（網站讀這張）。
# 欄位與 data/sources/warrant.TERMS_SCHEMA／DAILY_SCHEMA、reporting/warrant_summary.SUMMARY_SCHEMA 同名同序。
WARRANT_TERMS_SPEC = TableSpec(
    "warrant_terms",
    (
        ("valid_from", "date"),
        ("warrant_id", "text"),
        ("market", "text"),
        ("underlying_id", "text"),
        ("underlying_name", "text"),
        ("kind", "text"),
        ("style", "text"),
        ("category", "text"),
        ("expiry_date", "date"),
        ("last_trade_date", "date"),
        ("strike", "double precision"),
        ("ratio", "double precision"),
        ("cap", "double precision"),
        ("floor", "double precision"),
        ("issued_k_units", "double precision"),
        ("available_at", "timestamptz"),
    ),
    pk=("warrant_id", "valid_from"),
)
WARRANT_DAILY_SPEC = TableSpec(
    "warrant_daily",
    (
        ("date", "date"),
        ("warrant_id", "text"),
        ("market", "text"),
        ("underlying_id", "text"),
        ("kind", "text"),
        ("close", "double precision"),
        ("volume_units", "double precision"),
        ("trade_value", "double precision"),
        ("trades", "double precision"),
        ("last_bid", "double precision"),
        ("last_ask", "double precision"),
        ("underlying_close", "double precision"),
        ("available_at", "timestamptz"),
    ),
    pk=("warrant_id", "date"),
    indexes=(("underlying_id", "date"),),
)
WARRANT_SUMMARY_SPEC = TableSpec(
    "warrant_summary_daily",
    (
        ("date", "date"),
        ("underlying_id", "text"),
        ("n_call", "bigint"),
        ("n_put", "bigint"),
        ("call_value", "double precision"),
        ("put_value", "double precision"),
        ("underlying_amount", "double precision"),
        ("value_ratio", "double precision"),
        ("iv_call", "double precision"),
        ("iv_put", "double precision"),
        ("iv_call_days", "bigint"),
        ("iv_put_days", "bigint"),
        ("available_at", "timestamptz"),
    ),
    pk=("date", "underlying_id"),
    indexes=(("underlying_id", "date"),),
)

# 易跌標記（T-391／D-193）：D-192 模型對當天大盤精選 15 檔的機率，機率最高 5 檔 flagged。只給人看；小表全史保留。
PICK_DOWN_RISK_SPEC = TableSpec(
    "pick_down_risk",
    (
        ("date", "date"),
        ("line", "text"),
        ("stock_id", "text"),
        ("model_id", "text"),
        ("p", "double precision"),  # 20 日內碰 −15% 的模型機率（校準偏高，見 D-192 §七）
        ("flagged", "boolean"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "line", "stock_id"),
)

# 名單每檔一句話（T-393／D-195）：每條線每檔一列。source＝model（模型寫、通過檢查）或 rule（規則句：模型缺席或沒過檢查）。
# facts＝給模型看的事實句、checks＝沒過的原因；網站只讀 text 與 source。
PICK_BLURBS_SPEC = TableSpec(
    "pick_blurbs",
    (
        ("date", "date"),
        ("line", "text"),
        ("stock_id", "text"),
        ("text", "text"),
        ("source", "text"),
        ("model_id", "text"),
        ("prompt_version", "text"),
        ("facts", "jsonb"),
        ("checks", "jsonb"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "line", "stock_id"),
)

# 名單四面向評分、分析師觀點與 AI 首選（T-394／D-196）：每條線每檔一列。news／tech／chips／fund 1～5 分（沒過＝NULL）；
# is_top＝這條線當天的首選，source＝model（AI 挑、理由核對過）或 rule（理由沒過→四面向加總最高）。D-196 事後檢驗讀它。
PICK_REVIEWS_SPEC = TableSpec(
    "pick_reviews",
    (
        ("date", "date"),
        ("line", "text"),
        ("stock_id", "text"),
        ("news", "integer"),
        ("tech", "integer"),
        ("chips", "integer"),
        ("fund", "integer"),
        ("total", "integer"),
        ("news_view", "text"),  # 每個面向一句分析師觀點（沒過核對＝NULL）
        ("tech_view", "text"),
        ("chips_view", "text"),
        ("fund_view", "text"),
        ("stance", "text"),  # 偏多｜中性｜偏空（跟總分不一致＝NULL）
        (
            "conclusion",
            "text",
        ),  # 綜合結論（含建議）；名單列顯示這句，沒過核對＝NULL → 網站改顯示一句話（T-393）
        ("is_top", "boolean"),
        ("top_reason", "text"),
        ("source", "text"),
        ("model_id", "text"),
        ("prompt_version", "text"),
        ("facts", "jsonb"),
        ("checks", "jsonb"),
        ("created_at", "timestamptz"),
        # 每面向滿分：NULL＝AI 自評 1～5（D-196），10＝規則算 0～10（D-206）；加在最後，舊表 ADD COLUMN 對齊
        ("scale", "integer"),
        ("items", "jsonb"),  # 20 個小項 [{dim, name, points, text}]（D-206）
    ),
    pk=("date", "line", "stock_id"),
)

# 主力買賣超（T-425／D-206）：雲端不可能留 broker_trades 全歷史，隔日沖排除要的全期累計改成逐日事件表。
# 地端用全歷史寫一列「截至種子日的累計」當底（resolve_date＝種子日），之後每天加當天那一列；
# 某天的累計＝resolve_date ≤ 那天的列加總（數學同 factors/broker.py daytrade_events → cumulate_events）。
BROKER_DAYTRADE_EVENTS_SPEC = TableSpec(
    "broker_daytrade_events",
    (
        ("broker_id", "text"),
        (
            "resolve_date",
            "date",
        ),  # 「今買明賣」記在賣出日（前一交易日的買超到這天才知道有沒有倒出來）
        ("n_buys", "bigint"),  # 買超事件數
        ("n_flip", "bigint"),  # 其中隔日沖（下一交易日同檔賣超）
        ("created_at", "timestamptz"),
    ),
    pk=("broker_id", "resolve_date"),
)

# 每檔每天的主力淨買超（股）：前 15 大券商、排除隔日沖分點（同 chip.broker_* 的 clean_daily）。
MAIN_FORCE_DAILY_SPEC = TableSpec(
    "main_force_daily",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("net_shares", "bigint"),
        ("available_at", "timestamptz"),
    ),
    pk=("date", "stock_id"),
)

_ALL_SPECS: tuple[TableSpec, ...] = (
    *MIRROR_SPECS,
    *FACTOR_SPECS,
    SYNC_STATE_SPEC,
    FUBON_FETCH_LOG_SPEC,
    SCREENER_DAILY_SPEC,
    BROKER_FLOW_DAILY_SPEC,
    *DIRECT_PG_SPECS,
    DAYTRADE_FIRST_PUB_SPEC,
    DAILY_PICKS_SPEC,
    SCORECARD_SPEC,
    STOCK_GROUP_SPEC,
    GROUP_FLOW_SPEC,
    PICK_REASONS_SPEC,
    PICK_BLURBS_SPEC,
    PICK_REVIEWS_SPEC,
    SCORE_REFERENCE_SPEC,
    SIMILAR_LIBRARY_SPEC,
    PICK_SIMILAR_SPEC,
    *FUND_FLOW_SPECS,
    MOPS_ANNOUNCEMENTS_SPEC,
    INSIGHT_PACKS_SPEC,
    MARKET_DAILY_SPEC,
    MARKET_BREADTH_SPEC,
    TAIFEX_DAILY_SPEC,
    CONFERENCE_CALENDAR_SPEC,
    MORNING_BRIEF_SPEC,
    EVENING_BRIEF_SPEC,
    DIVIDEND_CALENDAR_SPEC,
    INSIDER_TRANSFER_SPEC,
    WARRANT_TERMS_SPEC,
    WARRANT_DAILY_SPEC,
    WARRANT_SUMMARY_SPEC,
    PICK_DOWN_RISK_SPEC,
    BROKER_DAYTRADE_EVENTS_SPEC,
    MAIN_FORCE_DAILY_SPEC,
)

MIRROR_TABLES: tuple[str, ...] = tuple(s.name for s in MIRROR_SPECS)
FACTOR_TABLES: tuple[str, ...] = tuple(s.name for s in FACTOR_SPECS)
DIRECT_PG_TABLES: tuple[str, ...] = tuple(s.name for s in DIRECT_PG_SPECS)
FUND_FLOW_TABLES: tuple[str, ...] = tuple(s.name for s in FUND_FLOW_SPECS)
ALL_TABLES: tuple[str, ...] = tuple(s.name for s in _ALL_SPECS)


def spec_of(table: str) -> TableSpec:
    """取某表的 TableSpec；未知表名 raise。"""
    for s in _ALL_SPECS:
        if s.name == table:
            return s
    raise ValueError(f"未知供應層表：{table}")


def rebuild_targets(tables: list[str] | None) -> tuple[TableSpec, ...]:
    """rebuild 要重灌的鏡射表 spec；tables=None ＝全部鏡射表。

    rebuild 只能處理**鏡射表**（自 clean 資料湖 COPY 全量重灌）。因子表
    （factor_values / factor_cross_stats / composite_daily）由 PitView 逐日物化、
    非鏡射自資料湖，故傳入這些表名（或任何非鏡射表名）一律 raise——不靜默略過無效
    targets（黃金規則 5）。因子表的錯誤訊息指引改用 `serving materialize --from/--to`。
    """
    if tables is None:
        return MIRROR_SPECS
    invalid = [t for t in tables if t not in MIRROR_TABLES]
    if invalid:
        factor = [t for t in invalid if t in FACTOR_TABLES]
        direct = [t for t in invalid if t in DIRECT_PG_TABLES]
        hint = (
            f" 其中 {factor} 為因子表，由 PitView 逐日物化（非鏡射自資料湖）——"
            "請改用 `python -m twquant serving materialize --from <起> --to <迄>` 重物化。"
            if factor
            else ""
        )
        if direct:
            hint += (
                f" 其中 {direct} 為直寫 PG 表（T-309，資料湖沒有它們）——"
                "重抓請用 `scripts/backfill_chip_batch1.py`。"
            )
        raise ValueError(
            f"serving rebuild 只支援鏡射表 {list(MIRROR_TABLES)}；"
            f"收到不支援的表名 {invalid}。{hint}"
        )
    return tuple(s for s in MIRROR_SPECS if s.name in tables)


def table_ddls(schema: str) -> dict[str, str]:
    """回傳 {表名: CREATE TABLE IF NOT EXISTS ...}（schema 限定，冪等）。"""
    _check_ident(schema)
    return {s.name: s.ddl(schema) for s in _ALL_SPECS}


def _spec_index_ddls(spec: TableSpec, schema: str) -> list[str]:
    out: list[str] = []
    for cols in spec.indexes:
        iname = f"{spec.name}_{'_'.join(cols)}_idx"
        out.append(
            f'CREATE INDEX IF NOT EXISTS "{iname}" ON "{schema}"."{spec.name}" ({", ".join(cols)})'
        )
    return out


def index_ddls(schema: str) -> list[str]:
    """回傳所有輔助索引 DDL（每 spec 的 indexes，冪等）。"""
    _check_ident(schema)
    return [ddl for s in _ALL_SPECS for ddl in _spec_index_ddls(s, schema)]


def ensure_tables(conn: Any, specs: tuple[TableSpec, ...], schema: str = "serving") -> None:
    """只建指定的表＋其索引（冪等）。conn 為呼叫端 PG 連線。

    與 `ensure_schema` 的差別：`CREATE INDEX IF NOT EXISTS` 在確認索引已存在**之前**就要先拿
    該表的 ShareLock，另一個 session 正在寫的大表（如 ml_scores）會讓它排隊、也讓後來的寫入
    排在它後面。只碰自己要用的表的腳本（T-309 回補）用這個，不去鎖別人的表。
    """
    _check_ident(schema)
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        for spec in specs:
            cur.execute(spec.ddl(schema))
        for spec in specs:
            for ddl in _spec_index_ddls(spec, schema):
                cur.execute(ddl)
    conn.commit()


def table_columns(conn: Any, table: str, schema: str = "serving") -> list[str]:
    """PG 裡該表現有的欄位名（依順序）；表不存在回空列表。"""
    _check_ident(schema)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, table),
        )
        return [str(row[0]) for row in cur.fetchall()]


def add_trailing_columns(conn: Any, spec: TableSpec, schema: str = "serving") -> list[str]:
    """既有表的欄位正好是 spec 的前段 → 補上後面缺的欄（只加不改，冪等）；回補了哪些。

    新欄位一律加在 spec 最後就能這樣升級（例：mops_announcements.content，2026-10-17）。
    欄位不是前段（改名、換順序、刪欄）→ 什麼都不做，交給 assert_columns 指名要 DROP。
    """
    actual = table_columns(conn, spec.name, schema)
    expected = list(spec.column_names)
    if not actual or actual == expected or actual != expected[: len(actual)]:
        return []
    types = dict(spec.columns)
    added = expected[len(actual) :]
    with conn.cursor() as cur:
        for col in added:
            cur.execute(
                f'ALTER TABLE "{schema}"."{spec.name}" ADD COLUMN IF NOT EXISTS "{col}" {types[col]}'
            )
    conn.commit()
    return added


def assert_columns(conn: Any, spec: TableSpec, schema: str = "serving") -> None:
    """既有表的欄位要與 spec 一致，否則 raise 並說怎麼修——`CREATE TABLE IF NOT EXISTS` 不會改舊表，
    欄位對不上時 COPY 會在半路噴出難懂的錯誤（2026-10-17 material_news 第一版表殘留在雲端時抓到）。"""
    actual = table_columns(conn, spec.name, schema)
    expected = list(spec.column_names)
    if actual and actual != expected:
        raise DataError(
            spec.name,
            None,
            f'PG 的欄位 {actual} 與程式的 {expected} 不同；確認表裡沒有別人要的資料後 DROP TABLE "{schema}"."{spec.name}" 再重跑，表會重建',
        )


def ensure_schema(conn: Any, schema: str = "serving") -> None:
    """建立 serving schema 與所有 T-031 資料表＋索引（冪等）。conn 為呼叫端 PG 連線。"""
    ensure_tables(conn, _ALL_SPECS, schema)
