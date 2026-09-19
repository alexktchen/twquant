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
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from twquant.config import BaseConfig
from twquant.conventions import TZ, as_of_cutoff
from twquant.errors import DataError, TwquantError

_SNAPSHOT_DIR = "strategy_targets"
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


class LiveStrategy(BaseConfig):
    """一支已登記、每天要產出目標持股的策略（configs/strategies_live.yaml 的一列）。"""

    code: str = Field(min_length=1)
    config: str = Field(min_length=1)  # configs/strategies/<config>.yaml
    label: str = Field(min_length=1)
    note: str = ""
    vault: VaultEvidence | None = None


class LiveStrategies(BaseConfig):
    """configs/strategies_live.yaml 的模型。"""

    strategies: tuple[LiveStrategy, ...] = ()

    @model_validator(mode="after")
    def _codes_are_unique(self) -> LiveStrategies:
        codes = [s.code for s in self.strategies]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            raise ValueError(f"strategies_live.yaml 的 code 重複：{dup}")
        return self


class TargetRow(BaseModel):
    """一檔目標部位。"""

    stock_id: str
    target_weight: float
    rank: int = Field(ge=1)

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


# (策略定義) -> [(stock_id, target_weight), ...]；空串列＝合法的空手日。
TargetsFn = Callable[[LiveStrategy], Sequence[tuple[str, float]]]


def _run(
    strat: LiveStrategy,
    status: Literal["ok", "flat", "failed"],
    *,
    detail: str | None = None,
    targets: tuple[TargetRow, ...] = (),
) -> StrategyRun:
    """由登記資料＋當日結果組出一筆 StrategyRun；金庫成績一律跟著走。"""
    return StrategyRun(
        code=strat.code,
        config=strat.config,
        label=strat.label,
        note=strat.note,
        vault=strat.vault,
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
            rows = list(compute_targets(strat))
        except (TwquantError, ValueError) as exc:
            runs.append(_run(strat, "failed", detail=f"{type(exc).__name__}: {exc}"[:500]))
            continue
        if not rows:
            runs.append(_run(strat, "flat", detail="空手日（無合格標的或曝險為 0）"))
            continue
        ordered = sorted(rows, key=lambda r: (-r[1], r[0]))
        targets = tuple(
            TargetRow(stock_id=sid, target_weight=float(w), rank=i)
            for i, (sid, w) in enumerate(ordered, start=1)
        )
        runs.append(_run(strat, "ok", targets=targets))
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


def select_snapshots(
    data_dir: str | Path, *, date: dt.date | None = None, limit: int = 1
) -> tuple[list[StrategyTargetsSnapshot], list[Mapping[str, str]]]:
    """讀出快照（新到舊）。壞檔列為問題項回傳，不丟棄也不讓整個請求失敗。"""
    root = Path(data_dir) / _SNAPSHOT_DIR
    if not root.exists():
        return [], []
    found: list[StrategyTargetsSnapshot] = []
    issues: list[Mapping[str, str]] = []
    for path in sorted(root.glob("stgt_*.json"), reverse=True):
        try:
            snap = read_snapshot(path)
        except DataError as exc:
            issues.append({"run_id": path.stem, "detail": str(exc)})
            continue
        if date is not None and snap.as_of.date() != date:
            continue
        found.append(snap)
    found.sort(key=lambda s: s.as_of, reverse=True)
    return found[: max(1, limit)], issues
