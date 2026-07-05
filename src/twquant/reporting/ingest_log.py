"""擷取日誌 ingest_log（SPEC-001 §1/§6）。

記錄每一次資料擷取嘗試（來源、表、日期、時點、狀態、列數、細節），供人（或
儀表板）在事後檢視歷史/每日的擷取狀態、缺口與失敗——不需要 LLM 參與。
append-only 分片寫入（SPEC-001 §7），並行安全。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from twquant.conventions import now_tw

_SCHEMA = {
    "source": pl.String,
    "table": pl.String,
    "date": pl.String,  # 資料所屬日期（YYYY-MM-DD 或 YYYY-MM）
    "attempt_ts": pl.String,  # 擷取時點（ISO，台北）
    "status": pl.String,  # success | failed
    "rows": pl.Int64,
    "detail": pl.String,  # 失敗原因（成功為空）
}


def _dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "ingest_log"


def record(
    data_dir: str | Path,
    source: str,
    table: str,
    date: dt.date | str,
    status: str,
    rows: int = 0,
    detail: str = "",
) -> None:
    """記一筆擷取嘗試。"""
    d = _dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = now_tw()
    row = {
        "source": source,
        "table": table,
        "date": date.isoformat() if isinstance(date, dt.date) else str(date),
        "attempt_ts": ts.isoformat(),
        "status": status,
        "rows": rows,
        "detail": detail[:300],
    }
    df = pl.DataFrame([row], schema=_SCHEMA)
    stamp = ts.strftime("%Y%m%dT%H%M%S%f")
    final = d / f"part-{stamp}.parquet"
    tmp = d / f"part-{stamp}.parquet.tmp"
    df.write_parquet(tmp)
    tmp.replace(final)


def read_ingest_log(data_dir: str | Path) -> pl.DataFrame:
    """讀全部擷取紀錄（union）。"""
    files = sorted(_dir(data_dir).glob("part-*.parquet"))
    if not files:
        return pl.DataFrame(schema=_SCHEMA)
    return pl.read_parquet(files)


def recent_failures(data_dir: str | Path, limit: int = 20) -> pl.DataFrame:
    """最近的失敗紀錄（依時點新到舊）。"""
    log = read_ingest_log(data_dir)
    if log.is_empty():
        return log
    return log.filter(pl.col("status") == "failed").sort("attempt_ts", descending=True).head(limit)


def last_run(data_dir: str | Path) -> str | None:
    """最後一次擷取時點。"""
    log = read_ingest_log(data_dir)
    if log.is_empty():
        return None
    return str(log["attempt_ts"].max())
