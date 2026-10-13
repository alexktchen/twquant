"""配置載入與驗證（SPEC-000 §配置）。

全部 YAML，載入即用 pydantic 驗證；未知欄位 = 錯誤（extra="forbid"）。各 config
的權威欄位定義在對應 SPEC（清單見 SPEC-000）。本模組提供載入機制與 system.yaml
的最小模型；其餘配置模型在各自任務加入。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from twquant.errors import ConfigError


class BaseConfig(BaseModel):
    """所有配置模型的基底：禁止未知欄位，強制顯式 schema。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PushConfig(BaseConfig):
    """configs/push.yaml：網頁推播（T-348）。"""

    vapid_public_key: str = Field(min_length=80)
    vapid_private_key_env: str = Field(min_length=1)
    vapid_subject: str = Field(pattern=r"^(mailto:|https://)")
    site_url: str = Field(pattern=r"^https://")
    request_timeout_seconds: float = Field(gt=0)
    ttl_seconds: int = Field(ge=60)
    allowed_hosts: tuple[str, ...] = Field(min_length=1)


class SourceRate(BaseConfig):
    """單一來源的限流設定（滑動窗語意，SPEC-001 §7）。"""

    max_requests: int
    per_seconds: float


class QualityGateConfig(BaseConfig):
    """品質閘門閾值（SPEC-001 §5）。"""

    min_rows_ratio: float = 0.8
    inst_cross_check_tol: float = 0.005
    disk_min_gb: float = 50.0
    # D-175：當天打分前必須抓到的表（"來源/表"）。缺任何一張＝還沒公布完，閘門擋下、交給下一輪，
    # 不拿缺欄特徵打分（之後補跑看到「已有分數」不會重算）。空＝不檢查。
    required_tables: tuple[str, ...] = ()


#: T-310：每日 pipeline 會重抓的兩張直寫表（其餘直寫表是公告，依查詢窗抓）。
CHIP_DAILY_TABLES = ("daytrade_daily", "sbl_short_daily")


class ChipDailyConfig(BaseConfig):
    """官方每日當沖／借券／處置／注意（T-310，D-154 §五／D-157）。權威說明在 system.yaml 的註解。"""

    # 當沖／借券只收這個宇宙（＝FinMind 回補的歷史宇宙，D-154 §二.5；表不在中途換宇宙）
    universe_pattern: str = "^[1-9][0-9]{3}$"
    # 每日 pipeline 抓「前第 k 個交易日」並覆寫；最小的 k 是當沖的首次公布值（存 daytrade_first_pub）
    refetch_offsets: dict[str, tuple[int, ...]] = {
        "daytrade_daily": (1, 2, 3),
        "sbl_short_daily": (1,),
    }
    catchup_trading_days: int = Field(default=10, ge=1)  # 往回找「某市場一列都沒有」的交易日
    # T-327：資料湖表也在同一個窗裡補缺日（PG 一列都沒有的交易日重抓，再由供應層鏡射進 PG）
    lake_catchup_tables: tuple[str, ...] = ("margin_daily",)
    alerts_lookback_days: int = Field(default=14, ge=0)  # 公告查詢窗往回的日曆日
    disposition_lookahead_days: int = Field(default=14, ge=1)  # 處置依處置期間查，迄日往後推

    @model_validator(mode="after")
    def _offsets_valid(self) -> ChipDailyConfig:
        try:
            re.compile(self.universe_pattern)
        except re.error as exc:
            raise ValueError(f"universe_pattern 不是合法的正規式：{exc}") from exc
        if set(self.refetch_offsets) != set(CHIP_DAILY_TABLES):
            raise ValueError(
                f"refetch_offsets 必須剛好列出 {list(CHIP_DAILY_TABLES)}，得到 {sorted(self.refetch_offsets)}"
            )
        for table, offsets in self.refetch_offsets.items():
            if not offsets or any(k < 1 for k in offsets):
                raise ValueError(
                    f"refetch_offsets.{table} 須為 ≥1 的交易日數（當天不抓），得到 {offsets}"
                )
        return self


