"""A20（安靜股 20 日 +20% 模型）逐日名單：每天池內前 15 名，只列「過去 20 個交易日沒進過前 15」的新名字。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/a20_picks.py --to 2026-08-31 --show 5
    TWQUANT_PG_DSN=... uv run --all-groups python scripts/a20_picks.py --to 2026-08-10 --show 5 --outcome

用最終模型（只訓練到 2026-04-30）對 `--to` 之前約 40 個交易日逐日打分；特徵、大盤狀態、流動性與安靜池篩選
全用訓練時的同一組函數（同 `d151_report.vault_scores`，只是沒有標籤）。任何一支特徵在某天整欄空 → 停下，不帶著缺值打分。
預設只列名單；`--outcome` 另列隔日開盤進場後 20 個交易日的漲跌（同 `load_market` 的 fwd20 慣例）與同日池內平均——
**用到的價格一律 < 2026-09-09**（第二保留期，D-109／D-148），訊號日太新、20 日窗會越界就拒絕。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

import lightgbm as lgb
import polars as pl
import psycopg

from twquant.config import MlConfig, load_config
from twquant.data.serving.ml_store import read_factor_frames, read_index_close
from twquant.ml import train as mlt
from twquant.ml.context import market_state_features
from twquant.ml.dataset import liquid_pool, state_pool

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d151_report import DATA_START, SCHEMA, load_market

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "ml_quiet_a20.yaml"
MODEL = pathlib.Path("D:/project/twquant/data/models/lgbm_quietA20_tu_h20_20260430_655df6d7")
TOP_N = 15
LOOKBACK_NEW = 20  # 「新」＝過去 20 個交易日沒進過前 15（第十三／十四輪的不重買規則）
H = 20  # 漲跌窗：隔日開盤進場 → 第 20 個交易日收盤（同 d151_report 的 fwd20）
HOLDOUT_START = dt.date(2026, 9, 9)  # 第二保留期起日：任何報酬計算都不得用到這天以後的價格
_S = "stock_id"


def _outcomes(conn: psycopg.Connection, days: list[dt.date]) -> pl.DataFrame:
    """days 每個訊號日、全部四碼股：隔日開盤（還原）進場，20 日內最高／最低收盤與第 20 日收盤的報酬。"""
    from twquant.data.serving.ml_store import read_corporate_events
    from twquant.ml.dataset import attach_adjustment

    last = HOLDOUT_START - dt.timedelta(days=1)
    with conn.cursor() as cur:
        cur.execute(
            f"""select date, stock_id, open, close from "{SCHEMA}".prices_daily
                where date between %s and %s and stock_id ~ '^[1-9][0-9]{{3}}$' and close > 0""",
            (min(days), last),
        )
        rows = cur.fetchall()
        px = pl.DataFrame(rows, schema=["date", _S, "open", "close"], orient="row").with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("open").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
        )
        px = attach_adjustment(px, read_corporate_events(conn, SCHEMA, last)).sort(_S, "date")
    if px["date"].max() >= HOLDOUT_START:  # type: ignore[operator]
        raise SystemExit("用到第二保留期的價格——停止")
    p = pl.col("close") / pl.col("adj")
    entry = (pl.col("open") / pl.col("adj")).shift(-1).over(_S)
    fwd = [p.shift(-k).over(_S) / entry - 1.0 for k in range(1, H + 1)]
    out = px.with_columns(
        entry_date=pl.col("date").shift(-1).over(_S),
        end_date=pl.col("date").shift(-H).over(_S),
        fwd20=p.shift(-H).over(_S) / entry - 1.0,
        maxfwd=pl.max_horizontal(fwd),
        minfwd=pl.min_horizontal(fwd),
    ).filter(pl.col("date").is_in(days))
    # 用市場交易日曆判斷窗口有沒有越界（個股停牌造成的空值不算越界，只標「不完整」）
    cal = sorted(px["date"].unique().to_list())
    if cal.index(max(days)) + H > len(cal) - 1:
        raise SystemExit(f"{max(days)} 的 20 日窗會越過 {last}（第二保留期）——訊號日請再早一點")
    return out.select("date", _S, "entry_date", "end_date", "fwd20", "maxfwd", "minfwd")


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--to", required=True, help="最後一個訊號日（含）")
    ap.add_argument("--show", type=int, default=5, help="列最後幾個交易日")
    ap.add_argument(
        "--outcome", action="store_true", help="另列 20 日漲跌（價格只用到 2026-09-08）"
    )
    ap.add_argument("--json", help="另把列出的名單寫成 JSON（給頁面用）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    end = dt.date.fromisoformat(args.to)
    cfg = load_config(CFG, MlConfig)
    meta = json.loads((MODEL / "meta.json").read_text("utf-8"))
    features = [str(f) for f in meta["features"]]
    factor_feats = [f for f in features if not f.startswith("ctx.")]
    ctx_cols = [f for f in features if f.startswith("ctx.")]
    booster = lgb.Booster(model_file=str(MODEL / "model.txt"))
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = load_market(conn).filter(pl.col("date") <= end)
        days = sorted(mk["date"].unique().to_list())
        lo = days[-(args.show + LOOKBACK_NEW + 5)]
        feats = read_factor_frames(conn, SCHEMA, factor_feats, lo, end)
        idx = read_index_close(conn, SCHEMA, "TAIEX", DATA_START, end)
        with conn.cursor() as cur:
            cur.execute(f'SELECT stock_id, name, market, industry FROM "{SCHEMA}".stock_meta')
            meta_df = pl.DataFrame(
                cur.fetchall(), schema=[_S, "name", "market", "industry"], orient="row"
            )
    px = mk.select("date", _S, "open", "close", "amount", "adj")
    frame = px.filter(pl.col("date") >= lo).select("date", _S, "close")
    for f in factor_feats:
        fr = feats[f]
        frame = frame.join(fr.rename({"value": f}), on=["date", _S], how="left")
    ctx = market_state_features(idx, mk.select("date", _S, "close"), window=cfg.context_window)
    frame = frame.join(ctx.select("date", *ctx_cols), on="date", how="left")
    pool = liquid_pool(
        px,
        adv_window=cfg.label.adv_window_sessions,
        min_adv_pctl=cfg.sample_min_adv_pctl,
        min_price=cfg.sample_min_price,
    )
    assert cfg.sample_state is not None
    frame = frame.join(pool, on=["date", _S]).join(
        state_pool(px, cfg.sample_state, adjusted=True), on=["date", _S]
    )
    for f in features:
        empty = frame.group_by("date").agg(pl.col(f).is_null().all().alias("e")).filter("e")
        if empty.height:
            raise SystemExit(
                f"特徵 {f} 在 {sorted(empty['date'].to_list())[:5]} 整欄空——先補物化再打分"
            )
    scores = mlt.predict_scores(booster, frame, features).join(
        frame.select("date", _S, "close"), on=["date", _S]
    )
    ranked = scores.with_columns(
        pl.col("score").rank("ordinal", descending=True).over("date").alias("rank"),
        pl.len().over("date").alias("pool"),
    )
    top = ranked.filter(pl.col("rank") <= TOP_N).sort("date", "rank")
    sdays = sorted(top["date"].unique().to_list())
    shown = sdays[-args.show :]
    outc = None
    if args.outcome:
        with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
            outc = _outcomes(conn, shown)
    seen: dict[str, int] = {}
    tag = "20 日漲跌只用到 2026-09-08 的價格" if args.outcome else "只列名字，不算事後報酬"
    print(f"**A20 名單（模型 {MODEL.name}；{tag}）**")
    all_new = []
    dump: list[dict[str, object]] = []
    for i, d in enumerate(sdays):
        today = top.filter(pl.col("date") == d)
        new = [s for s in today[_S].to_list() if i - seen.get(s, -(10**9)) > LOOKBACK_NEW]
        for s in today[_S].to_list():
            if s in new or s not in seen:
                seen[s] = i
        if d not in shown:
            continue
        rows = today.filter(pl.col(_S).is_in(new)).join(meta_df, on=_S, how="left")
        pool_n = int(today["pool"][0])
        print(f"\n{d}（安靜∩流動池 {pool_n} 檔；前 {TOP_N} 名裡新進 {rows.height} 檔）")
        day: dict[str, object] = {"date": d.isoformat(), "pool_n": pool_n}
        if outc is not None:
            rows = rows.join(outc, on=["date", _S], how="left")
            pool_day = ranked.filter(pl.col("date") == d).join(outc, on=["date", _S])
            day["pool_avg"] = float(pool_day["fwd20"].mean())
            day["pool_hit20"] = float((pool_day["maxfwd"] >= 0.20).mean())
            print(
                f"  同日池內全部 {pool_day.height} 檔：20 日平均 {float(pool_day['fwd20'].mean()):+.1%}、"
                f"20 日內碰到 +20% 的 {float((pool_day['maxfwd'] >= 0.20).mean()):.0%}"
            )
            all_new.append(rows)
        keep = [
            c
            for c in (
                "rank",
                _S,
                "name",
                "market",
                "industry",
                "close",
                "score",
                "entry_date",
                "end_date",
                "fwd20",
                "maxfwd",
                "minfwd",
            )
            if c in rows.columns
        ]
        day["picks"] = [
            {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in r.items()}
            for r in rows.select(keep).iter_rows(named=True)
        ]
        dump.append(day)
        for r in rows.iter_rows(named=True):
            line = (
                f"  #{r['rank']:>2}  {r[_S]} {r['name'] or '':<8} {r['market'] or '':<5} "
                f"{(r['industry'] or '')[:10]:<10} 收盤 {r['close']:>8.2f}　模型機率 {r['score']:.1%}"
            )
            if outc is not None and r["fwd20"] is None:
                line += "　｜ 20 日資料不完整（停牌或下市），不列漲跌"
            elif outc is not None:
                line += (
                    f"　｜ {r['entry_date']} 開盤進 → {r['end_date']} 收盤 {r['fwd20']:+.1%}　"
                    f"期間最高 {r['maxfwd']:+.1%}、最低 {r['minfwd']:+.1%}"
                    + ("　★碰到 +20%" if r["maxfwd"] >= 0.20 else "")
                )
            print(line)
    if all_new:
        a = pl.concat(all_new)
        print(
            f"\n合計新進 {a.height} 檔：20 日平均 {float(a['fwd20'].mean()):+.1%}、中位 {float(a['fwd20'].median()):+.1%}、"
            f"上漲 {float((a['fwd20'] > 0).mean()):.0%}、20 日內碰到 +20% 的 {int((a['maxfwd'] >= 0.20).sum())} 檔"
            "（未扣成本；五天、幾十檔，只是舉例，不是驗證）"
        )
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(
                {"model": MODEL.name, "outcome": args.outcome, "days": dump}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        print(f"\nJSON → {args.json}")


if __name__ == "__main__":
    main()
