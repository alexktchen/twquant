"""提案生命週期與持久化（SPEC-008 §4）。IO 層（洞悉層 insight/ 不得寫檔）。

狀態機：pending → backtesting → validated | rejected_by_gate → approved（人工）→
paper → promoted | demoted；任何 → discarded。提案 YAML 整包進 git（含 LLM metadata、
回測結果、審核紀錄）。no_action 不落地（AC-7，由 save_proposal 回空字串表示）。
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from twquant.insight.schemas import Proposal

_STATUSES = frozenset(
    {
        "pending",
        "backtesting",
        "validated",
        "rejected_by_gate",
        "approved",
        "paper",
        "promoted",
        "demoted",
        "discarded",
    }
)


def _coerce(value: str) -> object:
    """把提案的字串新值轉成合適型別。"""
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _set_path(obj: Any, path: str, value: object) -> None:
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if p.isdigit() else cur[p]
    last = parts[-1]
    key: Any = int(last) if last.isdigit() else last
    cur[key] = value


def apply_changes(base: dict[str, Any], changes: list[dict[str, str]]) -> dict[str, Any]:
    """把提案的 changes（yaml_path→new_value）套到策略配置 dict，回傳新 dict（不改原件）。"""
    out = copy.deepcopy(base)
    for ch in changes:
        _set_path(out, ch["yaml_path"], _coerce(ch["new_value"]))
    return out


def save_proposal(
    proposals_dir: str | Path,
    proposal: Proposal,
    llm_meta: dict[str, object],
    day: dt.date,
) -> str:
    """落地一份 pending 提案，回傳 id。action=no_action → 不落地、回空字串（AC-7）。"""
    if proposal.action == "no_action":
        return ""
    d = Path(proposals_dir)
    d.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        f"{day}|{proposal.target_strategy}|{proposal.rationale}".encode()
    ).hexdigest()[:6]
    pid = f"{day.isoformat()}-{digest}"
    payload = {
        "id": pid,
        "day": day.isoformat(),
        "status": "pending",
        "proposal": proposal.model_dump(),
        "llm_meta": llm_meta,
        "backtest": None,
        "review": None,
    }
    (d / f"{pid}.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return pid


def load_proposal(proposals_dir: str | Path, pid: str) -> dict[str, Any]:
    raw = (Path(proposals_dir) / f"{pid}.yaml").read_text(encoding="utf-8")
    result: dict[str, Any] = yaml.safe_load(raw)
    return result


def list_proposals(proposals_dir: str | Path) -> list[str]:
    d = Path(proposals_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def update_proposal(proposals_dir: str | Path, pid: str, **fields: object) -> None:
    """更新提案欄位（status/backtest/review）。status 非法 → ValueError。"""
    if "status" in fields and fields["status"] not in _STATUSES:
        raise ValueError(f"非法狀態：{fields['status']}")
    data = load_proposal(proposals_dir, pid)
    data.update(fields)
    (Path(proposals_dir) / f"{pid}.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
