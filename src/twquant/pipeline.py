"""每日營運 pipeline 編排 + heartbeat/watchdog（LOOP.md §3）。

多波次擷取 → 品質閘門 → 訊號生成，逐步結果記錄；關鍵步驟失敗 → degraded（不產生
新訊號、沿用前日權重）。每次跑完寫 heartbeat（日期+git hash）；獨立 watchdog 檢查
heartbeat 缺失/過期（dead-man switch，REVIEW M29）。狀態層 IO，不在純運算層。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

_HEARTBEAT = ("state", "heartbeat.json")


@dataclass(frozen=True)
class PipelineStep:
    name: str
    fn: Callable[[], str]  # 回傳細節字串；raise 視為失敗
    critical: bool = False  # 失敗即進 degraded
    skip_if_degraded: bool = False  # degraded 時略過（如訊號生成）
    skip_reason: str | None = None  # 非 None＝這次明確不跑（如雲端用不到的步驟），結果記 skipped


def with_skips(steps: list[PipelineStep], names: Sequence[str], reason: str) -> list[PipelineStep]:
    """把 `names` 列到的步驟標成略過。名稱對不上任何步驟即 raise——設定打錯字不得靜默變成「全跑」。"""
    known = {s.name for s in steps}
    unknown = sorted(set(names) - known)
    if unknown:
        raise ValueError(f"要略過的步驟不存在：{unknown}；現有步驟：{[s.name for s in steps]}")
    skip = set(names)
    return [replace(s, skip_reason=reason) if s.name in skip else s for s in steps]


def revenue_month(day: dt.date) -> str:
    """每日 pipeline 該抓哪個月的營收（YYYY-MM）：上個月——M 月營收在 M+1 月 10 日前公布，重抓冪等。"""
    first = day.replace(day=1) - dt.timedelta(days=1)
    return f"{first.year:04d}-{first.month:02d}"


def missing_required(required: Sequence[str], failed: Sequence[str]) -> list[str]:
    """D-175：必要的表（"來源/表"）裡這次沒抓到的，依必要清單的順序。"""
    bad = set(failed)
    return [t for t in required if t in bad]


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str  # ok | failed | skipped
    detail: str


@dataclass(frozen=True)
class PipelineResult:
    day: dt.date
    steps: list[StepResult] = field(default_factory=list)
    degraded: bool = False
    signals_produced: bool = False


def run_pipeline(
    day: dt.date,
    steps: list[PipelineStep],
    *,
    data_dir: str | Path,
    git_hash: str,
    now: dt.datetime,
) -> PipelineResult:
    """依序執行步驟；關鍵步驟失敗 → 後續可略過（degraded）。完成即寫 heartbeat。"""
    results: list[StepResult] = []
    degraded = False
    produced = False
    for step in steps:
        if step.skip_reason is not None:
            results.append(StepResult(step.name, "skipped", step.skip_reason))
            continue
        if degraded and step.skip_if_degraded:
            results.append(StepResult(step.name, "skipped", "前置降級，沿用前日"))
            continue
        try:
            detail = step.fn()
            results.append(StepResult(step.name, "ok", detail))
            if step.skip_if_degraded:  # 訊號類步驟成功 → 有產出
                produced = True
        except Exception as exc:  # noqa: BLE001  # 步驟失敗要記錄續跑，不中斷整條
            results.append(StepResult(step.name, "failed", str(exc)))
            if step.critical:
                degraded = True

    write_heartbeat(data_dir, day, git_hash, now)  # 跑完就記 heartbeat（防靜默停更）
    return PipelineResult(day=day, steps=results, degraded=degraded, signals_produced=produced)


# ── heartbeat / watchdog ───────────────────────────────────────────
@dataclass(frozen=True)
class Heartbeat:
    day: str
    git_hash: str
    ts: str


def _hb_path(data_dir: str | Path) -> Path:
    return Path(data_dir).joinpath(*_HEARTBEAT)


def write_heartbeat(data_dir: str | Path, day: dt.date, git_hash: str, now: dt.datetime) -> None:
    p = _hb_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"day": day.isoformat(), "git_hash": git_hash, "ts": now.isoformat()}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def read_heartbeat(data_dir: str | Path) -> Heartbeat | None:
    p = _hb_path(data_dir)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return Heartbeat(day=d["day"], git_hash=d.get("git_hash", ""), ts=d.get("ts", ""))
    except (json.JSONDecodeError, KeyError):
        return None


@dataclass(frozen=True)
class WatchdogResult:
    ok: bool
    reason: str


def watchdog_check(data_dir: str | Path, expected_day: dt.date) -> WatchdogResult:
    """dead-man switch：heartbeat 缺失或非當日 → 不 ok（發 CRIT 的依據）。"""
    hb = read_heartbeat(data_dir)
    if hb is None:
        return WatchdogResult(False, "無 heartbeat（pipeline 未跑或狀態遺失）")
    if hb.day != expected_day.isoformat():
        return WatchdogResult(False, f"heartbeat 停在 {hb.day}，非預期 {expected_day.isoformat()}")
    return WatchdogResult(True, "")
