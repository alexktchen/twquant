"""訓練（SPEC-010 §4）：walk-forward 訓練、模型落地、實驗登記。

邊界層——允許檔案 IO 與 lightgbm；純運算的資料集/切分在 dataset.py / splits.py
（受 IO 靜態閘門掃描，本檔不在其列）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import polars as pl

from twquant.backtest.costs import CostConfig
from twquant.config import MlConfig, MlLgbmConfig
from twquant.conventions import now_tw
from twquant.errors import TwquantError
from twquant.ml.splits import WalkSplit
from twquant.reporting.research_log import append_research_log

# 決定性旗標（SPEC-010 §0：同 config 同輸入 bit-identical）——非調參對象，故為常數非配置。
# num_threads 固定 4：LightGBM 在 deterministic+force_row_wise 下同執行緒數即可重現；
# 單執行緒對全史 walk-forward（百萬列 × 15+ 窗）慢到不可用，4 為固定值非調參。
_DETERMINISM: dict[str, object] = {
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbosity": -1,
}


def _params(lgbm: MlLgbmConfig) -> dict[str, object]:
    return {
        "objective": "regression",
        "num_leaves": lgbm.num_leaves,
        "learning_rate": lgbm.learning_rate,
        "min_data_in_leaf": lgbm.min_data_in_leaf,
        "feature_fraction": lgbm.feature_fraction,
        "seed": lgbm.seed,
        **_DETERMINISM,
    }


def fit_ranker(frame: pl.DataFrame, feature_cols: Sequence[str], lgbm: MlLgbmConfig) -> lgb.Booster:
    """對 label 非 null 列訓練 LightGBM 回歸（目標＝橫斷面百分位）。特徵缺值原生處理。"""
    data = frame.filter(pl.col("label").is_not_null())
    if data.is_empty():
        raise ValueError("無可訓練列：label 全為 null")
    ds = lgb.Dataset(
        data.select(feature_cols).to_numpy(),
        label=data["label"].to_numpy(),
        feature_name=list(feature_cols),
    )
    return lgb.train(_params(lgbm), ds, num_boost_round=lgbm.n_estimators)


def predict_scores(
    booster: lgb.Booster, frame: pl.DataFrame, feature_cols: Sequence[str]
) -> pl.DataFrame:
    """對 frame 逐列推論。回傳 (date, stock_id, score)，列序同輸入。"""
    scores = booster.predict(frame.select(feature_cols).to_numpy())
    return frame.select("date", "stock_id").with_columns(
        pl.Series("score", scores, dtype=pl.Float64)
    )


def oos_rank_ic(scored: pl.DataFrame) -> float:
    """逐日 spearman(score, label) 的均值。輸入需含 (date, score, label)。"""
    per_day = (
        scored.filter(pl.col("label").is_not_null())
        .group_by("date")
        .agg(pl.corr(pl.col("score").rank("average"), pl.col("label").rank("average")).alias("ic"))
    )
    mean = per_day["ic"].drop_nulls().mean()
    if not isinstance(mean, int | float):
        raise ValueError("無可計算 RankIC 的日期（label 全 null 或單一樣本日）")
    return float(mean)


@dataclass(frozen=True)
class MlTrainResult:
    """walk-forward 產出：OOS 分數表（date, stock_id, score, label）與各窗 RankIC。"""

    oos_scores: pl.DataFrame
    window_ics: list[float]


def walk_forward_train(
    frame: pl.DataFrame,
    splits: Sequence[WalkSplit],
    feature_cols: Sequence[str],
    lgbm: MlLgbmConfig,
    progress: Callable[[str], None] | None = None,
) -> MlTrainResult:
    """逐窗：以 ≤ train_end 訓練 → 對 [test_start, test_end] 推論（樣本外）。

    progress：每窗完成後回報一行（CLI 傳 typer.echo；長跑在 CI 上要看得到進度）。
    """
    outs: list[pl.DataFrame] = []
    ics: list[float] = []
    for i, s in enumerate(splits, start=1):
        train = frame.filter(pl.col("date").is_between(s.train_start, s.train_end))
        test = frame.filter(pl.col("date").is_between(s.test_start, s.test_end))
        booster = fit_ranker(train, feature_cols, lgbm)
        scored = predict_scores(booster, test, feature_cols).join(
            test.select("date", "stock_id", "label"), on=["date", "stock_id"], how="left"
        )
        outs.append(scored)
        try:
            ics.append(oos_rank_ic(scored))
        except ValueError as exc:
            n_train = int(train["label"].is_not_null().sum())
            n_test = int(test["label"].is_not_null().sum())
            raise ValueError(
                f"窗 {i}/{len(splits)}（train ≤ {s.train_end}、test {s.test_start}~{s.test_end}）："
                f"train {train.height} 列（label 非 null {n_train}）、"
                f"test {test.height} 列（label 非 null {n_test}）｜{exc}"
            ) from exc
        if progress is not None:
            progress(f"  窗 {i}/{len(splits)} {s.test_start}~{s.test_end}：RankIC {ics[-1]:+.4f}")
    return MlTrainResult(oos_scores=pl.concat(outs).sort("date", "stock_id"), window_ics=ics)


# 後加欄位 → 它「等同於加這個欄位之前的世界」的那個值。
# 取這個值時**整個鍵不進 fingerprint**，於是既有模型的 id 逐位元不變。
#
# 為什麼需要這張表：每加一個有預設值的欄位，`model_dump()` 就多一個鍵，
# 既有模型的 hash 全部改變——它們的 `meta.json` 與 `ml_scores` 已用舊 id 落地，
# 重算不出同一個 id，T-073 的內容定址就失效了。
#
# **這張表只往前防，補不回已經斷掉的。** D-074 加 `label.signal_dates` 與
# `label.max_lag_sessions` 時沒有這道防護，於是那批之前訓練的模型
# （`lgbm_net_h20_*_6a798962`、四個 `lgbm_h10_*`）已經對不回自己的目錄名。
# 補列那兩個鍵可以救回它們，但會反過來弄壞 D-074 之後訓練的
# `lgbm_dd_h60_*_1a2c3a8e`——它的 hash 本來就含 `max_lag_sessions: null`。
# 兩個世代是用兩套 schema 雜湊的，單一函數無法同時重現，故**不試**：
# 保住現在對得上的，斷掉的記在 T-173，重訓時自然消失。
#
# **只有「該值＝欄位不存在時的行為」才可列入。** 若某個預設值會改變訓練結果，
# 它就必須進 hash，列進這裡即是掩蓋一次真正的模型變更。
_BACKCOMPAT_DEFAULTS: tuple[tuple[tuple[str, ...], object], ...] = (
    (("sample_tag",), ""),  # 空＝預設樣本
)

# **永遠**不進訓練指紋的欄位——不論它們的值是什麼。
# 與上面那張表不同：那張是「取回溯相容預設值時才略過」，這張是「這根本不是訓練輸入」。
#
# `production_ranking_model` 是**營運綁定**（每日推論要用哪個模型），不是研究參數。
# 換綁一個模型不會改變任何模型學到了什麼，卻會改變 `training_config_hash`——
# 而那個 hash 同時是 research_log 的 `params_hash`，也就是 DSR 的試驗計數依據。
# 讓它進去等於每換一次生產模型就消耗一個試驗名額，而且既有八個模型的 id 全部改變。
# （合併 PR#65 時由 T-172 的回歸測試抓到：兩個模型目錄名都對不回來。）
# D-111／D-112 又加了兩個同性質的欄位：名單怎麼分組、每天額外推論哪些模型。
# 兩者都只影響「模型產出之後怎麼用」，與模型學到什麼無關——同樣不得進 hash。
# （同一個回歸測試再抓到一次：加了欄位，八個既有模型的 id 就全部對不回來。）
_NOT_TRAINING_INPUTS: tuple[tuple[str, ...], ...] = (
    ("production_ranking_model",),
    ("recommendation_buckets",),
    ("production_aux_models",),
)


def _hashable_dump(cfg: MlConfig) -> dict[str, object]:
    """訓練 fingerprint 用的配置字典，剔除取回溯相容預設值的後加欄位。"""
    dump: dict[str, object] = cfg.model_dump(mode="json")
    node: object
    for path in _NOT_TRAINING_INPUTS:
        node = dump
        for key in path[:-1]:
            if not isinstance(node, dict):
                break
            node = node.get(key)
        if isinstance(node, dict):
            node.pop(path[-1], None)
    for path, legacy in _BACKCOMPAT_DEFAULTS:
        node = dump
        for key in path[:-1]:
            if not isinstance(node, dict):
                break
            node = node.get(key)
        if isinstance(node, dict) and node.get(path[-1]) == legacy:
            node.pop(path[-1])
    return dump


def training_config_hash(cfg: MlConfig, cost_cfg: CostConfig | None = None) -> str:
    """訓練 fingerprint；成本感知模型同時綁定 ML 與成本配置。

    只有 `cost_adjusted_return` 的標籤真的吃 CostConfig（成本進了標籤本身）；
    D-074／D-075 的兩個標籤與成本無關，故與 `rank_pct` 一樣只綁 ML 配置。
    """
    ml = _hashable_dump(cfg)
    if cfg.label.transform != "cost_adjusted_return":
        encoded = json.dumps(ml, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]
    payload: dict[str, object] = {"ml": ml}
    if cost_cfg is None:
        raise ValueError("cost_adjusted_return 模型缺 CostConfig，無法建立 fingerprint")
    payload["cost"] = cost_cfg.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


# ── T-073：artifact 的內容定址 ────────────────────────────────────────────
# 訓練路徑上「改了就會改變模型」的純程式。因子計算不在此列——特徵來自 factor_values，
# 那屬於**資料**版本（training_data_hash），不是程式版本。
_CODE_SOURCES = ("dataset.py", "splits.py", "train.py")


def training_code_hash() -> str:
    """訓練路徑程式碼的內容雜湊。

    用內容而非 git SHA：worktree 有未提交的改動時 SHA 會說謊，而「同 id 不同模型」
    正是在改到一半重訓時發生的。
    """
    pkg = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in _CODE_SOURCES:
        h.update(name.encode("utf-8"))
        h.update((pkg / name).read_bytes())
    return h.hexdigest()[:16]


def training_data_hash(frame: pl.DataFrame) -> str:
    """訓練用特徵×標籤寬表的內容雜湊。

    要能分辨「形狀相同但值不同」——stock_meta 補列（T-111）、除權息還原（T-098）
    這類改動正是這個形狀：列數可能一樣，值變了。
    依賴 `assemble` 已對 (date, stock_id) 排序的契約（逐列雜湊隨列序而定）。
    """
    cols = sorted(frame.columns)
    h = hashlib.sha256()
    h.update("|".join(cols).encode("utf-8"))
    h.update(str(frame.height).encode("utf-8"))
    h.update(frame.select(cols).hash_rows().to_numpy().tobytes())
    return h.hexdigest()[:16]


def artifact_fingerprint(*, config: str, code: str, data: str) -> str:
    """artifact 的身分：配置 × 程式碼 × 資料。

    **刻意不進 `training_config_hash`**：後者同時是 research_log 的 `params_hash`，
    也就是 DSR 的 trial 計數依據。把程式碼版本塞進去，等於每修一次缺陷就消耗一個
    DSR 名額——而 trial 計的是「假設」，維護不是新假設（同型缺陷見 T-078）。
    """
    payload = json.dumps({"code": code, "config": config, "data": data}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# 標籤 → model_id 前綴。前綴是**因子身分**（薄因子以它取分數），不是調參對象；
# 換了預測標的就是不同的模型系列，共用前綴會讓 `ml.*` 因子撈到別條線的分數。
_ID_KIND: dict[str, str] = {
    "rank_pct": "",
    "cost_adjusted_return": "net_",
    "revenue_surprise": "rev_",  # D-074
    "forward_drawdown": "dd_",  # D-075
}

# research_log 的 trial_scope（DSR 的家族鍵）。第一段 IC 檢驗記在 ML 家族，
# 不佔回測試驗額度——第二段的經濟價值判定才是回測 trial（D-074／D-075）。
_TRIAL_SCOPE: dict[str, str] = {
    "rank_pct": "ml:h{h}",
    "cost_adjusted_return": "ml:net_h{h}",
    "revenue_surprise": "ml:revenue_surprise",
    "forward_drawdown": "ml:drawdown_h{h}",
}


def _scope(cfg: MlConfig) -> str:
    """DSR 的家族鍵。**換樣本＝換家族**——不同樣本上的同一組超參數不是同一個假設，
    算進同一個 trial_scope 會讓試驗計數與 DSR 門檻都失真。"""
    base = _TRIAL_SCOPE[cfg.label.transform].format(h=cfg.horizon_days)
    return f"{base}:{cfg.sample_tag}" if cfg.sample_tag else base


def make_model_id(train_end: dt.date, cfg: MlConfig, *, cost_cfg: CostConfig | None = None) -> str:
    """模型 id：演算法_h視窗_訓練截止_配置hash8——可回溯到 configs/ml.yaml（SPEC-010 §4）。"""
    fingerprint = training_config_hash(cfg, cost_cfg)
    # `sample_tag` 插在 **lgbm 之後**而不是尾端：薄因子以前綴取分數，
    # 放在前面 `lgbm_lw_net_h20_` 與 `lgbm_net_h20_` 才不會互相命中（放尾端就會）。
    tag = f"{cfg.sample_tag}_" if cfg.sample_tag else ""
    kind = _ID_KIND[cfg.label.transform]
    return f"lgbm_{tag}{kind}h{cfg.horizon_days}_{train_end:%Y%m%d}_{fingerprint[:8]}"


def save_model(
    out_dir: Path,
    booster: lgb.Booster,
    meta: Mapping[str, object],
    *,
    allow_overwrite: bool = False,
) -> None:
    """model.txt + meta.json 落地。meta 必含 train_end（load_model 斷言用）。

    T-073：同一目錄下若已有**不同 `artifact_fingerprint`** 的模型即 raise。
    先前是冪等覆寫，所以修好一個缺陷後重訓會產生「同一 model_id 的不同模型」
    並靜默蓋掉舊的，破壞 SPEC-011 §3 的可重現性保證。
    """
    fingerprint = meta.get("artifact_fingerprint")
    existing = out_dir / "meta.json"
    if existing.exists() and not allow_overwrite:
        prev = json.loads(existing.read_text(encoding="utf-8"))
        prev_fp = prev.get("artifact_fingerprint")
        if prev_fp is None:
            raise TwquantError(
                f"{out_dir} 已有模型，但它早於 T-073、沒有 artifact_fingerprint，"
                "無法證明與本次訓練是同一個模型。請先封存舊 artifact 再重訓"
                "（或明確傳 allow_overwrite=True）。"
            )
        if prev_fp != fingerprint:
            raise TwquantError(
                f"{out_dir} 已有**不同**的模型：既有 fingerprint {prev_fp}、"
                f"本次 {fingerprint}。同一 model_id 對應兩個模型會破壞可重現性；"
                "配置若相同而程式碼或資料變了，請先封存舊 artifact"
                "（或明確傳 allow_overwrite=True）。"
            )
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "model.txt"))
    (out_dir / "meta.json").write_text(
        json.dumps(dict(meta), ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )


def log_training_run(
    data_dir: str | Path,
    cfg: MlConfig,
    model_id: str,
    oos_ic: float,
    *,
    cost_cfg: CostConfig | None = None,
) -> None:
    """research_log 登記一列（AC-8）；trial_scope 綁標籤家族，trials 計數供 DSR。"""
    append_research_log(
        data_dir,
        [
            {
                "entry_type": "ml_train",
                "strategy": model_id,
                "factor": "ml",
                "params_hash": training_config_hash(cfg, cost_cfg),
                "date": now_tw().date().isoformat(),
                "metric_name": "oos_rank_ic",
                "metric_value": oos_ic,
                "trial_scope": _scope(cfg),
            }
        ],
    )
