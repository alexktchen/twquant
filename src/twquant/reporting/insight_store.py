"""洞悉證據包 ↔ PG `serving.insight_packs`（T-373／D-187）。

證據包的正本仍是 `data/insights/<insight_run_id>.json`（不可變、內容定址）；PG 是它的副本：
雲端 runner 每次都是新機器，檔案留不住，網站也只讀 PG。兩個方向：

* `restore_packs`：pipeline 開跑前把 PG 裡同一個推薦 run 的證據包寫回 data/insights，
  讓 `run_insights` 的冪等鍵認得它們——同一批證據、同一個 prompt 與模型就不再呼叫語言模型
  （雲端一天跑兩班，第二班不該再花一次 Neurons、也不該用另一份隨機結果覆蓋第一份）。
* `publish_packs`：跑完把這個 run 的證據包整包 upsert 進 PG。

只有 reporting 層碰 PG；insight/ 仍是純運算層。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl

from twquant.data.serving import schema as sch
from twquant.data.serving.sync import upsert
from twquant.errors import DataError
from twquant.reporting.insight_evidence import InsightPack, save_pack, select_packs

_log = logging.getLogger(__name__)
_SPEC = sch.INSIGHT_PACKS_SPEC


def packs_frame(packs: Sequence[InsightPack], now: dt.datetime) -> pl.DataFrame:
    """證據包 → 與 `INSIGHT_PACKS_SPEC` 同名同序的列；`pack` 欄是整包 JSON（給網站原樣讀）。"""
    rows = [
        {
            "insight_run_id": pack.insight_run_id,
            "recommendation_run_id": pack.recommendation_run_id,
            "stock_id": pack.stock_id,
            "date": pack.as_of.date(),
            "as_of": pack.as_of,
            "has_analysis": pack.analysis is not None,
            "stance": pack.analysis.stance if pack.analysis else None,
            "recommended_bucket": pack.analysis.recommended_bucket if pack.analysis else None,
            "pack": json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            "created_at": now,
        }
        for pack in packs
    ]
    schema: dict[str, pl.DataType] = {
        "insight_run_id": pl.Utf8(),
        "recommendation_run_id": pl.Utf8(),
        "stock_id": pl.Utf8(),
        "date": pl.Date(),
        "as_of": pl.Datetime("us", "Asia/Taipei"),
        "has_analysis": pl.Boolean(),
        "stance": pl.Utf8(),
        "recommended_bucket": pl.Utf8(),
        "pack": pl.Utf8(),
        "created_at": pl.Datetime("us", "Asia/Taipei"),
    }
    assert tuple(schema) == _SPEC.column_names
    return pl.DataFrame(rows, schema=schema, orient="row")


def publish_packs(
    conn: Any, schema: str, data_dir: str | Path, recommendation_run_id: str, now: dt.datetime
) -> int:
    """把這個推薦 run 在 data/insights 的證據包全部 upsert 進 PG；回寫入列數。"""
    packs = [
        pack
        for pack in select_packs(data_dir).packs
        if pack.recommendation_run_id == recommendation_run_id
    ]
    if not packs:
        return 0
    with conn.cursor() as cur:
        upsert(cur, schema, _SPEC, packs_frame(packs, now))
    conn.commit()
    return len(packs)


def restore_packs(conn: Any, schema: str, data_dir: str | Path, recommendation_run_id: str) -> int:
    """PG → data/insights：把同一個推薦 run 已有的證據包寫回本機；回寫回的份數。

    本機已有同 id 但內容不同 → 留本機的、記 WARN（正本在本機，PG 只是副本）。
    """
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT pack FROM "{schema}"."{_SPEC.name}" WHERE recommendation_run_id = %s',
            (recommendation_run_id,),
        )
        payloads = [row[0] for row in cur.fetchall()]
    restored = 0
    for payload in payloads:
        pack = InsightPack.model_validate(
            json.loads(payload) if isinstance(payload, str) else payload
        )
        try:
            if save_pack(data_dir, pack).created:
                restored += 1
        except DataError as exc:
            _log.warning("PG 的證據包與本機不同，保留本機：%s", exc)
    return restored
