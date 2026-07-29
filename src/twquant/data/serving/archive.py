"""每日增量歸檔（T-054，D-030/D-033）：serving 原始表單日切片 → csv.gz + manifest。

保命線：券商分點（broker_trades）過期無法重抓，每日增量上傳 Drive 前先由本模組落地。
歸檔範圍＝MIRROR_SPECS（原始鏡射表）＋簿記/衍生快照表（fubon_fetch_log、screener_daily）；
因子表（factor_values/factor_cross_stats/composite_daily）可由原始資料重算、不歸檔；
sync_state 為暫態簿記、不歸檔。stock_meta 無 date 欄 → 每日全量快照。

冪等設計（黃金規則 §4）：COPY 皆 ORDER BY 主鍵、session 時區固定 UTC、gzip mtime=0，
同一庫狀態重跑同日 → 檔案 bit-identical。佈局（OPS-archive.md §1）：
`<root>/<YYYY>/<YYYYMMDD>/<table>.csv.gz` + `manifest.json`。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twquant.conventions import TZ
from twquant.data.serving.schema import (
    BROKER_FLOW_DAILY_SPEC,
    FUBON_FETCH_LOG_SPEC,
    MIRROR_SPECS,
    SCREENER_DAILY_SPEC,
    TableSpec,
    ensure_schema,
)

ARCHIVE_SPECS: tuple[TableSpec, ...] = (
    *MIRROR_SPECS,
    FUBON_FETCH_LOG_SPEC,
    SCREENER_DAILY_SPEC,
    BROKER_FLOW_DAILY_SPEC,
)

_MANIFEST = "manifest.json"


@dataclass(frozen=True)
class ArchiveResult:
    """單表歸檔/還原結果。"""

    table: str
    rows: int
    path: Path


def day_dir(root: Path, day: dt.date) -> Path:
    """某日增量的落地目錄：<root>/<YYYY>/<YYYYMMDD>。"""
    return root / f"{day.year}" / day.strftime("%Y%m%d")


def _copy_out_sql(schema: str, spec: TableSpec, day: dt.date) -> str:
    cols = ", ".join(spec.column_names)
    where = "" if spec.snapshot else f" WHERE date = '{day.isoformat()}'"
    order = ", ".join(spec.pk)
    return (
        f'COPY (SELECT {cols} FROM "{schema}"."{spec.name}"{where} ORDER BY {order}) '
        "TO STDOUT (FORMAT csv, HEADER)"
    )


def dump_day(conn: Any, schema: str, day: dt.date, root: Path) -> list[ArchiveResult]:
    """把 ARCHIVE_SPECS 各表的單日切片（snapshot 表全量）寫成 csv.gz + manifest。

    重跑同日覆蓋同檔（冪等）；零列的表也寫檔（header-only），manifest 記 0——
    「有檔案＝該日已歸檔」的訊號比省空間重要（歸檔鏈不能斷）。
    """
    out = day_dir(root, day)
    out.mkdir(parents=True, exist_ok=True)
    results: list[ArchiveResult] = []
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'UTC'")  # timestamptz 輸出固定 UTC，跨環境 bit-identical
        for spec in ARCHIVE_SPECS:
            path = out / f"{spec.name}.csv.gz"
            tmp = out / f"{spec.name}.csv.gz.tmp"
            n = 0
            with (
                open(tmp, "wb") as raw,
                gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz,
                cur.copy(_copy_out_sql(schema, spec, day)) as copy,
            ):
                for data in copy:
                    gz.write(bytes(data))
            tmp.replace(path)  # 原子覆蓋（SPEC-001 §7 寫入協定精神）
            if spec.snapshot:
                cur.execute(f'SELECT count(*) FROM "{schema}"."{spec.name}"')
            else:
                cur.execute(
                    f'SELECT count(*) FROM "{schema}"."{spec.name}" WHERE date = %s', (day,)
                )
            row = cur.fetchone()
            n = int(row[0]) if row else 0
            results.append(ArchiveResult(spec.name, n, path))
    conn.commit()
    manifest = {
        "day": day.isoformat(),
        "generated_at": dt.datetime.now(TZ).isoformat(),
        "tables": {r.table: r.rows for r in results},
    }
    mtmp = out / f"{_MANIFEST}.tmp"
    mtmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    mtmp.replace(out / _MANIFEST)
    return results


def restore_day(conn: Any, schema: str, day: dt.date, root: Path) -> list[ArchiveResult]:
    """把某日增量檔還原進 schema（不存在則建）：staging COPY → ON CONFLICT DO NOTHING。

    冪等：已存在的主鍵不覆寫（首錄保留，與 screener_daily 語義一致；災難復原時
    按日期順序逐日還原即得正確狀態）。回傳各表「本次新插入」列數。
    """
    src = day_dir(root, day)
    manifest_path = src / _MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"找不到 {day} 的歸檔 manifest：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ensure_schema(conn, schema)
    results: list[ArchiveResult] = []
    with conn.cursor() as cur:
        for spec in ARCHIVE_SPECS:
            if spec.name not in manifest["tables"]:
                continue  # 舊佈局的檔可能缺新表——按 manifest 實際內容還原
            path = src / f"{spec.name}.csv.gz"
            if not path.is_file():
                raise FileNotFoundError(f"manifest 記載 {spec.name} 但檔案不存在：{path}")
            staging = f"_arch_restore_{spec.name}"
            cols = ", ".join(spec.column_names)
            cur.execute(
                f'CREATE TEMP TABLE "{staging}" '
                f'(LIKE "{schema}"."{spec.name}" INCLUDING DEFAULTS) ON COMMIT DROP'
            )
            with (
                gzip.open(path, "rb") as f,
                cur.copy(f'COPY "{staging}" ({cols}) FROM STDIN (FORMAT csv, HEADER)') as copy,
            ):
                while chunk := f.read(1 << 20):
                    copy.write(chunk)
            cur.execute(
                f'INSERT INTO "{schema}"."{spec.name}" ({cols}) '
                f'SELECT {cols} FROM "{staging}" ON CONFLICT DO NOTHING'
            )
            results.append(ArchiveResult(spec.name, cur.rowcount, path))
    conn.commit()
    return results
