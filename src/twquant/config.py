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
    # D-074／D-075 新增兩個**換預測標的**的標籤（非調參）：
    #   revenue_surprise ＝下次公布的月營收 YoY 對「本月 YoY ＋ 加速度」的橫斷面殘差
    #   forward_drawdown ＝未來 horizon 個交易日、相對 t 日收盤的最低點跌幅
    transform: Literal[
        "rank_pct", "cost_adjusted_return", "revenue_surprise", "forward_drawdown"
    ] = "rank_pct"
    # 訊號日：daily ＝每個交易日一列（既有模型）；month_end ＝每個日曆月的最後一個
    # 交易日（D-074／D-075）。月營收在次月約 10 日公布，月底是唯一對所有個股都站在
    # 「本月已公布、下月未公布」邊界上的日子。
    signal_dates: Literal["daily", "month_end"] = "daily"
    # revenue_surprise 專用：下一次營收公布距訊號日的**開市日**上限，超過即無標籤
    # （遲報或缺報不是驚奇）。其他 transform 下必須留空。
    max_lag_sessions: int | None = Field(default=None, ge=1)
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

    @property
    def is_price_based(self) -> bool:
        """標籤是否由價格構成（決定 D-054 的 total_return 表態是否適用）。"""
        return self.transform != "revenue_surprise"


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
    # 訓練樣本的標籤（如 "lw" ＝長窗口）。**參與 config hash，也進 model_id**。
    # 沒有它的話，同一份 config 在不同樣本上訓練會得到同一個 model_id——
    # `save_model` 的 T-073 防護只在同一個目錄下擋得住，寫到不同 `--out` 就擋不住，
    # 而 `ml_scores` 按 model_id 索引，兩個模型的分數會混在一起。
    # 空字串＝預設樣本，既有模型的 id 與 hash 逐位元不變。
    sample_tag: str = ""
    features: tuple[str, ...] = ()  # 空＝訓練時解析為全部註冊因子
    context_features: tuple[str, ...] = ()
    label: MlLabelConfig = MlLabelConfig()
    split: MlSplitConfig = MlSplitConfig()
    lgbm: MlLgbmConfig = MlLgbmConfig()
    refit: MlRefitConfig = MlRefitConfig()
    trials_log: str = "research_log"

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
    def _sample_tag_is_id_safe(self) -> MlConfig:
        # tag 會直接進 model_id，而薄因子以**前綴**取分數；含底線或非英數字元
        # 會讓前綴比對失準（`lgbm_lw_net_h20_` vs `lgbm_net_h20_` 是兩回事，
        # 但 `lgbm_l_w_...` 這種就說不準了）。
        if self.sample_tag and not self.sample_tag.isalnum():
            raise ValueError(
                f"sample_tag 只能是英數字（會直接進 model_id 與前綴比對），得到 {self.sample_tag!r}"
            )
        return self

    @model_validator(mode="after")
    def _alt_labels_are_monthly(self) -> MlConfig:
        # D-074／D-075 把訊號日寫定為月底：營收驚奇的 PIT 邊界（本月已公布、下月未公布）
        # 只在月底成立，而 R 系列沿用同一組日子純粹是為了讓兩條線的切分逐字可比。
        # 允許 daily 就等於允許一個未登記的變體悄悄長出來。
        alt = {"revenue_surprise", "forward_drawdown"}
        if self.label.transform in alt and self.label.signal_dates != "month_end":
            raise ValueError(
                f"label.transform={self.label.transform}（D-074／D-075）要求 "
                f"signal_dates=month_end，得到 {self.label.signal_dates}"
            )
        if self.label.transform not in alt and self.label.signal_dates != "daily":
            raise ValueError(
                f"label.signal_dates={self.label.signal_dates} 只適用於 D-074／D-075 的"
                f"月頻標籤，transform={self.label.transform} 不得使用"
            )
        return self

    @model_validator(mode="after")
    def _revenue_surprise_bans_fundamental_features(self) -> MlConfig:
        # D-074：標籤是營收的殘差，放營收／財報類特徵進去等於把答案的一部分餵給模型。
        # 這條寫在配置層而非靠審查——特徵清單是最容易「順手加一個」的地方。
        if self.label.transform != "revenue_surprise":
            if self.label.max_lag_sessions is not None:
                raise ValueError(
                    f"label.max_lag_sessions 只適用於 revenue_surprise，"
                    f"transform={self.label.transform} 不得設定"
                )
            return self
        if self.label.max_lag_sessions is None:
            raise ValueError(
                "label.transform=revenue_surprise 須指定 max_lag_sessions"
                "（下一次營收公布距訊號日的開市日上限，D-074 寫定 15）"
            )
        if self.label.total_return is not None:
            raise ValueError(
                "label.transform=revenue_surprise 的標籤不含價格，"
                "total_return 不適用；請移除該欄而非填 false（D-054 的表態只對價格標籤有意義）"
            )
        if self.label.max_span_sessions is not None:
            raise ValueError(
                "label.transform=revenue_surprise 的標籤不做前向價格位移，"
                "max_span_sessions 不適用（跨距由 max_lag_sessions 以公布日把關）"
            )
        if not self.features:
            raise ValueError(
                "label.transform=revenue_surprise 不得用「空 features ＝全部註冊因子」"
                "——那會把 fundamental.* 一起帶進來（D-074 明文禁止）。請顯式列出特徵"
            )
        banned = sorted(f for f in self.features if f.startswith("fundamental."))
        if banned:
            raise ValueError(
                f"label.transform=revenue_surprise 禁止營收／財報類特徵（D-074）："
                f"{'、'.join(banned)}。標籤是營收的殘差，這些特徵是答案的一部分"
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
