"""讀 TOML 設定並驗證。所有參數都從這裡進來，程式裡不寫死數字。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import time
from math import isfinite
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
NOTIFY_CHANNELS = ("telegram",)
# 引擎會寫出的事件種類；每一種 notify 都有白話範本（SPEC A-006 AC3，有測試守著兩邊一致）
NOTIFY_KINDS = (
    "fill",
    "halt",
    "release",
    "stop",
    "correction",
    "link",
    "broker_reject",
    "foreign_order",
    "reject",
    "order",
    "cancel",
    "cancelled",
)
# 真單模式一定要通知的種類：成交，與所有會標「注意／要處理」的事（SPEC A-006 AC8）
NOTIFY_LIVE_REQUIRED = (
    "fill",
    "halt",
    "stop",
    "correction",
    "link",
    "broker_reject",
    "foreign_order",
)


# 通知的三級（notify.Level 的名字，小寫）
NOTIFY_LEVELS = ("info", "warn", "act")


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
class ExportConfig:
    """給網站的狀態輸出。"""

    max_events: int  # 今日大事最多留幾則（留最新的）


@dataclass(frozen=True)
class NotifyConfig:
    """手機通知。金鑰與收件對象不在這裡，只從環境變數讀。"""

    channel: str  # 送到哪（NOTIFY_CHANNELS）
    name: str  # 通知裡怎麼稱呼這個商品
    kinds: tuple[str, ...]  # 要通知哪些種類的事（NOTIFY_KINDS）
    quiet_routine: bool  # 一般通知（成交、解除、連線恢復）靜音送達；「注意」「要處理」一定會響
    throttle_s: float  # 同一件事多久內不重複通知（超過的合併成一則）
    burst: int  # 同一類通知在 throttle_s 內最多即時送幾則
    timeout_s: float  # 送一次最多等幾秒
    warn_after_attempts: int  # 一則通知連續失敗幾次就留一筆紀錄（之後還是繼續試）
    retry_wait_s: float  # 每次重試前等幾秒
    queue_size: int  # 還沒送出的通知最多排幾則；滿了新的會被丟掉並留紀錄
    close_wait_s: float  # 程式結束時最多等幾秒把還沒送的送完


@dataclass(frozen=True)
class NotifyPushConfig:
    """第二管道：網站 App（PWA）的推播。私密金鑰不在這裡，只從環境變數讀。"""

    min_level: str  # 這一級以上才送（NOTIFY_LEVELS）
    url: str  # 點通知打開哪裡
    ttl_s: int  # 手機關機時推播服務最多替我們留幾秒
    timeout_s: float  # 送一次最多等幾秒
    allowed_hosts: tuple[str, ...]  # 只接受這些推播服務（本身或子網域）
    subject: str  # 推播服務出問題時聯絡誰（https: 或 mailto:）


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
    notify: NotifyConfig | None = None
    notify_push: NotifyPushConfig | None = None


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


def _amount(
    table: dict[str, Any], key: str, where: str, *, whole: bool = False, zero_ok: bool = False
) -> float:
    """讀一個有限、不是負的數字（zero_ok 為 False 時還要大於 0；whole 時要是整數）。"""
    value = _need(table, key, where)
    ok = isinstance(value, int | float) and not isinstance(value, bool) and isfinite(value)
    if not ok or (whole and value != int(value)):
        kind = "整數" if whole else "數字"
        raise ConfigError(f"[{where}] {key} 要是有限的{kind}，拿到 {value!r}")
    if value < 0 or (value == 0 and not zero_ok):
        limit = "不能是負的" if zero_ok else "要大於 0"
        raise ConfigError(f"[{where}] {key} {limit}：{value!r}")
    return float(value)


def _parse_notify_push(n: dict[str, Any]) -> NotifyPushConfig:
    level = _need(n, "min_level", "notify_push")
    if level not in NOTIFY_LEVELS:
        raise ConfigError(f"[notify_push] min_level 要是 {NOTIFY_LEVELS} 其中之一，拿到 {level!r}")
    url = _need(n, "url", "notify_push")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ConfigError(f"[notify_push] url 要是 https:// 開頭的網址，拿到 {url!r}")
    hosts = _need(n, "allowed_hosts", "notify_push")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and h for h in hosts):
        raise ConfigError(f"[notify_push] allowed_hosts 要是一串推播服務的主機名稱，拿到 {hosts!r}")
    subject = _need(n, "subject", "notify_push")
    if not isinstance(subject, str) or not subject.startswith(("https://", "mailto:")):
        raise ConfigError(f"[notify_push] subject 要是 https: 或 mailto: 開頭，拿到 {subject!r}")
    return NotifyPushConfig(
        min_level=level,
        url=url,
        ttl_s=int(_amount(n, "ttl_s", "notify_push", whole=True)),
        timeout_s=_amount(n, "timeout_s", "notify_push"),
        allowed_hosts=tuple(hosts),
        subject=subject,
    )


def _parse_notify(n: dict[str, Any]) -> NotifyConfig:
    kinds = _need(n, "kinds", "notify")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        raise ConfigError(f"[notify] kinds 要是一串種類名稱（至少一個），拿到 {kinds!r}")
    unknown = sorted(set(kinds) - set(NOTIFY_KINDS))
    if unknown:
        raise ConfigError(f"[notify] kinds 有不認得的種類 {unknown}（可用：{NOTIFY_KINDS}）")
    if len(set(kinds)) != len(kinds):
        raise ConfigError(f"[notify] kinds 有重複的種類：{kinds}")
    quiet = _need(n, "quiet_routine", "notify")
    if not isinstance(quiet, bool):
        raise ConfigError(f"[notify] quiet_routine 要是 true 或 false，拿到 {quiet!r}")

    def number(key: str, *, whole: bool = False, zero_ok: bool = False) -> float:
        return _amount(n, key, "notify", whole=whole, zero_ok=zero_ok)

    notify = NotifyConfig(
        channel=str(_need(n, "channel", "notify")),
        name=str(_need(n, "name", "notify")).strip(),
        kinds=tuple(kinds),
        quiet_routine=quiet,
        throttle_s=number("throttle_s"),
        burst=int(number("burst", whole=True)),
        timeout_s=number("timeout_s"),
        warn_after_attempts=int(number("warn_after_attempts", whole=True)),
        retry_wait_s=number("retry_wait_s"),  # 0 會讓送不出去時不停地重試，佔住處理器
        queue_size=int(number("queue_size", whole=True)),
        close_wait_s=number("close_wait_s", zero_ok=True),
    )
    if notify.channel not in NOTIFY_CHANNELS:
        raise ConfigError(
            f"[notify] channel 要是 {NOTIFY_CHANNELS} 其中之一，拿到 {notify.channel!r}"
        )
    if not notify.name:
        raise ConfigError("[notify] name 不能是空的（通知裡怎麼稱呼這個商品）")
    return notify


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
    notify = _parse_notify(raw["notify"]) if "notify" in raw else None
    notify_push = _parse_notify_push(raw["notify_push"]) if "notify_push" in raw else None
    if notify_push is not None and notify is None:
        raise ConfigError("[notify_push] 是第二個管道，要先有 [notify]（Telegram）才能開")
    if mode == "live":
        # D-223：通知沒做完不上真單；做完之後，真單沒開通知就拒絕啟動（D-224）
        if notify is None:
            raise ConfigError('真單模式（mode = "live"）一定要有 [notify]：成交與異常要通知手機')
        missing = [k for k in NOTIFY_LIVE_REQUIRED if k not in notify.kinds]
        if missing:
            raise ConfigError(f"真單模式的 [notify] kinds 少了一定要通知的種類：{missing}")
    return Config(
        mode=mode,
        contract=contract,
        risk=risk,
        session=session,
        sim=sim,
        momentum=momentum,
        export=export,
        notify=notify,
        notify_push=notify_push,
    )


def load_config(path: Path) -> Config:
    """讀一份 TOML 設定檔。"""
    with path.open("rb") as fh:
        return parse_config(tomllib.load(fh))
