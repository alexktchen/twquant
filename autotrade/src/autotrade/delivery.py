"""把通知送出去：背景佇列、失敗重試與紀錄；Telegram（真的送）與測試、預覽用的假實作。

這裡不懂交易，只管「一段文字送不送得出去」。引擎那邊只呼叫 Outbox.put，不等網路。
"""

from __future__ import annotations

import http.client
import json
import logging
import queue
import re
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TextIO

from autotrade.config import ConfigError, NotifyConfig

log = logging.getLogger(__name__)

ENV_TOKEN = "AUTOTRADE_TELEGRAM_BOT_TOKEN"  # Telegram 機器人的金鑰
ENV_CHAT = "AUTOTRADE_TELEGRAM_CHAT_ID"  # 收件代號（你跟機器人的那個聊天）
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_MAX_CHARS = 4096  # Telegram 一則文字訊息的長度上限
CLIPPED = "…（太長，後面省略）"
MASK = "***"
ERROR_BODY_BYTES = 200  # 對方回的不是看得懂的 JSON 時，錯誤訊息裡最多引用開頭這麼多
# Telegram 回的狀態碼 → 給人看的說明（沒列到的：5xx 是對方暫時有問題）
TELEGRAM_HINTS = {
    400: f"Telegram 不接受這個請求（常見原因：收件代號不對，請檢查 .env 的 {ENV_CHAT}）",
    401: f"金鑰不對（請檢查 .env 的 {ENV_TOKEN}）",
    403: "機器人不能傳訊息給這個對象（還沒在 Telegram 對機器人按「開始」，或把它封鎖了）",
    404: f"找不到這個機器人（請檢查 .env 的 {ENV_TOKEN}）",
    429: "Telegram 要我們慢一點",
}
_TOKEN_SHAPE = re.compile(r"\d+:[A-Za-z0-9_-]+")
_CHAT_SHAPE = re.compile(r"-?\d+|@[A-Za-z0-9_]+")

Post = Callable[[str, bytes, float], tuple[int, bytes]]


