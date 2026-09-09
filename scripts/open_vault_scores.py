"""金庫期推論（D-106 第一步）：用**凍結的模型**對 2026-03 起的未見資料算分數。

與 `ml score` 的唯一差別是 `available_at`：那條命令寫 `now_tw()`（生產路徑，當下推論），
本腳本寫**訊號日 19:00**——因為金庫是在事後補算「當時本來就算得出來的分數」，
寫 now 會讓 PitView 在每一個金庫日都看不到自己的分數，池子整個空掉。
語意與 `ml backfill-scores` 一致（見其 docstring）。

月頻模型（rev_h15／dd_h60）只在月底出分數並沿用到下一個月底，逐字複製訓練期的節奏；
日頻模型（net_h20）每個交易日都算。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twquant.cli import _carry_forward_scores, _load_serving, _ml_feature_frames  # noqa: E402
from twquant.conventions import TZ  # noqa: E402
from twquant.data.serving import store  # noqa: E402
from twquant.data.serving.ml_store import upsert_ml_scores  # noqa: E402
from twquant.data.serving.schema import ensure_schema  # noqa: E402
from twquant.ml import score as mls  # noqa: E402
from twquant.ml import train as mlt  # noqa: E402
from twquant.ml.dataset import assemble, cross_rank_pct  # noqa: E402

MODELS = Path("D:/project/twquant/data/models")
# （模型目錄, 是否月頻）——月頻的判定來自訓練期的訊號日節奏，不是猜的
SPEC = [
    ("lgbm_lw_net_h20_20260225_83890036", False),
    ("lgbm_lw_rev_h15_20260225_9d4a149c", True),
    ("lgbm_lw_dd_h60_20260225_1bd7c6a0", True),
]
# D-106 寫定的視窗；推論多算到 09-08 是為了讓 IC 有最多的前推報酬可用
FEAT_START, VAULT_END = dt.date(2026, 2, 1), dt.date(2026, 9, 8)


def _calendar(conn: object, schema: str, lo: dt.date, hi: dt.date) -> list[dt.date]:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f'SELECT DISTINCT date FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s ORDER BY date",
            (lo, hi),
        )
        return [r[0] for r in cur.fetchall()]


def _month_ends(days: list[dt.date]) -> list[dt.date]:
    """每個月的最後一個交易日。"""
    last: dict[tuple[int, int], dt.date] = {}
    for d in days:
        last[(d.year, d.month)] = d
    return sorted(last.values())


def main() -> None:
    scfg = _load_serving()
    conn = store.connect(scfg)
    ensure_schema(conn, scfg.pg_schema)
    cal = _calendar(conn, scfg.pg_schema, FEAT_START, VAULT_END)
    print(f"金庫日曆 {cal[0]} ~ {cal[-1]}，{len(cal)} 個交易日", flush=True)

    for name, monthly in SPEC:
        mdir = MODELS / name
        meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
        features = [str(f) for f in meta["features"]]
        booster, _ = mls.load_model(mdir, VAULT_END)

        sig_days = _month_ends(cal) if monthly else [d for d in cal if d >= dt.date(2026, 3, 2)]
        if monthly:
            # 2026-02 的月底分數要算：三月初的再平衡當時用的就是它
            sig_days = [d for d in sig_days if d <= VAULT_END]
        lo, hi = min(sig_days), max(sig_days)
        feats = _ml_feature_frames(conn, scfg.pg_schema, features, lo, hi)
        no_labels = pl.DataFrame(
            schema={"date": pl.Date, "stock_id": pl.String, "label": pl.Float64}
        )
        frame = assemble(feats, no_labels).drop("label").filter(pl.col("date").is_in(sig_days))
        if frame.is_empty():
            raise ValueError(f"{name}：{lo}~{hi} 組不出特徵，拒絕靜默跳過")

        scored = cross_rank_pct(
            mlt.predict_scores(booster, frame, features), "score", "rank_pct"
        ).with_columns(
            pl.lit(name).alias("model_id"),
            pl.col("date")
            .cast(pl.Datetime("us"))
            .dt.offset_by("19h")
            .dt.replace_time_zone(str(TZ))
            .alias("available_at"),
        )
        got = sorted(scored["date"].unique().to_list())
        print(f"\n{name}\n  訊號日 {len(got)}（{got[0]} ~ {got[-1]}）、{scored.height} 列", flush=True)
        if monthly:
            scored = _carry_forward_scores(scored, [d for d in cal if d >= got[0]], got)
            print(f"  沿用後 {scored['date'].n_unique()} 個交易日、{scored.height} 列", flush=True)
        n = upsert_ml_scores(conn, scfg.pg_schema, scored)
        print(f"  已寫入 PG：{n} 列", flush=True)

    conn.close()
    print("\n金庫期分數完成", flush=True)


if __name__ == "__main__":
    main()
