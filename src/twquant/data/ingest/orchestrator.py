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
) -> list[IngestOutcome]:
    """對每個 spec 的每張表：build 來源 → load → sink.write('clean') → 記 log。

    tolerant=True：單表 DataError 只記 failed、不中斷（每日排程/容錯用）；
    tolerant=False：DataError 直接 raise（手動單日擷取要大聲失敗）。
    """
    from twquant.reporting.ingest_log import record

    outcomes: list[IngestOutcome] = []
    for spec in specs:
        src = spec.build(cfg)
        for table in tables or src.tables:
            if table not in src.tables:
                continue  # 此來源不供應該表（如向 tpex 要 institutional_daily）
            if on_start is not None:
                on_start(spec.source_tag, table)
            try:
                df = src.load(table, period)
                sink.write("clean", table, df, source=spec.source_tag)
            except DataError as exc:
                record(data_dir, spec.source_tag, table, period, "failed", detail=str(exc))
                if not tolerant:
                    raise
                outcomes.append(IngestOutcome(spec.source_tag, table, "failed", 0, str(exc)))
                continue
            record(data_dir, spec.source_tag, table, period, "success", rows=df.height)
            outcomes.append(IngestOutcome(spec.source_tag, table, "success", df.height, ""))
    return outcomes