class SendError(Exception):
    """一則通知沒送出去。retryable＝再試可能會成功（連不上、逾時、對方要我們慢一點）。"""

    def __init__(self, reason: str, *, retryable: bool, retry_after_s: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after_s = retry_after_s


class Sender(Protocol):
    """送出端：把一段文字送到手機。送不出去就丟 SendError。"""

    def send(self, text: str, *, silent: bool) -> None:
        """送一則；silent＝靜音送達。"""
        ...


class MemorySender:
    """把要送的內容記在記憶體（測試用）。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    def send(self, text: str, *, silent: bool) -> None:
        """記下來。"""
        self.sent.append((text, silent))


class PrintSender:
    """把通知印在畫面上（預覽用，不連網）。畫面顯示不了的字（例如表情符號）換成問號。"""

    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out

    def send(self, text: str, *, silent: bool) -> None:
        """印一則，後面加一條分隔線。"""
        out = self._out if self._out is not None else sys.stdout
        encoding = getattr(out, "encoding", None) or "utf-8"
        block = f"{text}\n{'─' * 24}"
        print(block.encode(encoding, errors="replace").decode(encoding), file=out)


def http_post(url: str, body: bytes, timeout_s: float) -> tuple[int, bytes]:
    """用標準函式庫送一個 JSON POST，回（狀態碼、回應本文）；連不上或逾時丟 OSError。"""
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        finally:
            exc.close()


def telegram_call(
    token: str,
    method: str,
    payload: dict[str, Any],
    timeout_s: float,
    post: Post | None,
    hide: tuple[str, ...],
) -> Any:
    """呼叫一次 Telegram Bot API，回傳 result。失敗丟 SendError；訊息裡 hide 列的字串（金鑰、收件對象）一律遮掉。"""

    def clean(text: str) -> str:
        for secret in hide:
            text = text.replace(secret, MASK)
        return text

    url = f"{TELEGRAM_API}/bot{token}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        status, raw = (post or http_post)(url, body, timeout_s)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # 原本的錯誤可能帶著含金鑰的網址：只留遮過的文字，不把它接在後面一起丟
        reason = clean(f"連不上 Telegram：{type(exc).__name__}: {exc}")
        raise SendError(reason, retryable=True) from None
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = None
    if status == 200 and isinstance(answer, dict) and answer.get("ok") is True:
        return answer.get("result")
    description = answer.get("description") if isinstance(answer, dict) else None
    if not isinstance(description, str):
        description = raw[:ERROR_BODY_BYTES].decode("utf-8", errors="replace")
    parameters = answer.get("parameters") if isinstance(answer, dict) else None
    wait = parameters.get("retry_after") if isinstance(parameters, dict) else None
    fallback = "Telegram 那邊暫時有問題" if status >= 500 else "Telegram 沒有確認收到"
    hint = TELEGRAM_HINTS.get(status, fallback)
    raise SendError(
        clean(f"Telegram 回 {status}：{hint}（原文：{description}）"),
        # 200 卻不是成功的回應（例如被網路登入頁攔截）當成沒送到，可以再試
        retryable=status in (200, 429) or status >= 500,
        retry_after_s=float(wait) if isinstance(wait, int | float) else None,
    )


def _check_token(token: str) -> None:
    if not token:
        raise ConfigError(f"環境變數 {ENV_TOKEN} 沒有設定（Telegram 機器人的金鑰，放在 .env）")
    if _TOKEN_SHAPE.fullmatch(token) is None:
        raise ConfigError(
            f"環境變數 {ENV_TOKEN} 的內容不像 Telegram 機器人的金鑰（應該是「一串數字:一串英數字」）"
        )


class TelegramSender:
    """用 Telegram 機器人送文字訊息。金鑰與收件對象只留在這個物件裡，錯誤訊息一律遮掉。"""

    def __init__(
        self, token: str, chat_id: str, timeout_s: float, post: Post | None = None
    ) -> None:
        _check_token(token)
        if _CHAT_SHAPE.fullmatch(chat_id) is None:
            raise ConfigError(
                f"環境變數 {ENV_CHAT} 的內容不像 Telegram 的收件代號（應該是一串數字）"
            )
        self._token = token
        self._chat_id = chat_id
        self._timeout_s = timeout_s
        self._post = post

    def send(self, text: str, *, silent: bool) -> None:
        """送一則文字訊息；太長就截斷。"""
        if len(text) > TELEGRAM_MAX_CHARS:
            text = text[: TELEGRAM_MAX_CHARS - len(CLIPPED)] + CLIPPED
        numeric = self._chat_id.lstrip("-").isdigit()
        payload = {
            "chat_id": int(self._chat_id) if numeric else self._chat_id,
            "text": text,
            "disable_notification": silent,
            "link_preview_options": {"is_disabled": True},
        }
        hide = (self._token, self._chat_id)
        telegram_call(self._token, "sendMessage", payload, self._timeout_s, self._post, hide)


def telegram_from_env(
    env: Mapping[str, str], cfg: NotifyConfig, post: Post | None = None
) -> TelegramSender:
    """用環境變數裡的金鑰與收件代號建立送出端；缺了就 raise（訊息只寫變數名稱）。"""
    token = env.get(ENV_TOKEN, "").strip()
    chat_id = env.get(ENV_CHAT, "").strip()
    _check_token(token)
    if not chat_id:
        raise ConfigError(
            f"環境變數 {ENV_CHAT} 沒有設定（Telegram 的收件代號，放在 .env；可以用 notify-setup 找）"
        )
    return TelegramSender(token, chat_id, cfg.timeout_s, post)


def telegram_tell_chat_ids(token: str, timeout_s: float, post: Post | None = None) -> int:
    """請機器人回覆每個私下跟它說過話的人「你的收件代號是…」，回傳回覆了幾個人。

    代號只出現在對方自己的 Telegram 裡，不回傳、不印在電腦上。
    """
    _check_token(token)
    updates = telegram_call(token, "getUpdates", {"timeout": 0}, timeout_s, post, (token,))
    chats: list[int] = []
    for update in updates if isinstance(updates, list) else []:
        message = update.get("message") if isinstance(update, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        if not isinstance(chat, dict) or chat.get("type") != "private":
            continue
        chat_id = chat.get("id")
        if isinstance(chat_id, int) and chat_id not in chats:
            chats.append(chat_id)
    told, failed = 0, None
    for chat_id in chats:
        text = f"你的收件代號是 {chat_id}\n請把這串數字放進電腦上 .env 的 {ENV_CHAT}，再執行 notify-test。"
        payload = {"chat_id": chat_id, "text": text}
        try:
            telegram_call(token, "sendMessage", payload, timeout_s, post, (token, str(chat_id)))
        except SendError as exc:  # 其中一個人回不了（例如他封鎖了機器人）不影響其他人
            failed = exc
            continue
        told += 1
    if told == 0 and failed is not None:
        raise failed
    return told


@dataclass(frozen=True)
class Outgoing:
    """一則要送出去的通知。"""

    ts: datetime  # 這則通知講的事發生的時間
    title: str  # 第一行（失敗紀錄用它認是哪一則）
    text: str  # 完整內容
    silent: bool  # 靜音送達


@dataclass(frozen=True)
class Failure:
    """一筆「通知沒送出去」的紀錄。"""

    ts: datetime  # 那則通知講的事發生的時間
    title: str
    reason: str
    attempts: int  # 試了幾次
    final: bool  # True＝這一則確定沒送出去；False＝還在重試


@dataclass(eq=False)
class _Queued:
    seq: int
    item: Outgoing
    attempts: int = 0
    done: bool = False  # 已經結案（送出去或確定沒送出去）


class Outbox:
    """等著送出的通知。put 不等網路；真正送出在背景執行緒（start），或由呼叫端自己 drain。

    嚴格照順序送：前一則還沒有結果，後面的不會先送（網路恢復後收到的順序才不會亂）。
    每一則最後不是送出去，就是留一筆紀錄：sent + lost == offered。
    """

    def __init__(
        self,
        sender: Sender,
        *,
        warn_after_attempts: int,
        retry_wait_s: float,
        queue_size: int,
        on_failure: Callable[[Failure], None] | None = None,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._sender = sender
        self._warn_after_attempts = warn_after_attempts
        self._retry_wait_s = retry_wait_s
        self._queue_size = queue_size
        self._on_failure = on_failure
        self._stop = threading.Event()
        # 重試前的等待；回 True 表示程式要結束了、不用再等。預設可以被 close 叫醒
        self._wait = wait if wait is not None else self._stop.wait
        self._queue: queue.Queue[_Queued | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._open: set[_Queued] = set()  # 收下了、還沒結案的
        self._current: _Queued | None = None
        self._offered = 0
        self._sent = 0
        self._lost = 0
        self._unannounced = 0  # 沒送出去、還沒在別則通知裡提過的則數
        self._failures: list[Failure] = []

    @classmethod
    def from_config(
        cls,
        sender: Sender,
        cfg: NotifyConfig,
        *,
        on_failure: Callable[[Failure], None] | None = None,
        wait: Callable[[float], bool] | None = None,
    ) -> Outbox:
        """照 [notify] 的重試與佇列參數建立。"""
        return cls(
            sender,
            warn_after_attempts=cfg.warn_after_attempts,
            retry_wait_s=cfg.retry_wait_s,
            queue_size=cfg.queue_size,
            on_failure=on_failure,
            wait=wait,
        )

    @property
    def offered(self) -> int:
        """交給佇列的則數。"""
        with self._lock:
            return self._offered

    @property
    def sent(self) -> int:
        """送出去的則數。"""
        with self._lock:
            return self._sent

    @property
    def lost(self) -> int:
        """確定沒送出去的則數。"""
        with self._lock:
            return self._lost

    @property
    def pending(self) -> int:
        """收下了、還沒結案的則數。"""
        with self._lock:
            return len(self._open)

    @property
    def failures(self) -> tuple[Failure, ...]:
        """到目前為止的失敗紀錄。"""
        with self._lock:
            return tuple(self._failures)

    def record(self, failure: Failure) -> None:
        """留一筆失敗紀錄，並交給 on_failure（記錄的動作自己出錯不會往外丟）。"""
        with self._lock:
            self._failures.append(failure)
        if self._on_failure is not None:
            try:
                self._on_failure(failure)
            except Exception:
                log.exception("寫通知的失敗紀錄時出錯")

    def put(self, item: Outgoing) -> bool:
        """交一則通知給佇列，立刻回來。佇列滿了或已經關閉就丟掉這一則並留紀錄，回 False。"""
        with self._lock:
            self._offered += 1
            queued = _Queued(self._offered, item)
            if self._closed:
                refused = "通知已經關閉，這一則沒有送"
            elif len(self._open) >= self._queue_size:
                refused = f"還沒送出的通知已經排滿 {self._queue_size} 則，這一則被丟掉"
            else:
                refused = None
                self._open.add(queued)
        if refused is not None:
            self._give_up(queued, refused)
            return False
        self._queue.put(queued)
        return True

    def start(self) -> None:
        """開背景執行緒開始送。"""
        if self._thread is not None:
            raise RuntimeError("通知的背景執行緒已經開了")
        self._thread = threading.Thread(target=self._run, name="autotrade-notify", daemon=True)
        self._thread.start()

    def drain(self) -> None:
        """在呼叫的這條執行緒把排著的照順序送完，每一則都試到有結果（沒開背景執行緒時用）。"""
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                return
            if queued is not None:
                self._deliver_safely(queued)

    def close(self, wait_s: float) -> None:
        """不再收新的通知，最多等 wait_s 秒把排著的送完；還沒送出去的每一則都留紀錄。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        thread = self._thread
        if thread is None:
            self._stop.set()
            self.drain()
        else:
            self._queue.put(None)
            thread.join(wait_s)
            if thread.is_alive():
                self._stop.set()  # 叫醒重試前的等待：剩下的各再試一次，不行就放棄
                thread.join(wait_s)
        with self._lock:
            leftovers = sorted(self._open, key=lambda q: q.seq)
            sending = self._current
        for queued in leftovers:
            stuck = queued is sending
            self._give_up(queued, "程式結束時還在送，結果不明" if stuck else "程式結束時還沒送出去")
        if thread is not None and thread.is_alive():
            self._queue.put(None)  # 還卡在送出裡的背景執行緒回來後，看到這個就結束

    def _run(self) -> None:
        while True:
            queued = self._queue.get()
            if queued is None:
                return
            self._deliver_safely(queued)

    def _deliver_safely(self, queued: _Queued) -> None:
        with self._lock:
            self._current = queued
        try:
            self._deliver(queued)
        except Exception as exc:
            # 送出端丟出沒預期的錯：這一則記成沒送出去，後面的通知照送
            log.exception("通知送出時發生沒預期的錯誤")
            self._give_up(queued, f"送出時發生沒預期的錯誤：{type(exc).__name__}")
        finally:
            with self._lock:
                self._current = None

    def _deliver(self, queued: _Queued) -> None:
        """送一則：送不出去就一直試，直到送出去、確定沒救（不能重試的錯），或程式要結束。"""
        while True:
            with self._lock:
                if queued.done:  # 結束時已經被記成沒送出去，就不要再送
                    return
                owed = self._unannounced
            text, silent = queued.item.text, queued.item.silent
            if owed:
                text = f"{text}\n（前面有 {owed} 則通知沒送出去，請到電腦上看紀錄。）"
                silent = False
            queued.attempts += 1
            try:
                self._sender.send(text, silent=silent)
            except SendError as exc:
                if not exc.retryable:
                    self._give_up(queued, exc.reason)
                    return
                if queued.attempts == self._warn_after_attempts:
                    again = f"試了 {queued.attempts} 次還沒送出去，還會再試：{exc.reason}"
                    item = queued.item
                    self.record(Failure(item.ts, item.title, again, queued.attempts, False))
                if self._wait(max(self._retry_wait_s, exc.retry_after_s or 0.0)):
                    self._give_up(queued, f"程式結束前沒送出去：{exc.reason}")
                    return
                continue
            with self._lock:
                if not queued.done:
                    queued.done = True
                    self._open.discard(queued)
                    self._sent += 1
                    self._unannounced -= owed
            return

    def _give_up(self, queued: _Queued, reason: str) -> None:
        with self._lock:
            if queued.done:
                return
            queued.done = True
            self._open.discard(queued)
            self._lost += 1
            self._unannounced += 1
        self.record(Failure(queued.item.ts, queued.item.title, reason, queued.attempts, True))
