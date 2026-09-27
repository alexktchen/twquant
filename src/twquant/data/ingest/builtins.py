"""匯入即註冊所有內建來源（D-017）——比照 factors/builtins.py 的副作用匯入慣用法。

任何需要「來源都可用」的入口（每日 ingest、ingest-dispersion）只要
`import twquant.data.ingest.builtins` 一次即完成註冊。build 函數延遲 import 具體來源，
避免匯入註冊表時就拉起 requests/瀏覽器 headers 等重相依。

同一交易所的所有來源共用一個限流器（`http.shared_limiter`）：每日擷取對證交所會建好幾個
client（行情＋估值、當沖／借券、公告，T-310 的補抓還逐日再建），各自限流等於沒有限流。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from twquant.data.ingest.ports import FetchCleanSource
from twquant.data.ingest.registry import SourceSpec, register

if TYPE_CHECKING:
    from collections.abc import Callable

    from twquant.config import SystemConfig
    from twquant.data.http import HttpClient
    from twquant.data.ingest.ports import SourcePort

# T-310 直寫 PG 的來源標籤（每日 pipeline「籌碼」步驟依市場取用）
CHIP_SOURCES = {"twse": "twse_chip", "tpex": "tpex_chip"}
ALERT_SOURCES = {"twse": "twse_alerts", "tpex": "tpex_alerts"}


def _client(cfg: SystemConfig, market: str) -> HttpClient:
    """該交易所的 HttpClient（共用限流器）；櫃買要帶瀏覽器 headers。"""
    from twquant.data.http import HttpClient, shared_limiter
    from twquant.data.sources.tpex import browser_client

    if market == "twse":
        rate = cfg.source_rates["twse_rwd"]
        return HttpClient(source="twse_rwd", rate=rate, limiter=shared_limiter("twse_rwd", rate))
    rate = cfg.source_rates["tpex"]
    return browser_client(rate, limiter=shared_limiter("tpex", rate))


def _build_twse(cfg: SystemConfig) -> SourcePort:
    from twquant.data.clean import CLEANERS
    from twquant.data.sources.twse_rwd import TwseRwdSource

    return FetchCleanSource("twse", TwseRwdSource(client=_client(cfg, "twse")), CLEANERS)


def _build_tpex(cfg: SystemConfig) -> SourcePort:
    from twquant.data.clean import TPEX_CLEANERS
    from twquant.data.sources.tpex import TpexSource

    return FetchCleanSource("tpex", TpexSource(client=_client(cfg, "tpex")), TPEX_CLEANERS)


def _build_tdcc(cfg: SystemConfig) -> SourcePort:
    from twquant.data.http import HttpClient
    from twquant.data.sources.tdcc import TdccDispersionSource

    return TdccDispersionSource(client=HttpClient(source="tdcc", rate=cfg.source_rates["tdcc"]))


def _chip_builder(market: str) -> Callable[[SystemConfig], SourcePort]:
    def build(cfg: SystemConfig) -> SourcePort:
        import re

        from twquant.data.sources.official_chip import OfficialChipSource

        universe = re.compile(cfg.chip_daily.universe_pattern)
        return OfficialChipSource(market, _client(cfg, market), universe)

    return build


def _alert_builder(market: str) -> Callable[[SystemConfig], SourcePort]:
    def build(cfg: SystemConfig) -> SourcePort:
        from twquant.data.sources.alerts import AlertSource

        c = cfg.chip_daily
        return AlertSource(
            market, _client(cfg, market), c.alerts_lookback_days, c.disposition_lookahead_days
        )

    return build


# 每日免費官方來源（D-015）；T-313 起兩者也供應 stock_valuation（BWIBBU_d／peQryDate）
register(SourceSpec("twse", _build_twse, cadence="daily"))
register(SourceSpec("tpex", _build_tpex, cadence="daily"))
# T-310：直寫 PG（資料湖沒有這些表）。`_ingest_date` 只在明確指定這些表時抓；每日 pipeline 由
# 「籌碼」步驟抓前幾個交易日＋缺日（當天的還沒公布完）。公告休市日也查得到東西 → 不證明當天有交易。
for _market, _tag in CHIP_SOURCES.items():
    register(SourceSpec(_tag, _chip_builder(_market), cadence="daily", store="pg"))
for _market, _tag in ALERT_SOURCES.items():
    register(
        SourceSpec(_tag, _alert_builder(_market), cadence="daily", store="pg", proves_trading=False)
    )
# 週頻全市場快照（另循 ingest-dispersion 指令，非每日迴圈）
register(SourceSpec("tdcc", _build_tdcc, cadence="weekly"))
