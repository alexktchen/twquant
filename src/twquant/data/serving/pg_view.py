"""PG-backed PitView 讀取源（D-026）：因子物化改讀 PG serving 鏡射表，不再依賴本地
parquet 資料湖。取代 SPEC-009 舊定位「PG 永不回饋因子計算」——使用者裁決全面停用
parquet，因子計算的資料源與落地都改在 PG，見 DECISIONS.md D-026。

`PgTableSource` 結構滿足 `data.warehouse.TableSource`，可直接餵給既有 `PitView`；
因子函數（`factors/*.py`）完全不變，只認 `view.table(name)`，不知道背後是 parquet
還是 PG。本模組不 import psycopg——連線由呼叫端經 `store.connect()` 提供（SPEC-009 IO
邊界：只有 data/serving/ 得碰 PG 連線）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from twquant.conventions import TZ, as_of_cutoff
from twquant.data.warehouse import PitView
from twquant.errors import DataError


class PgTableSource:
    """從 PG serving schema 整表讀出，供 `PitView` 當讀取後端。

    `layer` 參數僅為與 `Warehouse`（parquet raw/clean/feature/signal 分層）簽名對齊而
    保留——PG serving 只有單一鏡射層，此參數不影響行為。
    """

    def __init__(
        self, conn: Any, schema: str, cache: bool = False, since: dt.date | None = None
    ) -> None:
        self._conn = conn
        self._schema = schema
        self._cache = cache
        self._cached: dict[str, pl.DataFrame] = {}
        self._since = since
        self._has_date: dict[str, bool] = {}

    def _assert_exists(self, table: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"{self._schema}.{table}",))
            if cur.fetchone()[0] is None:
                raise DataError(table, None, f"PG serving 無此表：{self._schema}.{table}")

    def _date_col(self, table: str) -> bool:
        if table not in self._has_date:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s AND column_name='date'",
                    (self._schema, table),
                )
                self._has_date[table] = cur.fetchone() is not None
        return self._has_date[table]

    def scan(self, table: str, layer: str = "clean") -> pl.LazyFrame:
        """整表讀出為 LazyFrame；available_at（若有）正規化到 Asia/Taipei（PitView 比較基準）。

        cache=True（一次性 CLI 流程用，如回測逐訊號日重算因子）：每表只讀 PG 一次、
        記憶體重用。長駐程序勿開（不會看見 PG 新寫入）。
        since：只讀 date >= since 的列（縮短長駐 API 的讀量）；設定值必須涵蓋
        最長因子回看（如營收 YoY 需 ~400 天），否則因子輸出會缺前段。
        """
        if self._cache and table in self._cached:
            return self._cached[table].lazy()
        self._assert_exists(table)
        query = f'SELECT * FROM "{self._schema}"."{table}"'
        if self._since is not None and self._date_col(table):
            query += f" WHERE date >= '{self._since.isoformat()}'"
        # infer_schema_length=None：看完**全部**列才定型別。預設只看前 100 列——某欄若前面全是
        # null、後面才出現值（`stock_meta.delist_date` 2026-09-20 實際發生），會被定成 Null 型，
        # 再遇到日期就 ComputeError；而 stock_meta 是每次宇宙過濾都要讀的表。
        df = pl.read_database(query, self._conn, infer_schema_length=None)
        if df.is_empty():
            raise DataError(table, None, f"PG serving 無資料：{self._schema}.{table}")
        if "available_at" in df.columns:
            df = df.with_columns(pl.col("available_at").dt.convert_time_zone(str(TZ)))
        if self._cache:
            self._cached[table] = df
        return df.lazy()

    def latest_date(self, table: str, layer: str = "clean") -> dt.date | None:
        self._assert_exists(table)
        with self._conn.cursor() as cur:
            cur.execute(f'SELECT max(date) FROM "{self._schema}"."{table}"')
            row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    def dates_in_range(
        self, table: str, start: dt.date, end: dt.date, layer: str = "clean"
    ) -> list[dt.date]:
        self._assert_exists(table)
        with self._conn.cursor() as cur:
            cur.execute(
                f'SELECT DISTINCT date FROM "{self._schema}"."{table}" '
                "WHERE date BETWEEN %s AND %s ORDER BY date",
                (start, end),
            )
            return [row[0] for row in cur.fetchall()]

    def view(self, as_of: dt.date | dt.datetime) -> PitView:
        """建立某 as_of 截點的 PIT 視圖（date 型 as_of coerce 到 20:00，D-008）。"""
        return PitView(self, as_of_cutoff(as_of))
