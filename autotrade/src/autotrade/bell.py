"""門鈴（A-015／D-228）：資料庫一有新的指令就馬上叫醒中繼程式，不必等下一次輪詢。

指令本身還是資料庫裡那一列（D-225）。門鈴只說「現在去看一下」，內容不當指令用：
漏聽一次由原本的輪詢補上，被亂按一次只是多看一次。

用的是資料庫內建的 LISTEN／NOTIFY。聽的那條連線自己一條執行緒：它連不上、斷線、卡住，
都不會拖慢中繼程式原本的每一輪——門鈴只會讓事情變快，不會變慢。
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# 資料庫的觸發器（relay.BELL_DDL）往這個頻道發通知
CHANNEL = "autotrade_ring"
# 每支程式自己專用的頻道（拿來測「這條線真的聽得到」）的開頭
OWN_PREFIX = "autotrade_bell_"
PING_SQL = "SELECT pg_notify(%s, %s)"

log = logging.getLogger(__name__)


class BellError(Exception):
    """這條連線聽不到資料庫的通知。"""


@dataclass(frozen=True)
class BellState:
    """門鈴現在的狀況。ok：None＝還在連、還沒確認；True＝確認聽得到；False＝聽不到（detail 寫原因）。"""

    ok: bool | None
    detail: str = ""


NOT_YET = BellState(None, "還沒連上")


class Bell(Protocol):
    """中繼程式看得到的門鈴。兩個動作都不可以丟例外。"""

    state: BellState

    def wait(self, timeout_s: float) -> bool:
        """等到有人按鈴、或時間到。有人按回 True。"""
        ...

    def close(self) -> None:
        """收工。"""
        ...


def _token() -> str:
    return uuid.uuid4().hex


@dataclass
class PgBell:
    """用 psycopg 聽資料庫的通知。兩條 autocommit 的連線：connect 專門聽，connect_ping 專門按測試鈴。

    測試鈴一定要從「另一條」連線按：在聽的那條連線自己按，就算它平常聽不到別人（例如連到交易模式的連線池），
    也會在自己那一句的回應裡收到，測不出來（2026-11-14 在雲端實測過）。errors 是連線平常會丟的例外。
    """

    connect: Callable[[], Any]
    connect_ping: Callable[[], Any]
    errors: tuple[type[BaseException], ...] = (OSError,)
    check_s: float = 20.0  # 每隔這麼久按一次測試鈴，確認這條線真的還聽得到
    heard_within_s: float = 5.0  # 測試鈴這麼久沒聽到，就當這條線聽不到
    retry_s: float = 2.0  # 聽不到之後隔多久重連；連續失敗每次加倍
    retry_max_s: float = 300.0
    clock: Callable[[], float] = time.monotonic
    token: Callable[[], str] = _token
    nap: Callable[[float], object] | None = None  # 重連前怎麼等（測試用；預設等到時間到或被叫停）
    state: BellState = NOT_YET
    _own: str = ""
    _rung: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __post_init__(self) -> None:
        suffix = self.token()[:12]
        if not suffix.isascii() or not suffix.isalnum():
            raise ValueError(f"頻道名稱只能用英數字：{suffix!r}")
        self._own = OWN_PREFIX + suffix.lower()

    def start(self) -> None:
        """開始在背景聽。叫幾次都只有一條執行緒。"""
        if self._thread is None:
            self._thread = threading.Thread(
                target=self.run_forever, name="autotrade-bell", daemon=True
            )
            self._thread.start()

    def wait(self, timeout_s: float) -> bool:
        """等到有人按鈴、或時間到。有人按回 True（一次鈴只叫醒一次）。"""
        rung = self._rung.wait(timeout_s)
        if rung:
            # 清掉之後才去查資料庫：這之間再來的鈴，查的時候那一列已經在了，不會漏
            self._rung.clear()
        return rung

    def close(self) -> None:
        """請背景執行緒收工（它等完手上這一次就結束）。"""
        self._stop.set()

    def join(self, timeout_s: float) -> bool:
        """等背景執行緒結束；結束了回 True。"""
        if self._thread is None:
            return True
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def run_forever(self) -> None:
        """背景執行緒做的事：一直聽；出問題就記下來、隔一陣子重連。任何錯誤都不會丟出去。"""
        gap = self.retry_s
        expected: tuple[type[BaseException], ...] = (*self.errors, BellError)
        while not self._stop.is_set():
            try:
                self.serve()
                continue  # 被叫停才會正常回來
            except expected as exc:  # 連不上、斷線、聽不到：平常就會遇到
                problem = str(exc).strip() or type(exc).__name__
            except Exception as exc:
                # 這條執行緒不能默默死掉：沒料到的錯也記下來（連同出錯的位置），照樣重連
                log.exception("門鈴的背景執行緒遇到沒料到的錯誤")
                problem = str(exc).strip() or type(exc).__name__
            if self.state.ok is True:
                gap = self.retry_s  # 好過一次之後，重連的間隔從頭算
            self.state = BellState(False, problem)
            (self.nap or self._stop.wait)(gap)
            gap = min(gap * 2, self.retry_max_s)

    def serve(self) -> None:
        """兩條連線的一生：一條聽通知，一條定時按測試鈴。被叫停才正常回來；連線出錯或聽不到就丟例外。"""
        conn = self.connect()
        try:
            conn.execute(f"LISTEN {CHANNEL}")
            conn.execute(f"LISTEN {self._own}")
            sender = self.connect_ping()
            try:
                self._listen(conn, sender)
            finally:
                sender.close()
        finally:
            conn.close()

    def _listen(self, conn: Any, sender: Any) -> None:
        proved_at: float | None = None
        sent_at: float | None = None
        nonce = ""
        while not self._stop.is_set():
            now = self.clock()
            if sent_at is None and (proved_at is None or now - proved_at >= self.check_s):
                nonce = self.token()
                sender.execute(PING_SQL, (self._own, nonce))
                sent_at = now
            if sent_at is not None:
                left = sent_at + self.heard_within_s - now
                if left <= 0:
                    raise BellError(
                        f"測試鈴 {self.heard_within_s:g} 秒內沒聽到（這條連線收不到資料庫的通知）"
                    )
            else:
                assert proved_at is not None
                left = proved_at + self.check_s - now
            for note in conn.notifies(timeout=max(left, 0.0), stop_after=1):
                if note.channel == CHANNEL:
                    self._rung.set()
                elif note.channel == self._own and note.payload == nonce and sent_at is not None:
                    proved_at, sent_at = self.clock(), None
                    if self.state.ok is not True:
                        self.state = BellState(True, "")


def open_bell(dsn: str, timeout_s: float, port: int | None = None) -> PgBell:
    """用連線字串做一個門鈴（需要 psycopg）。還沒 start() 之前不連線。

    port：聽的那條連線改連這個埠。Supabase 的連線池平常用的 6543 是交易模式，聽不到通知；要聽得改連 5432（session 模式）。
    按測試鈴的那條連線照連線字串原本的埠（跟網站寫入指令走的路一樣）。
    """
    psycopg = importlib.import_module("psycopg")

    def opener(**more: Any) -> Callable[[], Any]:
        # autocommit：LISTEN 馬上生效、通知馬上收得到。keepalives：線路默默斷掉時發現得了（同 relay.open_pg）
        return lambda: psycopg.connect(
            dsn,
            autocommit=True,
            prepare_threshold=None,
            connect_timeout=max(1, int(timeout_s)),
            keepalives=1,
            keepalives_idle=5,
            keepalives_interval=2,
            keepalives_count=2,
            **more,
        )

    listen = opener() if port is None else opener(port=port)
    return PgBell(listen, opener(), errors=(psycopg.Error, OSError))
