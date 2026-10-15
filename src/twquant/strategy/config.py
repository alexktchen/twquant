"""策略配置模型與載入（SPEC-003 §1）。

pydantic 模型（extra="forbid"）；載入時驗證：權重加總=1、因子名存在於註冊表
（AC-1，錯誤訊息附最相近的註冊名）。載入委派 twquant.config.load_config，本模組不做
原始 IO（維持策略層純度）。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from twquant.config import BaseConfig, load_config
from twquant.errors import ConfigError
from twquant.portfolio.config import VolTargetConfig


class PrefilterConfig(BaseConfig):
    """串接閘門：先用一個因子的橫斷面分位縮小池子，**再**用訊號因子在池內排序。

    設計意圖（使用者的話）：「ML 找出來的已經是相對優於大盤了，代表資金應該在裡面，
    再用因子訊號來判斷進出場點。」——把「資金該在哪個池子」與「何時進出」拆成兩層。

    為什麼這個用法值得測：ML 的長處是**排序整個橫斷面**（OOS RankIC +0.0452，
    16 個 walk-forward 窗有 15 個為正），短處是**挑不出最強的 20 檔**
    （零成本下對四個公開因子只贏 +1.24pp、t +0.33）。當閘門消費的是排序而非尾巴，
    剛好避開短處。**但這是假說不是結論**——`keep_pct` 之外的部分能不能被技術面
    有效篩選，要量了才知道。

    閘門在**訊號因子計算之前**執行，故訊號因子的橫斷面標準化是在縮小後的池子裡算的
    ——那正是「先挑再篩」的語意；若在全宇宙標準化，第二層就不是在池內比較。

    `keep_pct` 是保留比例（0 < p ≤ 1）。`side` 為 `top` 時保留分數最高的一段，
    方向已依因子的 `direction` 校正（低波動異象 direction=-1，故 top 是低波動那端）。
    """

    factor: str
    keep_pct: float = Field(gt=0.0, le=1.0)
    side: Literal["top", "bottom"] = "top"
    params: dict[str, object] = Field(default_factory=dict)


class UniverseConfig(BaseConfig):
    """選股宇宙過濾條件（SPEC-003 §2）。"""

    markets: list[str] = Field(default_factory=lambda: ["TWSE", "TPEX"])
    exclude_disposition: bool = True
    exclude_full_delivery: bool = True
    exclude_recent_ipo_days: int = 60
    min_adv_twd: float | None = 30_000_000  # 20 日成交金額中位數下限（**絕對**門檻）
    # D-062／T-163：**相對**流動性門檻——保留當日 ADV 在第 `min_adv_pctl` 百分位以上者。
    # 0.70 ＝ 保留前 30%，與現行絕對門檻的平均留存率（26.4%）對齊。
    #
    # 為什麼要它：絕對門檻的留存率有**九成八**由當年市場成交熱度解釋
    # （2015-06 起實測 r = +0.982），亦即宇宙大小是**行情的函數**而非公司品質的函數。
    # 壓力年宇宙縮四成、成分也不同——而 D-038 §5 的晉升條件裡有最大回撤，回撤正好
    # 發生在壓力年。等於在最需要穩定基準的時段換了一個池子。
    #
    # 實測（`docs/reports/universe-drift-p30-since2015.md`）：
    #   絕對門檻 留存率 19.2~34.4%（極差 15.2pp、標準差 5.29pp）
    #   前 30%   留存率 28.7~29.9%（極差  1.2pp、標準差 **0.42pp**）
    #
    # 與 `min_adv_twd` **互斥**（兩道流動性濾網疊起來說不清是哪一道在篩）；
    # 要用本欄位須把 `min_adv_twd` 顯式設為 null，不自動關掉——靜默改變既有配置的
    # 語意正是黃金規則 5 要消滅的事。
    min_adv_pctl: float | None = Field(default=None, gt=0.0, lt=1.0)
    min_price: float | None = None
    # D-029 衛生濾網：近窗出現跳價/交易日缺口（停牌復牌、減資、資料缺段）→ 排除
    max_jump_ratio: float | None = 0.35  # 相鄰列 |Δclose| 上限；None 停用
    # D-037：缺口以「相鄰兩列之間漏掉的開市日數」計，不用曆日——農曆年封關是全市場
    # 休市（2025 年 1/22→2/03 隔 12 曆日、0 個漏掉的開市日），用曆日會把整個宇宙同時
    # 判為資料事件。門檻 5 由舊值等效換算（10 曆日跨週末約當 6~7 個開市日）。
    max_gap_sessions: int | None = 5  # 相鄰列漏掉的開市日數上限；None 停用
    # D-059／T-119：品質硬濾網。TTM ROE 低於此值者在**排序之前**移出宇宙；
    # None（預設）＝停用，輸出與加入本欄位之前逐位元相同。
    # 事前寫定：ROE 為 null 一律排除——「不知道是不是品質股」不等於「是品質股」。
    min_roe: float | None = None
    # T-121／SPEC-013：市值排名帶。排名 1 = 當日市值最大。
    # **刻意用排名而非絕對市值**——T-120 已指出絕對門檻在長歷史上會漂移
    # （3,000 萬 TWD 在 2005 與 2026 是完全不同的門檻），排名依定義不漂移。
    # 排名母體是「當日有市值的四碼普通股全體」，不是流動性濾網之後的殘存集合：
    # 「市值前 100 大」是市場事實，不該隨我們調整 min_adv_twd 而變動。
    mktcap_rank_max: int | None = None  # 只留排名 ≤ 此值（大型股宇宙）
    mktcap_rank_min: int | None = None  # 只留排名 ≥ 此值（小型股宇宙）
    # T-122／SPEC-013 §3.4：月營收成長濾網。三個門檻是**外部先驗**（finlab 公開文章：
    # 連 3 月正成長、年增 10%~150%），非我們配適，故 D-060 禁止因回測難看而調整。
    # 上限刻意存在：排除減資、合併、基期異常造成的假成長。
    # 缺營收者一律排除（比照 min_roe，黃金規則 5 的精神）。
    min_revenue_yoy: float | None = None  # 最新已公布月營收 YoY 下限
    max_revenue_yoy: float | None = None  # 同上，上限
    min_revenue_streak: int | None = None  # 最近連續幾個「營收月份」YoY 為正
    hygiene_lookback: int = 60  # 衛生濾網回看列數

    @model_validator(mode="after")
    def _one_liquidity_gate_only(self) -> UniverseConfig:
        # 兩道流動性濾網同時開著，交集是「絕對且相對都過」——那既不是 D-062 的相對門檻，
        # 也不是原本的絕對門檻，而且留存率會重新耦合到行情上（絕對那道還在）。
        if self.min_adv_twd is not None and self.min_adv_pctl is not None:
            raise ValueError(
                f"min_adv_twd（{self.min_adv_twd:,.0f}）與 min_adv_pctl（{self.min_adv_pctl}）"
                "互斥：兩道流動性濾網疊起來說不清是哪一道在篩，且絕對那道會把行情耦合帶回來"
                "（D-062 實測 r = +0.982）。要用相對門檻請把 min_adv_twd 顯式設為 null"
            )
        return self

    @model_validator(mode="after")
    def _mktcap_ranks_positive(self) -> UniverseConfig:
        for name in ("mktcap_rank_max", "mktcap_rank_min"):
            v = getattr(self, name)
            if v is not None and v < 1:
                raise ValueError(f"{name} 須 ≥ 1（排名從 1 起算），得到 {v}")
        if self.min_revenue_streak is not None and self.min_revenue_streak < 1:
            raise ValueError(f"min_revenue_streak 須 ≥ 1，得到 {self.min_revenue_streak}")
        lo, hi = self.min_revenue_yoy, self.max_revenue_yoy
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"min_revenue_yoy({lo}) 不得大於 max_revenue_yoy({hi})")
        return self


class SignalConfig(BaseConfig):
    """單一因子訊號與其權重（SPEC-003 §1）。"""

    factor: str
    weight: float
    params: dict[str, object] = Field(default_factory=dict)


class SelectionConfig(BaseConfig):
    """選股方法（SPEC-003 §1；banded_top_n 見 SPEC-012 §1）。"""

    method: Literal["top_n", "threshold", "banded_top_n"] = "top_n"
    top_n: int | None = 40
    threshold: float | None = None
    # SPEC-012：遲滯門檻。刻意無預設——banded_top_n 必須顯式指定，
    # 靜默套預設等於讓事前登記的純量憑空出現。
    entry_rank_pct: float | None = None
    exit_rank_pct: float | None = None

    @model_validator(mode="after")
    def _banded_thresholds(self) -> SelectionConfig:
        if self.method != "banded_top_n":
            return self
        if self.entry_rank_pct is None or self.exit_rank_pct is None:
            raise ValueError(
                "method=banded_top_n 須同時指定 entry_rank_pct 與 exit_rank_pct"
                f"（目前 entry_rank_pct={self.entry_rank_pct}、"
                f"exit_rank_pct={self.exit_rank_pct}）"
            )
        if not 0.0 < self.exit_rank_pct < self.entry_rank_pct <= 1.0:
            raise ValueError(
                "banded_top_n 門檻須滿足 0 < exit_rank_pct < entry_rank_pct ≤ 1"
                f"（得到 exit_rank_pct={self.exit_rank_pct}、"
                f"entry_rank_pct={self.entry_rank_pct}）"
            )
        return self


class RebalanceConfig(BaseConfig):
    """再平衡排程（SPEC-003 §1）。"""

    schedule: Literal["daily", "weekly", "biweekly", "monthly"] = "weekly"
    day: str = "MON"  # weekly / biweekly 用（星期）
    day_of_month: int | None = None  # monthly 用
    execution: Literal["next_open", "close"] = "next_open"


# 不需要部位就能判斷的規則：可以用來擋進場。停損／trailing_ma 相對於買進價與持有期高點，
# 對還沒買的股票沒有定義，故不在此列。`backtest.exits.entry_blocked` 用同一份名單。
# bb_lower_stop 雖不需要部位，**刻意不列入**：D-159 要求各臂進場逐字相同，只換出場。
POSITION_FREE_KINDS: frozenset[str] = frozenset({"below_ma", "score_below"})


class ExitRuleConfig(BaseConfig):
    """一條每日出場規則（T-231／D-114 §四）。收盤判斷、隔日開盤出清、出清後留現金。

    三種 `kind` 各用各的參數；填了不屬於該 kind 的參數即 raise——
    寫了卻不生效的配置正是 `scripts/audit_silent.py` 要抓的那一類。
    """

    kind: Literal[
        "stop_loss",
        "below_ma",
        "score_below",
        "trailing_ma",
        "trailing_pct",
        "take_profit",
        "time_stop",
        "bb_lower_stop",
        "atr_trailing",
        "ma_lock",
    ]
    # stop_loss：收盤（含已收股利）較本輪均價的**跌**幅；take_profit：**漲**幅。皆為正的小數
    pct: float | None = None
    sessions: int | None = None  # time_stop：買進後持有滿幾個交易日即賣（買進日算第 0 天）
    window: int | None = (
        None  # below_ma／trailing_ma／ma_lock：均線天數；bb_lower_stop／atr_trailing：統計窗
    )
    # bb_lower_stop：幾倍標準差（布林下軌）；atr_trailing：持有期最高收盤往下幾倍 ATR（D-159）
    mult: float | None = None
    # trailing_ma／trailing_pct：本輪未實現報酬**曾經**達到此值之後才上膛（D-120 S0：讓獲利奔跑）
    min_peak_gain: float | None = None
    factor: str | None = None  # score_below：因子名（如 ml.lgbm_lw_net_20d）
    params: dict[str, object] = Field(default_factory=dict)  # score_below：傳給因子的參數
    threshold: float | None = None  # score_below：因子值低於此即出場

    @model_validator(mode="after")
    def _params_match_kind(self) -> ExitRuleConfig:
        given = {
            "pct": self.pct is not None,
            "window": self.window is not None,
            "mult": self.mult is not None,
            "min_peak_gain": self.min_peak_gain is not None,
            "sessions": self.sessions is not None,
            "factor": self.factor is not None,
            "threshold": self.threshold is not None,
            "params": bool(self.params),
        }
        allowed = {
            "stop_loss": {"pct"},
            "below_ma": {"window"},
            "trailing_ma": {"window", "min_peak_gain"},
            # D-143 §四 的 ratchet：上膛後自持有期高點回落 pct 即出場（與均線無關）
            "trailing_pct": {"pct", "min_peak_gain"},
            "take_profit": {"pct"},
            "time_stop": {"sessions"},
            "score_below": {"factor", "threshold", "params"},
            # D-159：布林下軌停損、ATR 移動停利、均線鎖利（後兩者線高於均價才生效）
            "bb_lower_stop": {"window", "mult"},
            "atr_trailing": {"window", "mult"},
            "ma_lock": {"window"},
        }[self.kind]
        required = allowed - {"params"}
        missing = sorted(k for k in required if not given[k])
        extra = sorted(k for k, on in given.items() if on and k not in allowed)
        if missing or extra:
            raise ValueError(f"exit_rules[{self.kind}] 缺 {missing}、多 {extra}")
        if self.pct is not None and not 0.0 < self.pct < 1.0:
            raise ValueError(f"{self.kind}.pct 須介於 (0, 1)，得到 {self.pct}")
        if self.sessions is not None and self.sessions < 1:
            raise ValueError(f"time_stop.sessions 須 >= 1，得到 {self.sessions}")
        if self.window is not None and self.window < 2:
            raise ValueError(f"{self.kind}.window 須 >= 2，得到 {self.window}")
        if self.mult is not None and self.mult <= 0.0:
            raise ValueError(f"{self.kind}.mult 須 > 0，得到 {self.mult}")
        if self.min_peak_gain is not None and self.min_peak_gain <= 0.0:
            raise ValueError(f"{self.kind}.min_peak_gain 須 > 0，得到 {self.min_peak_gain}")
        return self


class ExposureTargetConfig(BaseConfig):
    """每日曝險目標（T-286／D-146 §二）：把 V1 的 `vol_target_scaling` 帶進每日補位模式。

    V1 的 overlay 只在月頻再平衡路徑生效，每日補位從不呼叫它。這裡用**同一個**波動估計
    （`strategy.overlays._realized_vol`，持股等權組合近 window 日的年化波動）算倍數
    `mult = clip(target_vol / realized, min_mult, 1.0)`：新進場每格 `pv × mult / n`；
    現有持股在 `positions_value / pv` 超過 `mult` **一格（1/n）**以上時等比減碼到 `mult`。
    帶寬取一格是推導不是調參——小於一格的差距只是整張進位的噪音，追它會把周轉率灌爆。
    """

    target_vol: float = Field(gt=0.0)  # 年化目標波動
    window: int = Field(ge=2)  # 波動估計窗（交易日）
    min_mult: float = Field(gt=0.0, le=1.0)  # 倍數下限；避免極端行情下曝險歸零而完全踏空


class ConditionalVolConfig(BaseConfig):
    """條件式減碼（D-149）：把 D-004／SPEC-005 的條件式波動目標接到每日補位。

    `vol_target` 逐字沿用 `configs/portfolio.yaml`（測試守著兩邊相同）：觸發序列的波動落在
    自身 `percentile_window_days` 日的第 `reduce_above_pct` 百分位以上、**且**高於 `annual_vol`，
    才把曝險縮到 `annual_vol ÷ 持股波動`；其餘 1 倍。`trigger` 決定看哪條序列：
    `holdings`＝目前持股的等權報酬（F1），`index`＝`index_id` 的指數報酬（F2）。
    倍數一律以持股波動計算——兩臂唯一的差別是觸發源。減碼的執行沿用 T-286。
    """

    trigger: Literal["holdings", "index"]
    index_id: str | None = None  # trigger=index 時必填（例如 TAIEX）
    vol_target: VolTargetConfig

    @model_validator(mode="after")
    def _consistent(self) -> ConditionalVolConfig:
        if (self.trigger == "index") != (self.index_id is not None):
            raise ValueError("trigger=index 必須給 index_id；trigger=holdings 不得給 index_id")
        if not self.vol_target.enabled:
            raise ValueError(
                "vol_target.enabled=false 會讓整段設定變成 no-op；要關掉請拿掉 conditional_vol"
            )
        if self.vol_target.estimator.method != "ewma":
            raise ValueError(
                f"估計器只實作 ewma（D-004），收到 {self.vol_target.estimator.method!r}"
            )
        if self.vol_target.max_leverage > 1.0:
            raise ValueError("每日補位只減碼不加槓桿（D-149 §二）：max_leverage 不得大於 1")
        return self


class InverseVolSizingConfig(BaseConfig):
    """反波動部位（D-149 F3）：每格 × clip(σ_ref ÷ σ_i, floor, 1.0)，只降不升。

    σ_i＝該檔訊號日的 `factor` 值，σ_ref＝當晚候選前 n 名的 σ 中位數。上限 1.0 沿用
    D-004「只降不升」，`floor` 沿用 D-009 的 REDUCED 檔位——兩個邊界都借自既有登記。
    """

    factor: str  # σ 的來源因子（D-149：price.vol_20d）
    floor: float = Field(gt=0.0, le=1.0)


class SlotExcludeConfig(BaseConfig):
    """補位模式的進場排除規則（D-172）：因子原始值 ≥ `min_value` 的股票當天不得進場。

    只擋進場、不賣持股（與 entry filter 同語意）。因子值為 null＝狀態未知 → 不排除
    （排除是「已知處於某狀態」，不是「不確定就不買」；未知的比例由研究報告另列）。
    """

    factor: str
    min_value: float
    params: dict[str, object] = Field(default_factory=dict)


class SlotRequireConfig(BaseConfig):
    """補位模式的進場必要條件（D-174）：因子原始值 ≥ `min_value` 才是候選；值為 null（資料不足）→ 不是候選。

    與 `entry_filters` 的 score_below 不同：那邊查不到分數「不擋」（缺分數不等於模型說賣），
    這邊查不到就「不買」——必要條件沒被證實，就不算滿足。
    """

    factor: str
    min_value: float
    params: dict[str, object] = Field(default_factory=dict)


class SlotsConfig(BaseConfig):
    """每日補位模式（T-247／D-120 §五）。給了它，`rebalance`／`selection`／`weighting` 一律不用。

    進場分數＝唯一那個 signal 因子的**原始值**（不做橫斷面標準化）——門檻是絕對的，
    這正是「沒有夠好的候選就空手」與相對排名的差別。
    """

    n: int = Field(ge=1)  # 滿手檔數；每格＝成交當時權益 ÷ n
    entry_threshold: float  # 因子原始值 >= 此值才是候選
    exposure_target: ExposureTargetConfig | None = None  # T-286：None ＝ 永遠滿倉（既有行為）
    conditional_vol: ConditionalVolConfig | None = None  # D-149 F1／F2
    inverse_vol: InverseVolSizingConfig | None = None  # D-149 F3
    exclude: list[SlotExcludeConfig] = Field(default_factory=list)  # D-172：進場排除規則
    require: list[SlotRequireConfig] = Field(default_factory=list)  # D-174：進場必要條件

    @model_validator(mode="after")
    def _one_exposure_rule(self) -> SlotsConfig:
        if self.exposure_target is not None and self.conditional_vol is not None:
            raise ValueError(
                "exposure_target 與 conditional_vol 只能擇一：兩者寫的是同一個曝險倍數，會互相覆蓋"
            )
        return self


class MarketGateConfig(BaseConfig):
    """大盤緩跌閘門（D-184，規則沿用 D-124 §三 b1）：指數緩跌的日子不開新倉。

    分類用 `ml.regime.market_regime`（一般／緩跌／恐慌），只用當天（含）以前的指數收盤：
    指數收盤在 `ma_window` 日均線之下＝弱勢；弱勢且 `vol_window` 日報酬標準差落在自身過去
    `vol_lookback` 日的第 `vol_pctl` 分位以上＝恐慌（照常可買）；弱勢但不恐慌＝緩跌（不開新倉）。
    補位模式：緩跌那晚不排新進場，持股照出場規則走。排程換股：緩跌的換股日只賣不買
    （新進與加碼都取消），錢留現金到下一個換股日。分類算不出來（暖機不足、缺當天收盤）一律 raise。
    """

    index_id: str = "TAIEX"
    ma_window: int = Field(ge=2)
    vol_window: int = Field(ge=2)
    vol_lookback: int = Field(ge=2)
    vol_pctl: float = Field(gt=0.0, lt=1.0)


class StrategyConfig(BaseConfig):
    """完整策略配置（SPEC-003 §1）。同一份 YAML 供回測與生產。"""

    name: str
    description: str = ""
    trial_scope: str | None = None
    universe: UniverseConfig = UniverseConfig()
    signals: list[SignalConfig]
    combine: Literal["zscore_weighted", "rank_avg", "ic_weighted"] = "zscore_weighted"
    selection: SelectionConfig = SelectionConfig()
    # T-123／SPEC-013 §2.2.1：score_pow 與 mktcap 為本次新增；score_prop 是 score_pow 的
    # p=1 特例（同一套實作）；inv_vol 仍未實作，本規格用不到，不得順手補（CLAUDE.md §7）。
    # `fixed_slot`：每檔固定 1/top_n，**符合幾檔就投幾檔的資金，其餘留現金**。
    # 事件型策略必需——`equal` 永遠是 1/n 故永遠滿倉，無法表達「今天沒訊號就別買」。
    weighting: Literal["equal", "score_prop", "score_pow", "inv_vol", "mktcap", "fixed_slot"] = (
        "equal"
    )
    # score_pow 的指數。預設 1.0 ＝ score_prop，故既有配置行為不變；
    # SPEC-013 的 L4 與 B 軌明確用 2.0，YAML 須顯式寫出，不得依賴預設值。
    weighting_power: float = 1.0
    # D-061／R12：`rank_avg` 對**存在的列**求和，故缺少負向因子（如低波動）的股票
    # 拿不到那個負分、分數虛高。開啟後在選股前剔除因子不齊者。
    # 預設 False 是為了保住既有 15 個策略配置的逐位元行為——新的多因子配置須顯式開啟。
    require_all_factors: bool = False
    rebalance: RebalanceConfig = RebalanceConfig()
    # 串接閘門（可多層）：依序縮小池子，全部在訊號因子計算之前
    prefilter: list[PrefilterConfig] = Field(default_factory=list)
    overlays: list[str] = Field(default_factory=list)
    # 每個 overlay 的參數，key 為 overlay 名。**必須真的傳到 overlay**：
    # `build.py` 原本寫死傳 `{}`，那樣配置檔寫什麼都不生效且不會報錯
    # ——正是 `scripts/audit_silent.py` 的 no-op 配置那一類缺陷。
    overlay_params: dict[str, dict[str, object]] = Field(default_factory=dict)
    # 每日出場規則（T-231）。空＝既有行為；任一條觸發即隔日開盤出清。
    exit_rules: list[ExitRuleConfig] = Field(default_factory=list)
    # 只擋進場、**不賣出持股**的條件（D-137）。與 exit_rules 分開是必要的：
    # 實測同一條「站上 MA20」當進場濾網是每筆期望值 +0.96pp，當出場規則卻是 −80%
    # （D-136／D-117：任何把波動剪掉的規則會連上漲一起剪掉）。共用同一個欄位就做不到這個區分。
    # 只接受不需要部位就能判斷的規則；stop_loss 這類相對於買進價的條件對還沒買的股票沒有定義。
    entry_filters: list[ExitRuleConfig] = Field(default_factory=list)
    # 每日補位模式（T-247）。None＝既有的排程再平衡模式。
    slots: SlotsConfig | None = None
    # D-184：大盤緩跌時不開新倉。None＝沒有閘門（既有行為）
    market_gate: MarketGateConfig | None = None
    max_weight_per_stock: float = 0.05

    @model_serializer(mode="wrap")
    def _omit_unset_gate(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, object]:
        """D-184：沒設閘門時不輸出 `market_gate` 鍵。

        研究登記簿的 params_hash、第二保留期的凍結指紋都從序列化結果算——多一個 `null` 鍵，
        既有 175+ 個試驗的 hash 與三份登記的指紋就會一起改變（T-231、D-149 都踩過）。
        設了閘門照樣輸出，那才是行為改變。
        """
        data: dict[str, object] = handler(self)
        if data.get("market_gate", 0) is None:
            del data["market_gate"]
        return data

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> StrategyConfig:
        if not self.signals:
            raise ValueError("signals 不可為空")
        total = sum(s.weight for s in self.signals)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"signals 權重加總須為 1，實際 {total}")
        bad = sorted({r.kind for r in self.entry_filters} - POSITION_FREE_KINDS)
        if bad:
            raise ValueError(
                f"entry_filters 只接受不需要部位就能判斷的規則 "
                f"{sorted(POSITION_FREE_KINDS)}，得到 {bad}"
                "——停損這類相對於買進價的條件，對還沒買的股票沒有定義"
            )
        if self.slots is not None and len(self.signals) != 1:
            raise ValueError(
                f"slots 模式須恰好一個 signal（進場分數取其原始值），得到 {len(self.signals)} 個"
            )
        return self


def _check_factors_registered(cfg: StrategyConfig) -> None:
    """驗證每個 signal.factor 都已註冊；否則 raise 並附最相近的名字（AC-1）。"""
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.registry import list_factors

    known = list_factors()
    for s in cfg.signals:
        if s.factor not in known:
            near = difflib.get_close_matches(s.factor, known, n=1)
            hint = f"，最相近：{near[0]}" if near else ""
            raise ConfigError(f"策略 {cfg.name} 引用未註冊因子：{s.factor}{hint}")


def load_strategy(path: str | Path) -> StrategyConfig:
    """讀策略 YAML → 驗證（權重加總、因子存在）→ 回傳配置。"""
    cfg = load_config(path, StrategyConfig)
    _check_factors_registered(cfg)
    return cfg
