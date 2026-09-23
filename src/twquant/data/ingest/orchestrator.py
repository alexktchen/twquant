"""擷取編排（D-017）：把 SourcePort 的資料透過 IngestSink 落地並記 ingest_log。

只依賴埠（IngestSink）與註冊項（SourceSpec）——與 CLI/具體 Warehouse 解耦，可用
假 sink/假來源做單元測試（同時驗證來源與儲存皆可換）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from twquant.errors import DataError

if TYPE_CHECKING:
    from twquant.config import SystemConfig
    from twquant.data.ingest.ports import IngestSink
    from twquant.data.ingest.registry import SourceSpec


@dataclass(frozen=True)
class IngestOutcome:
    """單一 (來源, 表) 的擷取結果。"""

    source_tag: str
    table: str
    status: str  # "success" | "failed"
    rows: int
    detail: str


def run_ingest(
    specs: Sequence[SourceSpec],
    cfg: SystemConfig,
    sink: IngestSink,
    period: dt.date,
    *,
    data_dir: str | Path,
    tables: list[str] | None = None,
    tolerant: bool,
    on_start: Callable[[str, str], None] | None = None,
    retries: int = 0,
    retry_wait_s: float = 5.0,
    sleep: Callable[[float], None] | None = None,
) -> list[IngestOutcome]:
    """對每個 spec 的每張表：build 來源 → load → sink.write('clean') → 記 log。

    tolerant=True：單表 DataError 只記 failed、不中斷（每日排程/容錯用）；
    tolerant=False：DataError 直接 raise（手動單日擷取要大聲失敗）。

    retries（T-276）：DataError 重試次數。**TWSE 的暫時性失敗與真正的休市長得一模一樣**
    ——兩者都回 `stat != 'OK'`，都被包成同一個 DataError。2026-09-21／22 連兩天回報
    「沒有符合條件的資料」而讓 `margin_daily` 停在 09-18，手動重跑卻立刻進 1,297 列。
    重試把「這一次抓失敗」與「這天本來就沒有」分開；仍然分不開的情形由呼叫端依
    「同日有無其他表成功」判斷（見 `cli._ingest_date`）。
    """
    import time as _time

    _sleep = sleep if sleep is not None else _time.sleep
    from twquant.reporting.ingest_log import record

    outcomes: list[IngestOutcome] = []
    for spec in specs:
        src = spec.build(cfg)
        for table in tables or src.tables:
            if table not in src.tables:
                continue  # 此來源不供應該表（如向 tpex 要 institutional_daily）
            if on_start is not None:
                on_start(spec.source_tag, table)
            last: DataError | None = None
            df = None
            for attempt in range(retries + 1):
                try:
                    df = src.load(table, period)
                    sink.write("clean", table, df, source=spec.source_tag)
                    last = None
                    break
                except DataError as exc:
                    last, df = exc, None
                    if attempt < retries:
                        _sleep(retry_wait_s)
            if last is not None or df is None:
                detail = str(last) if last is not None else "load 未回傳資料"
                if retries:
                    detail = f"{detail}（已重試 {retries} 次）"
                record(data_dir, spec.source_tag, table, period, "failed", detail=detail)
                if not tolerant:
                    raise last if last is not None else DataError(table, period, detail)
                outcomes.append(IngestOutcome(spec.source_tag, table, "failed", 0, detail))
                continue
            record(data_dir, spec.source_tag, table, period, "success", rows=df.height)
            outcomes.append(IngestOutcome(spec.source_tag, table, "success", df.height, ""))
    return outcomes
