"""歷史相似情境（D-182）：名單上的這一檔，歷史上「大盤狀態相近、個股條件最像」的 k 次，之後 20 天怎麼走。

- 情境庫：每天有潛伏模型分數的股票各一個情境（同 D-179 母體）；結果同 D-179 一筆（`list_trades.simulate_entries`）。
- 像不像：第一關挑大盤狀態最近的一部分交易日；第二關在那些日子裡用個股特徵的加權距離取最像的 k 次，
  同一檔一段行情只取一次。缺值不補 0：只比兩邊都有值的維度，再除以那些維度的權重和。
- 上線前驗證：題目期間的每個情境，用「鄰居分布」與「同分數區間分布」（D-179 的做法）各預測一次，比每日 RankIC 與 Brier。

純函數：情境庫、題目由呼叫端讀好傳進來。只供顯示，不進模型、不進名單。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from pydantic import Field, model_validator

from twquant.config import BaseConfig
from twquant.reporting.score_reference import Bucket, bucket_of, distribution

# 浮點誤差：登記的權重四捨五入到小數 4 位，加總允許這麼多偏差
_WEIGHT_SUM_TOL = 1e-3


class SimilarValidation(BaseConfig):
    """上線前驗證（D-182 §二）。"""

    library_sell_end: dt.date
    query_start: dt.date
    min_day_queries: int = Field(ge=2)
    bootstrap: int = Field(ge=100)
    seed: int
    ci: float = Field(gt=0.0, lt=1.0)


class SimilarConfig(BaseConfig):
    """configs/similar_situations.yaml（D-182 事前登記）。"""

    registration: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    oos_start: dt.date
    dev_end: dt.date
    hold_sessions: int = Field(ge=1)
    notional: float = Field(gt=0.0)
    lot_size: int = Field(ge=1)
    max_participation: float = Field(gt=0.0, le=1.0)
    big_loss: float = Field(lt=0.0)
    warmup_days: int = Field(ge=1)
    context_features: list[str] = Field(min_length=1)
    context_keep: float = Field(gt=0.0, le=1.0)
    feature_weights: dict[str, float] = Field(min_length=1)
    feature_gain_cover: float = Field(gt=0.0, le=1.0)
    min_weight_share: float = Field(gt=0.0, le=1.0)
    k: int = Field(ge=1)
    dedupe_sessions: int = Field(ge=1)
    examples: int = Field(ge=0)
    surge_lines: list[str]
    surge_window: int = Field(ge=1)
    surge_ratio_min: float = Field(gt=0.0)
    lines: list[str] = Field(min_length=1)
    validation: SimilarValidation

    @model_validator(mode="after")
    def _consistent(self) -> SimilarConfig:
        if any(w <= 0.0 for w in self.feature_weights.values()):
            raise ValueError("特徵權重都要 > 0")
        total = sum(self.feature_weights.values())
        if abs(total - 1.0) > _WEIGHT_SUM_TOL:
            raise ValueError(f"特徵權重加總要是 1，得到 {total}")
        if any(f.startswith("ctx.") for f in self.feature_weights):
            raise ValueError("大盤特徵走第一關，不放進個股權重")
        if not set(self.surge_lines) <= set(self.lines):
            raise ValueError("surge_lines 要是 lines 的子集")
        v = self.validation
        if not (self.oos_start < v.library_sell_end < v.query_start <= self.dev_end):
            raise ValueError("要 oos_start < 驗證庫截止 < 題目起日 ≤ dev_end")
        return self

    @property
    def features(self) -> list[str]:
        """個股特徵，依權重由大到小（同 yaml 的順序）。"""
        return list(self.feature_weights)


def gain_weights(names: Sequence[str], gains: Sequence[float], cover: float) -> dict[str, float]:
    """模型 gain 重要度 → 登記用的權重：只看個股特徵（ctx. 除外），由大到小取到累計 ≥ cover，重新加總為 1。

    四捨五入到小數 4 位，誤差補在第一個，與 yaml 寫法一致（測試拿來比對登記值）。
    """
    stock = sorted(
        ((n, float(g)) for n, g in zip(names, gains, strict=True) if not n.startswith("ctx.")),
        key=lambda x: (-x[1], x[0]),
    )
    total = sum(g for _, g in stock)
    if total <= 0.0:
        raise ValueError("模型的個股特徵 gain 合計 ≤ 0")
    keep: list[tuple[str, float]] = []
    cum = 0.0
    for n, g in stock:
        keep.append((n, g))
        cum += g
        if cum / total >= cover:
            break
    s = sum(g for _, g in keep)
    out = [(n, round(g / s, 4)) for n, g in keep]
    out[0] = (out[0][0], round(out[0][1] + round(1.0 - sum(w for _, w in out), 4), 4))
    return dict(out)


@dataclass(frozen=True)
class Library:
    """情境庫：一列一個情境。`day`／`exit_day` 是 `sessions` 的位置；`x` 缺值為 NaN；`ret` 是 D-179 一筆的報酬。"""

    sessions: tuple[dt.date, ...]
    day: np.ndarray  # int64，公布日位置
    exit_day: np.ndarray  # int64，賣出日位置
    sid: np.ndarray  # str
    score: np.ndarray  # float64
    x: np.ndarray  # float64 (n, 特徵)，缺值 NaN
    surge: np.ndarray  # bool
    ret: np.ndarray  # float64
    ctx: np.ndarray  # float64 (len(sessions), 大盤特徵)，那天沒有為 NaN

    def __post_init__(self) -> None:
        n = len(self.day)
        for name in ("exit_day", "sid", "score", "surge", "ret"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"情境庫欄位 {name} 長度 {len(getattr(self, name))} ≠ {n}")
        if self.x.ndim != 2 or self.x.shape[0] != n:
            raise ValueError(f"情境庫特徵矩陣形狀 {self.x.shape} 與 {n} 列不符")
        if self.ctx.shape[0] != len(self.sessions):
            raise ValueError("大盤狀態要每個交易日一列")
        if n and (self.day.min() < 0 or self.exit_day.max() >= len(self.sessions)):
            raise ValueError("情境的交易日位置超出 sessions")
        if n and (self.exit_day <= self.day).any():
            raise ValueError("賣出日要在公布日之後")
        if np.isnan(self.ret).any():
            raise ValueError("情境庫的結果不得有缺值（買不到、還沒賣的不放進庫）")

    def __len__(self) -> int:
        return len(self.day)

    def take(self, mask: np.ndarray) -> Library:
        """只留 mask 為真的情境（大盤狀態表不動）。"""
        return Library(
            self.sessions,
            self.day[mask],
            self.exit_day[mask],
            self.sid[mask],
            self.score[mask],
            self.x[mask],
            self.surge[mask],
            self.ret[mask],
            self.ctx,
        )


def context_scale(lib: Library) -> tuple[np.ndarray, np.ndarray]:
    """第一關的標準化：情境庫「有情境的交易日」的大盤狀態平均與標準差（每一天算一次，不依情境數加權）。"""
    days = np.unique(lib.day)
    rows = lib.ctx[days]
    if np.isnan(rows).any():
        bad = [lib.sessions[d].isoformat() for d in days[np.isnan(rows).any(axis=1)][:5]]
        raise ValueError(f"情境庫有交易日缺大盤狀態：{bad}")
    std = rows.std(axis=0)
    if (std <= 0.0).any():
        raise ValueError("大盤狀態有一欄沒有變異，無法標準化")
    return rows.mean(axis=0), std


def near_days(
    query_ctx: np.ndarray, lib: Library, mean: np.ndarray, std: np.ndarray, keep: float
) -> np.ndarray:
    """第一關：大盤狀態標準化後歐氏距離最近的 ceil(keep × 交易日數) 個交易日位置（距離相同依日期）。"""
    if np.isnan(query_ctx).any():
        raise ValueError("題目那天缺大盤狀態，不能比")
    days = np.unique(lib.day)
    z = (lib.ctx[days] - mean) / std
    q = (query_ctx - mean) / std
    d = np.sqrt(((z - q) ** 2).sum(axis=1))
    n = math.ceil(keep * len(days))
    order = np.lexsort((days, d))
    return np.sort(days[order[:n]])


def weighted_distance(q: np.ndarray, c: np.ndarray, w: np.ndarray, min_share: float) -> np.ndarray:
    """加權歐氏距離 (題目數, 候選數)：只算兩邊都有值的維度，除以那些維度的權重和；權重和 < min_share 給 inf。

    缺值不補 0：遮罩把缺的那一維從分子與分母同時拿掉（下面把 NaN 換成 0 只是為了讓矩陣乘法跳過它）。
    """
    qm = (~np.isnan(q)).astype(np.float64)
    cm = (~np.isnan(c)).astype(np.float64)
    qv = np.where(qm > 0, q, 0.0)
    cv = np.where(cm > 0, c, 0.0)
    share = (qm * w) @ cm.T
    num = (qm * w * qv**2) @ cm.T - 2.0 * (qm * w * qv) @ (cm * cv).T + (qm * w) @ (cm * cv**2).T
    with np.errstate(invalid="ignore", divide="ignore"):
        d2 = num / share
    d2 = np.maximum(d2, 0.0)  # 浮點相消會出現極小的負數
    out: np.ndarray = np.sqrt(d2)
    out[share < min_share - 1e-12] = np.inf
    return out


def pick_neighbors(
    dist: np.ndarray,
    day: np.ndarray,
    sid: np.ndarray,
    k: int,
    dedupe_sessions: int,
) -> np.ndarray:
    """最像的 k 個候選位置：依（距離、交易日、代號）排序；同一檔交易日相差 < dedupe_sessions 只留最像的一次。

    inf（不能比）的不取；不足 k 個就回傳實際找到的。
    """
    finite = np.isfinite(dist)
    if not finite.any():
        return np.empty(0, dtype=np.int64)
    idx = np.flatnonzero(finite)
    # 先取一批最近的；去重後不夠再放寬——門檻用「第 m 小的距離」，同距離的全部帶進來，排序才可重現
    m = min(len(idx), k * 10)
    while True:
        if m >= len(idx):
            cand = idx
        else:
            thr = np.partition(dist[idx], m - 1)[m - 1]
            cand = idx[dist[idx] <= thr]
        order = cand[np.lexsort((sid[cand], day[cand], dist[cand]))]
        taken: dict[str, list[int]] = {}
        out: list[int] = []
        for i in order:
            s = str(sid[i])
            d = int(day[i])
            if any(abs(d - e) < dedupe_sessions for e in taken.get(s, ())):
                continue
            taken.setdefault(s, []).append(d)
            out.append(int(i))
            if len(out) == k:
                return np.asarray(out, dtype=np.int64)
        if len(cand) == len(idx):
            return np.asarray(out, dtype=np.int64)
        m *= 4


@dataclass(frozen=True)
class Neighbors:
    """一題的鄰居：情境庫列位置（最像的在前）與它們的分布。"""

    rows: np.ndarray
    stats: dict[str, Any]


def find(
    lib: Library,
    query_ctx: np.ndarray,
    query_x: np.ndarray,
    weights: np.ndarray,
    cfg: SimilarConfig,
    scale: tuple[np.ndarray, np.ndarray],
    *,
    surge_only: bool,
) -> list[Neighbors]:
    """同一天的一批題目（query_x：題目數 × 特徵）各自的鄰居。大盤關每天算一次，所以一次給同一天的題目。"""
    days = near_days(query_ctx, lib, scale[0], scale[1], cfg.context_keep)
    mask = np.isin(lib.day, days)
    if surge_only:
        mask &= lib.surge
    cand = np.flatnonzero(mask)
    if query_x.shape[0] == 0:
        return []
    dist = weighted_distance(query_x, lib.x[cand], weights, cfg.min_weight_share)
    out: list[Neighbors] = []
    for row in dist:
        pos = pick_neighbors(row, lib.day[cand], lib.sid[cand], cfg.k, cfg.dedupe_sessions)
        rows = cand[pos]
        out.append(Neighbors(rows, distribution(lib.ret[rows].tolist(), cfg.big_loss)))
    return out


# ── 上線前驗證（D-182 §二）──────────────────────────────────────────────────


def bucket_baseline(
    lib: Library, buckets: Sequence[Bucket], big_loss: float, *, surge_only: bool
) -> dict[str, tuple[float, float]]:
    """簡化版的做法：情境庫裡每個分數區間全部情境的（中位數, 虧超過門檻的比例）。空區間不給（題目落到就 raise）。"""
    keys = np.array([bucket_of(float(s), buckets) for s in lib.score], dtype=object)
    if any(k is None for k in keys):
        raise ValueError("情境庫有分數不在任何區間")
    use = lib.surge if surge_only else np.ones(len(keys), dtype=bool)
    out: dict[str, tuple[float, float]] = {}
    for b in buckets:
        r = lib.ret[(keys == b.key) & use]
        if len(r):
            out[b.key] = (float(np.median(r)), float((r <= big_loss).mean()))
    return out


def _rank(a: np.ndarray) -> np.ndarray:
    return pl.Series(a).rank("average").to_numpy().astype(np.float64)


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    """Spearman 相關（同分取平均名次）；任一邊沒有變異回 None（那天不算，不是 0）。"""
    ra, rb = _rank(a), _rank(b)
    sa, sb = ra.std(), rb.std()
    if sa == 0.0 or sb == 0.0:
        return None
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


@dataclass(frozen=True)
class DayScore:
    """一個題目日：兩種預測的 RankIC（不算的日子為 None）與 Brier 的平方誤差和、題數。"""

    day: dt.date
    n: int
    ic_full: float | None
    ic_simple: float | None
    se_full: float
    se_simple: float


def day_scores(
    day: dt.date,
    actual: np.ndarray,
    med_full: np.ndarray,
    med_simple: np.ndarray,
    p_full: np.ndarray,
    p_simple: np.ndarray,
    big_loss: float,
    min_n: int,
) -> DayScore:
    """一天的分數：題數 < min_n 時 RankIC 不算（Brier 照算）。"""
    n = len(actual)
    y = (actual <= big_loss).astype(np.float64)
    ic_f = ic_s = None
    if n >= min_n:
        ic_f, ic_s = spearman(med_full, actual), spearman(med_simple, actual)
        if ic_f is None or ic_s is None:  # 任一邊不能算，那天兩邊都不算，比較才公平
            ic_f = ic_s = None
    return DayScore(
        day,
        n,
        ic_f,
        ic_s,
        float(((p_full - y) ** 2).sum()),
        float(((p_simple - y) ** 2).sum()),
    )


def _metrics(days: Sequence[DayScore]) -> tuple[float, float]:
    """(RankIC 差＝完整−簡化, Brier 差＝簡化−完整)：正的代表完整版比較好。"""
    ics = [(d.ic_full, d.ic_simple) for d in days if d.ic_full is not None]
    if not ics:
        raise ValueError("沒有任何一天能算 RankIC")
    d_ic = sum(f - s for f, s in ics if f is not None and s is not None) / len(ics)
    n = sum(d.n for d in days)
    d_brier = (sum(d.se_simple for d in days) - sum(d.se_full for d in days)) / n
    return d_ic, d_brier


@dataclass(frozen=True)
class Verdict:
    """驗證結果：兩個指標的點估計、信賴區間與結論。"""

    ic_full: float
    ic_simple: float
    brier_full: float
    brier_simple: float
    d_ic: float
    d_ic_ci: tuple[float, float]
    d_brier: float
    d_brier_ci: tuple[float, float]
    days: int
    ic_days: int
    n: int
    passed: bool


def verdict(days: Sequence[DayScore], bootstrap: int, seed: int, ci: float) -> Verdict:
    """整天一起抽的 bootstrap 信賴區間；通過＝至少一項下限 > 0 而且沒有一項上限 < 0（D-182 §二）。"""
    if not days:
        raise ValueError("沒有題目日")
    d_ic, d_brier = _metrics(days)
    rng = np.random.default_rng(seed)
    arr = list(days)
    ics: list[float] = []
    briers: list[float] = []
    for _ in range(bootstrap):
        pick = [arr[i] for i in rng.integers(0, len(arr), len(arr))]
        if not any(d.ic_full is not None for d in pick):
            continue  # 抽到全是不能算 RankIC 的日子：這一抽不算（樣本極小時才會發生）
        a, b = _metrics(pick)
        ics.append(a)
        briers.append(b)
    lo_q, hi_q = (1.0 - ci) / 2.0, 1.0 - (1.0 - ci) / 2.0
    ic_ci = (float(np.quantile(ics, lo_q)), float(np.quantile(ics, hi_q)))
    br_ci = (float(np.quantile(briers, lo_q)), float(np.quantile(briers, hi_q)))
    passed = (ic_ci[0] > 0.0 or br_ci[0] > 0.0) and not (ic_ci[1] < 0.0 or br_ci[1] < 0.0)
    ic_rows = [d for d in days if d.ic_full is not None]
    n = sum(d.n for d in days)
    return Verdict(
        ic_full=sum(d.ic_full for d in ic_rows if d.ic_full is not None) / len(ic_rows),
        ic_simple=sum(d.ic_simple for d in ic_rows if d.ic_simple is not None) / len(ic_rows),
        brier_full=sum(d.se_full for d in days) / n,
        brier_simple=sum(d.se_simple for d in days) / n,
        d_ic=d_ic,
        d_ic_ci=ic_ci,
        d_brier=d_brier,
        d_brier_ci=br_ci,
        days=len(days),
        ic_days=len(ic_rows),
        n=n,
        passed=passed,
    )


def validate_line(
    library: Library,
    questions: Library,
    weights: np.ndarray,
    cfg: SimilarConfig,
    buckets: Sequence[Bucket],
    *,
    surge_only: bool,
) -> list[DayScore]:
    """一條線的驗證：每個題目日，用鄰居分布（完整版）與同分數區間分布（簡化版）各預測一次。

    `questions` 的 `ret` 是實際結果；題目與情境庫共用 sessions 與大盤狀態表。題目找不到任何可比的鄰居 → raise。
    """
    if questions.sessions != library.sessions:
        raise ValueError("題目與情境庫要用同一份交易日")
    if len(library) and len(questions):
        last_lib_exit = library.exit_day.max()
        if questions.day.min() <= last_lib_exit:
            raise ValueError("題目的公布日要在情境庫最後一個賣出日之後（不得偷看）")
    scale = context_scale(library)
    base = bucket_baseline(library, buckets, cfg.big_loss, surge_only=surge_only)
    use = questions.surge if surge_only else np.ones(len(questions), dtype=bool)
    out: list[DayScore] = []
    for d in np.unique(questions.day[use]):
        m = (questions.day == d) & use
        nbs = find(
            library, questions.ctx[d], questions.x[m], weights, cfg, scale, surge_only=surge_only
        )
        med_f: list[float] = []
        p_f: list[float] = []
        for i, nb in zip(np.flatnonzero(m), nbs, strict=True):
            if nb.stats["n"] == 0:
                raise ValueError(
                    f"{questions.sessions[d]} {questions.sid[i]} 找不到可比的情境（特徵缺太多）"
                )
            med_f.append(float(nb.stats["median"]))
            p_f.append(float(nb.stats["big_loss_rate"]))
        med_s: list[float] = []
        p_s: list[float] = []
        for i in np.flatnonzero(m):
            key = bucket_of(float(questions.score[i]), buckets)
            if key is None or key not in base:
                raise ValueError(
                    f"{questions.sessions[d]} {questions.sid[i]} 的分數區間在情境庫裡沒有情境"
                )
            med_s.append(base[key][0])
            p_s.append(base[key][1])
        out.append(
            day_scores(
                questions.sessions[d],
                questions.ret[m],
                np.asarray(med_f),
                np.asarray(med_s),
                np.asarray(p_f),
                np.asarray(p_s),
                cfg.big_loss,
                cfg.validation.min_day_queries,
            )
        )
    return out


@dataclass(frozen=True)
class Reference:
    """驗證的參考數字（不影響通過與否）：一律猜情境庫整體大賠比例的 Brier、兩種預測 RankIC 的 t 值。"""

    base_rate: float  # 情境庫整體「虧超過門檻」的比例＝不看任何條件時會給的機率
    actual_rate: float  # 題目實際的比例
    brier_base: float  # 每題都猜 base_rate 的 Brier
    ic_full_t: float | None
    ic_simple_t: float | None


def _t(xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    a = np.asarray(xs, dtype=np.float64)
    sd = a.std(ddof=1)
    return None if sd == 0.0 else float(a.mean() / (sd / math.sqrt(len(a))))


def reference(
    library: Library,
    questions: Library,
    days: Sequence[DayScore],
    big_loss: float,
    *,
    surge_only: bool,
) -> Reference:
    """參考數字：Brier 要跟「不看條件、一律猜平均」比，才知道有沒有真的分辨能力。"""
    lib_ret = library.ret[library.surge] if surge_only else library.ret
    q_ret = questions.ret[questions.surge] if surge_only else questions.ret
    if len(lib_ret) == 0 or len(q_ret) == 0:
        raise ValueError("情境庫或題目是空的")
    p0 = float((lib_ret <= big_loss).mean())
    y = (q_ret <= big_loss).astype(np.float64)
    return Reference(
        base_rate=p0,
        actual_rate=float(y.mean()),
        brier_base=float(((p0 - y) ** 2).mean()),
        ic_full_t=_t([d.ic_full for d in days if d.ic_full is not None]),
        ic_simple_t=_t([d.ic_simple for d in days if d.ic_simple is not None]),
    )


def render_validation(
    cfg: SimilarConfig,
    results: Mapping[str, Verdict],
    names: Mapping[str, str],
    library_n: int,
    library_span: tuple[dt.date, dt.date],
    refs: Mapping[str, Reference] | None = None,
) -> str:
    """markdown 報告：每條線一段，決策線標明。"""
    v = cfg.validation
    lines = [
        f"# {cfg.registration} 歷史相似情境（完整版）上線前驗證",
        "",
        f"驗證庫：賣出日 ≤ {v.library_sell_end} 的 {library_n:,} 個情境（公布日 {library_span[0]}～{library_span[1]}）。",
        f"題目：公布日 ≥ {v.query_start}、賣出日 ≤ {cfg.dev_end} 的母體情境（模型沒看過的期間）。",
        "比較：完整版（大盤相近的日子裡最像的 "
        f"{cfg.k} 次）vs 簡化版做法（同一驗證庫、同分數區間的全部情境）。",
        f"信賴區間：以交易日為單位整天抽 {v.bootstrap:,} 次（seed {v.seed}），{v.ci:.0%}。"
        "通過＝至少一項下限 > 0，而且沒有一項上限 < 0。",
        "",
    ]
    for line, r in results.items():
        decide = "（決策看這條）" if line == cfg.lines[0] else "（只列參考）"
        lines += [
            f"## {names.get(line, line)}（{line}）{decide}",
            "",
            f"題目 {r.n:,} 個、{r.days} 個交易日（{r.ic_days} 天題數夠算 RankIC）。",
            "",
            "| 指標 | 完整版 | 簡化版 | 完整版好多少 | 信賴區間 |",
            "|---|---:|---:|---:|---|",
            f"| 報酬排序（每日 RankIC 平均，越大越好） | {r.ic_full:+.4f} | {r.ic_simple:+.4f} | "
            f"{r.d_ic:+.4f} | {r.d_ic_ci[0]:+.4f} ～ {r.d_ic_ci[1]:+.4f} |",
            f"| 大賠機率（Brier，越小越好） | {r.brier_full:.4f} | {r.brier_simple:.4f} | "
            f"{r.d_brier:+.4f} | {r.d_brier_ci[0]:+.4f} ～ {r.d_brier_ci[1]:+.4f} |",
            "",
            f"**結論：{'通過' if r.passed else '不通過'}**",
            "",
        ]
        ref = (refs or {}).get(line)
        if ref is not None:

            def t(x: float | None) -> str:
                return "—" if x is None else f"{x:+.2f}"

            lines += [
                "參考數字（看到結果後才加，**不影響結論**）：",
                f"- 不看任何條件、每題都猜情境庫整體的大賠比例（{ref.base_rate:.1%}）的 Brier："
                f"{ref.brier_base:.4f}；題目實際大賠比例 {ref.actual_rate:.1%}。",
                f"- 每日 RankIC 的 t 值：完整版 {t(ref.ic_full_t)}、簡化版 {t(ref.ic_simple_t)}。",
                "",
            ]
    return "\n".join(lines)
