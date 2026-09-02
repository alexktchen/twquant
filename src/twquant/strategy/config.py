"""策略配置模型與載入（SPEC-003 §1）。

pydantic 模型（extra="forbid"）；載入時驗證：權重加總=1、因子名存在於註冊表
（AC-1，錯誤訊息附最相近的註冊名）。載入委派 twquant.config.load_config，本模組不做
原始 IO（維持策略層純度）。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from twquant.config import BaseConfig, load_config
from twquant.errors import ConfigError


class UniverseConfig(BaseConfig):
    """選股宇宙過濾條件（SPEC-003 §2）。"""

    markets: list[str] = Field(default_factory=lambda: ["TWSE", "TPEX"])
    exclude_disposition: bool = True
    exclude_full_delivery: bool = True
    exclude_recent_ipo_days: int = 60
    min_adv_twd: float | None = 30_000_000  # 20 日成交金額中位數下限
    min_price: float | None = None
    # D-029 衛生濾網：近窗出現跳價/交易日缺口（停牌復牌、減資、資料缺段）→ 排除
    max_jump_ratio: float | None = 0.35  # 相鄰列 |Δclose| 上限；None 停用
    # D-037：缺口以「相鄰兩列之間漏掉的開市日數」計，不用曆日——農曆年封關是全市場
    # 休市（2025 年 1/22→2/03 隔 12 曆日、0 個漏掉的開市日），用曆日會把整個宇宙同時
    # 判為資料事件。門檻 5 由舊值等效換算（10 曆日跨週末約當 6~7 個開市日）。
    max_gap_sessions: int | None = 5  # 相鄰列漏掉的開市日數上限；None 停用
    hygiene_lookback: int = 60  # 衛生濾網回看列數


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


class StrategyConfig(BaseConfig):
    """完整策略配置（SPEC-003 §1）。同一份 YAML 供回測與生產。"""

    name: str
    description: str = ""
    trial_scope: str | None = None
    universe: UniverseConfig = UniverseConfig()
    signals: list[SignalConfig]
    combine: Literal["zscore_weighted", "rank_avg", "ic_weighted"] = "zscore_weighted"
    selection: SelectionConfig = SelectionConfig()
    weighting: Literal["equal", "score_prop", "inv_vol"] = "equal"
    rebalance: RebalanceConfig = RebalanceConfig()
    overlays: list[str] = Field(default_factory=list)
    max_weight_per_stock: float = 0.05

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> StrategyConfig:
        if not self.signals:
            raise ValueError("signals 不可為空")
        total = sum(s.weight for s in self.signals)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"signals 權重加總須為 1，實際 {total}")
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
