"""T-123：組合加權（SPEC-013 §2.2.1 加權契約，AC-5/6/7/13/14）。

這裡鎖死的每一條，都是 D-061 審查指出「照原規格寫就會寫錯」的地方：

1. **市值加權用 `exp(value)`**：`fundamental.log_market_cap` 回傳 ln(市值)。
   按 log 值比例會把 1.5e6 vs 4e3 的三個數量級差距壓成 14.2 vs 8.3。
2. **截頂後要重新歸一**，且重分配要**反覆**——一次重分配可能讓別人超過上限。
3. **可行性用實際檔數**：設定 40 檔不代表當日有 40 檔通過濾網。
4. **`score_pow` 的平移無條件套用**，且 shift 取**全宇宙**最小值——取選中名單的
   最小值會把名單最後一名壓成權重 0，等於少持一檔。
"""

from __future__ import annotations

import math

import pytest

from twquant.errors import ConfigError
from twquant.strategy.weighting import (
    cap_and_renormalize,
    mktcap_weights,
    shifted_power_weights,
)

_TOL = 1e-12


def test_ac7_cap_redistributes_the_excess_instead_of_dropping_it() -> None:
    """截頂不是把超出的部分丟掉——那會讓總權重 < 1（等於偷偷持有現金）。"""
    got = cap_and_renormalize([0.5, 0.3, 0.2], cap=0.4)
    assert got == pytest.approx([0.4, 0.36, 0.24], abs=_TOL)
    assert sum(got) == pytest.approx(1.0, abs=1e-9)


def test_ac7_redistribution_repeats_until_no_new_position_is_capped() -> None:
    """一次重分配會把 0.25 推到 0.5（超過上限 0.4）→ 必須再跑一輪。

    只截頂一次的實作在這裡會回傳 [0.4, 0.5, 0.1]，違反單股上限而不自知。
    """
    got = cap_and_renormalize([0.7, 0.25, 0.05], cap=0.4)
    assert got == pytest.approx([0.4, 0.4, 0.2], abs=_TOL)
    assert max(got) <= 0.4 + 1e-9
    assert sum(got) == pytest.approx(1.0, abs=1e-9)


def test_ac7_no_cap_binding_leaves_weights_untouched() -> None:
    """未觸頂時必須逐位元原樣返回——既有等權策略（1/n ≤ cap）不得因本次改動而變。"""
    raw = [0.25, 0.25, 0.25, 0.25]
    assert cap_and_renormalize(raw, cap=0.5) == raw


def test_ac14_infeasible_cap_raises_and_names_both_numbers() -> None:
    """檔數 × 上限 < 1 時，權重數學上湊不到 1，必須 raise 而非靜默回傳低於 1 的總和。"""
    with pytest.raises(ConfigError) as exc:
        cap_and_renormalize([0.5, 0.5], cap=0.4)
    msg = str(exc.value)
    assert "2" in msg and "0.4" in msg, f"錯誤訊息須同時指出實際檔數與上限，得到：{msg}"


def test_ac14_exactly_feasible_is_allowed() -> None:
    """恰好等於 1（如 20 檔 × 5%）是合法的——既有 ml_net_h20_m 正是這一格。"""
    got = cap_and_renormalize([1 / 20] * 20, cap=0.05)
    assert sum(got) == pytest.approx(1.0, abs=1e-9)


def test_ac13_shift_uses_universe_min_not_selected_min() -> None:
    """全宇宙最低分是 −0.25（`rank_avg` 的 signed weights 必然產生負分）。

    若改用選中名單的最小值（0.25），最後一名平移後為 0 → 權重 0，等於少持一檔。
    """
    picked = [0.75, 0.50, 0.25]
    got = shifted_power_weights(picked, p=2.0, score_min=-0.25)
    # 平移後 [1.0, 0.75, 0.5] → 平方 [1, 0.5625, 0.25] → 總和 1.8125
    assert got == pytest.approx([1 / 1.8125, 0.5625 / 1.8125, 0.25 / 1.8125], abs=_TOL)
    assert min(got) > 0.0, "全宇宙平移後，入選者都應保有實質正權重"


def test_ac13_shift_is_unconditional_even_when_all_scores_are_positive() -> None:
    """不是「有負分才平移」——條件式會讓同一策略在不同日子適用不同公式。"""
    picked = [0.75, 0.50]
    with_shift = shifted_power_weights(picked, p=2.0, score_min=0.25)
    naive_square = [0.75**2 / (0.75**2 + 0.5**2), 0.5**2 / (0.75**2 + 0.5**2)]
    assert with_shift != pytest.approx(naive_square, abs=1e-6), (
        "分數全正時仍須平移；若與直接平方相同，代表平移被條件式跳過了"
    )


