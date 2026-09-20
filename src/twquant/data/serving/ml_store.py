"""ml_scores 直寫（SPEC-010 §5）：`ml score` 推論結果寫入 serving.ml_scores。

寫入前過 pandera 驗證（AC-7，壞批整批 raise 不落地）；ON CONFLICT
(model_id, stock_id, date) DO NOTHING——分數決定性，重跑冪等。
"""

from __future__ import annotations

from typing import Any

import polars as pl

from twquant.data import schemas

_COLS = ("date", "stock_id", "model_id", "score", "rank_pct", "available_at")


def read_factor_frames(
    conn: Any, schema: str, features: list[str], start: object, end: object
) -> dict[str, pl.DataFrame]:
    """factor_values.z 逐因子讀取 → {因子: (date, stock_id, value)}。

    逐因子查詢＋server 端過濾＋只取三欄：factor_values 全史 2 千萬列級，整表拉回
    （PgTableSource.scan）曾把 GHA runner 記憶體打爆（ml-first-train run #1 遭 OOM
    殺 VM）。無資料因子回空表——缺值語意由 assemble 保留（禁填 0）。
    """
    empty = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.String, "value": pl.Float64})
    out: dict[str, pl.DataFrame] = {}
    with conn.cursor() as cur:
        for name in features:
            cur.execute(
                f'SELECT date, stock_id, z FROM "{schema}"."factor_values" '
                "WHERE factor = %s AND date BETWEEN %s AND %s",
                (name, start, end),
            )
            rows = cur.fetchall()
            out[name] = pl.DataFrame(rows, schema=empty.schema, orient="row") if rows else empty
    return out


def read_factor_long(
    conn: Any, schema: str, features: list[str], start: object, end: object
) -> pl.DataFrame:
    """factor_values 的**原始 value** 長表 (date, stock_id, factor, value)。

    基準組合（strategy/combine.zscore_weighted）自己做 winsorize+z-score，故餵原始值
    而非已標準化的 z——與策略層的真實路徑一致，對照才公平。逐因子查詢，同 OOM 教訓。
    """
    parts: list[pl.DataFrame] = []
    schema_ = {"date": pl.Date, "stock_id": pl.String, "value": pl.Float64}
    with conn.cursor() as cur:
        for name in features:
            cur.execute(
                f'SELECT date, stock_id, value FROM "{schema}"."factor_values" '
                "WHERE factor = %s AND date BETWEEN %s AND %s AND value IS NOT NULL",
                (name, start, end),
            )
            rows = cur.fetchall()
            if rows:
                parts.append(
                    pl.DataFrame(rows, schema=schema_, orient="row").with_columns(
                        pl.lit(name).alias("factor")
                    )
                )
    if not parts:
        raise ValueError(f"factor_values 於 {start}~{end} 無任何指定因子資料")
    return pl.concat(parts).select("date", "stock_id", "factor", "value")


def read_prices_open(conn: Any, schema: str, start: object, end: object) -> pl.DataFrame:
    """prices_daily 的 (date, stock_id, open) 切片（server 端過濾，標籤計算用）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, open FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND open IS NOT NULL",
            (start, end),
        )
        rows = cur.fetchall()
    schema_ = {"date": pl.Date, "stock_id": pl.String, "open": pl.Float64}
    return pl.DataFrame(rows, schema=schema_, orient="row")


def read_corporate_events(conn: Any, schema: str, end: object) -> pl.DataFrame:
    """corporate_events 的 (date, stock_id, adj_factor) 切片（標籤總報酬還原用）。

    不設下界：累積因子是固定過去錨的連乘，錨之前的事件在報酬比值中恆等抵銷，
    故載入 `date <= end` 已足夠且與載入全期等價。
    """
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, adj_factor FROM "{schema}"."corporate_events" '
            "WHERE date <= %s AND adj_factor IS NOT NULL",
            (end,),
        )
        rows = cur.fetchall()
    schema_ = {"date": pl.Date, "stock_id": pl.String, "adj_factor": pl.Float64}
    return pl.DataFrame(rows, schema=schema_, orient="row")


def read_prices_training(conn: Any, schema: str, start: object, end: object) -> pl.DataFrame:
    """讀取成本感知標籤所需的行情欄位；期間在 PG server-side 過濾。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, open, amount FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND open IS NOT NULL",
            (start, end),
        )
        rows = cur.fetchall()
    schema_ = {
        "date": pl.Date,
        "stock_id": pl.String,
        "open": pl.Float64,
        "amount": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema_, orient="row")


