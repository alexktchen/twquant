"""T-178：`upsert_ml_scores` 走 COPY，不走逐列 executemany。

契約（由本測試凍結，用假 cursor 驗證**送出的 SQL 形狀**，不需要真的 PG）：
- AC-1 走 COPY 串流，不呼叫 executemany——後者對每列各一次往返，
  長窗口模型的 450 萬列實測 71 分鐘還沒寫完（PG 端一路 ClientRead，瓶頸在客戶端）。
- AC-2 語意不變：仍是 ON CONFLICT (model_id, stock_id, date) DO NOTHING。
- AC-3 批內重複鍵取第一列（DISTINCT ON），與逐列版「先插入的贏」一致。
- AC-4 空表不送任何 SQL，直接回 0。
- AC-5 驗證仍在最前面：壞資料整批 raise，不落地（沿用 AC-7 的既有契約）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving.ml_store import upsert_ml_scores


class _Copy:
    def __init__(self, sink: list[tuple[Any, ...]]) -> None:
        self._sink = sink

    def __enter__(self) -> _Copy:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def write_row(self, row: tuple[Any, ...]) -> None:
        self._sink.append(row)


class _Cursor:
    def __init__(self, log: list[str], rows: list[tuple[Any, ...]]) -> None:
        self._log, self._rows = log, rows
        self.rowcount = 7

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, *_: object) -> None:
        self._log.append(sql)

    def copy(self, sql: str) -> _Copy:
        self._log.append(sql)
        return _Copy(self._rows)

    def executemany(self, *_: object) -> None:  # pragma: no cover - 必須不被呼叫
        raise AssertionError("executemany：逐列往返，450 萬列跑不完（T-178）")


class _Conn:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.rows: list[tuple[Any, ...]] = []
        self.commits = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self.log, self.rows)

    def commit(self) -> None:
        self.commits += 1


def _frame(n: int = 3, *, dup: bool = False) -> pl.DataFrame:
    d = dt.date(2026, 1, 5)
    if n == 0:  # 空表也要帶正確 schema，否則測到的是 polars 的欄位錯誤而不是本函數
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "model_id": pl.String,
                "score": pl.Float64,
                "rank_pct": pl.Float64,
                "available_at": schemas.TW_DT,
            }
        )
    rows = [
        {
            "date": d + dt.timedelta(days=0 if dup else i),
            "stock_id": "2330",
            "model_id": "lgbm_lw_net_h20_20260225_83890036",
            "score": float(i),
            "rank_pct": 0.5,
            "available_at": dt.datetime.combine(d, dt.time(19), tzinfo=TZ),
        }
        for i in range(n)
    ]
    return pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def test_ac1_uses_copy_not_executemany() -> None:
    conn = _Conn()
    n = upsert_ml_scores(conn, "serving", _frame())
    assert n == 7  # 由 cur.rowcount 回報
    assert any(s.startswith("COPY _ml_scores_stage") for s in conn.log)
    assert len(conn.rows) == 3  # 三列都經 COPY 串流送出
    assert conn.commits == 1


def test_ac2_conflict_semantics_unchanged() -> None:
    conn = _Conn()
    upsert_ml_scores(conn, "serving", _frame())
    ins = next(s for s in conn.log if s.startswith("INSERT INTO"))
    assert "ON CONFLICT (model_id, stock_id, date) DO NOTHING" in ins
    assert '"serving"."ml_scores"' in ins


def test_ac3_intra_batch_duplicates_keep_first() -> None:
    conn = _Conn()
    upsert_ml_scores(conn, "serving", _frame(dup=True))
    ins = next(s for s in conn.log if s.startswith("INSERT INTO"))
    assert "DISTINCT ON (model_id, stock_id, date)" in ins
    # 暫存表隨交易結束消失，例外時不留殘骸
    assert any("ON COMMIT DROP" in s for s in conn.log)


def test_ac4_empty_frame_writes_nothing() -> None:
    conn = _Conn()
    assert upsert_ml_scores(conn, "serving", _frame(0)) == 0
    assert conn.log == []
    assert conn.commits == 0


def test_ac5_validation_still_runs_before_any_write() -> None:
    conn = _Conn()
    bad = _frame().with_columns(pl.col("stock_id").cast(pl.Int64))  # schema 要求字串
    with pytest.raises(Exception):  # noqa: B017  # pandera 的例外型別非契約的一部分
        upsert_ml_scores(conn, "serving", bad)
    assert conn.log == []
