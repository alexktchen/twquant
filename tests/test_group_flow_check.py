"""D-178／T-356：族群資金流入有沒有用——事前登記的門檻、分組、整天一起抽的 bootstrap、三種結論。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.config import load_config
from twquant.data.stock_groups import StockGroupsConfig
from twquant.reporting.group_flow_check import (
    GroupFlowCheckConfig,
    MarketLift,
    TradeObs,
    cluster_bootstrap,
    evaluate,
    label_trades,
    market_lift,
    render,
)
from twquant.reporting.holdout import config_fingerprint

_ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> GroupFlowCheckConfig:
    return load_config(_ROOT / "configs" / "group_flow_check.yaml", GroupFlowCheckConfig)


def test_d178_ac1_registered_thresholds_and_definition_are_unchanged() -> None:
    """登記之後不得修改：門檻寫死在這裡比對；「資金流入」的定義（stock_groups.yaml flow）用指紋比對。"""
    cfg = _cfg()
    assert cfg.registration == "D-178"
    assert cfg.start == dt.date(2026, 5, 4)
    assert (cfg.min_hot_trades, cfg.min_hot_dates, cfg.min_diff) == (30, 10, 0.02)
    assert (cfg.ci_level, cfg.bootstrap_reps, cfg.seed) == (0.95, 10000, 20261014)
    assert (cfg.consistency_min_trades, cfg.market_horizon, cfg.market_step) == (10, 20, 20)
    flow = load_config(_ROOT / "configs" / "stock_groups.yaml", StockGroupsConfig).flow
    assert config_fingerprint(flow) == cfg.flow_fingerprint, (
        "「資金流入」的定義在 D-178 登記之後被改了；要換定義請另開登記"
    )
    wf = (_ROOT / ".github" / "workflows" / "group-flow-check.yml").read_text(encoding="utf-8")
    assert "twquant group-flow-check" in wf and "GITHUB_STEP_SUMMARY" in wf


_D1, _D2 = dt.date(2026, 5, 6), dt.date(2026, 6, 2)


def _row(
    sid: str, signal: dt.date, ret: float | None, status: str = "closed", live: bool = False
) -> dict[str, object]:
    return {"signal": signal.isoformat(), "sid": sid, "status": status, "ret": ret, "live": live}


def test_d178_ac2_label_trades_by_the_group_on_the_signal_day() -> None:
    groups = {"2478": "被動元件", "3017": "散熱"}
    hot = {_D1: frozenset({"被動元件"}), _D2: frozenset({"散熱"})}
    rows = [
        _row("2478", _D1, 0.05),  # 那天被動元件資金流入
        _row("2478", _D2, -0.02, live=True),  # 那天不是
        _row("3017", _D2, 0.03),
        _row("9999", _D1, 0.01),  # 沒有族群 → 不算資金流入
        _row("3017", _D1, None, status="open"),  # 還抱著：不算
        _row("2478", dt.date(2026, 4, 30), 0.1),  # 開戶日之前：不算
    ]
    got = label_trades(rows, "CORE", groups, hot, dt.date(2026, 5, 4))
    assert [(o.sid, o.signal, o.hot, o.ret, o.live) for o in got] == [
        ("2478", _D1, True, 0.05, False),
        ("2478", _D2, False, -0.02, True),
        ("3017", _D2, True, 0.03, False),
        ("9999", _D1, False, 0.01, False),
    ]
    assert got[0].line == "CORE" and got[3].group is None
    with pytest.raises(ValueError, match="沒有族群資金"):
        label_trades(
            [_row("2478", dt.date(2026, 7, 1), 0.0)], "CORE", groups, hot, dt.date(2026, 5, 4)
        )
    with pytest.raises(ValueError, match="有限數"):
        label_trades([_row("2478", _D1, float("nan"))], "CORE", groups, hot, dt.date(2026, 5, 4))


def _obs(line: str, day: int, hot: bool, ret: float) -> TradeObs:
    return TradeObs(
        line, dt.date(2026, 5, 4) + dt.timedelta(days=day), f"{day:04d}", "G", hot, ret, False
    )


def _sample(hot_ret: float, cold_ret: float, days: int = 20, per_day: int = 2) -> list[TradeObs]:
    """每天兩組各 per_day 筆；資金流入組平均 hot_ret、其他 cold_ret，兩組每天各自上下擺動（平均為 0）。"""
    out: list[TradeObs] = []
    for d in range(days):
        hot_wiggle = (d % 5 - 2) * 0.01
        cold_wiggle = (d % 4 - 1.5) * 0.004
        for k in range(per_day):
            line = "CORE" if k % 2 == 0 else "QUIET"
            out.append(_obs(line, d, True, hot_ret + hot_wiggle))
            out.append(_obs(line, d, False, cold_ret + cold_wiggle))
    return out


def test_d178_ac3_cluster_bootstrap_is_reproducible_and_brackets_the_difference() -> None:
    obs = _sample(0.05, 0.01)
    a = cluster_bootstrap(obs, 2000, 7, 0.95)
    assert a == cluster_bootstrap(obs, 2000, 7, 0.95)  # 固定 seed 可重現
    assert a is not None and a[0] < 0.04 < a[1] and a[0] > 0
    # 沒有任何一筆資金流入：每次抽樣都缺一組 → 算不出來（只有一兩天的情況由「樣本不足」先擋掉）
    none_hot = [_obs("CORE", d, False, 0.0) for d in range(30)]
    assert cluster_bootstrap(none_hot, 500, 1, 0.95) is None
    assert cluster_bootstrap([], 500, 1, 0.95) is None


def test_d178_ac4_three_verdicts() -> None:
    cfg = _cfg().model_copy(update={"bootstrap_reps": 2000})
    good = evaluate(_sample(0.05, 0.01), cfg)
    assert good.verdict == "有用（開發期支持）"
    assert good.diff == pytest.approx(0.04)
    assert good.hot.n == 40 and good.hot.dates == 20
    assert set(good.by_line) == {"CORE", "QUIET"}
    small = evaluate(_sample(0.05, 0.01, days=8), cfg)  # 16 筆、8 天
    assert small.verdict == "樣本不足" and small.ci is None
    flat = evaluate(_sample(0.02, 0.01), cfg)  # 差 1 個百分點 < 2
    assert flat.verdict == "沒有用" and any("未達" in r for r in flat.reasons)
    # 合併差夠大，但 QUIET 那條線資金流入反而比較差 → 不一致，沒有用
    mixed = [
        o
        if o.line == "CORE" or not o.hot
        else TradeObs(o.line, o.signal, o.sid, o.group, True, -0.05, False)
        for o in _sample(0.12, 0.01)
    ]
    got = evaluate(mixed, cfg)
    assert got.diff is not None and got.diff >= 0.02
    assert got.verdict == "沒有用" and any(r.startswith("QUIET") for r in got.reasons)


def test_d178_ac5_market_lift_compares_hot_members_with_the_rest() -> None:
    days = [dt.date(2026, 5, 4) + dt.timedelta(days=i) for i in range(30)]
    rows = []
    for i, d in enumerate(days):
        rows.append((d, "1101", 100.0 * (1.01**i)))  # 資金流入族群：每天漲 1%
        rows.append((d, "1102", 100.0))
        rows.append((d, "0050", 100.0 * (1.05**i)))  # 不在全市場（不是四碼普通股）
    px = pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "close": pl.Float64}, orient="row"
    )
    got = market_lift(
        px, lambda d: frozenset({"1101"}) if d != days[3] else None, days, r"^[1-9][0-9]{3}$", 5, 2
    )
    assert got.days == 24  # 30 天 − 最後 5 天沒有之後的收盤 − 那天沒算的 1 天
    assert got.mean == pytest.approx(1.01**5 - 1)
    assert got.positive == 1.0 and got.sampled == 12
    assert got.t is None  # 每天的差都一樣（標準差 0），t 算不出來
    assert market_lift(px, lambda d: None, days, r"^[1-9][0-9]{3}$", 5, 2) == MarketLift(
        0, None, None, 0, None
    )


def test_d178_ac6_report_states_the_verdict_and_numbers() -> None:
    cfg = _cfg().model_copy(update={"bootstrap_reps": 2000})
    text = render(
        evaluate(_sample(0.05, 0.01), cfg),
        MarketLift(400, 0.004, 0.55, 20, 1.3),
        cfg,
        dt.date(2026, 10, 13),
    )
    assert "## 結論：**有用（開發期支持）**" in text
    assert "平均報酬差 **+4.00%**（門檻 +2.00%）" in text
    assert "| CORE |" in text and "t ＝ 1.30" in text and "資料到 2026-10-13" in text
