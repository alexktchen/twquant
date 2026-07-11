"""CLI 純邏輯測試（排程時間計算、富邦DJ 平行回補 D-022）。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from twquant.cli import _daterange, _fetch_fubon_day, _seconds_until
from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data.http import HttpClient


def test_seconds_until_later_today() -> None:
    now = dt.datetime(2026, 7, 5, 9, 0, tzinfo=TZ)
    assert _seconds_until(now, 16, 40) == (16 - 9) * 3600 + 40 * 60


def test_seconds_until_wraps_to_tomorrow() -> None:
    now = dt.datetime(2026, 7, 5, 20, 0, tzinfo=TZ)
    # 20:00 已過 16:40 → 明日 16:40
    delay = _seconds_until(now, 16, 40)
    assert delay == (24 - 20 + 16) * 3600 + 40 * 60


def test_seconds_until_positive() -> None:
    now = dt.datetime(2026, 7, 5, 16, 40, 0, tzinfo=TZ)
    # 剛好等於觸發時刻 → 視為已過，算明日（>0）
    assert _seconds_until(now, 16, 40) == 24 * 3600


def test_daterange_inclusive() -> None:
    days = _daterange(dt.date(2026, 6, 29), dt.date(2026, 7, 1))
    assert days == [dt.date(2026, 6, 29), dt.date(2026, 6, 30), dt.date(2026, 7, 1)]


def test_daterange_weekday_filter() -> None:
    # 2026-06-16(二)~06-22(一)：週末 20/21 應可被 weekday<5 濾掉
    days = _daterange(dt.date(2026, 6, 16), dt.date(2026, 6, 22))
    weekdays = [d for d in days if d.weekday() < 5]
    assert dt.date(2026, 6, 20) in days  # 週六在原範圍
    assert dt.date(2026, 6, 20) not in weekdays  # 但被濾掉


# ── 富邦DJ 平行回補（D-022，_fetch_fubon_day）──────────────────────────

_FUBON_DAY = dt.date(2026, 5, 7)


@dataclass
class _Resp:
    status_code: int
    text: str = ""
    content: bytes = b""


def _fubon_client(
    calls: dict[str, int], fail: set[str] | None = None, max_retries: int = 3
) -> HttpClient:
    """注入式富邦 client：每檔回傳可辨識 HTML，fail 內的代號回 503（→ DataError）。

    限流 max_requests 開很大 → 窗永不滿、不觸發 sleep（測試零 wall-clock、不依賴時鐘並行）。
    """
    fail = fail or set()

    def transport(method: str, url: str, *, params: object, timeout: float) -> _Resp:
        assert isinstance(params, dict)
        sid = params["a"]
        calls[sid] = calls.get(sid, 0) + 1
        if sid in fail:
            return _Resp(503)
        return _Resp(200, content=f"<html>{sid}</html>".encode())

    return HttpClient(
        source="fubon",
        rate=SourceRate(max_requests=10_000, per_seconds=1.0),
        transport=transport,
        max_retries=max_retries,
    )


def test_fetch_fubon_day_parallel_matches_sequential(tmp_path: Path) -> None:
    # 冪等/決定性（CLAUDE.md §4）：workers=6 與序列版逐元素相同、且永遠依 sids 序
    sids = [f"{i:04d}" for i in range(1000, 1030)]  # 30 檔
    seq_dir, par_dir = tmp_path / "seq", tmp_path / "par"
    seq_dir.mkdir()
    par_dir.mkdir()

    seq_calls: dict[str, int] = {}
    par_calls: dict[str, int] = {}
    seq = _fetch_fubon_day(_fubon_client(seq_calls), sids, _FUBON_DAY, seq_dir, 1, True)
    par = _fetch_fubon_day(_fubon_client(par_calls), sids, _FUBON_DAY, par_dir, 6, True)

    assert [s for s, _, _ in seq] == sids  # 依 sids 序
    assert seq == par  # 平行與序列 bit-identical
    assert seq_calls == {s: 1 for s in sids}  # 每檔恰一次請求
    assert par_calls == {s: 1 for s in sids}


def test_fetch_fubon_day_cache_resume_no_refetch(tmp_path: Path) -> None:
    # raw HTML 快取續傳：第二次全部命中 → 零網路請求
    sids = ["2330", "2317"]
    raw = tmp_path / "d"
    raw.mkdir()
    calls: dict[str, int] = {}
    client = _fubon_client(calls)
    first = _fetch_fubon_day(client, sids, _FUBON_DAY, raw, 2, True)
    calls.clear()
    second = _fetch_fubon_day(client, sids, _FUBON_DAY, raw, 2, True)
    assert first == second
    assert calls == {}  # 續傳：無新請求
    assert (raw / "2330.html").read_bytes() == b"<html>2330</html>"


def test_fetch_fubon_day_stream_mode_writes_no_html(tmp_path: Path) -> None:
    # keep_raw=False（D-023）：純串流——不落 HTML，且每次都重抓（無快取續傳）
    sids = ["2330", "2317"]
    raw = tmp_path / "d"  # 刻意不 mkdir：串流模式不應觸碰此目錄
    calls: dict[str, int] = {}
    client = _fubon_client(calls)
    first = _fetch_fubon_day(client, sids, _FUBON_DAY, raw, 2, False)
    second = _fetch_fubon_day(client, sids, _FUBON_DAY, raw, 2, False)

    assert [(s, h) for s, h, _ in first] == [(s, f"<html>{s}</html>".encode()) for s in sids]
    assert first == second  # 解析結果一致（決定性）
    assert not raw.exists()  # 未寫任何實體檔
    assert calls == {s: 2 for s in sids}  # 兩趟各重抓一次（無續傳）


def test_fetch_fubon_day_dataerror_recorded_not_raised(tmp_path: Path) -> None:
    # 單檔重試耗盡 → 記入 err（不中斷整批、不落快取），順序仍保留
    sids = ["2330", "9999", "2317"]
    raw = tmp_path / "d"
    raw.mkdir()
    calls: dict[str, int] = {}
    out = _fetch_fubon_day(
        _fubon_client(calls, fail={"9999"}, max_retries=0), sids, _FUBON_DAY, raw, 3, True
    )

    assert [s for s, _, _ in out] == sids  # 序保留
    ok = {s for s, _, err in out if err is None}
    failed = [(s, err) for s, _, err in out if err is not None]
    assert ok == {"2330", "2317"}
    assert len(failed) == 1 and failed[0][0] == "9999"
    assert not (raw / "9999.html").exists()  # 失敗檔不落快取
    assert (raw / "2330.html").exists()
