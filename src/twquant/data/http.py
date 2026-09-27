"""HTTP 基礎設施（SPEC-001 §2 / §7）：滑動窗限流、指數退避重試、raw 版本化落地。

限流語意採「任意 N 秒滑動窗 ≤ M 次」（SPEC-001 §7 / REVIEW M24），不是標準
token bucket——後者冷啟動 burst 可在窗內超額觸發封鎖。時鐘與 transport 皆可注入，
使限流與重試可離線、決定性測試（CLAUDE.md §4：測試不得連網）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from twquant.config import SourceRate
from twquant.errors import DataError

# 觸發重試的 HTTP 狀態碼
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_DEFAULT_UA = "twquant/0.0 (+research use; contact repo owner)"


class Clock(Protocol):
    """可注入時鐘，供限流與退避的決定性測試。"""

    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """真實時鐘。"""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class Response(Protocol):
    """transport 回傳物件的最小介面（相容 requests.Response 的唯讀屬性）。"""

    @property
    def status_code(self) -> int: ...
    @property
    def text(self) -> str: ...
    @property
    def content(self) -> bytes: ...


class Transport(Protocol):
    """發一個 HTTP 請求。預設實作包住 requests.Session.request。"""

    def __call__(
        self, method: str, url: str, *, params: dict[str, str] | None, timeout: float
    ) -> Response: ...


class SlidingWindowLimiter:
    """任意 per_seconds 秒的滑動窗內，最多放行 max_requests 次。"""

    def __init__(self, max_requests: int, per_seconds: float, clock: Clock) -> None:
        if max_requests < 1:
            raise ValueError("max_requests 必須 ≥ 1")
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self._clock = clock
        self._times: deque[float] = deque()
        self._lock = threading.Lock()  # 多執行緒共用同一 limiter 時保護窗記帳（backfill --workers）

    def acquire(self) -> None:
        """阻塞直到可發下一個請求（滑動窗未滿），並記錄本次時間。

        鎖涵蓋整個窗記帳與等待：多執行緒下 acquire 序列化（不會 race 讓 burst 漏過），
        但實際 HTTP 請求在 client.get 的鎖外執行，故各執行緒的網路往返仍重疊。
        """
        with self._lock:
            while True:
                now = self._clock.monotonic()
                cutoff = now - self.per_seconds
                while self._times and self._times[0] <= cutoff:
                    self._times.popleft()
                if len(self._times) < self.max_requests:
                    self._times.append(now)
                    return
                wait = self._times[0] + self.per_seconds - now
                self._clock.sleep(max(wait, 0.0))


_SHARED_LIMITERS: dict[tuple[str, int, float], SlidingWindowLimiter] = {}
_SHARED_LOCK = threading.Lock()


def shared_limiter(source: str, rate: SourceRate) -> SlidingWindowLimiter:
    """同一行程內、同一來源共用的限流器（真實時鐘）。

    SPEC-001 §7.7：限流器狀態要跨呼叫共享。每日擷取對同一個交易所會建好幾個 HttpClient
    （行情、估值、當沖／借券、公告各一個來源，T-310 的補抓還會逐日再建），各自一個限流器
    就等於沒有限流——證交所 5 秒 3 次的窗會被好幾個 client 一起灌爆。
    """
    key = (source, rate.max_requests, rate.per_seconds)
    with _SHARED_LOCK:
        limiter = _SHARED_LIMITERS.get(key)
        if limiter is None:
            limiter = SlidingWindowLimiter(rate.max_requests, rate.per_seconds, SystemClock())
            _SHARED_LIMITERS[key] = limiter
        return limiter


@dataclass
class HttpClient:
    """單一來源的 HTTP 客戶端：限流 + 指數退避重試。

    transport 可注入以離線測試；預設在首次使用時建立 requests.Session。
    limiter 給定時共用該限流器（`shared_limiter`），否則自建一個。
    """

    source: str
    rate: SourceRate
    clock: Clock = field(default_factory=SystemClock)
    transport: Transport | None = None
    user_agent: str = _DEFAULT_UA
    headers: dict[str, str] | None = None  # 額外 headers（如 TPEX 需 Referer/Accept）
    max_retries: int = 3
    base_delay: float = 1.0
    timeout: float = 20.0
    limiter: SlidingWindowLimiter | None = None

    def __post_init__(self) -> None:
        self._limiter = (
            self.limiter
            if self.limiter is not None
            else SlidingWindowLimiter(self.rate.max_requests, self.rate.per_seconds, self.clock)
        )
        self._transport_lock = threading.Lock()  # 多執行緒下只建一次共用 Session

    def _get_transport(self) -> Transport:
        if self.transport is not None:
            return self.transport
        with self._transport_lock:  # double-checked：避免多執行緒各自建 Session
            if self.transport is not None:
                return self.transport
            import requests  # 延遲 import：純測試路徑（注入 transport）不需 requests

            # requests.Session 由多執行緒共用發 GET：urllib3 連線池與 cookiejar 皆自帶鎖，
            # header 建立後唯讀，故並行讀取安全（backfill --workers 的共用 client）。
            session = requests.Session()
            session.headers["User-Agent"] = self.user_agent
            if self.headers:
                session.headers.update(self.headers)

            def _send(
                method: str, url: str, *, params: dict[str, str] | None, timeout: float
            ) -> Response:
                return session.request(method, url, params=params, timeout=timeout)

            self.transport = _send
            return _send

    def get(self, url: str, params: dict[str, str] | None = None) -> Response:
        """GET，含限流與重試。重試耗盡後 raise DataError。"""
        transport = self._get_transport()
        last_detail = ""
        for attempt in range(self.max_retries + 1):
            self._limiter.acquire()
            try:
                resp = transport("GET", url, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001  # 重試邊界：任何連線層錯誤都記錄後重試，非靜默（耗盡才 raise DataError）
                last_detail = f"transport error: {exc}"
            else:
                if resp.status_code not in _RETRYABLE_STATUS:
                    return resp
                last_detail = f"HTTP {resp.status_code}"
            if attempt < self.max_retries:
                self.clock.sleep(self.base_delay * (2**attempt))
        raise DataError(self.source, None, f"{url} 重試 {self.max_retries} 次仍失敗：{last_detail}")


def land_raw(
    data_dir: str | Path,
    table: str,
    date_key: str,
    content: bytes,
    fetched_at: str,
    seq: int = 0,
    ext: str = "json",
) -> Path:
    """把原始 response 落地到 raw 層，檔名含單調遞增版本鍵（SPEC-001 §7 / REVIEW H19）。

    路徑：data/raw/{table}/date={date_key}/fetched_at={fetched_at}_{seq}.{ext}
    採原子寫入（temp + rename）。回傳最終路徑。
    """
    part = Path(data_dir) / "raw" / table / f"date={date_key}"
    part.mkdir(parents=True, exist_ok=True)
    final = part / f"fetched_at={fetched_at}_{seq}.{ext}"
    tmp = final.with_suffix(final.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(final)
    return final