class SystemConfig(BaseConfig):
    """system.yaml 的最小模型（SPEC-001 為權威來源，後續任務擴充）。"""

    data_dir: str
    source_rates: dict[str, SourceRate]
    quality_gate: QualityGateConfig = QualityGateConfig()
    chip_daily: ChipDailyConfig = ChipDailyConfig()


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
    # pattern_tables 中仍保留「stock_meta 有產業別」股票的表（特別股會進產業平均，刪了產業因子就和地端不同）
    industry_member_tables: tuple[str, ...] = ()
    # pattern_tables 剔除非白名單時仍保留的代號（名單績效的對照 0050，D-176；它不是四碼普通股）
    keep_stocks: tuple[str, ...] = ()
    # 滾動窗口（交易日數）；未列出的表全史保留（小表）。硬下限＝因子最大回看（有測試把關）
    windows: dict[str, int] = {}
    # 雲端不算的因子：需要的歷史超過該表的滾動窗（含 expanding 統計）。列在這裡＝明確承認
    # 「本機研究用、雲端沒有」，而非讓雲端在殘缺窗口上算出看似正常的數字。test_cloud 把關：
    # 每個被排除的因子都必須真的超窗，清單不能拿來藏錯。
    exclude_factors: tuple[str, ...] = ()
    # 雲端跑每日 pipeline（`pipeline --cloud`）時明確略過的步驟名（D-170：網站只要每日推薦，
    # 不要回測重播、紙上交易、選股池）。名稱對不上任何步驟即 raise。
    skip_pipeline_steps: tuple[str, ...] = ()


class ServingConfig(BaseConfig):
    """serving.yaml 的模型（SPEC-009 §1 為權威）。PG 供應層＝衍生、可重建、僅供展示。"""

    enabled: bool = False  # 總開關；false 時 API 一律走 DuckDB 路徑
    dsn_env: str = "TWQUANT_PG_DSN"  # 存 DSN 的環境變數名（DSN 本體只在 .env）
    pg_schema: str = "serving"  # PG schema 名（避開 pydantic BaseModel.schema 遮蔽）
    pool_min: int = 1
    pool_max: int = 8
    factor_history_days: int = 1100  # factor_values 保留窗（≈3 年）
    # 地端物化要略過的因子（T-277）。與 `cloud.exclude_factors` 分開：那個只管雲端。
    # 用途是擋掉「跑很久卻產不出東西」的因子——`chip.broker_*` 的隔日沖判定是 expanding
    # 統計，每天都要掃 broker_trades 全部 5,770 萬列，實測單日 > 10 分鐘且**歷來一列都沒寫成功**，
    # 卻佔掉整條每日 pipeline 的時間（2026-09-21／22 因此整天沒有選股名單）。
    # 等 T-271 的整批路徑做好再移除。
    exclude_factors: tuple[str, ...] = ()
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
    #   triple_barrier ＝t+1 開盤進場後 horizon 日內，收盤先碰 +barrier_up（於 −barrier_down 之前）＝1（D-120）
    #   tail_up ＝t+1 開盤進場後 horizon 日內，收盤最大漲幅 ≥ barrier_up ＝1（D-143，右尾；
    #     與 triple_barrier 的差別是**先跌不取消後漲**——路徑交給出場規則管，不混進標籤）
    transform: Literal[
        "rank_pct",
        "cost_adjusted_return",
        "revenue_surprise",
        "forward_drawdown",
        "triple_barrier",
        "down_probability",
        "tail_up",
    ] = "rank_pct"
    # down_probability（D-131）：未來 horizon 日內最大跌幅達此值即為 1。其他 transform 下須留空
    drop_threshold: float | None = Field(default=None, gt=0.0, lt=1.0)
    # triple_barrier 專用；其他 transform 下必須留空（寫了卻不生效的配置是 no-op 缺陷）
    barrier_up: float | None = Field(default=None, gt=0.0)
    barrier_down: float | None = Field(default=None, gt=0.0)
    # D-127：上下緣改為 `barrier × σ`（σ＝該檔 vol_window 日報酬標準差 × √horizon）。
    # 固定百分比的邊界讓高波動股天生容易碰到，模型會學成「挑波動大的」而非「挑會漲的」。
    vol_scaled: bool = False
    vol_window: int | None = Field(default=None, ge=2)
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

    @model_validator(mode="after")
    def _barriers_match_transform(self) -> MlLabelConfig:
        given = self.barrier_up is not None or self.barrier_down is not None
        if self.transform == "triple_barrier":
            if self.barrier_up is None or self.barrier_down is None:
                raise ValueError("triple_barrier 須同時給 barrier_up 與 barrier_down")
        elif self.transform == "tail_up":
            # 右尾只有上緣：給了 barrier_down 是寫了卻不生效的配置（no-op 缺陷）
            if self.barrier_up is None:
                raise ValueError("tail_up 須給 barrier_up")
            if self.barrier_down is not None:
                raise ValueError("tail_up 沒有下緣，不得給 barrier_down（它不會生效）")
        elif given:
            raise ValueError(
                f"barrier_up／barrier_down 只用於 triple_barrier 與 tail_up，"
                f"transform={self.transform}"
            )
        if (self.transform == "down_probability") != (self.drop_threshold is not None):
            raise ValueError(
                f"down_probability 須給 drop_threshold，其他 transform 須留空；"
                f"transform={self.transform}、drop_threshold={self.drop_threshold}"
            )
        if self.vol_scaled:
            if self.transform != "triple_barrier":
                raise ValueError(f"vol_scaled 只用於 triple_barrier，transform={self.transform}")
            if self.vol_window is None:
                raise ValueError("vol_scaled=True 須同時給 vol_window")
        elif self.vol_window is not None:
            raise ValueError("vol_window 只在 vol_scaled=True 時有意義")
        return self

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