def read_prices_close(conn: Any, schema: str, start: object, end: object) -> pl.DataFrame:
    """prices_daily 的 (date, stock_id, close) 切片（D-075 的回撤標籤以收盤價為基準）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, close FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND close IS NOT NULL",
            (start, end),
        )
        rows = cur.fetchall()
    schema_ = {"date": pl.Date, "stock_id": pl.String, "close": pl.Float64}
    return pl.DataFrame(rows, schema=schema_, orient="row")


def read_monthly_revenue(conn: Any, schema: str, start: object, end: object) -> pl.DataFrame:
    """monthly_revenue 的 (stock_id, period_end, available_at, yoy) 切片（D-074 標籤用）。

    下界放寬一個月：訊號日 `start` 當天要看得到「上一次公布」，而那次公布可能落在
    `start` 之前——用 `start` 直接切會讓首月的加速度整排變 null。
    """
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT stock_id, period_end, available_at, yoy FROM "{schema}"."monthly_revenue" '
            "WHERE available_at <= %s AND yoy IS NOT NULL "
            "AND available_at >= (%s::date - INTERVAL '120 days')",
            (end, start),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "stock_id": pl.String,
            "period_end": pl.Date,
            "available_at": pl.Datetime("us", "Asia/Taipei"),
            "yoy": pl.Float64,
        },
        orient="row",
    )


def upsert_ml_scores(conn: Any, schema: str, frame: pl.DataFrame) -> int:
    """驗證後批次寫入；回傳實際新增列數（衝突略過不計）。

    走 **COPY → 暫存表 → INSERT SELECT**，不用 `executemany`。
    後者對每一列各一次往返：長窗口模型的 walk-forward 樣本外分數約 450 萬列，
    實測跑 71 分鐘還沒寫完，而 PG 端一直是 `ClientRead`——瓶頸在送資料的方式，
    不在資料庫。COPY 是單一串流，同一批資料以秒計。

    語意與原本逐列版**相同**：批內重複鍵取第一列（`DISTINCT ON`，與逐列版
    「先插入的贏、後來的被 DO NOTHING 擋掉」一致），與既有列衝突則略過。
    """
    df = schemas.validate("ml_scores", frame.select(_COLS))
    if df.height == 0:
        return 0
    cols = ", ".join(_COLS)
    with conn.cursor() as cur:
        # ON COMMIT DROP：暫存表活在這個交易裡，例外時不留殘骸
        cur.execute(
            f'CREATE TEMP TABLE _ml_scores_stage (LIKE "{schema}"."ml_scores") ON COMMIT DROP'
        )
        with cur.copy(f"COPY _ml_scores_stage ({cols}) FROM STDIN") as cp:
            for row in df.iter_rows():
                cp.write_row(row)
        cur.execute(
            f'INSERT INTO "{schema}"."ml_scores" ({cols}) '
            f"SELECT DISTINCT ON (model_id, stock_id, date) {cols} FROM _ml_scores_stage "
            "ORDER BY model_id, stock_id, date "
            "ON CONFLICT (model_id, stock_id, date) DO NOTHING"
        )
        inserted = cur.rowcount
    conn.commit()
    return int(inserted)


def read_prices_barrier(conn: Any, schema: str, start: object, end: object) -> pl.DataFrame:
    """prices_daily 的 (date, stock_id, open, close, amount) 切片（T-248：triple-barrier 標籤
    要開盤進場價與逐日收盤，流動性樣本池要成交金額）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, open, close, amount FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND close IS NOT NULL",
            (start, end),
        )
        rows = cur.fetchall()
    schema_ = {
        "date": pl.Date,
        "stock_id": pl.String,
        "open": pl.Float64,
        "close": pl.Float64,
        "amount": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema_, orient="row")


def read_index_close(
    conn: Any, schema: str, index_id: str, start: object, end: object
) -> pl.DataFrame:
    """index_daily 的 (date, close) 切片（T-248：大盤狀態特徵）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, close FROM "{schema}"."index_daily" '
            "WHERE index_id = %s AND date BETWEEN %s AND %s AND close IS NOT NULL ORDER BY date",
            (index_id, start, end),
        )
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema={"date": pl.Date, "close": pl.Float64}, orient="row")


def read_model_rank_pct(
    conn: Any, schema: str, model_id: str, start: object, end: object
) -> pl.DataFrame:
    """ml_scores 的 (date, stock_id, rank_pct) 切片（T-248：別的模型的樣本外分位當特徵）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, rank_pct FROM "{schema}"."ml_scores" '
            "WHERE model_id = %s AND date BETWEEN %s AND %s",
            (model_id, start, end),
        )
        rows = cur.fetchall()
    schema_ = {"date": pl.Date, "stock_id": pl.String, "rank_pct": pl.Float64}
    return pl.DataFrame(rows, schema=schema_, orient="row")
