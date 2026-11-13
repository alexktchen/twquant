"""A-005：把狀態寫給網站（SPEC.md §4）。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from autotrade.broker.sim import SimBroker
from autotrade.cli import main
from autotrade.config import Config, ConfigError, ExportConfig, load_config, parse_config
from autotrade.engine import Engine
from autotrade.export import (
    STATE_HALTED,
    STATE_LINK_DOWN,
    STATE_OK,
    STATE_SUSPENDED,
    STATUS_DDL,
    STATUS_UPSERT,
    dumps,
    env_value,
    snapshot,
    status_row,
)
from autotrade.model import TAIPEI, Side, Tick
from autotrade.risk import HALT_BROKER_REJECT, HALT_KILL, HALT_MISMATCH
from test_a002_engine import ROWS, write_csv
from test_a010_broker_requirements import FlakyBroker, Scripted

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)
MANY = 10_000


def cfg(max_events: int | None = 30, **risk: object) -> Config:
    base = load_config(CONFIG_PATH)
    defaults: dict[str, object] = {
        "max_entries_per_day": MANY,
        "max_orders_per_minute": None,
        "max_consecutive_broker_rejects": None,
        "position_stop_loss_twd": None,
    }
    export = None if max_events is None else ExportConfig(max_events=max_events)
    return replace(
        base,
        risk=replace(base.risk, **{**defaults, **risk}),  # type: ignore[arg-type]
        export=export,
    )


def tick(seconds: float, price: float = 27000.0) -> Tick:
    return Tick(ts=T0 + timedelta(seconds=seconds), price=price, qty=1)


def engine_with(plan: dict[int, Side], config: Config | None = None) -> tuple[Engine, FlakyBroker]:
    config = config or cfg()
    broker = FlakyBroker(SimBroker(config.sim))
    return Engine(config, Scripted(plan), broker), broker


def snap(engine: Engine, seconds: float, status: str = "live") -> dict[str, object]:
    return snapshot(
        engine,
        T0 + timedelta(seconds=seconds),
        source="replay",
        status=status,
        strategy="scripted",
    )


def round_trip() -> Engine:
    """買 1 口（27001 成交）→ 漲到 27050 → 賣掉（27049 成交）。"""
    engine, _ = engine_with({0: Side.BUY, 3: Side.SELL})
    for i, price in enumerate([27000.0, 27000.0, 27050.0, 27050.0, 27050.0, 27050.0]):
        engine.on_tick(tick(i, price))
    return engine


def test_ac1_snapshot_has_position_pnl_and_limits() -> None:
    engine, _ = engine_with({0: Side.BUY}, cfg(daily_loss_limit_twd=3000.0))
    for i, price in enumerate([27000.0, 27000.0, 27040.0]):
        engine.on_tick(tick(i, price))
    s = snap(engine, 2)
    assert s["schema"] == 1 and s["day"] == "2026-11-13"
    assert (s["mode"], s["source"], s["status"]) == ("sim", "replay", "live")
    assert (s["symbol"], s["strategy"]) == ("TMF", "scripted")
    assert s["updated_at"] == "2026-11-13T09:00:02+08:00"
    assert s["last_ts"] == "2026-11-13T09:00:02+08:00"
    assert (s["position"], s["avg_price"], s["last_price"]) == (1, 27001.0, 27040.0)
    point = engine.cfg.contract.point_value
    assert s["open_pnl_twd"] == 39.0 * point
    assert s["realized_twd"] == -engine.ledger.costs_twd
    assert s["day_pnl_twd"] == 39.0 * point - engine.ledger.costs_twd
    assert s["costs_twd"] == engine.ledger.costs_twd
    assert s["entries"] == 1
    assert s["limits"] == {
        "max_position": engine.cfg.risk.max_position,
        "max_entries_per_day": MANY,
        "daily_loss_limit_twd": 3000.0,
        "position_stop_loss_twd": None,
    }
    assert s["contract"] == {
        "point_value": point,
        "commission_twd": engine.cfg.contract.commission_twd,
        "tax_rate": engine.cfg.contract.tax_rate,
    }
    assert s["sim"] == {
        "latency_s": engine.cfg.sim.latency_s,
        "slippage_points": engine.cfg.sim.slippage_points,
    }


def test_ac1_live_mode_carries_no_sim_assumptions() -> None:
    engine, _ = engine_with({}, replace(cfg(), mode="live"))
    engine.on_tick(tick(0))
    s = snap(engine, 0)
    assert s["mode"] == "live" and s["sim"] is None


def test_ac1_bad_status_or_source_raises() -> None:
    engine = round_trip()
    with pytest.raises(ValueError, match="status"):
        snap(engine, 6, status="done")
    with pytest.raises(ValueError, match="source"):
        snapshot(engine, T0, source="guess", status="live", strategy="x")
    with pytest.raises(ValueError, match="時區"):
        snapshot(engine, datetime(2026, 11, 13, 9, 0), source="replay", status="live", strategy="x")


def test_ac2_state_ok_halted_suspended_link_down() -> None:
    engine = round_trip()
    assert (snap(engine, 6)["state"], snap(engine, 6)["halt"]) == (STATE_OK, None)
    engine.kill(T0 + timedelta(seconds=6))
    assert (snap(engine, 6)["state"], snap(engine, 6)["halt"]) == (STATE_HALTED, HALT_KILL)

    engine, broker = engine_with(
        {i: Side.BUY for i in range(0, 12, 2)}, cfg(max_consecutive_broker_rejects=2)
    )
    broker.reject_all = True
    for i in range(8):
        engine.on_tick(tick(i))
    s = snap(engine, 8)
    assert (s["state"], s["halt"]) == (STATE_SUSPENDED, HALT_BROKER_REJECT)
    engine.on_disconnect(T0 + timedelta(seconds=9))
    assert snap(engine, 9)["state"] == STATE_LINK_DOWN  # 同時發生時連線中斷優先


def test_ac3_fills_are_todays_and_sum_to_realized() -> None:
    engine = round_trip()
    s = snap(engine, 6, status="closed")
    fills = s["fills"]
    assert isinstance(fills, list) and len(fills) == 2
    assert [(f["side"], f["qty"], f["price"]) for f in fills] == [
        ("BUY", 1, 27001.0),
        ("SELL", 1, 27049.0),
    ]
    assert fills[0]["ts"].endswith("+08:00")
    assert sum(f["pnl_twd"] for f in fills) == pytest.approx(s["realized_twd"])
    assert s["realized_twd"] == pytest.approx(
        48.0 * engine.cfg.contract.point_value - engine.ledger.costs_twd
    )
    assert s["position"] == 0 and s["open_pnl_twd"] == 0.0 and s["corrected"] is False

    # 隔天：昨天的成交不列，昨天的損益不算進今天
    engine.on_tick(Tick(ts=T0 + timedelta(days=3), price=27100.0, qty=1))
    nxt = snapshot(
        engine, T0 + timedelta(days=3), source="replay", status="live", strategy="scripted"
    )
    assert nxt["day"] == "2026-11-16" and nxt["fills"] == []
    assert nxt["realized_twd"] == 0.0 and nxt["costs_twd"] == 0.0


def test_ac3_corrected_day_is_flagged() -> None:
    engine, broker = engine_with({0: Side.BUY})
    engine.on_tick(tick(0))
    broker.drift = 1
    engine.on_tick(tick(1))
    assert engine.risk.halt == HALT_MISMATCH
    s = snap(engine, 1)
    assert s["corrected"] is True and s["state"] == STATE_HALTED


def test_ac4_events_keep_only_notable_and_latest() -> None:
    engine = round_trip()
    assert snap(engine, 6)["events"] == []  # 委託與成交不算大事
    for i in range(5):
        now = T0 + timedelta(seconds=10 + i)
        engine.kill(now)
        engine.release_kill(now)
    events = snap(engine, 20)["events"]
    assert isinstance(events, list) and len(events) == 10
    assert {e["kind"] for e in events} == {"halt", "release"}
    assert [e["ts"] for e in events] == sorted(e["ts"] for e in events)
    assert set(events[0]) == {"ts", "kind", "text"}

    limited = replace(engine.cfg, export=ExportConfig(max_events=3))
    engine.cfg = limited
    last3 = snap(engine, 20)["events"]
    assert last3 == events[-3:]


def test_ac5_no_quote_yet_raises() -> None:
    engine, _ = engine_with({})
    with pytest.raises(ValueError, match="還沒收到行情"):
        snap(engine, 0)


def test_ac6_deterministic_and_json_ready() -> None:
    a, b = dumps(snap(round_trip(), 6)), dumps(snap(round_trip(), 6))
    assert a == b
    assert json.loads(a)["symbol"] == "TMF"
    engine = round_trip()
    engine.kill(T0 + timedelta(seconds=7))
    assert "緊急停止" in dumps(snap(engine, 7))  # 中文不轉義


def test_ac7_export_config_required_and_validated() -> None:
    engine, _ = engine_with({}, cfg(max_events=None))
    engine.on_tick(tick(0))
    with pytest.raises(ConfigError, match=r"\[export\]"):
        snap(engine, 0)
    raw = _raw_config()
    raw["export"] = {"max_events": 0}
    with pytest.raises(ConfigError, match="max_events"):
        parse_config(raw)
    raw["export"] = {"max_events": 5}
    assert parse_config(raw).export == ExportConfig(max_events=5)
    assert load_config(CONFIG_PATH).export is not None


def _raw_config() -> dict[str, object]:
    import tomllib

    with CONFIG_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_ac8_cli_writes_closed_status_atomically(tmp_path: Path) -> None:
    ticks = write_csv(tmp_path / "Daily_2026_11_13.csv", ROWS, encoding="utf-8-sig")
    out = tmp_path / "status.json"
    args = ["replay", "--config", str(CONFIG_PATH), "--ticks", str(ticks), "--day", "2026-11-13"]
    assert main([*args, "--strategy", "none", "--status-out", str(out)]) == 0
    s = json.loads(out.read_text(encoding="utf-8"))
    assert (s["day"], s["status"], s["source"], s["strategy"]) == (
        "2026-11-13",
        "closed",
        "replay",
        "none",
    )
    assert s["position"] == 0 and s["fills"] == []
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
    with pytest.raises(FileNotFoundError):
        main([*args, "--strategy", "none", "--status-out", str(tmp_path / "nope" / "s.json")])


# twquant src/twquant/data/serving/schema.py 的 AUTOTRADE_STATUS_SPEC（tests/test_autotrade_status_store.py 守著同一句）
TWQUANT_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."autotrade_status" '
    "(day date, mode text, source text, status text, payload jsonb, updated_at timestamptz, "
    "PRIMARY KEY (day, mode))"
)


def test_ac9_ddl_matches_twquant_and_upsert_overwrites() -> None:
    assert STATUS_DDL == TWQUANT_DDL
    assert "ON CONFLICT (day, mode) DO UPDATE" in STATUS_UPSERT
    assert STATUS_UPSERT.count("%s") == 6


def test_ac9_status_row_validates_and_round_trips() -> None:
    text = dumps(snap(round_trip(), 6, status="closed"))
    day, mode, source, status, payload, updated_at = status_row(text)
    assert (day, mode, source, status) == ("2026-11-13", "sim", "replay", "closed")
    assert payload == text and updated_at == "2026-11-13T09:00:06+08:00"

    good = json.loads(text)
    for broken in (
        {k: v for k, v in good.items() if k != "fills"},
        {**good, "mode": "paper"},
        {**good, "status": "done"},
        {**good, "schema": 99},
        {**good, "day": "2026/11/13"},
    ):
        with pytest.raises(ValueError):
            status_row(json.dumps(broken))
    with pytest.raises(ValueError):
        status_row("[]")


def test_ac9_env_value_reads_only_the_named_variable() -> None:
    text = (
        "# 註解\nA=1\nexport TWQUANT_PG_DSN = \"postgresql://u:p@h:5433/db?x=1\"\nB='two'\nEMPTY=\n"
    )
    assert env_value(text, "TWQUANT_PG_DSN") == "postgresql://u:p@h:5433/db?x=1"
    assert env_value(text, "B") == "two"
    assert env_value(text, "A") == "1"
    assert env_value(text, "EMPTY") == ""
    assert env_value(text, "MISSING") is None
    assert env_value(text, "TWQUANT_PG") is None  # 名稱要完全一樣，不是比開頭


def test_ac9_status_files_from_before_the_remote_stop_still_load() -> None:
    # 加遠端緊急停止（A-011）之前產生的狀態檔沒有 control 那一塊：照實補成「沒有在聽」，其他一字不動
    snap = snapshot_for_old_file()
    old = {k: v for k, v in snap.items() if k != "control"}
    day, mode, _, _, payload, _ = status_row(json.dumps(old))
    assert (day, mode) == ("2026-11-13", "sim")
    loaded = json.loads(payload)
    assert loaded["control"] == {
        "listening": False,
        "ok": None,
        "checked_at": None,
        "poll_s": None,
        "last_command_id": 0,
    }
    assert {k: v for k, v in loaded.items() if k != "control"} == old


def snapshot_for_old_file() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(dumps(snap(round_trip(), 6, status="closed")))
    return loaded