def test_ac5_p_changes_the_concentration() -> None:
    """p 是集中度旋鈕：p=1 等同 score_prop，p 越大越集中在高分股。"""
    picked = [0.75, 0.50, 0.25]
    w1 = shifted_power_weights(picked, p=1.0, score_min=-0.25)
    w2 = shifted_power_weights(picked, p=2.0, score_min=-0.25)
    assert w1 == pytest.approx([1 / 2.25, 0.75 / 2.25, 0.5 / 2.25], abs=_TOL)
    assert w2[0] > w1[0] and w2[-1] < w1[-1], "p=2 應比 p=1 更集中"


def test_ac13_zero_denominator_degrades_to_equal_weight() -> None:
    """全宇宙同分 → 平移後全為 0 → 不得除以 0，也不得 raise（同分是合法資料狀態）。"""
    got = shifted_power_weights([0.4, 0.4, 0.4], p=2.0, score_min=0.4)
    assert got == pytest.approx([1 / 3, 1 / 3, 1 / 3], abs=_TOL)


def test_ac6_mktcap_weights_use_linear_value_not_the_log() -> None:
    """因子回傳 ln(市值)；直接按 log 值比例會把三個數量級的差距壓成不到 2 倍。"""
    logs = [math.log(1.5e6), math.log(4e3)]
    got = mktcap_weights(logs)
    total = 1.5e6 + 4e3
    assert got == pytest.approx([1.5e6 / total, 4e3 / total], abs=1e-9)
    # 若誤用 log 值比例，大檔權重只有 0.63；正確值 > 0.99
    assert got[0] > 0.99, f"用了 log 值比例（大檔權重僅 {got[0]:.3f}）"


def test_ac6_mktcap_never_inverts_large_and_small() -> None:
    """`fundamental.log_market_cap` 的 `direction=-1` 是給因子排序用的。

    加權時若誤套方向，大小市值會反轉——那是市值加權策略最嚴重的靜默錯誤。
    """
    got = mktcap_weights([math.log(1e9), math.log(1e6)])
    assert got[0] > got[1], "大市值必須拿到較大權重"


# ── 固定格位加權：讓「訊號少就少投」變成可表達的行為 ──────────────────────


def test_ac1_fixed_slot_holds_cash_when_signals_are_scarce() -> None:
    """符合 4 檔、格位 20 → 只投 4/20 = 20%，其餘 80% 是現金。

    既有的 `equal` 永遠是 `1/n`，不論當日有幾檔符合都滿倉——事件型策略因此
    **無法表達**「今天沒訊號就別交易」。而 `cap_and_renormalize` 更直接把
    「總曝險 < 1」當成 bug（docstring：「等於偷偷持有現金」），
    `n × cap < 1` 時還會 raise，於是稀疏事件日會讓整個回測崩掉。

    掛零不是缺陷，是事件策略的正確行為。
    """
    from twquant.strategy.weighting import fixed_slot_weights

    w = fixed_slot_weights(4, slots=20, cap=0.10)
    assert w == [pytest.approx(0.05)] * 4
    assert sum(w) == pytest.approx(0.20), "只投 20%，不得補到滿倉"


def test_ac2_fixed_slot_is_fully_invested_when_signals_are_plentiful() -> None:
    """符合 20 檔、格位 20 → 滿倉，與 `equal` 等價。"""
    from twquant.strategy.weighting import fixed_slot_weights

    w = fixed_slot_weights(20, slots=20, cap=0.10)
    assert sum(w) == pytest.approx(1.0)
    assert w == [pytest.approx(0.05)] * 20


def test_ac3_fixed_slot_never_exceeds_the_cap_and_never_redistributes() -> None:
    """單股上限仍生效，但**不重分配**——重分配會把省下的現金又投出去。"""
    from twquant.strategy.weighting import fixed_slot_weights

    w = fixed_slot_weights(3, slots=5, cap=0.10)  # 1/5 = 0.20 > cap 0.10
    assert w == [pytest.approx(0.10)] * 3
    assert sum(w) == pytest.approx(0.30), "截頂後不得把差額補給其他部位"


def test_ac4_fixed_slot_with_no_candidates_is_all_cash() -> None:
    """一檔都不符合 → 空手。這是事件策略最重要的一種狀態，不得 raise。"""
    from twquant.strategy.weighting import fixed_slot_weights

    assert fixed_slot_weights(0, slots=20, cap=0.10) == []


def test_ac5_fixed_slot_rejects_more_names_than_slots() -> None:
    """入選數超過格位數是呼叫端的錯（`_select` 應已限制在 top_n 內）。

    靜默容忍會讓總曝險 > 1，而引擎沒有融資模型——那是一個無法成交的目標權重。
    """
    from twquant.errors import ConfigError
    from twquant.strategy.weighting import fixed_slot_weights

    with pytest.raises(ConfigError, match="格位"):
        fixed_slot_weights(21, slots=20, cap=0.10)
