"""CLI 純邏輯測試（排程時間計算、富邦DJ 平行回補 D-022/D-024/D-025）。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from twquant.cli import (
    _daterange,
    _fetch_fubon_day,
    _fubon_status_lines,
    _make_progress,
    _probe_all_empty,
    _run_fubon_day,
    _seconds_until,
)
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


def test_run_fubon_day_weekend_short_circuits() -> None:
    # 每日 pipeline 券商買賣超步驟：週末直接跳過（不碰 config/PG/網路，故可離線斷言）。
    assert _run_fubon_day(dt.date(2026, 7, 18)) == "週末，跳過"  # 2026-07-18 為週六


# ── 富邦DJ 平行抓取（D-022/D-025，_fetch_fubon_day）───────────────────

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


def test_fetch_fubon_day_parallel_matches_sequential() -> None:
    # 冪等/決定性（CLAUDE.md §4）：workers=6 與序列版逐元素相同、且永遠依 sids 序
    sids = [f"{i:04d}" for i in range(1000, 1030)]  # 30 檔
    seq_calls: dict[str, int] = {}
    par_calls: dict[str, int] = {}
    seq = _fetch_fubon_day(_fubon_client(seq_calls), sids, _FUBON_DAY, 1)
    par = _fetch_fubon_day(_fubon_client(par_calls), sids, _FUBON_DAY, 6)

    assert [s for s, _, _ in seq] == sids  # 依 sids 序
    assert seq == par  # 平行與序列 bit-identical
    assert seq_calls == {s: 1 for s in sids}  # 每檔恰一次請求
    assert par_calls == {s: 1 for s in sids}


def test_fetch_fubon_day_dataerror_recorded_not_raised() -> None:
    # 單檔重試耗盡 → 記入 err（不中斷整批），順序仍保留
    sids = ["2330", "9999", "2317"]
    calls: dict[str, int] = {}
    out = _fetch_fubon_day(_fubon_client(calls, fail={"9999"}, max_retries=0), sids, _FUBON_DAY, 3)

    assert [s for s, _, _ in out] == sids  # 序保留
    assert {s for s, _, err in out if err is None} == {"2330", "2317"}
    assert [s for s, _, err in out if err is not None] == ["9999"]


def test_fetch_fubon_day_skip_if_checked_per_stock() -> None:
    # D-025 帳本跳過：skip_if 回 True 的檔不發請求、不回呼 on_fetched，結果 (sid, None, None)
    sids = ["2330", "2317", "1101"]
    calls: dict[str, int] = {}
    fetched: list[str] = []
    out = _fetch_fubon_day(
        _fubon_client(calls),
        sids,
        _FUBON_DAY,
        2,
        skip_if=lambda sid: sid == "2317",
        on_fetched=lambda sid, html: fetched.append(sid),
    )
    assert out[1] == ("2317", None, None)  # 跳過非錯誤
    assert "2317" not in calls  # 未發請求
    assert sorted(fetched) == ["1101", "2330"]  # 只有實際抓到的才回呼


def test_fetch_fubon_day_on_fetched_not_called_on_error() -> None:
    fetched: list[str] = []
    out = _fetch_fubon_day(
        _fubon_client({}, fail={"9999"}, max_retries=0),
        ["9999"],
        _FUBON_DAY,
        1,
        on_fetched=lambda sid, html: fetched.append(sid),
    )
    assert fetched == []  # 失敗不回呼（不會把壞頁寫進 PG）
    assert out[0][2] is not None


def test_fetch_fubon_day_on_done_called_once_per_sid() -> None:
    # 進度回呼：每檔完成恰呼叫一次（成功/失敗/跳過皆算），平行與序列一致
    import threading

    sids = ["2330", "9999", "2317", "1101"]
    seen: list[str] = []
    lock = threading.Lock()

    def cb(sid: str) -> None:
        with lock:
            seen.append(sid)

    for workers in (1, 3):
        seen.clear()
        client = _fubon_client({}, fail={"9999"}, max_retries=0)
        _fetch_fubon_day(
            client, sids, _FUBON_DAY, workers, skip_if=lambda sid: sid == "1101", on_done=cb
        )
        assert sorted(seen) == sorted(sids)


# ── 假日探測（D-024）與進度列／進度查詢（D-025）───────────────────────

_EMPTY_HTML = b"<html><body>no table</body></html>"  # 無 t01 表 → parse []
# 有一列資料的最小 t01 表（前 8 列表頭會被 parse 跳過）
_DATA_HTML = (
    "<table class='t01'>"
    + "<tr><td>h</td></tr>" * 8
    + "<tr>"
    + "".join(f"<td>{c}</td>" for c in ["券商A", "1", "2", "-1", "", "", "", "", ""])
    + "</tr></table>"
).encode()


def test_probe_all_empty_detects_holiday() -> None:
    # 全數成功且皆無分點表 → True（非交易日）
    assert _probe_all_empty([("1101", _EMPTY_HTML, None), ("1102", _EMPTY_HTML, None)]) is True


def test_probe_all_empty_any_data_means_trading_day() -> None:
    assert _probe_all_empty([("1101", _EMPTY_HTML, None), ("2330", _DATA_HTML, None)]) is False


def test_probe_all_empty_error_or_skip_is_not_evidence() -> None:
    # err（被擋/重試耗盡）或帳本跳過（html=None err=None）都不算證據 → False（照抓整天）
    assert _probe_all_empty([("1101", None, "HTTP 503"), ("1102", _EMPTY_HTML, None)]) is False
    assert _probe_all_empty([("1101", None, None), ("1102", _EMPTY_HTML, None)]) is False


def test_probe_all_empty_no_probe_is_false() -> None:
    assert _probe_all_empty([]) is False


def test_make_progress_tty_rewrites_single_line(capsys: pytest.CaptureFixture[str]) -> None:
    progress = _make_progress(4, tty=True)
    progress("2026-05-07 2330", 1)
    out = capsys.readouterr().out
    assert out.startswith("\r") and not out.endswith("\n")  # \r 重繪、不換行
    assert "25.0%" in out and "1/4" in out and "2026-05-07 2330" in out
    assert "開始" in out and "預估結束" in out
    progress("2026-05-07 收尾", 3)
    out = capsys.readouterr().out
    assert "100.0%" in out and "4/4" in out


def test_make_progress_non_tty_emits_lines_for_docker_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 非 TTY（docker logs / 背景）：換行完整 log；step 節流（total=400 → 每 2 檔一行）
    progress = _make_progress(400, tty=False)
    progress("2026-05-07 1101", 1)  # done=1：未達 step → 不輸出
    assert capsys.readouterr().out == ""
    progress("2026-05-07 1102", 1)  # done=2：step 命中 → 完整一行
    out = capsys.readouterr().out
    assert out.endswith("\n") and "\r" not in out
    assert "0.5%" in out and "2/400" in out and "1102" in out
    progress("2026-05-08 已存在", 397)  # advance>1 的日級事件必印（done=399 非 step 倍數）
    assert "399/400" in capsys.readouterr().out
    progress("2026-05-09 收尾", 1)  # done=total 必印
    assert "100.0%" in capsys.readouterr().out


def test_fubon_status_lines_pct_and_holiday() -> None:
    rows = [
        (dt.date(2026, 5, 1), 0, 0, True),  # SENTINEL 標記日
        (dt.date(2026, 5, 7), 1432, 1180, False),
    ]
    lines = _fubon_status_lines(rows, total=1797)
    assert "2026-05-01" in lines[0] and "非交易日" in lines[0]
    assert "1432/1797" in lines[1] and "79.7%" in lines[1] and "1180" in lines[1]


def test_fubon_status_lines_zero_total_no_crash() -> None:
    # 資料湖不可用（宇宙總數 0）→ 百分比顯示 n/a，不除零
    lines = _fubon_status_lines([(dt.date(2026, 5, 7), 3, 1, False)], total=0)
    assert "n/a" in lines[0]
