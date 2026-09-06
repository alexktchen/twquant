"""配置載入與驗證（SPEC-000 §配置）。

全部 YAML，載入即用 pydantic 驗證；未知欄位 = 錯誤（extra="forbid"）。各 config
的權威欄位定義在對應 SPEC（清單見 SPEC-000）。本模組提供載入機制與 system.yaml
的最小模型；其餘配置模型在各自任務加入。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from twquant.errors import ConfigError


class BaseConfig(BaseModel):
    """所有配置模型的基底：禁止未知欄位，強制顯式 schema。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRate(BaseConfig):
    """單一來源的限流設定（滑動窗語意，SPEC-001 §7）。"""

    max_requests: int
    per_seconds: float


class QualityGateConfig(BaseConfig):
    """品質閘門閾值（SPEC-001 §5）。"""

    min_rows_ratio: float = 0.8
    inst_cross_check_tol: float = 0.005
    disk_min_gb: float = 50.0


class SystemConfig(BaseConfig):
    """system.yaml 的最小模型（SPEC-001 為權威來源，後續任務擴充）。"""

    data_dir: str
    source_rates: dict[str, SourceRate]
    quality_gate: QualityGateConfig = QualityGateConfig()


# D-020 綜合分有效子集的預設值（serving.yaml.composite_factors 缺省時後備）。
# 權威值在 configs/serving.yaml；此常數僅為 pydantic 預設與 snapshot fallback 的單一來源。
DEFAULT_COMPOSITE_FACTORS: tuple[str, ...] = (
    "price.reversal_5d",
    "price.residual_reversal_5d",
    "fundamental.revenue_yoy",
)


class CloudConfig(BaseConfig):
    """雲端供應層（D-030：Supabase Free 滾動窗口，T-055）。"""

    dsn_env: str = "TWQUANT_SUPABASE_DSN"  # Supabase transaction pooler DSN 的環境變數名
    # 大表只留符合此 pattern 的股票（四碼普通股，同 screener 宇宙）；只套用在 pattern_tables
    stock_pattern: str = "^[1-9][0-9]{3}$"
    pattern_tables: tuple[str, ...] = ("prices_daily", "broker_trades")
    # 滾動窗口（交易日數）；未列出的表全史保留（小表）。硬下限＝因子最大回看（有測試把關）
    windows: dict[str, int] = {}
    # 雲端不算的因子：需要的歷史超過該表的滾動窗（含 expanding 統計）。列在這裡＝明確承認
    # 「本機研究用、雲端沒有」，而非讓雲端在殘缺窗口上算出看似正常的數字。test_cloud 把關：
    # 每個被排除的因子都必須真的超窗，清單不能拿來藏錯。
    exclude_factors: tuple[str, ...] = ()


class ServingConfig(BaseConfig):
    """serving.yaml 的模型（SPEC-009 §1 為權威）。PG 供應層＝衍生、可重建、僅供展示。"""

    enabled: bool = False  # 總開關；false 時 API 一律走 DuckDB 路徑
    dsn_env: str = "TWQUANT_PG_DSN"  # 存 DSN 的環境變數名（DSN 本體只在 .env）
    pg_schema: str = "serving"  # PG schema 名（避開 pydantic BaseModel.schema 遮蔽）
    pool_min: int = 1
    pool_max: int = 8
    factor_history_days: int = 1100  # factor_values 保留窗（≈3 年）
    # D-035：不設 DuckDB 降級——雲端無資料湖，PG 不可用即 503（不假裝有資料，黃金規則 5）
    # D-020：綜合分（composite_daily）只納入的實證有效子集（非全因子等權）。
    composite_factors: tuple[str, ...] = DEFAULT_COMPOSITE_FACTORS
    # D-030：雲端滾動窗口（Supabase）。本機部署不受影響（僅 seed/prune/GHA 讀取）。
    cloud: CloudConfig = CloudConfig()


class ScreenerPool(BaseConfig):
    """單一選股池的展示定義（規則屬程式碼，key 對應 screener.py 的條件；SPEC-007 §6）。"""

    key: str
    name: str
    rule: str
    note: str = ""


class ScreenerWindows(BaseConfig):
    """選股池指標的回看窗（交易日）。"""

    conc: int = 10  # 主力短窗集中度
    accum: int = 60  # 主力長窗累積
    ma_fast: int = 20
    ma_slow: int = 60
    amt: int = 20  # 流動性均額窗


class ScreenerConfig(BaseConfig):
    """screener.yaml 的模型（SPEC-007 §6 為權威）。"""

    liquidity_floor_twd: float = 20_000_000.0
    windows: ScreenerWindows = ScreenerWindows()
    pct_hi: float = 0.9  # 「前 10%」門檻（宇宙百分位）
    pct_lo: float = 0.1  # 「後 10%」門檻
    yoy_min: float = 0.2  # 月營收 YoY 下限
    streak_days: int = 30  # 連續在池天數的回看窗
    pools: tuple[ScreenerPool, ...] = ()


