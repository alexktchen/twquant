"""讀 TOML 設定並驗證。所有參數都從這裡進來，程式裡不寫死數字。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

MODES = ("sim", "live")


class ConfigError(ValueError):
    """設定檔有問題。"""


@dataclass(frozen=True)
class ContractConfig:
    """商品規格與成本。"""

    symbol: str
    point_value: float  # 一點幾元
    commission_twd: float  # 每口單邊手續費
    tax_rate: float  # 期交稅率（對契約價值）


@dataclass(frozen=True)
class RiskConfig:
    """風控參數。daily_loss_limit_twd 在模擬可以不設，真單一定要設。"""

    min_position: int
    max_position: int
    max_order_qty: int
    max_entries_per_day: int
    entry_start: time
    entry_end: time
    max_quote_age_s: float
    force_flat_time: time  # 這個時間之後還有部位就由系統強制平倉
    order_timeout_s: float  # 委託送出後超過這麼久沒成交就刪單
    daily_loss_limit_twd: float | None


@dataclass(frozen=True)
class SessionConfig:
    """一般交易時段。"""

    start: time
    end: time


@dataclass(frozen=True)
class SimConfig:
    """模擬券商的成交假設。"""

    latency_s: float  # 送出後過這麼久的第一筆行情才成交
    slippage_points: float  # 市價單每邊滑價幾點


@dataclass(frozen=True)
class Config:
    """整份設定。"""

    mode: str
    contract: ContractConfig
    risk: RiskConfig
    session: SessionConfig
    sim: SimConfig


def _need(table: dict[str, Any], key: str, where: str) -> Any:
    if key not in table:
        raise ConfigError(f"設定缺 [{where}] {key}")
    return table[key]


def _clock(text: Any, where: str) -> time:
    if not isinstance(text, str):
        raise ConfigError(f'{where} 要是 "HH:MM:SS" 字串，拿到 {text!r}')
    try:
        return time.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(f"{where} 不是合法時間：{text!r}") from exc


def parse_config(raw: dict[str, Any]) -> Config:
    """把 TOML 讀出來的 dict 轉成 Config，順便驗證。"""
    mode = _need(raw, "mode", "根")
    if mode not in MODES:
        raise ConfigError(f"mode 要是 {MODES} 其中之一，拿到 {mode!r}")
    c = _need(raw, "contract", "根")
    r = _need(raw, "risk", "根")
    se = _need(raw, "session", "根")
    si = _need(raw, "sim", "根")
    contract = ContractConfig(
        symbol=str(_need(c, "symbol", "contract")),
        point_value=float(_need(c, "point_value", "contract")),
        commission_twd=float(_need(c, "commission_twd", "contract")),
        tax_rate=float(_need(c, "tax_rate", "contract")),
    )
    limit = r.get("daily_loss_limit_twd")
    risk = RiskConfig(
        min_position=int(_need(r, "min_position", "risk")),
        max_position=int(_need(r, "max_position", "risk")),
        max_order_qty=int(_need(r, "max_order_qty", "risk")),
        max_entries_per_day=int(_need(r, "max_entries_per_day", "risk")),
        entry_start=_clock(_need(r, "entry_start", "risk"), "risk.entry_start"),
        entry_end=_clock(_need(r, "entry_end", "risk"), "risk.entry_end"),
        max_quote_age_s=float(_need(r, "max_quote_age_s", "risk")),
        force_flat_time=_clock(_need(r, "force_flat_time", "risk"), "risk.force_flat_time"),
        order_timeout_s=float(_need(r, "order_timeout_s", "risk")),
        daily_loss_limit_twd=None if limit is None else float(limit),
    )
    session = SessionConfig(
        start=_clock(_need(se, "start", "session"), "session.start"),
        end=_clock(_need(se, "end", "session"), "session.end"),
    )
    sim = SimConfig(
        latency_s=float(_need(si, "latency_s", "sim")),
        slippage_points=float(_need(si, "slippage_points", "sim")),
    )
    if session.start >= session.end:
        raise ConfigError(f"交易時段起點要早於終點：{session.start} ~ {session.end}")
    if sim.latency_s < 0 or sim.slippage_points < 0:
        raise ConfigError(f"sim 的延遲與滑價不能是負的：{sim}")
    if risk.order_timeout_s <= 0:
        raise ConfigError(f"risk.order_timeout_s 要大於 0：{risk.order_timeout_s}")
    if not risk.entry_end <= risk.force_flat_time <= session.end:
        raise ConfigError(
            f"強制平倉時間要在進場終點與收盤之間：{risk.entry_end} ≤ {risk.force_flat_time} ≤ {session.end}"
        )
    if contract.point_value <= 0 or contract.commission_twd < 0 or contract.tax_rate < 0:
        raise ConfigError(f"contract 的數值不合理：{contract}")
    if not risk.min_position <= 0 <= risk.max_position:
        raise ConfigError(f"部位上下限要包含 0：min={risk.min_position} max={risk.max_position}")
    if risk.max_order_qty <= 0 or risk.max_entries_per_day <= 0 or risk.max_quote_age_s <= 0:
        raise ConfigError(f"risk 的上限要大於 0：{risk}")
    if risk.entry_start >= risk.entry_end:
        raise ConfigError(f"進場時段起點要早於終點：{risk.entry_start} ~ {risk.entry_end}")
    if risk.daily_loss_limit_twd is not None and risk.daily_loss_limit_twd <= 0:
        raise ConfigError(f"每日虧損上限要是正數（元）：{risk.daily_loss_limit_twd}")
    if mode == "live" and risk.daily_loss_limit_twd is None:
        raise ConfigError('真單模式（mode = "live"）一定要設 [risk] daily_loss_limit_twd')
    return Config(mode=mode, contract=contract, risk=risk, session=session, sim=sim)


def load_config(path: Path) -> Config:
    """讀一份 TOML 設定檔。"""
    with path.open("rb") as fh:
        return parse_config(tomllib.load(fh))
