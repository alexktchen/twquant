"""T-010 / D-017：來源註冊表、內建註冊、擷取編排器。

編排器測試以假 SourcePort + 假 IngestSink 走完整流程——同時證明「來源」與「儲存」皆可
獨立抽換（可插拔設計的核心 AC）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import polars as pl
import pytest

from twquant.config import SystemConfig, load_config
from twquant.data.ingest import registry
from twquant.data.ingest.orchestrator import run_ingest
from twquant.data.ingest.ports import IngestSink, SourcePort
from twquant.data.ingest.registry import SourceSpec, get, list_specs, register
from twquant.data.warehouse import Warehouse
from twquant.errors import TwquantError

_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "system.yaml"


@pytest.fixture
def isolated() -> Iterator[None]:
    """存檔→清空→還原註冊表，讓變異測試不污染內建註冊。"""
    saved = dict(registry._REGISTRY)
    registry._clear()
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def test_builtins_registers_daily_and_weekly() -> None:
    import twquant.data.ingest.builtins  # noqa: F401  # 匯入觸發內建來源註冊

    assert {"twse", "tpex", "tdcc"} <= {s.source_tag for s in list_specs()}
    assert {"twse", "tpex"} <= {s.source_tag for s in list_specs(cadence="daily")}
    assert "tdcc" in {s.source_tag for s in list_specs(cadence="weekly")}
    assert get("tdcc").cadence == "weekly"


def _dummy(cfg: SystemConfig) -> SourcePort:
    raise NotImplementedError


def test_register_get_and_duplicate(isolated: None) -> None:
    register(SourceSpec("x", _dummy, "daily"))
    assert get("x").source_tag == "x"
    with pytest.raises(TwquantError):  # 重複註冊
        register(SourceSpec("x", _dummy, "daily"))
    with pytest.raises(TwquantError):  # 未註冊
        get("nope")


def test_warehouse_satisfies_ingest_sink(tmp_path: Path) -> None:
    # 儲存埠零改動即結構符合：Warehouse 本身就是 IngestSink（runtime_checkable）。
    assert isinstance(Warehouse(tmp_path), IngestSink)


def test_orchestrator_source_and_sink_are_swappable(tmp_path: Path) -> None:
    """假來源（load 回固定 frame）+ 假 sink（記寫入）→ 證明來源與儲存皆可換。"""
    writes: list[tuple[str, str, str | None, int]] = []

    class FakeSink:
        def write(
            self, layer: str, table: str, df: pl.DataFrame, source: str | None = None
        ) -> None:
            writes.append((layer, table, source, df.height))

        def partition_exists(
            self, layer: str, table: str, date: dt.date, source: str | None = None
        ) -> bool:
            return False

        def source_dates(self, table: str, source: str, layer: str = "clean") -> list[dt.date]:
            return []

        def latest_date(self, table: str, layer: str = "clean") -> dt.date | None:
            return None

    class FakeSource:
        name = "fake"

        @property
        def tables(self) -> list[str]:
            return ["prices_daily"]

        def load(self, table: str, period: dt.date) -> pl.DataFrame:
            return pl.DataFrame({"a": [1, 2, 3]})

    cfg = load_config(_CONFIG, SystemConfig)
    spec = SourceSpec("fake", lambda c: FakeSource(), "weekly")
    outcomes = run_ingest(
        [spec], cfg, FakeSink(), dt.date(2026, 7, 3), data_dir=tmp_path, tolerant=True
    )
    assert writes == [("clean", "prices_daily", "fake", 3)]
    assert outcomes[0].status == "success"
    assert outcomes[0].rows == 3
