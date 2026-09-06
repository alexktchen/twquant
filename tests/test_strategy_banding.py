"""T-076：buy/hold banding 降周轉（SPEC-012 AC-1~7）。

banding 的核心語意：**已持有**與**未持有**適用不同門檻。已持有者只要沒跌出
exit 就續抱，未持有者要進得了 entry 才建倉——中間那段（exit ≤ rank_pct < entry）
是遲滯區，決定持股與否的不是分數本身，而是「昨天在不在裡面」。
"""

from __future__ import annotations

import polars as pl
import pytest
from pydantic import ValidationError

from twquant.strategy.banding import banded_selection
from twquant.strategy.config import SelectionConfig


def _score(pairs: list[tuple[str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"stock_id": [p[0] for p in pairs], "score": [p[1] for p in pairs]},
        schema={"stock_id": pl.String, "score": pl.Float64},
    )


# 10 檔、分數 1..10 → rank_pct 依序 0.1, 0.2, ... 1.0
_TEN = _score([(f"{1100 + i}", float(i + 1)) for i in range(10)])


def test_ac1_hysteresis_band_keeps_held_and_blocks_unheld() -> None:
    """AC-1：手算。rank_pct 落在 [0.70, 0.90) 的遲滯區——已持有續抱、未持有不建倉。"""
    # rank_pct: 1109→1.0, 1108→0.9, 1107→0.8, 1106→0.7, 1105→0.6 ...
    out = banded_selection(
        _TEN,
        held=frozenset({"1107"}),  # rank_pct 0.8，在遲滯區內
        top_n=10,  # 放寬名額，隔離出遲滯效果
        entry_rank_pct=0.90,
        exit_rank_pct=0.70,
    )
    picked = set(out["stock_id"].to_list())

    assert "1107" in picked, "已持有且 rank_pct 0.8 ≥ exit 0.70，應續抱"
    assert "1106" not in picked, "未持有且 rank_pct 0.7 < entry 0.90，不應建倉"
    # 只有 rank_pct ≥ 0.90 的才是新建倉標的
    assert picked == {"1107", "1108", "1109"}


def test_ac1_held_falling_below_exit_is_dropped() -> None:
    """AC-1：已持有但跌破 exit → 出場（遲滯不是永久豁免）。"""
    out = banded_selection(
        _TEN,
        held=frozenset({"1105"}),  # rank_pct 0.6 < exit 0.70
        top_n=10,
        entry_rank_pct=0.90,
        exit_rank_pct=0.70,
    )
    assert "1105" not in set(out["stock_id"].to_list())


def test_ac1_top_n_is_respected_and_held_take_priority() -> None:
    """AC-1：名額不足時，續抱優先於新建倉——這才是降周轉的來源。"""
    out = banded_selection(
        _TEN,
        held=frozenset({"1106", "1107"}),  # rank_pct 0.7 與 0.8，皆 ≥ exit
        top_n=2,
        entry_rank_pct=0.90,
        exit_rank_pct=0.70,
    )
    assert set(out["stock_id"].to_list()) == {"1106", "1107"}, (
        "續抱應優先於分數更高的新標的（1108/1109），否則遲滯區形同虛設"
    )


def test_ac1_without_holdings_banding_reduces_to_entry_threshold() -> None:
    """AC-1：空手起步時，banding 等價於「取 rank_pct ≥ entry 的前 top_n 檔」。"""
    out = banded_selection(
        _TEN, held=frozenset(), top_n=10, entry_rank_pct=0.90, exit_rank_pct=0.70
    )
    assert set(out["stock_id"].to_list()) == {"1108", "1109"}


def test_ac2_entry_not_above_exit_fails_to_load() -> None:
    """AC-2：entry ≤ exit 的配置載入即失敗（遲滯區為負或為零＝設定錯誤）。"""
    for entry, exit_ in ((0.70, 0.70), (0.60, 0.80)):
        with pytest.raises(ValidationError, match="entry_rank_pct"):
            SelectionConfig(
                method="banded_top_n", top_n=20, entry_rank_pct=entry, exit_rank_pct=exit_
            )


def test_ac2_banded_method_requires_both_thresholds() -> None:
    """AC-2：選 banded_top_n 卻沒給門檻 → 載入即失敗，不得靜默套預設。"""
    with pytest.raises(ValidationError, match=r"entry_rank_pct|exit_rank_pct"):
        SelectionConfig(method="banded_top_n", top_n=20)


def test_ac3_banded_selection_is_deterministic() -> None:
    """AC-3：相同輸入兩次呼叫 bit-identical（同分以 stock_id 決定性斷開）。"""
    tied = _score([("1101", 5.0), ("1102", 5.0), ("1103", 9.0), ("1104", 1.0)])
    kw = {
        "held": frozenset({"1101"}),
        "top_n": 2,
        "entry_rank_pct": 0.90,
        "exit_rank_pct": 0.40,
    }
    a = banded_selection(tied, **kw)  # type: ignore[arg-type]
    b = banded_selection(tied, **kw)  # type: ignore[arg-type]
    assert a.equals(b)


def test_ac4_banding_turnover_is_never_higher_than_plain_top_n() -> None:
    """AC-4：逐期模擬——banding 的換手不得高於無 banding 版。這是本任務的存在理由。

    構造一段分數在名次邊界反覆抖動的序列：無 banding 會來回換股，banding 應吸收掉。
    """
    days = [
        [("1101", 10.0), ("1102", 9.0), ("1103", 8.0), ("1104", 7.0), ("1105", 6.0)],
        [("1101", 10.0), ("1102", 7.0), ("1103", 9.0), ("1104", 8.0), ("1105", 6.0)],
        [("1101", 10.0), ("1102", 9.0), ("1103", 7.0), ("1104", 8.0), ("1105", 6.0)],
        [("1101", 10.0), ("1102", 8.0), ("1103", 9.0), ("1104", 7.0), ("1105", 6.0)],
    ]
    top_n = 2

    def turnover(banded: bool) -> int:
        held: frozenset[str] = frozenset()
        churn = 0
        for rows in days:
            df = _score(rows)
            if banded:
                nxt = banded_selection(
                    df, held=held, top_n=top_n, entry_rank_pct=0.80, exit_rank_pct=0.40
                )
            else:
                nxt = df.sort(["score", "stock_id"], descending=[True, False]).head(top_n)
            new = frozenset(nxt["stock_id"].to_list())
            churn += len(new ^ held)
            held = new
        return churn

    assert turnover(banded=True) < turnover(banded=False), (
        "在名次抖動的序列上，banding 必須嚴格降低換手；否則遲滯區沒有作用"
    )


def test_ac6_config_yaml_loads_as_banded_with_pinned_scalars() -> None:
    """AC-6：出貨的 YAML 確實是 banded_top_n，且門檻為事前登記的 0.90 / 0.70。"""
    from pathlib import Path

    from twquant.strategy.config import load_strategy

    root = Path(__file__).resolve().parents[1]
    cfg = load_strategy(root / "configs" / "strategies" / "ml_rank_band.yaml")
    assert cfg.selection.method == "banded_top_n"
    assert cfg.selection.entry_rank_pct == 0.90
    assert cfg.selection.exit_rank_pct == 0.70
    assert cfg.selection.top_n == 20
    # 與 ml_rank 同族但不同 scope，避免污染既有 trial 家族
    assert cfg.trial_scope == "ml_rank_h10_band"


def test_ac5_plain_top_n_path_is_untouched_by_held() -> None:
    """AC-5：既有 top_n 路徑不受 held 影響——T-069 的結果必須維持可重現。"""
    import twquant.factors.builtins  # noqa: F401  # 觸發因子註冊
    from twquant.strategy.build import _select
    from twquant.strategy.config import SignalConfig, StrategyConfig

    cfg = StrategyConfig(
        name="t076_ac5",
        signals=[SignalConfig(factor="price.mom_20d", weight=1.0)],
        selection=SelectionConfig(method="top_n", top_n=3),
    )
    scored = _TEN.with_columns(pl.lit(None).alias("date"))
    a = _select(scored, cfg, held=frozenset())
    b = _select(scored, cfg, held=frozenset({"1101", "1102", "1103"}))
    assert a.equals(b), "top_n 選股不得因持股而改變，否則舊回測不可重現"