class RecommendationBucketConfig(BaseConfig):
    """ML 選股名單的晉級門檻（D-111）。

    匯出層明文禁止自己發明門檻（「發明門檻等於在畫面上偷偷做晉級」），
    所以門檻宣告在這裡、理由寫在 D-111，匯出層只是套用已宣告的政策。
    全部候選仍原樣存進快照，本設定只決定 `bucket` 欄位。

    `risk_gate_model` 空字串＝不套風控閘門（分組退化成純排名切點）。
    """

    enabled: bool = False
    top_n: int = 20  # 118 格回測的組合幾乎全是 20 檔等權——唯一被實際測過的檔數
    watch_n: int = 50  # D-104 的分散度階梯測 20→50→100，50 是下一個被測過的節點
    risk_gate_model: str = ""
    # D-075 寫死 R 系列的用途：prefilter 保留前 90%，即剔除預測回撤最深的一成。
    risk_gate_keep_pct: float = 0.9
    # D-113：流動性閘門。兩個數字與全部長窗口回測的 universe 區塊逐字相同
    # （ADV 前 30%、股價 ≥ 10）——名單不得比回測驗過的池子更寬。None＝不套。
    liquidity_min_adv_pctl: float | None = None
    liquidity_min_price: float | None = None

    @model_validator(mode="after")
    def _bands_are_ordered(self) -> RecommendationBucketConfig:
        pctl = self.liquidity_min_adv_pctl
        if pctl is not None and not 0.0 < pctl < 1.0:
            raise ValueError(f"liquidity_min_adv_pctl 須介於 (0, 1)，得到 {pctl}")
        if self.top_n < 1:
            raise ValueError(f"top_n 須 >= 1，得到 {self.top_n}")
        if self.watch_n < self.top_n:
            raise ValueError(
                f"watch_n({self.watch_n}) 不得小於 top_n({self.top_n})"
                "——觀察區是前段之後的延伸，不是另一個更小的切點"
            )
        if not 0.0 < self.risk_gate_keep_pct <= 1.0:
            raise ValueError(f"risk_gate_keep_pct 須介於 (0, 1]，得到 {self.risk_gate_keep_pct}")
        return self


