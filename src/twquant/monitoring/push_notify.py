"""網頁推播（T-348）：雲端排程寫好當天名單後，發「今日名單已更新」給開了通知的裝置。

上半部是純函數（組訊息、檢查推播網址、統計發送結果）；下半部連資料庫。實際發送的函式由呼叫端注入，
測試不連網。訂閱存在雲端的 web.push_subscriptions（網站 /api/push 寫入，見 frontend/functions/_lib/push.ts）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

# 網站使用者資料的 schema（與 serving 分開：重灌、修剪都不碰）
WEB_SCHEMA = "web"
# 推播服務回這兩個狀態碼＝訂閱已失效（使用者關了通知、移除主畫面 App 或換手機），要刪掉
GONE_STATUSES = (404, 410)


@dataclass(frozen=True)
class LineCount:
    """一條線當天的檔數，與比上一個有名單的日子新進幾檔（這條線之前沒有名單 → None）。"""

    code: str
    name: str
    count: int
    new: int | None


@dataclass(frozen=True)
class Subscription:
    """瀏覽器給的推播訂閱。"""

    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True)
class SendResult:
    """發送結果：成功幾則、哪些訂閱已失效、哪些失敗（推播服務與狀態碼）。"""

    sent: int
    gone: tuple[str, ...]
    failed: tuple[str, ...]


def host_allowed(endpoint: str, hosts: Sequence[str]) -> bool:
    """推播網址必須是 https，且是已知推播服務（本身或子網域）；擋掉有人登記任意網址讓排程替他打請求。"""
    u = urlsplit(endpoint)
    host = u.hostname
    if u.scheme != "https" or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in hosts)


def line_counts(
    today: dict[str, list[str]],
    previous: dict[str, list[str]],
    lines: Sequence[tuple[str, str]],
) -> list[LineCount]:
    """依設定的線別順序整理檔數與新進數。previous＝各線上一個有名單的日子（沒有就不在 dict 裡）。"""
    out: list[LineCount] = []
    for code, name in lines:
        cur = today.get(code, [])
        prev = previous.get(code)
        out.append(
            LineCount(code, name, len(cur), None if prev is None else len(set(cur) - set(prev)))
        )
    return out


def compose(day: dt.date, lines: Sequence[LineCount], site_url: str) -> dict[str, str]:
    """通知內容：標題「10/13 今日名單已更新」，內文每條線的檔數與新進數；tag 同一天只留一則。"""
    parts: list[str] = []
    for ln in lines:
        if ln.count == 0:
            parts.append(f"{ln.name}今天沒有")
        elif ln.new:
            parts.append(f"{ln.name} {ln.count} 檔（新進 {ln.new}）")
        else:
            parts.append(f"{ln.name} {ln.count} 檔")
    return {
        "title": f"{day.month}/{day.day} 今日名單已更新",
        "body": "、".join(parts),
        "url": site_url,
        "tag": f"picks-{day.isoformat()}",
    }


def send_all(
    subs: Sequence[Subscription],
    payload: dict[str, str],
    send: Callable[[Subscription, str], int],
) -> SendResult:
    """逐一發送。send 回傳推播服務的 HTTP 狀態碼；2xx 成功、404／410 失效，其餘記成失敗（不吞掉）。"""
    data = json.dumps(payload, ensure_ascii=False)
    sent = 0
    gone: list[str] = []
    failed: list[str] = []
    for s in subs:
        status = send(s, data)
        if 200 <= status < 300:
            sent += 1
        elif status in GONE_STATUSES:
            gone.append(s.endpoint)
        else:
            failed.append(f"{urlsplit(s.endpoint).hostname} HTTP {status}")
    return SendResult(sent, tuple(gone), tuple(failed))


# ── 資料庫 ─────────────────────────────────────────────────────────────────


def _table_exists(conn: Any, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{WEB_SCHEMA}.{table}",))
        row = cur.fetchone()
    return bool(row and row[0])


def load_line_counts(
    conn: Any, schema: str, day: dt.date, lines: Sequence[tuple[str, str]]
) -> list[LineCount] | None:
    """當天各線的名單與新進數；當天一條名單都沒有 → None（還沒寫好，不發）。"""
    with conn.cursor() as cur:
        cur.execute(f'SELECT line, stock_id FROM "{schema}"."daily_picks" WHERE date = %s', (day,))
        rows = cur.fetchall()
        if not rows:
            return None
        today: dict[str, list[str]] = {}
        for line, sid in rows:
            today.setdefault(str(line), []).append(str(sid))
        previous: dict[str, list[str]] = {}
        for code, _ in lines:
            cur.execute(
                f'SELECT stock_id FROM "{schema}"."daily_picks" WHERE line = %s AND date = '
                f'(SELECT max(date) FROM "{schema}"."daily_picks" WHERE line = %s AND date < %s)',
                (code, code, day),
            )
            prev = [str(r[0]) for r in cur.fetchall()]
            if prev:
                previous[code] = prev
    return line_counts(today, previous, lines)


def already_sent(conn: Any, day: dt.date, kind: str) -> bool:
    """這天這種通知發過沒（web.push_log）。"""
    if not _table_exists(conn, "push_log"):
        return False
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {WEB_SCHEMA}.push_log WHERE date = %s AND kind = %s", (day, kind)
        )
        return cur.fetchone() is not None


def load_subscriptions(conn: Any) -> list[Subscription]:
    """所有訂閱；還沒有人開過通知時表還沒建 → 空清單。"""
    if not _table_exists(conn, "push_subscriptions"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT endpoint, p256dh, auth FROM {WEB_SCHEMA}.push_subscriptions ORDER BY created_at"
        )
        return [Subscription(str(e), str(p), str(a)) for e, p, a in cur.fetchall()]


def record(conn: Any, day: dt.date, kind: str, result: SendResult, now: dt.datetime) -> None:
    """記錄已發（同一天同一種只發一次），並刪掉失效的訂閱。"""
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {WEB_SCHEMA}")
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {WEB_SCHEMA}.push_log ("
            "date date NOT NULL, kind text NOT NULL, sent_at timestamptz NOT NULL, "
            "sent integer NOT NULL, gone integer NOT NULL, failed integer NOT NULL, PRIMARY KEY (date, kind))"
        )
        cur.execute(
            f"INSERT INTO {WEB_SCHEMA}.push_log (date, kind, sent_at, sent, gone, failed) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (date, kind) DO NOTHING",
            (day, kind, now, result.sent, len(result.gone), len(result.failed)),
        )
        if result.gone:
            cur.execute(
                f"DELETE FROM {WEB_SCHEMA}.push_subscriptions WHERE endpoint = ANY(%s)",
                (list(result.gone),),
            )
    conn.commit()


def webpush_sender(
    private_key: str, subject: str, ttl: int, timeout: float
) -> Callable[[Subscription, str], int]:
    """用 pywebpush 發（VAPID 簽章、aes128gcm 加密）。推播服務回錯誤時回它的狀態碼，連不上才丟錯。"""
    from pywebpush import WebPushException, webpush

    def send(sub: Subscription, data: str) -> int:
        try:
            resp = webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=data,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},  # pywebpush 會在 dict 裡補 aud／exp，每次給新的
                ttl=ttl,
                timeout=timeout,
            )
        except WebPushException as e:
            if e.response is not None:
                return int(e.response.status_code)
            raise
        return int(resp.status_code)

    return send
