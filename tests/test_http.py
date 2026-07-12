"""T-006 / SPEC-001 §2 §7：限流、重試、raw 落地（全離線）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from twquant.config import SourceRate
from twquant.data.http import HttpClient, SlidingWindowLimiter, land_raw
from twquant.errors import DataError


class FakeClock:
    """虛擬時鐘：sleep 直接推進虛擬時間，測試零 wall-clock。"""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class FakeResponse:
    status_code: int
    text: str = ""
    content: bytes = b""


def test_ac4_sliding_window_max_3_per_5s() -> None:
    clock = FakeClock()
    lim = SlidingWindowLimiter(max_requests=3, per_seconds=5.0, clock=clock)
    grants: list[float] = []
    for _ in range(10):
        lim.acquire()
        grants.append(clock.monotonic())
    # 任意 5 秒滑動窗 ≤ 3：等價於第 i 次與第 i-3 次至少相隔 5 秒
    for i in range(3, len(grants)):
        assert grants[i] - grants[i - 3] >= 5.0 - 1e-9
    # 前三次同時（t=0），第四次等到 t=5
    assert grants[:3] == [0.0, 0.0, 0.0]
    assert grants[3] == 5.0


def test_limiter_thread_safe_no_over_admission() -> None:
    # D-022：多執行緒共用同一 limiter（backfill --workers）。鎖保護窗記帳：
    # 8 執行緒各 acquire 4 次應 (1) 不因 deque race 崩潰/死鎖、(2) 全域吞吐仍受限流節制。
    import threading
    import time

    from twquant.data.http import SystemClock

    lim = SlidingWindowLimiter(max_requests=5, per_seconds=0.2, clock=SystemClock())
    count = {"n": 0}
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(4):
            lim.acquire()
            with lock:
                count["n"] += 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0

    assert count["n"] == 32  # 全數放行、無死鎖、deque 未損毀
    # 32 次 @ 5/0.2s 滑動窗 → 理論最少 ~1.2s；限流器有作用即耗時 ≥ 此下界（慢機只會更久）
    assert elapsed >= 0.9


def test_retry_succeeds_after_two_failures() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConnectionError("boom")
        return FakeResponse(200, "ok")

    client = HttpClient(
        source="test",
        rate=SourceRate(max_requests=3, per_seconds=5.0),
        clock=clock,
        transport=transport,
    )
    resp = client.get("http://x")
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_retry_exhausted_raises_dataerror() -> None:
    clock = FakeClock()

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(503)

    client = HttpClient(
        source="twse",
        rate=SourceRate(max_requests=3, per_seconds=5.0),
        clock=clock,
        transport=transport,
        max_retries=2,
    )
    with pytest.raises(DataError, match="重試"):
        client.get("http://x")


def test_non_retryable_status_returned() -> None:
    clock = FakeClock()

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(404, "not found")

    client = HttpClient(
        source="twse",
        rate=SourceRate(max_requests=3, per_seconds=5.0),
        clock=clock,
        transport=transport,
    )
    resp = client.get("http://x")
    assert resp.status_code == 404


def test_land_raw_atomic_versioned(tmp_path: Path) -> None:
    p = land_raw(tmp_path, "prices_daily", "2026-07-03", b'{"a":1}', "20260703T180000", seq=0)
    assert p.exists()
    assert p.name == "fetched_at=20260703T180000_0.json"
    assert p.read_bytes() == b'{"a":1}'
    # 無殘留 .tmp
    assert not list(p.parent.glob("*.tmp"))
    # 同日第二版：不同檔名，兩版並存
    p2 = land_raw(tmp_path, "prices_daily", "2026-07-03", b'{"a":2}', "20260703T190000", seq=1)
    assert p2 != p
    assert len(list(p.parent.glob("*.json"))) == 2