class MlSampleStateConfig(BaseConfig):
    """只在某種「狀態」的股票上訓練、也只在那裡預測（D-151）。只用 t 日（含）以前的收盤。

    - `quiet`（模型 A）：過去 `past_window` 日還原報酬 < `max_past_ret`，**且** `vol_window` 日
      報酬標準差（與 `price.vol_20d` 同算法）在同日全體的百分位 < `max_vol_pctl`。
      排除「已經在飆」的股票——D-143 §十二 量到，讓模型看全部股票它就走捷徑挑最熱的。
    - `hot`（模型 B）：過去 `past_window` 日還原報酬 ≥ `min_past_ret`——已經飆上去的股票。

    寫了卻不生效的欄位一律拒絕（no-op 缺陷）：quiet 不得給 `min_past_ret`，hot 不得給波動條件。
    """

    kind: Literal["quiet", "hot"]
    past_window: int = Field(ge=1)
    max_past_ret: float | None = None
    min_past_ret: float | None = None
    vol_window: int | None = Field(default=None, ge=2)
    max_vol_pctl: float | None = Field(default=None, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _shape(self) -> MlSampleStateConfig:
        vol_given = self.vol_window is not None or self.max_vol_pctl is not None
        if self.kind == "quiet":
            if self.max_past_ret is None or self.vol_window is None or self.max_vol_pctl is None:
                raise ValueError("quiet 須同時給 max_past_ret、vol_window、max_vol_pctl")
            if self.min_past_ret is not None:
                raise ValueError("quiet 不得給 min_past_ret（它不會生效）")
        else:
            if self.min_past_ret is None:
                raise ValueError("hot 須給 min_past_ret")
            if self.max_past_ret is not None or vol_given:
                raise ValueError("hot 只看過去報酬下限，不得給 max_past_ret 或波動條件")
        return self


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
    # 大盤狀態特徵的滾動視窗（T-248）；context_features 為空時不生效
    context_window: int = Field(default=20, ge=2)
    # 把**別的模型**的樣本外分位當特徵：{特徵欄名: model_id}（T-248）。只准 walk-forward 樣本外分數
    model_score_features: dict[str, str] = Field(default_factory=dict)
    # 訓練樣本限縮到流動性合格池（與回測 universe 同兩個門檻）；None＝全部四碼普通股（既有行為）
    sample_min_adv_pctl: float | None = Field(default=None, gt=0.0, lt=1.0)
    sample_min_price: float | None = Field(default=None, gt=0.0)
    # D-151：只在安靜股／已飆股上訓練與預測；None＝全部四碼普通股（既有行為）
    sample_state: MlSampleStateConfig | None = None
    label: MlLabelConfig = MlLabelConfig()
    split: MlSplitConfig = MlSplitConfig()
    lgbm: MlLgbmConfig = MlLgbmConfig()
    refit: MlRefitConfig = MlRefitConfig()
    trials_log: str = "research_log"
    # 每日推論要用哪一個選股模型（目錄名）。空＝未綁定：`ml score` 會回到依用途自動選，
    # 而候選不唯一時寧可 raise 也不猜（T-224）。這是營運綁定不是研究參數——換模型是
    # 改這一行，不是改程式。
    production_ranking_model: str = ""
    # 除了排序模型與風控閘門之外，每天還要推論並寫進 PG 的模型（D-112）。
    # 已登記的策略若以某個模型的分數當池子（如 LW-F1 用營收驚奇模型），那個模型就得列在這裡，
    # 否則策略每天都讀不到當日分數。
    production_aux_models: tuple[str, ...] = ()
    # D-147：平台上架的**研究策略**所用的模型，每日推論寫進 PG。與上面兩個鍵的差別：
    # 這些在正式名單匯出**之後**才打分，失敗只寫進步驟摘要、不擋名單——
    # 生產名單（與風控閘門）不得依賴一個研究模型能不能跑。
    research_live_models: tuple[str, ...] = ()
    # D-163 潛伏飆股（QUIET）：安靜股模型每日推論。它要先篩安靜∩流動池、再加大盤狀態特徵
    # （`ml/quiet_live.py`），不能混進上面兩個鍵走通用 `ml score`。空＝不打分。
    quiet_live_model: str = ""
    quiet_live_config: str = ""  # 該模型的訓練配置（configs/ 下檔名），池子與大盤窗口以它為準
    # 安靜狀態看 60 日、流動性看 20 日，往前讀的日曆天數要涵蓋兩者與假期
    quiet_price_lookback_days: int = Field(default=200, ge=120)
    # 名單分組政策（D-111）。預設停用＝既有快照的 bucket 逐字不變。
    recommendation_buckets: RecommendationBucketConfig = RecommendationBucketConfig()

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


class ModelMeasurement(BaseConfig):
    """模型的一次量測：數字、期間與出處（D-122）。**沒有出處的數字不准上畫面。**

    `hit_rate` 一律是 **IC 勝率**——當天橫斷面 RankIC > 0 的訊號日比例。D-061 的
    日勝率（組合報酬為正的交易日）與逐筆勝率（平倉往返為正）是另外兩件事，
    不得填進這個欄位；三者在同一張卡片上差十幾到四十個百分點。
    """

    label: str  # 畫面上這組數字的名字，如「樣本外」「未見資料（金庫期）」
    rank_ic: float
    hit_rate: float
    n_days: int
    period_start: dt.date
    period_end: dt.date
    measured_on: dt.date
    source: str  # 重算指令或報告路徑——數字要能被重算，否則沒人知道它何時過期

    @model_validator(mode="after")
    def _numbers_are_possible(self) -> ModelMeasurement:
        if not 0.0 <= self.hit_rate <= 1.0:
            raise ValueError(f"hit_rate 是比例，須介於 [0, 1]，得到 {self.hit_rate}")
        if self.n_days < 1:
            raise ValueError(f"n_days 須 >= 1，得到 {self.n_days}")
        if self.period_end < self.period_start:
            raise ValueError(
                f"period_end（{self.period_end}）不得早於 period_start（{self.period_start}）"
            )
        if self.measured_on < self.period_end:
            raise ValueError(
                f"measured_on（{self.measured_on}）不得早於 period_end（{self.period_end}）"
                "——量測不可能發生在被量測的期間結束之前"
            )
        if not self.source.strip():
            raise ValueError("source 不得為空：數字要能被重算，否則下一次重訓不會有人發現它過期")
        return self


class ModelCatalogEntry(BaseConfig):
    """一個模型在畫面上的人話身分（D-122）。代號仍是身分，這裡是註解。"""

    name: str
    role: Literal["ranking", "risk_gate", "aux", "retired"]
    description: str
    caveat: str = ""
    # 登記時那一版模型的指紋（meta.json 的 artifact_fingerprint）。推薦快照存的是同一個值；
    # 不符＝模型重訓過而數字還是舊的——T-180 就是這樣讓三份文件留著重訓前的 IC 至今。
    artifact_fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    measurements: tuple[ModelMeasurement, ...] = ()

    @model_validator(mode="after")
    def _card_says_something(self) -> ModelCatalogEntry:
        if not self.name.strip() or not self.description.strip():
            raise ValueError("name 與 description 不得為空——卡片上不印空白註解")
        if not self.measurements:
            raise ValueError("measurements 不得為空：沒有量測過的模型不在卡片上給數字")
        return self


class ModelCatalogConfig(BaseConfig):
    """model_catalog.yaml（SPEC-014 §10）：卡片上人話名稱、描述與勝率的唯一來源。

    讀取層不重算 IC（SPEC-014 §3 禁止 GET 觸發研究運算），前端不寫死數字；
    要更新數字就跑 `scripts/model_ic_hitrate.py` 改這個檔。
    """

    hit_rate_definition: str = ""
    models: dict[str, ModelCatalogEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _registered_models_need_a_definition(self) -> ModelCatalogConfig:
        if self.models and not self.hit_rate_definition.strip():
            raise ValueError(
                "hit_rate_definition 不得為空：有登記模型就會有百分比上畫面，"
                "而 D-061 的三個勝率必須在畫面上分辨得出來"
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


# ── 投信／ETF 資金流（SPEC-016／D-171）──────────────────────────────────────


class FundFlowUnitsConfig(BaseConfig):
    """ETF 已發行單位數（`etf_units_daily`，T-335）的來源與每日補抓窗。"""

    finmind_dataset: str = "TaiwanStockShareholding"
    finmind_field: str = "NumberOfSharesIssued"
    available_hour: int = Field(default=21, ge=0, le=23)  # 與外資持股統計同表：同日 21:00
    history_start: dt.date = dt.date(2015, 6, 1)  # PG prices_daily（交易日曆）起點
    catchup_trading_days: int = Field(default=5, ge=1)
    # 分割／反分割：corporate_events 的事件日，以及它**之前**幾個交易日的 units_delta 設 null
    # （T-335 實測：0050、0052 的單位數跳動落在事件日的前一個交易日）
    split_event_types: tuple[str, ...] = ("split", "reverse_split")
    split_null_sessions_before: int = Field(default=1, ge=0)
    # 只報不修：單日 units 比值超出 [1/x, x] 且不在分割窗內 → 回補時列出（新 ETF 初期會有真的大申購）
    jump_report_ratio: float = Field(default=1.5, gt=1.0)


class FundFlowHoldingsConfig(BaseConfig):
    """ETF 每日持股（`etf_holdings_daily`，T-336）的來源與取樣。"""

    issuers: tuple[str, ...] = ("元大", "統一")  # 有歷史持股 API 的投信（T-334）
    available_hour: int = Field(
        default=18, ge=0, le=23
    )  # available_at＝max(基準日此時, 來源公布時點)
    weight_sum_max: float = Field(
        default=1.02, gt=1.0
    )  # 股票權重和上限（AC-8）；主動式持現金，不設下限
    catchup_trading_days: int = Field(default=5, ge=1)
    # 歷史回補：指數型每 N 個交易日取一份，成分變動的區間再逐日補（找出生效日）；主動式逐日
    index_sample_sessions: int = Field(default=5, ge=1)
    pace_seconds: float = Field(default=1.0, gt=0.0)
    probe_calendar_days: int = Field(
        default=7, ge=1
    )  # 找「基準日之後第一個可查詢日」最多往後試幾天


class FundFlowEventsConfig(BaseConfig):
    """SPEC-016 §4 的事件定義（D-171 §一 事前登記；不得依結果修改）。"""

    flow_hi_pctl: float = Field(default=0.95, gt=0.0, lt=1.0)  # E3
    flow_lo_pctl: float = Field(default=0.05, gt=0.0, lt=1.0)  # E4
    quarter_end_pctl: float = Field(default=0.90, gt=0.0, lt=1.0)  # E7
    absorb_pctl: float = Field(default=0.80, gt=0.0, lt=1.0)  # E8 的借券與融資
    streak_start: int = Field(default=3, ge=1)  # E5／E6
    quiet_before: int = Field(default=5, ge=1)  # E5／E6 的前置空窗
    quarter_end_sessions: int = Field(default=5, ge=1)  # E7
    dedup_sessions: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _tails_ordered(self) -> FundFlowEventsConfig:
        if not self.flow_lo_pctl < self.flow_hi_pctl:
            raise ValueError(
                f"flow_lo_pctl（{self.flow_lo_pctl}）須小於 flow_hi_pctl（{self.flow_hi_pctl}）"
            )
        return self


class FundFlowFactorsConfig(BaseConfig):
    """SPEC-016 §3 的因子窗口（交易日）。"""

    pressure_window: int = Field(default=5, ge=1)
    disc_window: int = Field(default=10, ge=1)
    market_window: int = Field(default=5, ge=1)
    adv_window: int = Field(default=20, ge=1)  # SPEC-003：20 日成交金額中位數


class FundFlowTracesConfig(BaseConfig):
    """SPEC-016 §5 的軌跡量測（D-171 §一）。"""

    pre_sessions: int = Field(default=20, ge=1)
    post_sessions: int = Field(default=60, ge=1)
    horizons: tuple[int, ...] = (1, 5, 10, 20, 40, 60)
    matched_min: int = Field(default=5, ge=1)
    match_deciles: int = Field(default=10, ge=2)
    flow_lags: tuple[int, ...] = (0, 1, 2)
    bootstrap_n: int = Field(default=2000, ge=100)
    seed: int = 42
    last_date: dt.date = dt.date(2026, 8, 31)  # D-109：第二保留期（2026-09-09 起）不碰

    @model_validator(mode="after")
    def _horizons_valid(self) -> FundFlowTracesConfig:
        if not self.horizons or any(h < 1 or h > self.post_sessions for h in self.horizons):
            raise ValueError(
                f"horizons 須在 1..post_sessions（{self.post_sessions}），得到 {self.horizons}"
            )
        if any(lag < 0 for lag in self.flow_lags):
            raise ValueError(f"flow_lags 不得為負，得到 {self.flow_lags}")
        return self


class FundFlowTailsConfig(BaseConfig):
    """D-171 §一之四：右尾標籤（與潛伏飆股 A20 逐字相同）。"""

    horizon: int = Field(default=20, ge=1)
    up: float = Field(default=0.20, gt=0.0)
    max_span_sessions: int = Field(default=42, ge=1)


class FundFlowConfig(BaseConfig):
    """`configs/fund_flow.yaml`（SPEC-016）：資金流資料、事件、因子與軌跡量測的全部參數。"""

    etf_meta_path: str = "configs/etf_meta.yaml"
    units: FundFlowUnitsConfig = FundFlowUnitsConfig()
    holdings: FundFlowHoldingsConfig = FundFlowHoldingsConfig()
    events: FundFlowEventsConfig = FundFlowEventsConfig()
    factors: FundFlowFactorsConfig = FundFlowFactorsConfig()
    traces: FundFlowTracesConfig = FundFlowTracesConfig()
    tails: FundFlowTailsConfig = FundFlowTailsConfig()


class EtfMetaEntry(BaseConfig):
    """`configs/etf_meta.yaml` 的一檔 ETF（SPEC-016 §2.2）。"""

    etf_id: str = Field(pattern=r"^00[0-9A-Z]{2,5}$")
    name: str
    issuer: str
    fund_type: str
    is_active: bool
    index_name: str | None = None
    listed_date: dt.date
    delisted_date: dt.date | None = None
    # False＝不追蹤單位數（來源沒有它），須在 note 寫原因；完整性閘門與機械流量都不算它
    units_tracked: bool = True
    note: str | None = None

    @model_validator(mode="after")
    def _untracked_has_reason(self) -> EtfMetaEntry:
        if not self.units_tracked and not (self.note or "").strip():
            raise ValueError(f"{self.etf_id}：units_tracked=false 必須在 note 寫原因")
        return self


class EtfMetaConfig(BaseConfig):
    """`configs/etf_meta.yaml`：資金流研究涵蓋的國內成分股票型 ETF。"""

    etfs: tuple[EtfMetaEntry, ...]

    @model_validator(mode="after")
    def _unique(self) -> EtfMetaConfig:
        ids = [e.etf_id for e in self.etfs]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"etf_id 重複：{dup}")
        if not ids:
            raise ValueError("etfs 不得為空")
        return self
