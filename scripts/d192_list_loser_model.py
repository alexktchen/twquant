"""D-192（2026-10-18，使用者明示推翻 D-141 §六一次）：名單內「避開跌」模型，定義逐字照 DECISIONS.md D-192 §二～§三。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant uv run --all-groups python scripts/d192_list_loser_model.py \
        --report docs/reports/d192-list-loser-model.md [--json docs/reports/d192-list-loser-model.json]

只讀本機 PG，不寫 PG、不存模型。決定性：固定 seed、固定超參數、同一份資料跑兩次相同。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, cast

import lightgbm as lgb
import numpy as np
import polars as pl
import psycopg

DEV_START = dt.date(2017, 9, 26)
DEV_END = dt.date(2026, 9, 8)
HORIZON = 20
STOP = -0.15
TOP_K = 15
DROP_K = 5  # 濾網：每天剔掉風險最高的 5 檔
FIRST_TEST_MONTH = dt.date(2019, 7, 1)
LAST_TEST_MONTH = dt.date(2026, 8, 1)
MIN_TRAIN_YEARS = 1.5
SEED = 20261018
BOOT = 10_000
# LightGBM 原生 API（本機沒裝 scikit-learn，不用 LGBMClassifier）；D-192 §二 寫的 n_estimators 300＝num_boost_round、
# min_child_samples 50＝min_data_in_leaf，同一組超參數、只是名字不同
NUM_ROUNDS = 300
LGB_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "num_leaves": 15,
    "learning_rate": 0.05,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": SEED,
    "deterministic": True,
    "num_threads": 1,
    "verbose": -1,
}
MODELS = {
    "CORE": "lgbm_lw_net_h20_20260225_83890036",
    "QUIET": "lgbm_quietA20_tu_h20_20260430_655df6d7",
}
LINE_NAME = {"CORE": "大盤精選", "QUIET": "潛伏飆股", "ALL": "合併"}
STOCK_RE = r"^[1-9][0-9]{3}$"
AUC_GATE, T_GATE = 0.02, 2.0
HIT_GATE_PP, RET_GATE_PP, VS_VOL_PP = 0.03, 0.005, 0.003


def _read(
    dsn: str, sql: str, params: tuple[object, ...], schema: dict[str, pl.DataType]
) -> pl.DataFrame:
    with psycopg.connect(dsn, connect_timeout=60) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema=schema, orient="row")


def load_lists(dsn: str) -> pl.DataFrame:
    """兩條線每天前 15：date, stock_id, line, score, rank。"""
    frames = []
    for line, mid in MODELS.items():
        df = _read(
            dsn,
            "SELECT date, stock_id, score, rank FROM ("
            "  SELECT date, stock_id, score, ROW_NUMBER() OVER (PARTITION BY date ORDER BY score DESC) AS rank"
            "  FROM serving.ml_scores WHERE model_id = %s AND stock_id ~ %s AND date BETWEEN %s AND %s AND score IS NOT NULL"
            ") t WHERE rank <= %s",
            (mid, STOCK_RE, DEV_START, DEV_END, TOP_K),
            {"date": pl.Date(), "stock_id": pl.Utf8(), "score": pl.Float64(), "rank": pl.Int64()},
        )
        frames.append(df.with_columns(pl.lit(line).alias("line")))
    return pl.concat(frames)


def load_labels(dsn: str, lists: pl.DataFrame) -> pl.DataFrame:
    """每筆 (股, 訊號日) 的 20 日最低／第 20 日收盤；窗內有事件的剔除（同 D-191 §三-1）。"""
    sids = sorted(set(lists["stock_id"].to_list()))
    px = _read(
        dsn,
        "SELECT date, stock_id, close FROM serving.prices_daily WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s "
        "AND close IS NOT NULL ORDER BY stock_id, date",
        (sids, DEV_START, DEV_END),
        {"date": pl.Date(), "stock_id": pl.Utf8(), "close": pl.Float64()},
    )
    ev = _read(
        dsn,
        "SELECT DISTINCT date, stock_id FROM serving.corporate_events WHERE stock_id = ANY(%s) AND date >= %s",
        (sids, DEV_START),
        {"date": pl.Date(), "stock_id": pl.Utf8()},
    )
    ev_cum = (
        ev.sort(["stock_id", "date"])
        .with_columns(pl.lit(1).alias("one"))
        .with_columns(pl.col("one").cum_sum().over("stock_id").alias("ev_cum"))
        .drop("one")
    )
    px = px.sort(["stock_id", "date"])
    px = px.join_asof(ev_cum, on="date", by="stock_id", strategy="backward").with_columns(
        pl.col("ev_cum").fill_null(0)
    )
    rev_min = pl.col("close").reverse().rolling_min(HORIZON).reverse().shift(-1)
    out = (
        px.with_columns(
            fwd_min=rev_min.over("stock_id"),
            close_h=pl.col("close").shift(-HORIZON).over("stock_id"),
            end_date=pl.col("date").shift(-HORIZON).over("stock_id"),
            ev_end=pl.col("ev_cum").shift(-HORIZON).over("stock_id"),
        )
        .drop_nulls(["fwd_min", "close_h", "end_date"])
        .filter(pl.col("end_date") <= DEV_END)
        .filter((pl.col("ev_end") - pl.col("ev_cum")) == 0)
        .filter(pl.col("close") > 0)
        .with_columns(
            y=((pl.col("fwd_min") / pl.col("close") - 1.0) <= STOP).cast(pl.Int8),
            ret=pl.col("close_h") / pl.col("close") - 1.0,
        )
        .select("date", "stock_id", "end_date", "y", "ret")
    )
    return out


def load_features(dsn: str, keys: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """訊號日全部因子原值 → 寬表（date, stock_id, <factor>...）。"""
    with psycopg.connect(dsn, connect_timeout=60) as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE k (date date, stock_id text, PRIMARY KEY (stock_id, date))")
        with cur.copy("COPY k (date, stock_id) FROM STDIN") as cp:
            for d, s in keys.select("date", "stock_id").unique().iter_rows():
                cp.write_row((d, s))
        cur.execute(
            "SELECT f.date, f.stock_id, f.factor, f.value FROM serving.factor_values f "
            "JOIN k ON k.stock_id = f.stock_id AND k.date = f.date WHERE f.value IS NOT NULL"
        )
        rows = cur.fetchall()
    long = pl.DataFrame(
        rows,
        schema={
            "date": pl.Date(),
            "stock_id": pl.Utf8(),
            "factor": pl.Utf8(),
            "value": pl.Float64(),
        },
        orient="row",
    )
    wide = long.pivot(
        on="factor", index=["date", "stock_id"], values="value", aggregate_function="first"
    )
    factors = sorted(c for c in wide.columns if c not in ("date", "stock_id"))
    return wide.select("date", "stock_id", *factors), factors


def auc(score: np.ndarray, y: np.ndarray) -> float | None:
    """Mann-Whitney AUC（平手取半）；只有一類 → None。"""
    pos = score[y == 1]
    neg = score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order), dtype=float)
    allv = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and allv[j + 1] == allv[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


@dataclass
class Window:
    month: str
    n_test: int
    pos_rate: float
    auc_model: float | None
    auc_vol: float | None


def month_starts() -> list[dt.date]:
    out = []
    d = FIRST_TEST_MONTH
    while d <= LAST_TEST_MONTH:
        out.append(d)
        d = dt.date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)
    return out


def walk_forward(data: pl.DataFrame, feats: list[str]) -> tuple[list[Window], pl.DataFrame]:
    """月走動式：訓練列＝end_date 早於測試月第一天；回每窗 AUC 與全部測試列的樣本外機率。"""
    windows: list[Window] = []
    preds = []
    for m0 in month_starts():
        m1 = dt.date(m0.year + (m0.month == 12), 1 if m0.month == 12 else m0.month + 1, 1)
        train = data.filter(pl.col("end_date") < m0)
        test = data.filter((pl.col("date") >= m0) & (pl.col("date") < m1))
        if test.is_empty() or train.is_empty():
            continue
        d_max = cast(dt.date, train["date"].max())
        d_min = cast(dt.date, train["date"].min())
        span = (d_max - d_min).days / 365.25
        if span < MIN_TRAIN_YEARS:
            continue
        xtr = train.select(feats).to_numpy().astype(float)
        ytr = train["y"].to_numpy().astype(int)
        xte = test.select(feats).to_numpy().astype(float)
        yte = test["y"].to_numpy().astype(int)
        if ytr.sum() == 0 or ytr.sum() == len(ytr):
            continue
        booster = lgb.train(LGB_PARAMS, lgb.Dataset(xtr, label=ytr), num_boost_round=NUM_ROUNDS)
        p = np.asarray(booster.predict(xte), dtype=float)
        vol = test["price.vol_20d"].to_numpy().astype(float)
        vol_ok = ~np.isnan(vol)
        windows.append(
            Window(
                month=m0.strftime("%Y-%m"),
                n_test=len(yte),
                pos_rate=float(yte.mean()),
                auc_model=auc(p, yte),
                auc_vol=auc(vol[vol_ok], yte[vol_ok]) if vol_ok.sum() else None,
            )
        )
        preds.append(
            test.select("date", "stock_id", "line", "y", "ret", "price.vol_20d").with_columns(
                pl.Series("p", p)
            )
        )
    return windows, pl.concat(preds) if preds else pl.DataFrame()


def gate1(windows: list[Window]) -> dict[str, object]:
    diffs = np.array(
        [
            w.auc_model - w.auc_vol
            for w in windows
            if w.auc_model is not None and w.auc_vol is not None
        ]
    )
    if len(diffs) < 2:
        return {"n_windows": len(diffs), "mean_diff": None, "t": None, "pass": False}
    mean = float(diffs.mean())
    se = float(diffs.std(ddof=1) / math.sqrt(len(diffs)))
    t = mean / se if se > 0 else float("inf")
    return {
        "n_windows": len(diffs),
        "mean_auc_model": float(np.mean([w.auc_model for w in windows if w.auc_model is not None])),
        "mean_auc_vol": float(np.mean([w.auc_vol for w in windows if w.auc_vol is not None])),
        "mean_diff": mean,
        "t": float(t),
        "share_positive": float((diffs > 0).mean()),
        "pass": bool(mean >= AUC_GATE and t >= T_GATE),
    }


def _keep_lowest(preds: pl.DataFrame, score_col: str) -> pl.DataFrame:
    """每天每條線剔掉 score 最高的 DROP_K 檔（風險最高），留其餘。"""
    ranked = preds.with_columns(
        pl.col(score_col).rank(method="ordinal", descending=True).over(["date", "line"]).alias("_r")
    )
    return ranked.filter(pl.col("_r") > DROP_K).drop("_r")


def _day_means(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.group_by("date")
        .agg(ret=pl.col("ret").mean(), hit=pl.col("y").cast(pl.Float64).mean(), n=pl.len())
        .sort("date")
    )


def gate2(preds: pl.DataFrame) -> dict[str, object]:
    """濾網：模型留 10 檔 vs 全留 15 檔 vs vol 留 10 檔；以訊號日為單位 bootstrap 平均報酬差。"""
    full = _day_means(preds)
    kept_m = _day_means(_keep_lowest(preds, "p"))
    kept_v = _day_means(_keep_lowest(preds.drop_nulls("price.vol_20d"), "price.vol_20d"))
    j = full.join(kept_m, on="date", suffix="_m").join(kept_v, on="date", suffix="_v")
    hit_full, hit_m, hit_v = (float(cast(float, j[c].mean())) for c in ("hit", "hit_m", "hit_v"))
    ret_full, ret_m, ret_v = (float(cast(float, j[c].mean())) for c in ("ret", "ret_m", "ret_v"))
    diff_m = (j["ret_m"] - j["ret"]).to_numpy()
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(diff_m), size=(BOOT, len(diff_m)))
    boot = diff_m[idx].mean(axis=1)
    lo, hi = float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))
    a = hit_full - hit_m >= HIT_GATE_PP
    b = ret_m - ret_full >= RET_GATE_PP and lo > 0
    c = ret_m - ret_v >= VS_VOL_PP
    return {
        "n_days": int(j.height),
        "hit_full": hit_full,
        "hit_model_kept": hit_m,
        "hit_vol_kept": hit_v,
        "ret_full": ret_full,
        "ret_model_kept": ret_m,
        "ret_vol_kept": ret_v,
        "ret_diff_model_vs_full": ret_m - ret_full,
        "ret_diff_ci95": [lo, hi],
        "a_hit_drop": bool(a),
        "b_ret_gain": bool(b),
        "c_beats_vol": bool(c),
        "pass": bool(a and b and c),
    }


def calibration(preds: pl.DataFrame) -> list[dict[str, float]]:
    q = preds.with_columns(
        (pl.col("p").rank(method="ordinal") * 10 / pl.len())
        .ceil()
        .clip(1, 10)
        .cast(pl.Int32)
        .alias("bin")
    )
    agg = (
        q.group_by("bin")
        .agg(pred=pl.col("p").mean(), real=pl.col("y").cast(pl.Float64).mean(), n=pl.len())
        .sort("bin")
    )
    return [
        {"bin": int(r["bin"]), "pred": float(r["pred"]), "real": float(r["real"]), "n": int(r["n"])}
        for r in agg.iter_rows(named=True)
    ]


def pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{d}f}%"


def render(res: dict[str, Any], generated_at: str) -> str:
    lines = [
        "# D-192：名單內「避開跌」模型（使用者明示推翻 D-141 §六一次）",
        "",
        f"產生時間 {generated_at}。定義與門檻見 DECISIONS.md D-192 §二～§三，一字未改。母體＝兩條線生產模型每日前 {TOP_K}；"
        f"標籤＝20 日內最低收盤 ≤ {STOP:.0%}；特徵＝訊號日全部因子原值＋分數＋名次；LightGBM 超參數寫死；月走動式重訓；對照 `price.vol_20d`。",
        "",
        "## 樣本",
        "",
        "| 組 | 筆數 | 訊號日 | 碰 −15% 比例 | 特徵數 |",
        "|---|---|---|---|---|",
    ]
    for line, s in res["samples"].items():
        lines.append(
            f"| {LINE_NAME[line]} | {s['n']:,} | {s['days']:,} | {pct(s['pos_rate'])} | {s['n_features']} |"
        )
    lines += [
        "",
        "## 核心關卡：樣本外 AUC，模型 vs 波動度（窗＝月）",
        "",
        "| 組 | 窗數 | 模型 AUC | 波動度 AUC | 差 | t | 差 > 0 的窗 | 過？ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for line, g in res["gate1"].items():
        if g["mean_diff"] is None:
            lines.append(f"| {LINE_NAME[line]} | {g['n_windows']} | — | — | — | — | — | 否 |")
            continue
        lines.append(
            f"| {LINE_NAME[line]} | {g['n_windows']} | {g['mean_auc_model']:.3f} | {g['mean_auc_vol']:.3f} | "
            f"{g['mean_diff']:+.4f} | {g['t']:+.2f} | {pct(g['share_positive'], 0)} | {'**過**' if g['pass'] else '否'} |"
        )
    lines += [
        "",
        f"## 濾網關卡：每天剔掉風險最高 {DROP_K} 檔（留 {TOP_K - DROP_K}）",
        "",
        "| 組 | 天數 | 碰 −15%：全留／模型留／vol 留 | 20 日平均報酬：全留／模型留／vol 留 | 模型−全留 差（95% 區間） | (a)(b)(c) | 過？ |",
        "|---|---|---|---|---|---|---|",
    ]
    for line, g in res["gate2"].items():
        lines.append(
            f"| {LINE_NAME[line]} | {g['n_days']:,} | {pct(g['hit_full'])}／{pct(g['hit_model_kept'])}／{pct(g['hit_vol_kept'])} | "
            f"{pct(g['ret_full'], 2)}／{pct(g['ret_model_kept'], 2)}／{pct(g['ret_vol_kept'], 2)} | "
            f"{g['ret_diff_model_vs_full'] * 100:+.2f} pp（{g['ret_diff_ci95'][0] * 100:+.2f}～{g['ret_diff_ci95'][1] * 100:+.2f}） | "
            f"{'✓' if g['a_hit_drop'] else '✗'}{'✓' if g['b_ret_gain'] else '✗'}{'✓' if g['c_beats_vol'] else '✗'} | {'**過**' if g['pass'] else '否'} |"
        )
    lines += [
        "",
        "## 校準（合併組，只報）",
        "",
        "| 十分組 | 預測 | 實際 | 筆數 |",
        "|---|---|---|---|",
    ]
    for c in res["calibration"]:
        lines.append(f"| {c['bin']} | {pct(c['pred'])} | {pct(c['real'])} | {c['n']:,} |")
    verdict = res["verdict"]
    lines += ["", "## 判讀", "", f"**{verdict}**", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument(
        "--save-model",
        default=None,
        help="D-193：另在大盤精選全部開發期列訓練一次最終模型，存到這個目錄（<dir>/model.txt＋meta.json）；只給網站當資訊標記",
    )
    args = ap.parse_args()
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        print("需要 TWQUANT_PG_DSN", file=sys.stderr)
        return 1
    lists = load_lists(dsn)
    print(f"名單代理 {lists.height:,} 筆、{lists['date'].n_unique():,} 日")
    labels = load_labels(dsn, lists)
    data = lists.join(labels, on=["date", "stock_id"], how="inner")
    feats_wide, factors = load_features(dsn, data)
    data = data.join(feats_wide, on=["date", "stock_id"], how="left")
    if "price.vol_20d" not in data.columns:
        raise SystemExit("factor_values 沒有 price.vol_20d，對照算不了")
    data = data.with_columns((pl.col("line") == "QUIET").cast(pl.Int8).alias("is_quiet"))
    base_feats = [*factors, "score", "rank"]
    res: dict[str, Any] = {"samples": {}, "gate1": {}, "gate2": {}}
    preds_all: pl.DataFrame | None = None
    for line in ("CORE", "QUIET", "ALL"):
        sub = data if line == "ALL" else data.filter(pl.col("line") == line)
        feats = base_feats + (["is_quiet"] if line == "ALL" else [])
        res["samples"][line] = {
            "n": sub.height,
            "days": sub["date"].n_unique(),
            "pos_rate": float(cast(float, sub["y"].cast(pl.Float64).mean())),
            "n_features": len(feats),
        }
        windows, preds = walk_forward(sub, feats)
        res["gate1"][line] = gate1(windows)
        res["gate2"][line] = gate2(preds) if not preds.is_empty() else {"pass": False, "n_days": 0}
        if line == "ALL":
            preds_all = preds
        print(line, res["gate1"][line], res["gate2"][line].get("pass"))
    assert preds_all is not None
    res["calibration"] = calibration(preds_all)
    g1 = res["gate1"]["ALL"]["pass"]
    g2 = res["gate2"]["ALL"]["pass"]
    res["verdict"] = (
        "核心與濾網兩關都過：依 D-192 §三.4 寫上線提案給使用者裁決（不自動改名單）。"
        if g1 and g2
        else f"收線：核心關卡{'過' if g1 else '不過'}、濾網關卡{'過' if g2 else '不過'}。依 D-192 §三.4，日頻因子的下跌預測不再以任何母體重試，除非有新資料。"
    )
    generated_at = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    pathlib.Path(args.report).write_text(render(res, generated_at), encoding="utf-8")
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    print(res["verdict"])
    if args.save_model:
        save_final_model(
            data.filter(pl.col("line") == "CORE"), base_feats, pathlib.Path(args.save_model)
        )
    return 0


FINAL_MODEL_ID = "lgbm_d192_core_down15_20261018"


def save_final_model(core: pl.DataFrame, feats: list[str], out_dir: pathlib.Path) -> None:
    """D-193：大盤精選全部開發期列訓練一次（同超參數、同特徵）→ model.txt＋meta.json。只給網站標記，不進生產模型清單。"""
    x = core.select(feats).to_numpy().astype(float)
    y = core["y"].to_numpy().astype(int)
    booster = lgb.train(LGB_PARAMS, lgb.Dataset(x, label=y), num_boost_round=NUM_ROUNDS)
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "model.txt"))
    meta = {
        "model_id": FINAL_MODEL_ID,
        "registered": "DECISIONS.md D-192 §二（訓練）／D-193 §二（用途：只標大盤精選、只給人看）",
        "line": "CORE",
        "label": f"{HORIZON} 日內最低收盤 ≤ {STOP:.0%}",
        "train_rows": int(core.height),
        "train_start": str(core["date"].min()),
        "train_end": str(core["date"].max()),
        "pos_rate": float(cast(float, core["y"].cast(pl.Float64).mean())),
        "features": feats,
        "params": LGB_PARAMS,
        "num_boost_round": NUM_ROUNDS,
        "flag_top_k": DROP_K,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"最終模型存到 {out_dir}（{core.height:,} 列、{len(feats)} 特徵）")


if __name__ == "__main__":
    raise SystemExit(main())
