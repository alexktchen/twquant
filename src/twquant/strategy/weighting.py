"""組合加權的純函數（SPEC-013 §2.2.1 加權契約）。

抽成獨立模組是為了可單獨檢驗——D-061 的審查指出，先前規格在三處會導致靜默錯誤的
權重，而這三處都無法從 `build_targets` 的整合測試看出來：

1. 市值加權誤用 log 值比例（三個數量級的差距被壓成不到 2 倍）
2. 截頂只做一次（重分配會把別人推過上限）或不重新歸一（總曝險 < 1，等於偷藏現金）
3. 可行性以設定的 `top_n` 判斷（實際入選檔數不足時放行一個湊不到 1 的組合）

本模組無 IO、無狀態（黃金規則 10）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from twquant.errors import ConfigError

_SUM_TOL = 1e-9


def shifted_power_weights(scores: Sequence[float], *, p: float, score_min: float) -> list[float]:
    """`w_i ∝ (score_i − score_min) ** p`，正規化為總和 1。

    `score_min` 是**當日全宇宙**的最低分，不是入選名單的最低分：`rank_avg` 以
    signed weights 表達方向（正向百分位總和減去負向百分位總和），四因子分數落在
    [−0.25, +0.75]，**必然出現負分**，而 `score^p` 對負分無意義（p=2 時 −0.25 與
    +0.25 得到相同權重，方向反轉）。用入選名單的最小值平移則會把名單最後一名
    壓成 0 權重，等於少持一檔。

    平移**無條件**套用：條件式（「有負分才平移」）會讓同一個策略在不同日子適用
    不同公式，權重取決於當日是否恰好出現負分。

    全宇宙同分時分母為 0 → 退化為等權。橫斷面全同分是合法的資料狀態，
    不得除以 0，也不得 raise。
    """
    if not scores:
        return []
    shifted = [max(s - score_min, 0.0) for s in scores]
    powered = [x**p for x in shifted]
    total = sum(powered)
    if total <= 0.0:
        return [1.0 / len(scores)] * len(scores)
    return [x / total for x in powered]


def mktcap_weights(log_mktcaps: Sequence[float]) -> list[float]:
    """依市值比例加權。輸入是 **ln(市值)**（`fundamental.log_market_cap` 的回傳）。

    必須先 `exp` 還原成線性市值：直接按 log 值比例會把 1.5e6 與 4e3 的三個數量級
    差距壓成 14.2 比 8.3（權重 0.63 比 0.37，而正確值是 0.997 比 0.003）。

    **不得套用該因子的 `direction=-1`**——那是給因子排序用的（size 異象為負向），
    在加權時套用會讓大小市值反轉，是市值加權策略最嚴重的靜默錯誤。
    """
    if not log_mktcaps:
        return []
    linear = [math.exp(v) for v in log_mktcaps]
    total = sum(linear)
    if total <= 0.0:  # exp 恆正，僅防禦性；真發生代表輸入有 -inf
        return [1.0 / len(linear)] * len(linear)
    return [x / total for x in linear]


def cap_and_renormalize(raw: Sequence[float], cap: float) -> list[float]:
    """套單股上限後把剩餘權重按比例重分配給未觸頂者，**反覆至收斂**，總和維持 1。

    只截頂不重分配會讓總曝險 < 1（等於偷偷持有現金）；只重分配一次則可能把原本
    未觸頂的部位推過上限（例：[0.7, 0.25, 0.05] 上限 0.4，一輪後第二檔變成 0.5）。

    終止條件是**某一輪沒有任何新的部位觸頂**，不是固定迴圈次數。

    `len(raw) × cap < 1` 時權重數學上湊不到 1，直接 raise——這是配置錯誤，
    不是可以靜默降低曝險的情況（黃金規則 5）。判準用**實際傳入的檔數**而非
    設定的 `top_n`：設定 40 檔不代表當日有 40 檔通過濾網。
    """
    n = len(raw)
    if n == 0:
        return []
    if n * cap < 1.0 - _SUM_TOL:
        raise ConfigError(
            f"單股上限 {cap} × 實際入選 {n} 檔 = {n * cap:.4f} < 1，權重無法加總為 1。"
            "請放寬 max_weight_per_stock 或檢查宇宙濾網是否過嚴"
            "（注意：判準用的是實際入選檔數，不是設定的 top_n）。"
        )
    total = sum(raw)
    if total <= 0.0:
        return [1.0 / n] * n
    w = [x / total for x in raw]

    capped: set[int] = set()
    while True:
        newly = {i for i, x in enumerate(w) if i not in capped and x > cap + _SUM_TOL}
        if not newly:
            return w
        capped |= newly
        free = [i for i in range(n) if i not in capped]
        remaining = 1.0 - cap * len(capped)
        free_sum = sum(w[i] for i in free)
        out = list(w)
        for i in capped:
            out[i] = cap
        if not free or free_sum <= 0.0 or remaining <= 0.0:
            # 全部觸頂（n × cap == 1）或自由部位無權重可分：平均分配剩餘
            for i in free:
                out[i] = remaining / len(free) if free else 0.0
            return out
        for i in free:
            out[i] = w[i] / free_sum * remaining
        w = out


def fixed_slot_weights(n: int, *, slots: int, cap: float) -> list[float]:
    """固定格位：每檔拿 `1/slots`，**符合幾檔就投幾檔的資金，其餘留現金**。

    為什麼需要它：`equal` 永遠是 `1/n`，不論當日有幾檔符合都滿倉；而
    `cap_and_renormalize` 更把「總曝險 < 1」當成 bug（它的 docstring 寫
    「等於偷偷持有現金」），`n × cap < 1` 時直接 raise。於是**事件型策略
    無法表達**——「今天只有 4 檔符合就只投 4 檔」在既有框架裡不是選項，
    而是會讓回測崩掉的錯誤。

    掛零不是缺陷，是事件策略的正確行為：沒有訊號就不該持有部位。

    與 `equal` + `cap_and_renormalize` 的關鍵差異是**不重分配**——截頂後省下的
    資金留在現金，不會被推給其他部位。重分配會讓「少投一點」的意圖被抵銷。

    `n > slots` 時 raise：那會讓總曝險 > 1，而引擎沒有融資模型，
    產生的目標權重根本無法成交（呼叫端的 `_select` 應已限制在 top_n 內）。
    """
    if slots < 1:
        raise ConfigError(f"格位數須 ≥ 1，得到 {slots}")
    if cap <= 0.0:
        raise ConfigError(f"單股上限須 > 0，得到 {cap}")
    if n < 0:
        raise ConfigError(f"入選檔數不得為負，得到 {n}")
    if n > slots:
        raise ConfigError(
            f"入選 {n} 檔超過格位數 {slots}，總曝險會 > 1——引擎沒有融資模型，"
            "這樣的目標權重無法成交。應由 selection 先限制在 top_n 內。"
        )
    if n == 0:
        return []
    return [min(1.0 / slots, cap)] * n
