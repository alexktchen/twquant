"""讀 TOML 設定並驗證。所有參數都從這裡進來，程式裡不寫死數字。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

MODES = ("sim", "live")
# 真單模式一定要設的風控上限（期貨商對程式交易的要求，SPEC A-010）
LIVE_REQUIRED = (
    "daily_loss_limit_twd",
    "max_orders_per_minute",
    "max_consecutive_broker_rejects",
    "position_stop_loss_twd",
)


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
    """風控參數。四個可以是 None 的上限在模擬可以不設，真單一定要設（LIVE_REQUIRED）。"""

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
    max_orders_per_minute: int | None = None  # 一分鐘內最多送幾筆委託（含平倉）；到了就暫停送單
    max_consecutive_broker_rejects: int | None = None  # 連續被券商拒絕幾筆就暫停送單
    position_stop_loss_twd: float | None = None  # 單一部位未實現虧損到這個金額就由系統平倉


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
class MomentumConfig:
    """動能順勢轉接的參數（策略本身的參數用原版預設值，不在這裡）。"""

    eval_interval_s: float  # 每隔多久評估一次訊號
    market_refresh_s: float  # 大盤 context 多久重建一次
    expected_slippage_points: float  # 送單前驗證用的預估成交價＝現價＋這個點數
    contracts: int  # 每次進場幾口


@dataclass(frozen=True)
class ControlConfig:
    """遠端緊急停止（A-011）。"""

    poll_s: float  # 多久讀一次停止指令（用時鐘，不靠行情）
    flatten_timeout_s: float  # 停止後這麼久還沒確認平掉，就回報「要人處理」
    stale_s: float  # 指令檔超過這麼久沒更新，就當成遠端停止這條路斷了


@dataclass(frozen=True)
class ExportConfig:
    """給網站的狀態輸出。"""

    max_events: int  # 今日大事最多留幾則（留最新的）


@dataclass(frozen=True)
class Config:
    """整份設定。"""

    mode: str
    contract: ContractConfig
    risk: RiskConfig
    session: SessionConfig
    sim: SimConfig
    momentum: MomentumConfig | None = None
    export: ExportConfig | None = None
    control: ControlConfig | None = None


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
    rate = r.get("max_orders_per_minute")
    rejects = r.get("max_consecutive_broker_rejects")
    stop = r.get("position_stop_loss_twd")
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
        max_orders_per_minute=None if rate is None else int(rate),
        max_consecutive_broker_rejects=None if rejects is None else int(rejects),
        position_stop_loss_twd=None if stop is None else float(stop),
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
    for key in LIVE_REQUIRED[1:]:
        value = getattr(risk, key)
        if value is not None and value <= 0:
            raise ConfigError(f"[risk] {key} 要大於 0：{value}")
    for key in LIVE_REQUIRED:
        if mode == "live" and getattr(risk, key) is None:
            raise ConfigError(f'真單模式（mode = "live"）一定要設 [risk] {key}')
    momentum = None
    if "momentum" in raw:
        m = raw["momentum"]
        momentum = MomentumConfig(
            eval_interval_s=float(_need(m, "eval_interval_s", "momentum")),
            market_refresh_s=float(_need(m, "market_refresh_s", "momentum")),
            expected_slippage_points=float(_need(m, "expected_slippage_points", "momentum")),
            contracts=int(_need(m, "contracts", "momentum")),
        )
        if momentum.eval_interval_s <= 0 or momentum.market_refresh_s <= 0:
            raise ConfigError(f"momentum 的間隔要大於 0：{momentum}")
        if momentum.expected_slippage_points < 0:
            raise ConfigError(f"momentum.expected_slippage_points 不能是負的：{momentum}")
        if not 0 < momentum.contracts <= risk.max_order_qty:
            raise ConfigError(
                f"momentum.contracts={momentum.contracts} 要在 1～risk.max_order_qty={risk.max_order_qty}"
            )
    export = None
    if "export" in raw:
        export = ExportConfig(max_events=int(_need(raw["export"], "max_events", "export")))
        if export.max_events <= 0:
            raise ConfigError(f"[export] max_events 要大於 0：{export.max_events}")
    control = None
    if "control" in raw:
        c2 = raw["control"]
        control = ControlConfig(
            poll_s=float(_need(c2, "poll_s", "control")),
            flatten_timeout_s=float(_need(c2, "flatten_timeout_s", "control")),
            stale_s=float(_need(c2, "stale_s", "control")),
        )
        if control.poll_s <= 0 or control.flatten_timeout_s <= 0:
            raise ConfigError(f"[control] poll_s 與 flatten_timeout_s 要大於 0：{control}")
        if control.stale_s < 2 * control.poll_s:
            raise ConfigError(
                f"[control] stale_s 至少要是 poll_s 的兩倍（不然正常的延遲也會被當成斷線）：{control}"
            )
    if mode == "live" and control is None:
        raise ConfigError('真單模式（mode = "live"）一定要設 [control]（遠端緊急停止，D-224）')
    return Config(
        mode=mode,
        contract=contract,
        risk=risk,
        session=session,
        sim=sim,
        momentum=momentum,
        export=export,
        control=control,
    )


def load_config(path: Path) -> Config:
    """讀一份 TOML 設定檔。"""
    with path.open("rb") as fh:
        return parse_config(tomllib.load(fh))
