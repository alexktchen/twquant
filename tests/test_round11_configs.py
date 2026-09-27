"""T-298／D-149 §三：第十一輪三臂各只跟 tail_b 差一件事，參數逐字出自既有登記。

登記寫「本輪不發明任何參數」——這條測試把它變成機器可攔：F1／F2 的 vol_target 必須等於
`configs/portfolio.yaml`（D-004），F3 的下限必須等於 `configs/risk.yaml` 的 REDUCED 檔位（D-009），
而且除了 name／description／各自那一段 slots 設定，其餘欄位與 tail_b 逐欄相同。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.config import load_config
from twquant.factors.registry import list_factors
from twquant.portfolio.config import PortfolioConfig, RiskConfig
from twquant.strategy.config import load_strategy

_CFG = Path(__file__).resolve().parents[1] / "configs"
_ARMS = {"tail_f1": "conditional_vol", "tail_f2": "conditional_vol", "tail_f3": "inverse_vol"}


def _dump(name: str) -> dict[str, Any]:
    return load_strategy(str(_CFG / "strategies" / f"{name}.yaml")).model_dump(mode="json")


def _strip(d: dict[str, Any], key: str | None) -> dict[str, Any]:
    out = {k: v for k, v in d.items() if k not in ("name", "description")}
    out["slots"] = {k: v for k, v in d["slots"].items() if k != key}
    return out


@pytest.mark.parametrize(("name", "key"), sorted(_ARMS.items()))
def test_ac1_each_arm_differs_from_tail_b_only_in_its_slot_block(name: str, key: str) -> None:
    arm, base = _dump(name), _dump("tail_b")
    assert arm["slots"][key] is not None, f"{name} 沒有 {key}"
    assert _strip(arm, key) == _strip(base, key)
    assert arm["trial_scope"] == "lw:slots_tail"  # D-149 §六：同一個試驗族


def test_ac2_f1_f2_use_the_d004_values_verbatim() -> None:
    vt = load_config(_CFG / "portfolio.yaml", PortfolioConfig).vol_target
    for name in ("tail_f1", "tail_f2"):
        got = _dump(name)["slots"]["conditional_vol"]["vol_target"]
        assert got == vt.model_dump(mode="json"), f"{name} 的 vol_target 與 portfolio.yaml 不同"


def test_ac3_f1_and_f2_differ_only_in_the_trigger_source() -> None:
    f1 = _dump("tail_f1")["slots"]["conditional_vol"]
    f2 = _dump("tail_f2")["slots"]["conditional_vol"]
    assert (f1["trigger"], f1["index_id"]) == ("holdings", None)
    assert (f2["trigger"], f2["index_id"]) == ("index", "TAIEX")
    assert {k: v for k, v in f1.items() if k not in ("trigger", "index_id")} == {
        k: v for k, v in f2.items() if k not in ("trigger", "index_id")
    }


def test_ac4_f3_bounds_come_from_registered_values() -> None:
    inv = _dump("tail_f3")["slots"]["inverse_vol"]
    assert inv["floor"] == load_config(_CFG / "risk.yaml", RiskConfig).reduced_scale
    assert inv["factor"] == "price.vol_20d"
    assert inv["factor"] in set(list_factors())
