"""歷史相似情境（D-182／T-364）：本機建情境庫，跑上線前驗證並寫報告。

讀本機 PG：潛伏模型分數（母體）、22 個個股特徵（factor_values.z，只讀母體那些列）、行情與除權息（結果）、
加權指數與全市場收盤（大盤狀態）、成交量（爆量）。定義寫死在 configs/similar_situations.yaml（登記後不改）。
行情只讀到 dev_end（2026-08-31），第二保留期一律不碰。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant \\
    uv run --all-groups python scripts/similar_library.py \\
        --data-dir D:/project/twquant/data --report docs/reports/d182-similar-validation.md --validate
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import dividends_by_date, price_panel, splits_by_date
from twquant.backtest.list_trades import simulate_entries
from twquant.config import MlConfig, ServingConfig, load_config
from twquant.conventions import now_tw
from twquant.data import calendar as cal
from twquant.data.serving import store
from twquant.data.serving.ml_store import read_index_close, read_prices_barrier
from twquant.data.serving.pg_view import PgTableSource
from twquant.data.serving.similar_store import write_library
from twquant.ml.context import market_state_features
from twquant.reporting.pick_board import PickBoardConfig
from twquant.reporting.score_reference import ScoreReferenceConfig, volume_surge
from twquant.reporting.similar import (
    Library,
    SimilarConfig,
    decode_library,
    encode_library,
    gain_weights,
    reference,
    render_validation,
    validate_line,
    verdict,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
_UNIVERSE = r"^[1-9][0-9]{3}$"
_CONTEXT_INDEX_ID = "TAIEX"  # 同 ml/quiet_live.py


def _say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _pool(conn: Any, schema: str, model_id: str, start: dt.date, end: dt.date) -> pl.DataFrame:
    rows = _rows(
        conn,
        f'SELECT date, stock_id, score FROM "{schema}"."ml_scores" '
        "WHERE model_id = %s AND date BETWEEN %s AND %s",
        (model_id, start, end),
    )
    if not rows:
        raise SystemExit(f"ml_scores 沒有 {model_id} 在 {start}～{end} 的分數")
    return pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "score": pl.Float64}, orient="row"
    )


def _features(
    conn: Any, schema: str, model_id: str, names: list[str], start: dt.date, end: dt.date
) -> dict[str, pl.DataFrame]:
    """只讀母體（有分數的那些 date × stock_id）的 factor_values.z；伺服器端 join，免得把全市場 9 年拉回來。"""
    out: dict[str, pl.DataFrame] = {}
    for name in names:
        t0 = time.time()
        rows = _rows(
            conn,
            f'SELECT f.date, f.stock_id, f.z FROM "{schema}"."factor_values" f '
            f'JOIN "{schema}"."ml_scores" s ON s.date = f.date AND s.stock_id = f.stock_id '
            "WHERE s.model_id = %s AND f.factor = %s AND f.date BETWEEN %s AND %s",
            (model_id, name, start, end),
        )
        out[name] = pl.DataFrame(
            rows, schema={"date": pl.Date, "stock_id": pl.String, name: pl.Float64}, orient="row"
        )
        _say(f"  {name}：{len(rows):,} 列（{time.time() - t0:.0f} 秒）")
    return out


def _volumes(conn: Any, schema: str, start: dt.date, end: dt.date) -> pl.DataFrame:
    rows = _rows(
        conn,
        f'SELECT date, stock_id, volume FROM "{schema}"."prices_daily" '
        "WHERE date BETWEEN %s AND %s AND stock_id ~ %s",
        (start, end, _UNIVERSE),
    )
    return pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "volume": pl.Float64}, orient="row"
    )


def build_library(conn: Any, cfg: SimilarConfig, data_dir: Path) -> Library:
    """讀本機 PG，建公布日 oos_start～、賣出日 ≤ dev_end 的完整情境庫。"""
    scfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    cost = load_config(CONFIGS / "costs.yaml", CostConfig)
    ml_cfg = load_config(CONFIGS / "ml.yaml", MlConfig)
    model_cfg = load_config(CONFIGS / str(ml_cfg.quiet_live_config), MlConfig)
    schema = scfg.pg_schema
    if str(ml_cfg.quiet_live_model) != cfg.model_id:
        raise SystemExit(f"正式潛伏模型是 {ml_cfg.quiet_live_model}，登記的是 {cfg.model_id}")

    booster = lgb.Booster(model_file=str(data_dir / "models" / cfg.model_id / "model.txt"))
    again = gain_weights(
        booster.feature_name(), booster.feature_importance("gain").tolist(), cfg.feature_gain_cover
    )
    if again != cfg.feature_weights:
        raise SystemExit("登記的特徵權重與 model.txt 重算的不一致——模型檔換了？")

    lo = cfg.oos_start - dt.timedelta(days=cfg.warmup_days)
    source = PgTableSource(conn, schema, cache=True, since=lo)
    sessions = cal.load(source).between(cfg.oos_start, cfg.dev_end)
    if not sessions or sessions[-1] > cfg.dev_end:
        raise SystemExit("交易日序列不對")
    _say(f"{len(sessions)} 個交易日（{sessions[0]}～{sessions[-1]}）；讀行情面板…")
    panel = price_panel(source, cfg.oos_start, cfg.dev_end)
    divs = dividends_by_date(source, cfg.oos_start, cfg.dev_end)
    splits = splits_by_date(source, cfg.oos_start, cfg.dev_end)

    pool = _pool(conn, schema, cfg.model_id, cfg.oos_start, cfg.dev_end)
    _say(
        f"母體 {pool.height:,} 個情境、{pool['date'].n_unique()} 天；讀 {len(cfg.features)} 個特徵…"
    )
    frame = pool
    for df in _features(
        conn, schema, cfg.model_id, cfg.features, cfg.oos_start, cfg.dev_end
    ).values():
        frame = frame.join(df, on=["date", "stock_id"], how="left")

    _say("大盤狀態與爆量…")
    px = read_prices_barrier(conn, schema, lo, cfg.dev_end).filter(
        pl.col("stock_id").str.contains(_UNIVERSE) & (pl.col("close") > 0)
    )
    index_close = read_index_close(conn, schema, _CONTEXT_INDEX_ID, lo, cfg.dev_end)
    ctx = market_state_features(
        index_close, px.select("date", "stock_id", "close"), window=model_cfg.context_window
    )
    surge = volume_surge(
        _volumes(conn, schema, lo, cfg.dev_end), cfg.surge_window, cfg.surge_ratio_min
    ).with_columns(pl.lit(True).alias("surge"))
    frame = frame.join(surge, on=["date", "stock_id"], how="left").with_columns(
        pl.col("surge").fill_null(False)  # 不在爆量清單＝沒有爆量（不是缺值）
    )

    _say(f"逐筆結果（{frame.height:,} 筆，同 D-179：隔天開盤買、抱 {cfg.hold_sessions} 天）…")
    t0 = time.time()
    frame = frame.sort("date", "stock_id")
    entries = [(d, s) for d, s in frame.select("date", "stock_id").iter_rows()]
    trades = simulate_entries(
        entries,
        sessions,
        panel,
        divs,
        splits,
        cost,
        hold_sessions=cfg.hold_sessions,
        notional=cfg.notional,
        max_participation=cfg.max_participation,
        lot=cfg.lot_size,
    )
    status: dict[str, int] = {}
    for t in trades:
        status[t.status] = status.get(t.status, 0) + 1
    _say(f"  {time.time() - t0:.0f} 秒；{status}")
    idx = {d: i for i, d in enumerate(sessions)}
    keep = np.array([t.status == "closed" for t in trades])
    for t in trades:
        if t.status == "closed" and (t.exit_date is None or t.exit_date > cfg.dev_end):
            raise SystemExit(f"{t.stock_id} {t.signal_date} 在 {cfg.dev_end} 之後才賣出")
    closed = [t for t in trades if t.status == "closed"]
    frame = frame.filter(pl.Series(keep))

    ctx_mat = np.full((len(sessions), len(cfg.context_features)), np.nan)
    ctx_rows = ctx.select("date", *cfg.context_features).filter(pl.col("date").is_in(sessions))
    for row in ctx_rows.iter_rows():
        ctx_mat[idx[row[0]]] = row[1:]
    return Library(
        sessions=tuple(sessions),
        day=np.array([idx[d] for d in frame["date"].to_list()], dtype=np.int64),
        exit_day=np.array([idx[t.exit_date] for t in closed if t.exit_date], dtype=np.int64),
        sid=np.array(frame["stock_id"].to_list()),
        score=frame["score"].to_numpy().astype(np.float64),
        x=frame.select(cfg.features).to_numpy().astype(np.float64),
        surge=frame["surge"].to_numpy().astype(bool),
        ret=np.array([t.ret for t in closed], dtype=np.float64),
        ctx=ctx_mat,
    )


def check_inputs(conn: Any, cfg: SimilarConfig, lib: Library, data_dir: Path, days: int) -> None:
    """情境庫最後幾個交易日的特徵與大盤狀態，要跟每日打分（`quiet_live.load_inference`）組出來的一模一樣。

    雲端每天比對時用的是 load_inference；兩邊對不上，比的就不是同一種東西 → raise。
    """
    from twquant.ml.quiet_live import load_inference

    scfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    ml_cfg = load_config(CONFIGS / "ml.yaml", MlConfig)
    model_cfg = load_config(CONFIGS / str(ml_cfg.quiet_live_config), MlConfig)
    # 庫裡最後幾個「有情境」的交易日（dev_end 前 20 天內公布的還沒賣，不在庫裡）
    have = np.unique(lib.day)
    start, end = lib.sessions[int(have[-days])], lib.sessions[int(have[-1])]
    frame, _, _, _ = load_inference(
        conn,
        scfg.pg_schema,
        cfg=model_cfg,
        model_dir=data_dir / "models" / cfg.model_id,
        stock_pattern=_UNIVERSE,
        start=start,
        end=end,
        lookback_days=ml_cfg.quiet_price_lookback_days,
    )
    pos = {d: i for i, d in enumerate(lib.sessions)}
    mine = pl.DataFrame(
        {
            "date": [lib.sessions[i] for i in lib.day],
            "stock_id": lib.sid.tolist(),
            **{f: lib.x[:, j] for j, f in enumerate(cfg.features)},
        }
    ).filter(pl.col("date").is_between(start, end))
    live = frame.select("date", "stock_id", *cfg.features)
    both = mine.join(live, on=["date", "stock_id"], suffix="_live")
    if both.height == 0 or both.height < 0.9 * mine.height:
        raise SystemExit(f"{start}～{end} 只對到 {both.height}／{mine.height} 個情境——母體不一致")
    for f in cfg.features:
        a, b = both[f].to_numpy(), both[f"{f}_live"].to_numpy()
        same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9)
        if not same.all():
            raise SystemExit(f"特徵 {f} 有 {int((~same).sum())} 列跟每日打分的輸入不同")
    ctx_live = frame.select("date", *cfg.context_features).unique("date")
    for row in ctx_live.iter_rows():
        got = lib.ctx[pos[row[0]]]
        if not np.allclose(got, np.asarray(row[1:], dtype=np.float64), rtol=0, atol=1e-12):
            raise SystemExit(f"{row[0]} 的大盤狀態跟每日打分不同：{got} vs {row[1:]}")
    _say(f"輸入核對：{start}～{end} {both.height:,} 個情境的特徵與大盤狀態都跟每日打分相同")


def publish(lib: Library, cfg: SimilarConfig, scfg: ServingConfig, *, cloud: bool) -> None:
    """正式情境庫（賣出日 ≤ dev_end 的全部）壓縮後寫進本機 PG（可同時寫雲端）。寫之前先解回來核對。"""
    parts = encode_library(lib, cfg.features, cfg.registration)
    back, feats, reg = decode_library(parts)
    same = (
        feats == cfg.features
        and reg == cfg.registration
        and back.sessions == lib.sessions
        and np.array_equal(back.day, lib.day)
        and np.array_equal(back.sid, lib.sid)
        and np.array_equal(back.surge, lib.surge)
        and np.allclose(back.ret, lib.ret, rtol=0, atol=1e-6)
        and np.allclose(back.x, lib.x, rtol=1e-6, atol=1e-6, equal_nan=True)
    )
    if not same:
        raise SystemExit("情境庫壓縮後解不回原樣")
    size = sum(len(b) for b in parts.values())
    _say(f"情境庫壓縮後 {size / 1e6:.1f} MB、{len(parts)} 份")
    at = now_tw()
    conn = store.connect(scfg)
    try:
        write_library(conn, scfg.pg_schema, parts, cfg.registration, at)
    finally:
        conn.close()
    _say("已寫本機 serving.similar_library")
    if cloud:
        remote = store.connect_cloud(scfg)
        try:
            write_library(remote, scfg.pg_schema, parts, cfg.registration, at)
        finally:
            remote.close()
        _say("已寫雲端 serving.similar_library")


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":  # Windows 主控台
        getattr(sys.stdout, "reconfigure")(encoding="utf-8")  # noqa: B009
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", required=True, help="data 目錄（絕對路徑，讀模型檔）")
    ap.add_argument("--report", help="驗證報告輸出路徑（markdown；--validate 才要）")
    ap.add_argument("--validate", action="store_true", help="跑上線前驗證（D-182 §二）")
    ap.add_argument("--publish", action="store_true", help="把正式情境庫寫進本機 PG（D-182 §三）")
    ap.add_argument(
        "--cloud", action="store_true", help="--publish 時也寫進雲端（TWQUANT_SUPABASE_DSN）"
    )
    args = ap.parse_args()
    if args.validate and not args.report:
        ap.error("--validate 要給 --report")
    if args.cloud and not args.publish:
        ap.error("--cloud 要跟 --publish 一起用")

    cfg = load_config(CONFIGS / "similar_situations.yaml", SimilarConfig)
    buckets = load_config(CONFIGS / "score_reference.yaml", ScoreReferenceConfig).buckets
    board = load_config(CONFIGS / "pick_board.yaml", PickBoardConfig)
    names = {s.code: s.name for s in board.strategies}
    scfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    conn = store.connect(scfg)
    try:
        lib = build_library(conn, cfg, Path(args.data_dir))
        check_inputs(conn, cfg, lib, Path(args.data_dir), days=5)
    finally:
        conn.close()
    _say(f"情境庫 {len(lib):,} 個情境（其中爆量 {int(lib.surge.sum()):,}）")
    if args.publish:
        publish(lib, cfg, scfg, cloud=args.cloud)
    if not args.validate:
        return

    v = cfg.validation
    pos = {d: i for i, d in enumerate(lib.sessions)}
    sell_end = max(i for d, i in pos.items() if d <= v.library_sell_end)
    q_start = min(i for d, i in pos.items() if d >= v.query_start)
    library = lib.take(lib.exit_day <= sell_end)
    questions = lib.take(lib.day >= q_start)
    _say(f"驗證庫 {len(library):,}、題目 {len(questions):,}")
    weights = np.array([cfg.feature_weights[f] for f in cfg.features])
    results = {}
    refs = {}
    for line in cfg.lines:
        t0 = time.time()
        days = validate_line(
            library, questions, weights, cfg, buckets, surge_only=line in cfg.surge_lines
        )
        results[line] = verdict(days, v.bootstrap, v.seed, v.ci)
        refs[line] = reference(
            library, questions, days, cfg.big_loss, surge_only=line in cfg.surge_lines
        )
        r = results[line]
        _say(
            f"{line}（{time.time() - t0:.0f} 秒）：RankIC 差 {r.d_ic:+.4f} {r.d_ic_ci}；"
            f"Brier 差 {r.d_brier:+.4f} {r.d_brier_ci}；{'通過' if r.passed else '不通過'}"
        )
    span = (lib.sessions[int(library.day.min())], lib.sessions[int(library.day.max())])
    out = Path(str(args.report))
    out.write_text(
        render_validation(cfg, results, names, len(library), span, refs)
        + f"\n產生於 {now_tw():%Y-%m-%d %H:%M}（{cfg.registration}）\n",
        encoding="utf-8",
    )
    _say(f"報告：{out}")


if __name__ == "__main__":
    main()
