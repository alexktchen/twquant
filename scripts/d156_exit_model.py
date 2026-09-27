"""D-156：第十四輪模型出場（第一階段，逐筆）——**只報不判**。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d156_exit_model.py                # S0／SIG／STOP10／RAND／M0
    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d156_exit_model.py --with-batch1  # 另加 M1（借券回補完成後）

設計全部寫在 D-156，本檔照做：
- 部位：A20 樣本外分數每日池內前 15 名，同檔 21 列內不重買、開盤漲停不進；第 0 天開盤進場、最長第 21 天開盤賣；
- 決策：第 0~19 天收盤；標籤 y_k＝ln(還原開盤[21] ÷ 還原開盤[k+1])＝「繼續抱」相對「明天開盤賣」的報酬；
- 模型：LightGBM 迴歸，參數逐字取 A20 配置（`mlt.fit_ranker`），分季 walk-forward、標籤結束日早於季初 14 天才可訓練；
- 規則：預測 ≤ 0 → 明天開盤賣（理論門檻，無參數）；
- 評估：進場日 2019-10-01~2026-03-31、路徑在 2026-04-30 前結束；每持有日淨報酬（每筆扣 0.40%）＋進場月區塊 t。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import os
import pathlib
import sys

import numpy as np
import polars as pl
import psycopg

from twquant.config import MlConfig, load_config
from twquant.data.serving.ml_store import read_index_close
from twquant.ml import train as mlt
from twquant.ml.context import market_state_features

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d151_report import DATA_END, DATA_START, LIMIT_GAP, SCHEMA, load_market

ROOT = pathlib.Path(__file__).resolve().parents[1]
A20_CFG = ROOT / "configs" / "ml_quiet_a20.yaml"  # 特徵清單與 LightGBM 參數逐字沿用
A20_DIR = pathlib.Path("D:/project/twquant/data/models/lgbm_quietA20_tu_h20_20260430_655df6d7")
TOP_N = 15  # 第十三輪的候選數
NO_REENTRY = 21  # 列距 ≥ 21：前一個部位（第 0~20 天）已在第 21 天開盤賣掉
EXIT_DAY = 21  # 第 21 天開盤賣（＝第十三輪 S0 的 time_stop 20）
N_DECISIONS = 20  # 第 0~19 天收盤各一次
EVAL = (dt.date(2019, 10, 1), dt.date(2026, 3, 31))
PATH_END = dt.date(2026, 4, 30)  # 路徑不得越過（D-156 §一：不碰保留段）
EMBARGO = dt.timedelta(days=14)
MIN_TRAIN = dt.timedelta(days=730)  # 至少兩年
ROUND_TRIP = 0.004
STOP = 0.90  # Han, Zhou & Zhu 的 −10%
SIG_PCT = 0.5  # 第十三輪 S2 的門檻
SEEDS = 20
_S = "stock_id"
HOLD = ["h.k", "h.unreal", "h.maxgain", "h.drawdown", "h.ret1"]
RESCORE = ["a20.score", "a20.pct"]
B1 = ["b1.sbl_sell5", "b1.sbl_bal_chg5", "b1.dt_share5", "b1.attn5", "b1.disposition"]


# ── 資料 ─────────────────────────────────────────────────────────────────────


def _a20_scores() -> pl.DataFrame:
    """A20 樣本外分數與當日池內百分位（池＝當天有分數的股票）。"""
    oos = pl.read_parquet(A20_DIR / "oos_scores.parquet").select("date", _S, "score")
    return oos.with_columns(
        (pl.col("score").rank("average").over("date") / pl.col("score").count().over("date")).alias(
            "pct"
        )
    )


def build_positions(mk: pl.DataFrame, scores: pl.DataFrame) -> pl.DataFrame:
    """虛擬部位：(pos, stock_id, r0, entry_date, exit_date)。r0＝第 0 天在 mk 的列號。"""
    sid = mk[_S].to_numpy()
    ao = (mk["open"] / mk["adj"]).to_numpy()
    gap = mk["gap"].to_numpy()
    cut = np.flatnonzero(sid[1:] != sid[:-1]) + 1
    end = np.empty(len(sid), dtype=np.int64)  # 每列所屬股票的最後一列
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(sid)], strict=True):
        end[a:b] = b - 1
    rows = mk.with_row_index("r").select(pl.col("r").cast(pl.Int64), "date", _S)
    top = scores.filter(pl.col("score").rank("ordinal", descending=True).over("date") <= TOP_N)
    cand = top.join(rows, on=["date", _S]).sort(_S, "r")
    kept: list[tuple[str, int]] = []
    skipped_gap = 0
    last: dict[str, int] = {}
    for s, r in cand.select(_S, "r").iter_rows():
        if r - last.get(s, -(10**9)) < NO_REENTRY:
            continue
        r0 = r + 1
        if r0 + EXIT_DAY > end[r]:
            continue  # 路徑不完整（停牌、下市、資料到尾）
        g = gap[r]
        if g is None or not math.isfinite(g) or g >= LIMIT_GAP:
            skipped_gap += 1  # 開盤漲停買不到（或沒有隔日開盤）：沒開部位，不更新 last
            continue
        if not (math.isfinite(ao[r0]) and ao[r0] > 0):
            continue
        last[s] = r
        kept.append((s, r0))
    print(f"  候選 {cand.height:,}；開盤漲停跳過 {skipped_gap:,}；開部位 {len(kept):,}")
    pos = pl.DataFrame(kept, schema={_S: pl.String, "r0": pl.Int64}, orient="row").with_row_index(
        "pos"
    )
    dates = rows.select(pl.col("r").alias("r0"), pl.col("date").alias("entry_date"))
    exits = rows.select((pl.col("r") - EXIT_DAY).alias("r0"), pl.col("date").alias("exit_date"))
    return pos.join(dates, on="r0").join(exits, on="r0").sort("pos")


def decision_frame(
    mk: pl.DataFrame, pos: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """每個部位第 0~19 天各一列：持有狀態特徵＋標籤。另回傳評估用的價格矩陣。"""
    ao = (mk["open"] / mk["adj"]).to_numpy()
    ac = (mk["close"] / mk["adj"]).to_numpy()
    r0 = pos["r0"].to_numpy()
    k = np.arange(N_DECISIONS)
    close = ac[r0[:, None] + k[None, :]]  # 第 0~19 天收盤
    opens = ao[r0[:, None] + np.arange(EXIT_DAY + 1)[None, :]]  # 第 0~21 天開盤
    ok = np.isfinite(close).all(axis=1) & np.isfinite(opens).all(axis=1) & (opens > 0).all(axis=1)
    if not ok.all():
        print(f"  還原價有缺的部位排除 {int((~ok).sum()):,}")
    entry = opens[:, [0]]
    runmax = np.maximum.accumulate(close, axis=1)
    prev = np.concatenate([entry, close[:, :-1]], axis=1)  # 第 0 天的「前一個價」是進場開盤
    label = np.log(opens[:, [EXIT_DAY]] / opens[:, 1 : N_DECISIONS + 1])
    n = len(r0)
    dates = mk["date"].to_numpy()
    frame = pl.DataFrame(
        {
            "pos": np.repeat(pos["pos"].to_numpy(), N_DECISIONS),
            _S: np.repeat(pos[_S].to_numpy(), N_DECISIONS),
            "date": dates[(r0[:, None] + k[None, :]).ravel()],
            "h.k": np.tile(k, n).astype(np.float64),
            "h.unreal": np.log(close / entry).ravel(),
            "h.maxgain": np.log(runmax / entry).ravel(),
            "h.drawdown": np.log(close / runmax).ravel(),
            "h.ret1": np.log(close / prev).ravel(),
            "label": label.ravel(),
        }
    ).with_columns(pl.col("date").cast(pl.Date))
    keep = np.repeat(ok, N_DECISIONS)
    frame = frame.filter(pl.Series(keep)).join(
        pos.select("pos", "entry_date", "exit_date"), on="pos"
    )
    prices = {"close": close, "opens": opens, "ok": ok}
    return frame, prices


def _factor_z(conn: psycopg.Connection, factors: list[str], pairs: pl.DataFrame) -> pl.DataFrame:
    """只取決策列需要的 (date, stock_id)：暫存表 join，逐因子 COPY 回來（全表 2 億列不能整批拉）。"""
    out = pairs
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _pairs (date date, stock_id text) ON COMMIT DROP")
        with cur.copy("COPY _pairs (date, stock_id) FROM STDIN") as cp:
            for d, s in pairs.iter_rows():
                cp.write_row((d, s))
        cur.execute("ANALYZE _pairs")
        for f in factors:
            buf = io.BytesIO()
            with cur.copy(
                f'COPY (SELECT v.date, v.stock_id, v.z FROM "{SCHEMA}".factor_values v '
                "JOIN _pairs p ON v.stock_id = p.stock_id AND v.date = p.date "
                f"WHERE v.factor = '{f}') TO STDOUT WITH (FORMAT csv)"
            ) as cp:
                for chunk in cp:
                    buf.write(chunk)
            data = buf.getvalue()
            schema = {"date": pl.Date, _S: pl.String, f: pl.Float64}
            fr = (
                pl.read_csv(data, has_header=False, schema=schema)
                if data
                else pl.DataFrame(schema=schema)
            )
            out = out.join(fr, on=["date", _S], how="left")
    conn.commit()
    return out


def _batch1(conn: psycopg.Connection, mk: pl.DataFrame, pairs: pl.DataFrame) -> pl.DataFrame:
    """D-156 §二 M1 特徵。可得時間：借券 21:00 → 落後 1 日；當沖 T+2 定案 → 落後 2 日；注意／處置 18:00 → 當日。"""
    with conn.cursor() as cur:
        cur.execute(f'SELECT date, stock_id, sbl_sell, sbl_balance FROM "{SCHEMA}".sbl_short_daily')
        sbl = pl.DataFrame(
            cur.fetchall(), schema=["date", _S, "sbl_sell", "sbl_balance"], orient="row"
        )
        cur.execute(f'SELECT date, stock_id, daytrade_shares FROM "{SCHEMA}".daytrade_daily')
        dtr = pl.DataFrame(cur.fetchall(), schema=["date", _S, "dt_shares"], orient="row")
        cur.execute(f'SELECT date, stock_id FROM "{SCHEMA}".attention_stocks')
        att = pl.DataFrame(cur.fetchall(), schema=["date", _S], orient="row")
        cur.execute(f'SELECT date, stock_id, effective_end FROM "{SCHEMA}".disposition_stocks')
        dsp = pl.DataFrame(cur.fetchall(), schema=["ann", _S, "end"], orient="row")
    panel = (
        mk.select("date", _S, pl.col("volume").cast(pl.Float64))
        .join(sbl.with_columns(pl.col("date").cast(pl.Date)), on=["date", _S], how="left")
        .join(dtr.with_columns(pl.col("date").cast(pl.Date)), on=["date", _S], how="left")
        .join(
            att.with_columns(pl.col("date").cast(pl.Date), attn=pl.lit(1.0)),
            on=["date", _S],
            how="left",
        )
        .sort(_S, "date")
    )
    # 兩步算（不巢狀視窗）：先同日的 5 日量，再依可得時間往後移
    panel = panel.with_columns(
        (pl.col("sbl_sell").rolling_sum(5) / pl.col("volume").rolling_sum(5))
        .over(_S)
        .alias("_sbl5"),
        (
            (pl.col("sbl_balance") - pl.col("sbl_balance").shift(5))
            / pl.col("volume").rolling_mean(20)
        )
        .over(_S)
        .alias("_bal5"),
        (pl.col("dt_shares").rolling_sum(5) / pl.col("volume").rolling_sum(5))
        .over(_S)
        .alias("_dt5"),
        # 沒被列注意＝0（成員旗標，不是缺值）
        pl.col("attn").fill_null(0.0).rolling_sum(5).over(_S).alias("b1.attn5"),
    ).with_columns(
        pl.col("_sbl5").shift(1).over(_S).alias("b1.sbl_sell5"),  # 借券 21:00 才可得 → 落後 1 日
        pl.col("_bal5").shift(1).over(_S).alias("b1.sbl_bal_chg5"),
        pl.col("_dt5").shift(2).over(_S).alias("b1.dt_share5"),  # 當沖 T+2 定案 → 落後 2 日
    )
    feats = pairs.join(
        panel.select("date", _S, "b1.sbl_sell5", "b1.sbl_bal_chg5", "b1.dt_share5", "b1.attn5"),
        on=["date", _S],
        how="left",
    )
    # 處置中（含明天起生效）：公告日 ≤ 決策日 ≤ 生效迄日
    active = (
        pairs.join(
            dsp.with_columns(pl.col("ann").cast(pl.Date), pl.col("end").cast(pl.Date)), on=_S
        )
        .filter((pl.col("ann") <= pl.col("date")) & (pl.col("date") <= pl.col("end")))
        .select("date", _S)
        .unique()
        .with_columns(pl.lit(1.0).alias("b1.disposition"))
    )
    return feats.join(active, on=["date", _S], how="left").with_columns(
        pl.col("b1.disposition").fill_null(0.0)
    )


# ── 模型 ─────────────────────────────────────────────────────────────────────


def _quarters(lo: dt.date, hi: dt.date) -> list[tuple[dt.date, dt.date]]:
    out, y, m = [], lo.year, lo.month
    while dt.date(y, m, 1) <= hi:
        start = dt.date(y, m, 1)
        ny, nm = (y + (m + 2) // 12, (m + 2) % 12 + 1)
        out.append((start, min(dt.date(ny, nm, 1) - dt.timedelta(days=1), hi)))
        y, m = ny, nm
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pl.Series(a).rank("average").to_numpy()
    rb = pl.Series(b).rank("average").to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1]) if len(a) > 2 else float("nan")


def walk_forward(
    frame: pl.DataFrame, feats: list[str], cfg: MlConfig
) -> tuple[pl.DataFrame, list, dict]:
    """分季：只用標籤結束日早於季初 − 14 天的樣本訓練，預測該季的決策列。"""
    preds, quarters, imp = [], [], {f: 0.0 for f in feats}
    first = frame["date"].min()
    for q0, q1 in _quarters(EVAL[0], PATH_END):
        train = frame.filter(pl.col("exit_date") < q0 - EMBARGO)
        # D-156 §二 澄清：「至少兩年」＝季初距第一筆樣本滿兩年（標籤長一個月、再加 14 天 embargo）
        if train.is_empty() or q0 - first < MIN_TRAIN:  # type: ignore[operator]
            raise SystemExit(f"{q0}：距第一筆樣本不足兩年")
        test = frame.filter(pl.col("date").is_between(q0, q1))
        if test.is_empty():
            continue
        booster = mlt.fit_ranker(train, feats, cfg.lgbm, objective="regression")
        p = mlt.predict_scores(booster, test, feats).rename({"score": "pred"})
        preds.append(test.select("pos", "date", _S, "label").join(p, on=["date", _S]))
        rho = _spearman(preds[-1]["pred"].to_numpy(), preds[-1]["label"].to_numpy())
        quarters.append((q0, train.height, test.height, rho))
        gain = booster.feature_importance(importance_type="gain")
        tot = float(gain.sum()) or 1.0
        for f, g in zip(feats, gain, strict=True):
            imp[f] += float(g) / tot
    n = len(quarters)
    return pl.concat(preds), quarters, {f: v / n for f, v in imp.items()}


# ── 出場規則與評估 ────────────────────────────────────────────────────────────


def _first(cond: np.ndarray) -> np.ndarray:
    """每列第一個 True 的 k → 出場日 k+1；都沒有 → EXIT_DAY。"""
    hit = cond.any(axis=1)
    return np.where(hit, cond.argmax(axis=1) + 1, EXIT_DAY)


def _net(opens: np.ndarray, e: np.ndarray) -> np.ndarray:
    return opens[np.arange(len(e)), e] / opens[:, 0] - 1.0 - ROUND_TRIP


def _per_day(net: np.ndarray, e: np.ndarray) -> float:
    return float(net.sum() / e.sum())


def _month_t(
    month: np.ndarray, a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray]
) -> tuple[float, float, int]:
    """進場月區塊：每月 (Σnet/Σ天)_A − (Σnet/Σ天)_B 的平均與 t。"""
    df = pl.DataFrame({"m": month, "na": a[0], "ea": a[1], "nb": b[0], "eb": b[1]})
    g = df.group_by("m").agg(
        (pl.col("na").sum() / pl.col("ea").sum() - pl.col("nb").sum() / pl.col("eb").sum()).alias(
            "d"
        )
    )
    d = g["d"].to_numpy()
    d = d[np.isfinite(d)]
    if d.size < 2:
        return float("nan"), float("nan"), int(d.size)
    return float(d.mean()), float(d.mean() / (d.std(ddof=1) / math.sqrt(d.size))), int(d.size)


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument(
        "--with-batch1", action="store_true", help="另跑 M1（第 1 批資料；借券回補完成後）"
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    cfg = load_config(A20_CFG, MlConfig)
    base = [f for f in json.loads((A20_DIR / "meta.json").read_text("utf-8"))["features"]]
    stock_feats = [f for f in base if not f.startswith("ctx.")]
    ctx_feats = [f for f in base if f.startswith("ctx.")]
    print(
        f"**D-156：不設門檻、只報數字。** 第一階段逐筆；評估進場日 {EVAL[0]} ~ {EVAL[1]}、路徑 ≤ {PATH_END}"
    )
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        if args.with_batch1:
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(DISTINCT stock_id) FROM "{SCHEMA}".sbl_short_daily')
                n_sbl = cur.fetchone()[0]  # type: ignore[index]
                cur.execute(f'SELECT count(DISTINCT stock_id) FROM "{SCHEMA}".daytrade_daily')
                n_dt = cur.fetchone()[0]  # type: ignore[index]
            if n_sbl < 0.9 * n_dt:
                raise SystemExit(f"借券只有 {n_sbl} 檔（當沖 {n_dt}）——回補還沒完成，M1 先不跑")
        mk = load_market(conn).sort(_S, "date")
        print("── 部位")
        pos = build_positions(mk, _a20_scores())
        frame, prices = decision_frame(mk, pos)
        pairs = frame.select("date", _S).unique()
        print(
            f"  決策列 {frame.height:,}（{pos.height:,} 個部位 × {N_DECISIONS}）；讀 {len(stock_feats)} 支因子 …"
        )
        zf = _factor_z(conn, stock_feats, pairs)
        idx = read_index_close(conn, SCHEMA, "TAIEX", DATA_START, DATA_END)
        ctx = market_state_features(idx, mk.select("date", _S, "close"), window=cfg.context_window)
        b1 = _batch1(conn, mk, pairs) if args.with_batch1 else None
    frame = (
        frame.join(zf, on=["date", _S], how="left")
        .join(ctx.select("date", *ctx_feats), on="date", how="left")
        .join(
            _a20_scores().rename({"score": "a20.score", "pct": "a20.pct"}),
            on=["date", _S],
            how="left",
        )
    )
    if b1 is not None:
        frame = frame.join(b1, on=["date", _S], how="left")
    frame = frame.filter(pl.col("exit_date") <= PATH_END).sort("pos", "h.k")

    arms = {"M0": HOLD + RESCORE + stock_feats + ctx_feats}
    if args.with_batch1:
        arms["M1"] = arms["M0"] + B1

    # 評估部位：進場日在窗內、路徑完整
    ev = pos.filter(
        pl.col("entry_date").is_between(*EVAL) & (pl.col("exit_date") <= PATH_END)
    ).join(pl.DataFrame({"pos": pos["pos"], "ok": prices["ok"]}), on="pos")
    ev = ev.filter(pl.col("ok"))
    idx_rows = ev["pos"].to_numpy()
    pos_index = {int(p): i for i, p in enumerate(pos["pos"].to_numpy())}
    sel = np.array([pos_index[int(p)] for p in idx_rows])
    opens, close = prices["opens"][sel], prices["close"][sel]
    month = ev["entry_date"].dt.strftime("%Y-%m").to_numpy()
    cohort = ev["entry_date"].to_numpy()
    print(f"\n── 評估部位 {len(sel):,}（進場 {EVAL[0]} ~ {EVAL[1]}）")

    def grid(col: str) -> np.ndarray:
        g = (
            frame.filter(pl.col("pos").is_in(idx_rows))
            .select("pos", "h.k", col)
            .pivot(on="h.k", index="pos", values=col)
        )
        g = pl.DataFrame({"pos": idx_rows}).join(g, on="pos", how="left")
        return g.drop("pos").to_numpy().astype(np.float64)

    pct = grid("a20.pct")
    exits: dict[str, np.ndarray] = {
        "S0": np.full(len(sel), EXIT_DAY),
        "SIG": _first(np.nan_to_num(pct, nan=1.0) < SIG_PCT),  # 不在池內（空值）不觸發
        "STOP10": _first(close <= STOP * opens[:, [0]]),
    }
    skill: dict[str, tuple] = {}
    for name, feats in arms.items():
        preds, quarters, imp = walk_forward(frame, feats, cfg)
        pg = pl.DataFrame({"pos": idx_rows}).join(
            preds.join(frame.select("pos", "date", "h.k"), on=["pos", "date"])
            .select("pos", "h.k", "pred")
            .pivot(on="h.k", index="pos", values="pred"),
            on="pos",
            how="left",
        )
        pm = pg.drop("pos").to_numpy().astype(np.float64)
        if np.isnan(pm).any():
            raise SystemExit(f"{name}：評估部位有 {int(np.isnan(pm).sum())} 個決策沒有預測")
        exits[name] = _first(pm <= 0.0)
        skill[name] = (preds, quarters, imp)

    rng_exits = []
    for seed in range(SEEDS):
        rng = np.random.default_rng(seed)
        e = exits["M0"].copy()
        for d in np.unique(cohort):
            ix = np.flatnonzero(cohort == d)
            e[ix] = e[rng.permutation(ix)]
        rng_exits.append(e)

    print("\n── 各臂（每筆扣來回成本 0.40%；主指標＝每持有日淨報酬）")
    res = {}
    for name, e in exits.items():
        net = _net(opens, e)
        res[name] = (net, e)
        early = float((e < EXIT_DAY).mean())
        print(
            f"  {name:6} 每持有日 {_per_day(net, e):+.4%}　每筆 平均 {net.mean():+.2%}／中位 {np.median(net):+.2%}　"
            f"勝率 {(net > 0).mean():.1%}　提早出場 {early:.1%}　平均持有 {e.mean():.1f} 日"
        )
    rand_pd = np.array([_per_day(_net(opens, e), e) for e in rng_exits])
    print(
        f"  RAND   每持有日 20 seed 平均 {rand_pd.mean():+.4%}（5%~95%：{np.percentile(rand_pd, 5):+.4%} ~ {np.percentile(rand_pd, 95):+.4%}）"
    )
    oracle = (opens[:, 1:].max(axis=1) / opens[:, 0] - 1.0 - ROUND_TRIP).mean()
    print(f"  參考：同一最長持有期的天花板（第 1~21 天最高開盤賣）每筆 {oracle:+.2%}")

    print("\n── 差距（每持有日；進場月區塊 t）")
    for a in [n for n in exits if n != "S0"]:
        m, t, nm = _month_t(month, res[a], res["S0"])
        print(f"  {a} − S0：{m:+.4%}／日（t {t:+.2f}，{nm} 個月）")
    for a in arms:
        m_r = [_month_t(month, res[a], (_net(opens, e), e))[0] for e in rng_exits]
        beat = float((_per_day(*res[a]) > rand_pd).mean())
        print(
            f"  {a} − RAND：{_per_day(*res[a]) - rand_pd.mean():+.4%}／日（月區塊平均差 {np.mean(m_r):+.4%}；"
            f"勝過 {beat:.0%} 的 seed；高於第 95 百分位：{_per_day(*res[a]) > np.percentile(rand_pd, 95)}）"
        )

    for name, (preds, quarters, imp) in skill.items():
        rhos = np.array([q[3] for q in quarters])
        print(
            f"\n── 模型 {name}：{len(quarters)} 季；樣本外 Spearman 平均 {np.nanmean(rhos):+.3f}（正的季 {int((rhos > 0).sum())}／{len(rhos)}）"
        )
        dec = (
            preds.with_columns(
                ((pl.col("pred").rank("ordinal") - 1) * 10 / pl.len())
                .floor()
                .cast(pl.Int32)
                .alias("d")
            )
            .group_by("d")
            .agg(pl.col("label").mean())
            .sort("d")
        )
        print(
            "  預測十分位的實現 y（ln）："
            + "　".join(f"D{r['d'] + 1} {r['label']:+.2%}" for r in dec.iter_rows(named=True))
        )
        groups = {
            "持有狀態": HOLD,
            "A20 重評分": RESCORE,
            "大盤": ctx_feats,
            "第1批": B1,
        }
        shares = {g: sum(imp.get(f, 0.0) for f in fs) for g, fs in groups.items()}
        shares["個股特徵"] = 1.0 - sum(shares.values())
        print("  重要度分組（各季平均）：" + "　".join(f"{g} {s:.1%}" for g, s in shares.items()))
        top = sorted(imp.items(), key=lambda x: -x[1])[:10]
        print("  前十：" + "、".join(f"{f} {s:.1%}" for f, s in top))


if __name__ == "__main__":
    main()
