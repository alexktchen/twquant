"""匯入即註冊所有內建來源（D-017）——比照 factors/builtins.py 的副作用匯入慣用法。

任何需要「來源都可用」的入口（每日 ingest、ingest-dispersion）只要
`import twquant.data.ingest.builtins` 一次即完成註冊。build 函數延遲 import 具體來源，
避免匯入註冊表時就拉起 requests/瀏覽器 headers 等重相依。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from twquant.data.ingest.ports import FetchCleanSource
from twquant.data.ingest.registry import SourceSpec, register

if TYPE_CHECKING:
    from twquant.config import SystemConfig
    from twquant.data.ingest.ports import SourcePort


def _build_twse(cfg: SystemConfig) -> SourcePort:
    from twquant.data.clean import CLEANERS
    from twquant.data.http import HttpClient
    from twquant.data.sources.twse_rwd import TwseRwdSource

    src = TwseRwdSource(client=HttpClient(source="twse_rwd", rate=cfg.source_rates["twse_rwd"]))
    return FetchCleanSource("twse", src, CLEANERS)


def _build_tpex(cfg: SystemConfig) -> SourcePort:
    from twquant.data.clean import TPEX_CLEANERS
    from twquant.data.sources.tpex import TpexSource, browser_client

    src = TpexSource(client=browser_client(cfg.source_rates["tpex"]))
    return FetchCleanSource("tpex", src, TPEX_CLEANERS)


def _build_tdcc(cfg: SystemConfig) -> SourcePort:
    from twquant.data.http import HttpClient
    from twquant.data.sources.tdcc import TdccDispersionSource

    return TdccDispersionSource(client=HttpClient(source="tdcc", rate=cfg.source_rates["tdcc"]))


# 每日免費官方來源（D-015）
register(SourceSpec("twse", _build_twse, cadence="daily"))
register(SourceSpec("tpex", _build_tpex, cadence="daily"))
# 週頻全市場快照（另循 ingest-dispersion 指令，非每日迴圈）
register(SourceSpec("tdcc", _build_tdcc, cadence="weekly"))