class MlLabelConfig(BaseConfig):
    """標籤定義（SPEC-010 §1/§2、SPEC-011 §1）。"""

    execution: Literal["next_open"] = "next_open"
    excess: Literal["market_mean"] = "market_mean"
    transform: Literal["rank_pct", "cost_adjusted_return"] = "rank_pct"
    adv_window_sessions: int = Field(default=20, ge=1)
    position_notional_twd: float = Field(default=500_000.0, gt=0.0)
    downside_penalty: float = Field(default=0.25, ge=0.0)
    # T-098／D-054：標籤是否含股利（總報酬）。**None ＝ 未表態，`ml train` 拒絕執行**
    # ——未還原時 6~8 月的 h=20 標籤有 38.2% 樣本換分位、原「最差一成」17.9% 是除息
    # 假象；若給預設值，忘了寫的配置會靜默沿用被污染的標籤（黃金規則 5）。
    total_return: bool | None = None
    # T-144／T-139：標籤的出場列與當前列的**開市日跨距**上限；超過即設 null。
    # `shift` 是列位移，某檔中間缺 N 個開市日時「h 日報酬」實際橫跨數月而不帶標記
    # ——它只是一個看起來很大的數字，會被模型當成真實極端報酬學進去。
    # None ＝不啟用（既有配置逐位元不變）；啟用與否由配置顯式決定。
    max_span_sessions: int | None = Field(default=None, ge=1)


class MlSplitConfig(BaseConfig):
    """walk-forward 切分（SPEC-010 §3）。年/月 → 交易日的換算基數也是配置（無魔術數字）。"""

    train_min_years: float = 2.0
    test_months: int = 3
    embargo_days: int = 10  # 須 ≥ horizon_days（MlConfig 驗證把關）
    vault_months: int = 6  # 金庫期：最後 N 個月凍結，僅最終定案評估
    days_per_year: int = 244  # 台股一年交易日近似（換算 train_min_years 用）
    days_per_month: int = 21

    @property
    def train_min_days(self) -> int:
        """首窗訓練交易日數。"""
        return int(self.train_min_years * self.days_per_year)

    @property
    def test_days(self) -> int:
        """每段測試期交易日數。"""
        return self.test_months * self.days_per_month

    @property
    def vault_days(self) -> int:
        """金庫期交易日數。"""
        return self.vault_months * self.days_per_month


class MlLgbmConfig(BaseConfig):
    """LightGBM 參數（SPEC-010 §1：只暴露這幾個，其餘鎖庫預設＋決定性旗標）。"""

    num_leaves: int = 31
    learning_rate: float = 0.05
    min_data_in_leaf: int = 100
    feature_fraction: float = 0.8
    n_estimators: int = 400
    seed: int = 42


class MlRefitConfig(BaseConfig):
    """生產期重訓（SPEC-010 §4；T-070 接排程）。"""

    schedule: Literal["monthly"] = "monthly"


class MlConfig(BaseConfig):
    """ml.yaml 的模型（SPEC-010 §1 為權威）。"""

    horizon_days: int = 10
    aux_horizons: tuple[int, ...] = (5, 20)
    features: tuple[str, ...] = ()  # 空＝訓練時解析為全部註冊因子
    context_features: tuple[str, ...] = ()
    label: MlLabelConfig = MlLabelConfig()
    split: MlSplitConfig = MlSplitConfig()
    lgbm: MlLgbmConfig = MlLgbmConfig()
    refit: MlRefitConfig = MlRefitConfig()
    trials_log: str = "research_log"
    # 每日推論要用哪一個選股模型（目錄名）。空＝未綁定：`ml score` 會回到依用途自動選，
    # 而候選不唯一時寧可 raise 也不猜（T-224）。這是營運綁定不是研究參數——換模型是
    # 改這一行，不是改程式。
    production_ranking_model: str = ""

    @model_validator(mode="after")
    def _max_span_covers_horizon(self) -> MlConfig:
        # 在載入時擋，而不是等訓練跑完才發現標籤全空：出場列至少在 h+1 個開市日之後，
        # 設得更小不是「防護嚴一點」，是把標籤整個關掉。
        span = self.label.max_span_sessions
        if span is not None and span < self.horizon_days + 1:
            raise ValueError(
                f"label.max_span_sessions（{span}）須 ≥ horizon_days+1"
                f"（{self.horizon_days + 1}），否則所有標籤都會變 null"
            )
        return self

    @model_validator(mode="after")
    def _embargo_covers_horizon(self) -> MlConfig:
        # SPEC-010 §3：embargo 清除標籤前向視窗與測試期的重疊，必須 ≥ horizon
        if self.split.embargo_days < self.horizon_days:
            raise ValueError(
                f"split.embargo_days（{self.split.embargo_days}）須 ≥ "
                f"horizon_days（{self.horizon_days}），否則標籤洩漏進測試期"
            )
        return self


def load_config[T: BaseConfig](path: str | Path, model: type[T]) -> T:
    """讀 YAML → 用 model 驗證 → 回傳實例。缺欄位/多欄位/型別錯 → ConfigError（含欄位名）。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置檔不存在：{p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"配置 {p.name} 驗證失敗：{fields}") from exc


def config_hash(model: BaseConfig) -> str:
    """配置內容的決定性 hash（供 artifact metadata，SPEC-000 §可重現性）。"""
    payload = json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
