"""策略持股快照（D-112）：把已登記的策略每天算出的目標權重存成不可變快照，供後端提供。

與推薦快照（`recommendation_snapshot`）同一套語意：一個訊號日一份、內容相同可重跑、
內容不同拒絕覆寫、失敗不留半成品。快照落在 `data/`（D: 上的檔案），
2026-09-10 的事故證明了這件事的價值——PG 整個消失時，檔案型的研究產出一份都沒掉。

**這是研究輸出，不是下單指令。** D-107 判定沒有任何一支策略可部署；
每一支策略的快照都帶著它在金庫期（未見資料）的實際成績，頁面不得只顯示名單而不顯示成績。

本模組只做「組裝與保存」；目標權重由 `strategy.build.build_targets` 計算（純運算），
由 CLI 注入，故這裡不 import 策略層，也不開資料連線。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import os
import re
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from twquant.config import BaseConfig
from twquant.conventions import TZ, as_of_cutoff
from twquant.errors import DataError, TwquantError

_SNAPSHOT_DIR = "strategy_targets"
TargetKind = Literal["rebalance", "slots"]
_RUN_ID = re.compile(r"^stgt_\d{8}_[0-9a-f]{16}$")
# 權重和的容忍（與回測不變量同一個數字）：浮點累加的誤差，不是放寬。
_WEIGHT_SUM_TOL = 1e-9


class VaultEvidence(BaseConfig):
    """這支策略在金庫期（2026-03-02~08-31，未見資料）的實際成績（D-107）。

    跟著名單一起走，是為了讓「看到名單」與「知道它的成績」不可能被拆開。
    """

    total_return: float
    max_drawdown: float
    benchmark_total_return: float
    benchmark_max_drawdown: float


class ResearchEvidence(BaseConfig):
    """沒有金庫期成績的上架策略所帶的成績（D-147）：walk-forward 樣本外回測。

    **不是金庫期**——每個測試窗的模型只用更早的資料訓練，但整段期間研究者都看過、
    也參與了設計。頁面必須把這一點跟數字一起講清楚。
    """

    decision: str = Field(min_length=1)  # 數字的出處（如 "D-143 §十一"）
    window_start: dt.date
    window_end: dt.date
    cagr: float
    max_drawdown: float = Field(le=0.0)
    calmar: float


class LiveStrategy(BaseConfig):
    """一支已登記、每天要產出目標持股的策略（configs/strategies_live.yaml 的一列）。"""

    code: str = Field(min_length=1)
    config: str = Field(min_length=1)  # configs/strategies/<config>.yaml
    label: str = Field(min_length=1)
    note: str = ""
    vault: VaultEvidence | None = None
    research: ResearchEvidence | None = None  # D-147：沒有金庫期成績時的替代成績（須標明）


class LiveStrategies(BaseConfig):
    """configs/strategies_live.yaml 的模型。"""

    strategies: tuple[LiveStrategy, ...] = ()
    # T-300：頁面的「連續 N 天」最多往回看幾份快照。只影響顯示，不影響名單。
    tracking_lookback: int = Field(default=20, ge=2, le=250)

    @model_validator(mode="after")
    def _codes_are_unique(self) -> LiveStrategies:
        codes = [s.code for s in self.strategies]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            raise ValueError(f"strategies_live.yaml 的 code 重複：{dup}")
        # D-112「名單不得在沒有成績時呈現」：原本只由一條測試守著；D-147 把它搬進登記表本身，
        # 並允許兩種**標明類型**的成績——金庫期（未見資料）或研究期（walk-forward 樣本外）。
        bare = [s.code for s in self.strategies if s.vault is None and s.research is None]
        if bare:
            raise ValueError(
                f"strategies_live.yaml 有策略沒有任何成績：{bare}——名單不得脫離成績呈現"
            )
        return self


class TargetRow(BaseModel):
    """一檔目標部位。"""

    stock_id: str
    target_weight: float
    rank: int = Field(ge=1)
    # T-300：每日補位型排序用的模型分數（順位即依它）；月頻型與 T-300 之前的快照為 None。
    score: float | None = None

    @field_validator("stock_id", mode="before")
    @classmethod
    def _stock_id_is_string(cls, value: object) -> str:
        # 黃金規則 4：代號一律字串、保留前導零。int 進來代表上游已經弄丟了前導零。
        if not isinstance(value, str) or not value:
            raise ValueError("stock_id 必須是非空字串（不得為數字型別）")
        return value


class StrategyRun(BaseModel):
    """一支策略在某訊號日的結果。失敗也是一種結果——記下來，不丟掉。"""

    code: str
    config: str
    label: str
    note: str = ""
    status: Literal["ok", "flat", "failed"]
    detail: str | None = None  # status=failed 時的原因；flat＝合法的空手日
    exposure: float = 0.0
    targets: tuple[TargetRow, ...] = ()
    vault: VaultEvidence | None = None
    research: ResearchEvidence | None = None
    # T-300：名單的性質——月頻目標權重（rebalance）或每日補位候選（slots）。
    # None＝T-300 之前的快照或當天算不出來；讀取端由策略配置補上。
    kind: TargetKind | None = None

    @model_validator(mode="after")
    def _status_matches_payload(self) -> StrategyRun:
        if self.status == "ok" and not self.targets:
            raise ValueError(f"{self.code}：status=ok 卻沒有任何目標部位")
        if self.status != "ok" and self.targets:
            raise ValueError(f"{self.code}：status={self.status} 不得帶目標部位")
        if self.status == "failed" and not self.detail:
            raise ValueError(f"{self.code}：status=failed 必須說明原因")
        total = sum(t.target_weight for t in self.targets)
        if total > 1.0 + _WEIGHT_SUM_TOL:
            raise ValueError(f"{self.code}：權重和 {total:.6f} 超過 1")
        if any(t.target_weight < 0 for t in self.targets):
            raise ValueError(f"{self.code}：出現負權重（本系統無放空引擎）")
        if self.kind == "slots":
            ordered = sorted(self.targets, key=lambda t: t.rank)
            scores = [t.score for t in ordered if t.score is not None]
            if len(scores) != len(ordered):
                raise ValueError(f"{self.code}：補位型名單每一檔都要有分數（順位依分數）")
            if any(a < b for a, b in itertools.pairwise(scores)):
                raise ValueError(f"{self.code}：補位型名單的順位與分數順序不符")
        return self


class StrategyTargetsSnapshot(BaseModel):
    """一個訊號日、全部已登記策略的目標持股。"""

    schema_version: Literal[1] = 1
    run_id: str
    as_of: dt.datetime
    generated_at: dt.datetime
    # 沒有「published」這個狀態：D-107 判定無策略可部署，故只有研究模式。
    mode: Literal["research"] = "research"
    strategies: tuple[StrategyRun, ...]

    @field_validator("as_of", "generated_at")
    @classmethod
    def _timestamp_is_taipei(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp 必須 tz-aware（黃金規則 2）")
        return value.astimezone(TZ)

    @model_validator(mode="after")
    def _run_id_is_wellformed(self) -> StrategyTargetsSnapshot:
        if _RUN_ID.fullmatch(self.run_id) is None:
            raise ValueError("run_id 格式錯誤")
        return self


@dataclass(frozen=True)
class SnapshotWriteResult:
    """保存結果：路徑，以及這次是新建還是沿用既有的相同內容。"""

    path: Path
    created: bool


def run_id_for(day: dt.date, configs: Sequence[str]) -> str:
    """同一天、同一組策略 → 同一個 run_id；策略清單變了就是另一個 run。"""
    key = f"stgt:{day.isoformat()}:{','.join(sorted(configs))}"
    return f"stgt_{day:%Y%m%d}_{hashlib.sha256(key.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class TargetList:
    """一支策略當日的名單與它的性質（T-300）。

    `rows` 是 (stock_id, 權重)；每日補位型另帶 `scores`（排序用的模型分數），
    快照的順位就依它——補位型權重全是 1/n，只看權重會退回代號順序。
    """

    rows: Sequence[tuple[str, float]]
    kind: TargetKind
    scores: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if self.kind == "slots" and (
            self.scores is None or any(sid not in self.scores for sid, _ in self.rows)
        ):
            raise ValueError("補位型名單每一檔都要有模型分數（順位依分數）")
        if self.kind == "rebalance" and self.scores is not None:
            raise ValueError("月頻型名單的順位依權重，不帶模型分數")

    def __iter__(self) -> Iterator[tuple[str, float]]:
        """逐檔 (stock_id, 權重)——呼叫端照舊當串列用（T-300 之前的介面）。"""
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


# (策略定義) -> 名單；空＝合法的空手日。舊呼叫端回傳 [(stock_id, target_weight), ...]
TargetsFn = Callable[[LiveStrategy], Sequence[tuple[str, float]] | TargetList]


def _run(
    strat: LiveStrategy,
    status: Literal["ok", "flat", "failed"],
    *,
    detail: str | None = None,
    targets: tuple[TargetRow, ...] = (),
    kind: TargetKind | None = None,
) -> StrategyRun:
    """由登記資料＋當日結果組出一筆 StrategyRun；金庫成績一律跟著走。"""
    return StrategyRun(
        kind=kind,
        code=strat.code,
        config=strat.config,
        label=strat.label,
        note=strat.note,
        vault=strat.vault,
        research=strat.research,
        status=status,
        detail=detail,
        exposure=float(sum(t.target_weight for t in targets)),
        targets=targets,
    )


def build_snapshot(
    day: dt.date,
    strategies: Sequence[LiveStrategy],
    compute_targets: TargetsFn,
    *,
    generated_at: dt.datetime,
) -> StrategyTargetsSnapshot:
    """逐支策略計算並組成快照。**單支失敗不影響其餘，但一定留下紀錄。**

    `compute_targets` 由呼叫端注入（CLI 包一層 `build_targets`），本函數不碰資料。
    只接 `TwquantError`／`ValueError`——那是「這支策略今天算不出來」的已知形狀
    （宇宙為空、缺 ML 分數、配置錯誤）；其他例外是程式錯誤，讓它往上炸。
    """
    if not strategies:
        raise DataError("strategy_targets", day, "沒有任何已登記的策略，拒絕產生空快照")
    runs: list[StrategyRun] = []
    for strat in strategies:
        try:
            got = compute_targets(strat)
        except (TwquantError, ValueError) as exc:
            runs.append(_run(strat, "failed", detail=f"{type(exc).__name__}: {exc}"[:500]))
            continue
        listed = got if isinstance(got, TargetList) else None
        rows = list(listed.rows if listed else cast(Sequence[tuple[str, float]], got))
        kind = listed.kind if listed else None
        scores = listed.scores if listed else None
        if not rows:
            runs.append(_run(strat, "flat", detail="空手日（無合格標的或曝險為 0）", kind=kind))
            continue
        if scores is not None:  # 補位型：依模型分數，同分依代號（與 slot_candidates 一致）
            ordered = sorted(rows, key=lambda r: (-scores[r[0]], r[0]))
        else:
            ordered = sorted(rows, key=lambda r: (-r[1], r[0]))
        targets = tuple(
            TargetRow(
                stock_id=sid,
                target_weight=float(w),
                rank=i,
                score=None if scores is None else float(scores[sid]),
            )
            for i, (sid, w) in enumerate(ordered, start=1)
        )
        runs.append(_run(strat, "ok", targets=targets, kind=kind))
    return StrategyTargetsSnapshot(
        run_id=run_id_for(day, [s.config for s in strategies]),
        as_of=as_of_cutoff(day),
        generated_at=generated_at,
        strategies=tuple(runs),
    )


def snapshot_path(data_dir: str | Path, run_id: str) -> Path:
    """已驗證的 run_id → 固定的伺服端路徑（不接受任何路徑片段）。"""
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise DataError("strategy_targets", None, "run_id 格式錯誤")
    return Path(data_dir) / _SNAPSHOT_DIR / f"{run_id}.json"


def _canonical_bytes(snapshot: StrategyTargetsSnapshot) -> bytes:
    """冪等比對用的決定性序列化。`generated_at` 不參與——重跑同一天不該因為時鐘而衝突。"""
    payload = snapshot.model_dump(mode="json")
    payload.pop("generated_at", None)
    return (json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()


def read_snapshot(path: str | Path) -> StrategyTargetsSnapshot:
    """讀取並驗證一份快照。"""
    try:
        payload = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError("strategy_targets", None, "快照檔無法讀取") from exc
    try:
        return StrategyTargetsSnapshot.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise DataError("strategy_targets", None, "快照合約無效") from exc


def save_snapshot(data_dir: str | Path, snapshot: StrategyTargetsSnapshot) -> SnapshotWriteResult:
    """原子地建立一份不可變快照：內容相同的重跑成功、內容不同則 raise。"""
    target = snapshot_path(data_dir, snapshot.run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    full = (
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=1, sort_keys=True)
        + "\n"
    ).encode()
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(full)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _canonical_bytes(read_snapshot(target)) != _canonical_bytes(snapshot):
                raise DataError(
                    "strategy_targets",
                    None,
                    f"run_id={snapshot.run_id} 已存在不同內容，拒絕覆寫"
                    "（同一天同一組策略算出不同名單＝上游資料變了，先查原因）",
                ) from None
            return SnapshotWriteResult(path=target, created=False)
    except OSError as exc:
        raise DataError("strategy_targets", None, "快照無法原子保存") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return SnapshotWriteResult(path=target, created=True)


def load_snapshots(
    data_dir: str | Path,
) -> tuple[list[StrategyTargetsSnapshot], list[Mapping[str, str]]]:
    """讀出全部快照（新到舊；同一刻依產生時間、run_id）。壞檔列為問題項，不丟棄也不中斷。"""
    root = Path(data_dir) / _SNAPSHOT_DIR
    if not root.exists():
        return [], []
    found: list[StrategyTargetsSnapshot] = []
    issues: list[Mapping[str, str]] = []
    for path in sorted(root.glob("stgt_*.json"), reverse=True):
        try:
            found.append(read_snapshot(path))
        except DataError as exc:
            issues.append({"run_id": path.stem, "detail": str(exc)})
    found.sort(key=lambda s: (s.as_of, s.generated_at, s.run_id), reverse=True)
    return found, issues


def select_snapshots(
    data_dir: str | Path, *, date: dt.date | None = None, limit: int = 1
) -> tuple[list[StrategyTargetsSnapshot], list[Mapping[str, str]]]:
    """讀出快照（新到舊）。壞檔列為問題項回傳，不丟棄也不讓整個請求失敗。"""
    found, issues = load_snapshots(data_dir)
    kept = [s for s in found if date is None or s.as_of.date() == date]
    return kept[: max(1, limit)], issues


# ── T-300：每日異動與追蹤（只比名單成員，不碰行情——D-148 禁止對保留期做績效統計）──

StreakOpen = Literal["lookback", "history", "failed"]
CompareStatus = Literal[
    "compared",
    "no_previous",
    "strategy_new",
    "config_changed",
    "previous_failed",
    "current_failed",
]


@dataclass(frozen=True)
class Streak:
    """連續出現在名單上的快照日數（含當日）。`open` 非 None＝實際可能更久，值是原因。"""

    days: int
    open: StreakOpen | None


@dataclass(frozen=True)
class RunTracking:
    """一支策略與前一份快照的比較。`status != "compared"` 時不給新進／移出。"""

    previous_as_of: dt.date | None
    status: CompareStatus
    entered: tuple[str, ...]
    exited: tuple[TargetRow, ...]  # 前一份的那一列，依前一份的順位
    streaks: Mapping[str, Streak]


@dataclass(frozen=True)
class StrategyDisplay:
    """讀取端要的兩件配置：回看上限，與每個策略配置的名單性質（補舊快照缺的 kind）。"""

    lookback: int
    kinds: Mapping[str, TargetKind]


def daily_series(snapshots: Sequence[StrategyTargetsSnapshot]) -> list[StrategyTargetsSnapshot]:
    """一天一份（同一天取最晚產生者），新到舊。追蹤比的是「日」，不是檔案。"""
    best: dict[dt.date, StrategyTargetsSnapshot] = {}
    for snap in snapshots:
        day = snap.as_of.date()
        held = best.get(day)
        if held is None or (snap.generated_at, snap.run_id) > (held.generated_at, held.run_id):
            best[day] = snap
    return [best[d] for d in sorted(best, reverse=True)]


def _same_strategy(snap: StrategyTargetsSnapshot, run: StrategyRun) -> StrategyRun | None:
    """同一支策略（code 相同且配置相同）在另一份快照裡的那一筆；換了配置就是另一個東西。"""
    for other in snap.strategies:
        if other.code == run.code:
            return other if other.config == run.config else None
    return None


def _streak(sid: str, chain: Sequence[StrategyRun | None], beyond: bool) -> Streak:
    """往回數 `sid` 連續在名單上的天數；`chain` 是更早的快照裡同一支策略（新到舊）。"""
    days = 1
    for run in chain:
        if run is None:  # 策略還不存在或配置不同：名單從這裡開始，天數是確切的
            return Streak(days, None)
        if run.status == "failed":  # 那天算不出來：不知道它在不在
            return Streak(days, "failed")
        if all(t.stock_id != sid for t in run.targets):
            return Streak(days, None)
        days += 1
    return Streak(days, "lookback" if beyond else "history")


def track_runs(
    snapshot: StrategyTargetsSnapshot,
    series: Sequence[StrategyTargetsSnapshot],
    lookback: int,
) -> dict[str, RunTracking]:
    """每支策略（以 code）與前一份快照比較，並算每檔的連續天數（SPEC-014 §11）。

    `series` 為 `daily_series` 的結果；前一份＝日期早於 `snapshot` 的最新一份。
    """
    day = snapshot.as_of.date()
    earlier = [s for s in series if s.as_of.date() < day]
    window = earlier[: max(0, lookback - 1)]
    beyond = len(earlier) > len(window)
    previous = earlier[0] if earlier else None
    out: dict[str, RunTracking] = {}
    for run in snapshot.strategies:
        prev_run = _same_strategy(previous, run) if previous is not None else None
        status: CompareStatus
        if run.status == "failed":
            status = "current_failed"
        elif previous is None:
            status = "no_previous"
        elif all(o.code != run.code for o in previous.strategies):
            status = "strategy_new"
        elif prev_run is None:
            status = "config_changed"
        elif prev_run.status == "failed":
            status = "previous_failed"
        else:
            status = "compared"
        entered: tuple[str, ...] = ()
        exited: tuple[TargetRow, ...] = ()
        if status == "compared" and prev_run is not None:
            before = {t.stock_id for t in prev_run.targets}
            now = {t.stock_id for t in run.targets}
            entered = tuple(t.stock_id for t in run.targets if t.stock_id not in before)
            exited = tuple(t for t in prev_run.targets if t.stock_id not in now)
        chain = [_same_strategy(s, run) for s in window]
        streaks = {t.stock_id: _streak(t.stock_id, chain, beyond) for t in run.targets}
        out[run.code] = RunTracking(
            previous_as_of=previous.as_of.date() if previous is not None else None,
            status=status,
            entered=entered,
            exited=exited,
            streaks=streaks,
        )
    return out


def project_snapshot(
    snapshot: StrategyTargetsSnapshot,
    series: Sequence[StrategyTargetsSnapshot],
    display: StrategyDisplay,
) -> dict[str, object]:
    """API 投影：快照原樣＋每支策略的 `kind`／`tracking`＋每檔的新進與連續天數。"""
    tracking = track_runs(snapshot, series, display.lookback)
    body = snapshot.model_dump(mode="json")
    runs: list[dict[str, object]] = []
    for run in snapshot.strategies:
        tr = tracking[run.code]
        compared = tr.status == "compared"
        raw = run.model_dump(mode="json")
        targets = [
            {
                **target,
                "entered": (target["stock_id"] in tr.entered) if compared else None,
                "streak_days": tr.streaks[target["stock_id"]].days,
                "streak_open": tr.streaks[target["stock_id"]].open,
            }
            for target in raw["targets"]
        ]
        runs.append(
            {
                **raw,
                "kind": run.kind or display.kinds.get(run.config),
                "targets": targets,
                "tracking": {
                    "previous_as_of": (
                        tr.previous_as_of.isoformat() if tr.previous_as_of else None
                    ),
                    "status": tr.status,
                    "entered": list(tr.entered),
                    "exited": [t.model_dump(mode="json") for t in tr.exited],
                },
            }
        )
    body["strategies"] = runs
    return body
