"""T-308／D-153 §一：第十三輪六個配置的關係——進場六臂逐字相同、S 臂只差出場、R 臂只差進場、
S1 的出場＝tail_b 逐字（D-143 §四）、唯一的新門檻是 S2／S3 的 rank_pct 0.5。
登記寫「參數全部出自既有登記」——這條測試把它變成機器可攔。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.strategy.config import load_strategy

_CFG = Path(__file__).resolve().parents[1] / "configs" / "strategies"
_MODEL = "lgbm_quietA20_tu_h20_20260430_655df6d7"
_ARMS = ("quiet_s0", "quiet_s1", "quiet_s2", "quiet_s3", "quiet_r0", "quiet_r1")


def _dump(name: str) -> dict[str, Any]:
    return load_strategy(str(_CFG / f"{name}.yaml")).model_dump(mode="json")


def _core(d: dict[str, Any]) -> dict[str, Any]:
    """除了 name／description／signals／exit_rules 以外的每一欄。"""
    return {k: v for k, v in d.items() if k not in ("name", "description", "signals", "exit_rules")}


@pytest.mark.parametrize("name", _ARMS)
def test_ac1_everything_but_entry_and_exit_is_identical_to_s0(name: str) -> None:
    assert _core(_dump(name)) == _core(_dump("quiet_s0"))
    assert _dump(name)["trial_scope"] == "tail:quiet_a20_exits"


@pytest.mark.parametrize("name", ("quiet_s1", "quiet_s2", "quiet_s3"))
def test_ac2_s_arms_differ_from_s0_only_in_exit_rules(name: str) -> None:
    assert _dump(name)["signals"] == _dump("quiet_s0")["signals"]
    assert _dump(name)["exit_rules"] != _dump("quiet_s0")["exit_rules"]


@pytest.mark.parametrize(("arm", "base"), (("quiet_r0", "quiet_s0"), ("quiet_r1", "quiet_s1")))
def test_ac3_r_arms_differ_from_their_s_arm_only_in_the_entry_signal(arm: str, base: str) -> None:
    r, s = _dump(arm), _dump(base)
    assert r["exit_rules"] == s["exit_rules"]
    assert [sig["factor"] for sig in r["signals"]] == ["ml.pool_random"]
    assert r["signals"][0]["params"]["model_id"] == _MODEL, "亂數池必須是 A20 有分數的股票"
    assert r["signals"][0]["params"]["seed"] == 20260924


def test_ac4_s1_exit_is_tail_b_verbatim() -> None:
    assert _dump("quiet_s1")["exit_rules"] == _dump("tail_b")["exit_rules"]
    assert _dump("quiet_r1")["exit_rules"] == _dump("tail_b")["exit_rules"]


def test_ac5_entry_is_a20_probability_with_no_absolute_threshold() -> None:
    for name in ("quiet_s0", "quiet_s1", "quiet_s2", "quiet_s3"):
        d = _dump(name)
        assert [sig["factor"] for sig in d["signals"]] == ["ml.lgbm_tail_up_20d"]
        assert d["signals"][0]["params"] == {"model_id": _MODEL}
        assert d["slots"]["n"] == 15
        assert d["slots"]["entry_threshold"] == 0.0


def test_ac6_signal_exit_uses_pool_rank_below_half_and_holds_when_unscored() -> None:
    """S2＝訊號消失＋20 日；S3＝S1 的三條＋同一條訊號規則（順序：停損、移動停利、訊號、時間）。"""
    s2 = _dump("quiet_s2")["exit_rules"]
    sig = {
        "kind": "score_below",
        "factor": "ml.lgbm_tail_up_20d",
        "params": {"model_id": _MODEL, "value_col": "rank_pct"},
        "threshold": 0.5,
    }
    assert [r["kind"] for r in s2] == ["score_below", "time_stop"]
    assert {k: s2[0][k] for k in sig} == sig
    assert s2[1]["sessions"] == 20
    s1, s3 = _dump("quiet_s1")["exit_rules"], _dump("quiet_s3")["exit_rules"]
    assert s3[:2] == s1[:2]
    assert {k: s3[2][k] for k in sig} == sig
    assert s3[3] == s1[2]
