"""LLM 用量記錄（SPEC-008 §3，llm_log.parquet）。

每次呼叫記模型、tokens、成本、prompt 版本；供日成本上限判斷（AC-4）與稽核（AC-5）。
append-only 分片。落地在 reporting（IO 層）——洞悉層 insight/ 屬純運算層、不得直接寫檔。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

_SCHEMA = {
    "task": pl.String,
    "model_id": pl.String,
    "prompt_version": pl.String,
    "input_tokens": pl.Int64,
    "output_tokens": pl.Int64,
    "cost_twd": pl.Float64,
    "date": pl.String,  # YYYY-MM-DD
    "ts": pl.String,
}


def _dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "llm_log"


def record_usage(
    data_dir: str | Path,
    *,
    task: str,
    model_id: str,
    prompt_version: str,
    input_tokens: int,
    output_tokens: int,
    cost_twd: float,
    now: dt.datetime,
) -> None:
    d = _dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    row = {
        "task": task,
        "model_id": model_id,
        "prompt_version": prompt_version,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_twd": cost_twd,
        "date": now.date().isoformat(),
        "ts": now.isoformat(),
    }
    stamp = now.strftime("%Y%m%dT%H%M%S%f")
    final = d / f"part-{stamp}.parquet"
    tmp = d / f"part-{stamp}.parquet.tmp"
    pl.DataFrame([row], schema=_SCHEMA).write_parquet(tmp)
    tmp.replace(final)


def read_usage(data_dir: str | Path) -> pl.DataFrame:
    files = sorted(_dir(data_dir).glob("part-*.parquet"))
    if not files:
        return pl.DataFrame(schema=_SCHEMA)
    return pl.read_parquet(files)


def today_cost_twd(data_dir: str | Path, day: dt.date) -> float:
    """當日累計 LLM 成本（TWD）。"""
    log = read_usage(data_dir)
    if log.is_empty():
        return 0.0
    same = log.filter(pl.col("date") == day.isoformat())
    if same.is_empty():
        return 0.0
    return float(same.select(pl.col("cost_twd").sum()).item())
