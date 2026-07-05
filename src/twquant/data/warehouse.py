"""倉儲與 PIT 視圖（SPEC-001 §4 / §7）。

Parquet 資料湖 + DuckDB 查詢引擎。寫入採原子（temp+rename）、分區級替換（同一
(table,date) 重寫等於最後一次，不累加，SPEC-001 §7）。讀取以 available_at 為
point-in-time 過濾：訊號生成截點 = 當日 20:00（D-008），回測與生產同一 cut-off。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl

from twquant.conventions import as_of_cutoff
from twquant.data import schemas
from twquant.errors import DataError

_LAYERS = frozenset({"raw", "clean", "feature", "signal"})


class PitView:
    """某個 as_of 截點的資料視圖：只看 available_at <= 截點的列。"""

    def __init__(self, warehouse: Warehouse, cutoff: dt.datetime) -> None:
        self._wh = warehouse
        self._cutoff = cutoff

    @property
    def cutoff(self) -> dt.datetime:
        return self._cutoff

    def table(self, name: str) -> pl.LazyFrame:
        """回傳已按 available_at <= 截點過濾的 LazyFrame。無 available_at 欄的表原樣回傳。"""
        lf = self._wh.scan(name)
        if "available_at" in lf.collect_schema().names():
            lf = lf.filter(pl.col("available_at") <= self._cutoff)
        return lf


class Warehouse:
    """Parquet 資料湖 + DuckDB 查詢。"""

    def __init__(self, data_dir: str | Path) -> None:
        self._root = Path(data_dir)

    def _partition_dir(self, layer: str, table: str, date: dt.date) -> Path:
        return self._root / layer / table / f"date={date.isoformat()}"

    def write(self, layer: str, table: str, df: pl.DataFrame, source: str | None = None) -> None:
        """寫入資料湖。clean 層先跑 schema 驗證；按 date 分區、原子、分區級替換。

        source 給定時，同一分區內各來源寫各自的 part-<source>.parquet（上市/上櫃共存
        於同表同分區而不互相覆蓋）；未給定則寫 part.parquet 替換整個分區。
        """
        if layer not in _LAYERS:
            raise DataError(table, None, f"未知資料層：{layer}")
        if layer == "clean":
            df = schemas.validate(table, df)
        if df.is_empty():
            return
        fname = f"part-{source}.parquet" if source else "part.parquet"
        if "date" not in df.columns:  # 緩變表（stock_meta 等）：單一分區
            self._write_partition(self._root / layer / table / "date=_all", df, fname)
            return
        for (day,), sub in df.group_by(["date"], maintain_order=True):
            assert isinstance(day, dt.date)
            self._write_partition(self._partition_dir(layer, table, day), sub, fname)

    def _write_partition(self, part: Path, df: pl.DataFrame, fname: str) -> None:
        part.mkdir(parents=True, exist_ok=True)
        final = part / fname
        tmp = part / f"{fname}.tmp"
        df.write_parquet(tmp)
        tmp.replace(final)  # 原子替換該來源的分區檔

    def _glob(self, table: str, layer: str) -> str:
        return (self._root / layer / table / "**" / "*.parquet").as_posix()

    def scan(self, table: str, layer: str = "clean") -> pl.LazyFrame:
        """掃描某表所有分區為 LazyFrame（跨分區 union_by_name，SPEC-001 §7）。"""
        files = sorted((self._root / layer / table).rglob("*.parquet"))
        if not files:
            raise DataError(table, None, f"{layer} 層無資料：{table}")
        # missing_columns="insert"：跨分區 schema 演進時，舊分區缺的新欄補 null（§7）
        return pl.scan_parquet(files, missing_columns="insert")

    def partition_exists(
        self, layer: str, table: str, date: dt.date, source: str | None = None
    ) -> bool:
        """該 (table, date[, source]) 分區是否已有資料（供 backfill 續傳跳過）。"""
        part = self._partition_dir(layer, table, date)
        fname = f"part-{source}.parquet" if source else "part.parquet"
        return (part / fname).exists()

    def latest_date(self, table: str, layer: str = "clean") -> dt.date | None:
        """該表最新一個有資料的日期分區。"""
        base = self._root / layer / table
        dates = [
            dt.date.fromisoformat(p.name[len("date=") :])
            for p in base.glob("date=*")
            if p.name != "date=_all"
        ]
        return max(dates) if dates else None

    def source_dates(self, table: str, source: str, layer: str = "clean") -> list[dt.date]:
        """某來源在該表已存資料的所有日期（排序）。供擷取狀態/缺口檢視。"""
        base = self._root / layer / table
        fname = f"part-{source}.parquet"
        out = [
            dt.date.fromisoformat(p.name[len("date=") :])
            for p in base.glob("date=*")
            if p.name != "date=_all" and (p / fname).exists()
        ]
        return sorted(out)

    def view(self, as_of: dt.date | dt.datetime) -> PitView:
        """建立某 as_of 截點的 PIT 視圖（date 型 as_of coerce 到 20:00，D-008）。"""
        return PitView(self, as_of_cutoff(as_of))

    def connect(self, layer: str = "clean") -> duckdb.DuckDBPyConnection:
        """回傳註冊好各表視圖的 DuckDB 連線，供 SQL 分析。"""
        con = duckdb.connect()
        base = self._root / layer
        if base.exists():
            for tdir in sorted(base.iterdir()):
                if not tdir.is_dir() or not any(tdir.rglob("*.parquet")):
                    continue
                glob = (tdir / "**" / "*.parquet").as_posix()
                con.execute(
                    f'CREATE VIEW "{tdir.name}" AS '
                    f"SELECT * FROM read_parquet('{glob}', union_by_name=true)"
                )
        return con

    def sql(self, query: str, layer: str = "clean") -> pl.DataFrame:
        """對資料湖跑 SQL，回傳 polars DataFrame。各 clean 表已註冊為同名視圖。"""
        con = self.connect(layer)
        try:
            return con.execute(query).pl()
        finally:
            con.close()
