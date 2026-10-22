"""網頁推播（T-348）：雲端排程寫好當天名單後，發「今日名單已更新」給開了通知的裝置；
名單上同族群兩檔以上、而且那個族群當天資金流入時，另發一則族群資金通知（T-354）。
這兩種都跟名單有關，只發給 configs/push.yaml 的 list_recipients（＝網站 admin，T-429／D-207）。

上半部是純函數（組訊息、檢查推播網址、統計發送結果）；下半部連資料庫。實際發送的函式由呼叫端注入，
測試不連網。訂閱存在雲端的 web.push_subscriptions（網站 /api/push 寫入，見 frontend/functions/_lib/push.ts）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
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
    """瀏覽器給的推播訂閱，與開通知的帳號（email）。"""

    endpoint: str
    p256dh: str
    auth: str
    email: str


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


def for_recipients(subs: Sequence[Subscription], recipients: Sequence[str]) -> list[Subscription]:
    """只留收件人帳號的訂閱（T-429：名單只給 admin，名單通知也是）；email 不分大小寫。"""
    allow = {r.strip().lower() for r in recipients}
    return [s for s in subs if s.email.strip().lower() in allow]


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


@dataclass(frozen=True)
class HotGroup:
    """名單上有好幾檔、而且當天資金流入的族群。picks＝名單上這個族群的股票（股名，查不到用代號）。"""

    group: str
    ratio: float
    n: int
    up: int
    picks: tuple[str, ...]


def hot_groups(
    picks: Sequence[tuple[str, str | None]],
    groups: Mapping[str, str],
    flows: Mapping[str, tuple[float | None, int, int, bool]],
    min_picks: int,
) -> list[HotGroup]:
    """名單（代號、股名，照線別與名次排、可能重複）→ 同族群至少 min_picks 檔且資金流入的族群。

    flows：族群 → (資金倍數, 有成交檔數, 上漲檔數, 資金流入)。名單上的檔數多的在前，一樣多看資金倍數。
    """
    seen: set[str] = set()
    by_group: dict[str, list[str]] = {}
    for sid, name in picks:
        group = groups.get(sid)
        if sid in seen or group is None:
            continue
        seen.add(sid)
        by_group.setdefault(group, []).append(name or sid)
    out: list[HotGroup] = []
    for group, names in by_group.items():
        flow = flows.get(group)
        if flow is None or len(names) < min_picks:
            continue
        ratio, n, up, hot = flow
        if hot and ratio is not None:
            out.append(HotGroup(group, ratio, n, up, tuple(names)))
    out.sort(key=lambda h: (-len(h.picks), -h.ratio, h.group))
    return out


def compose_groups(
    day: dt.date, hot: Sequence[HotGroup], site_url: str, max_listed: int
) -> dict[str, str]:
    """族群資金通知：標題寫族群與倍數，內文一個族群一行（倍數、漲跌家數、名單上的股票）；點開到「盤勢」分頁（原「族群」）。"""
    if not hot:
        raise ValueError("沒有資金流入的族群，不該組通知")
    shown = hot[:max_listed]
    head = "、".join(f"{h.group} {h.ratio:.1f} 倍" for h in shown)
    more = f" 等 {len(hot)} 個" if len(hot) > max_listed else ""
    body = "\n".join(
        f"{h.group}：資金 {h.ratio:.1f} 倍、{h.n} 檔 {h.up} 漲｜名單 {len(h.picks)} 檔：{'、'.join(h.picks)}"
        for h in shown
    )
    return {
        "title": f"{day.month}/{day.day} 資金流入：{head}{more}",
        "body": body,
        "url": f"{site_url}?tab=group",
        "tag": f"groups-{day.isoformat()}",
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


def load_hot_groups(conn: Any, schema: str, day: dt.date, min_picks: int) -> list[HotGroup] | None:
    """當天名單上同族群至少 min_picks 檔、而且資金流入的族群。

    當天還沒有名單或族群資金還沒算 → None（還沒好，不發也不記錄，晚上那班再看）。族群照網站：
    族群表上有就用表上的，沒有（重抓之間新上市）用官方產業別。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.group_flow_daily",))
        row = cur.fetchone()
        if not (row and row[0]):
            return None
        cur.execute(
            f'SELECT grp, ratio, n, up, hot FROM "{schema}"."group_flow_daily" WHERE date = %s',
            (day,),
        )
        flows = {str(g): (r, int(n), int(u), bool(h)) for g, r, n, u, h in cur.fetchall()}
        if not flows:
            return None
        cur.execute(
            f"SELECT p.stock_id, m.name, coalesce(g.grp, m.industry) "
            f'FROM "{schema}"."daily_picks" p '
            f'LEFT JOIN "{schema}"."stock_meta" m ON m.stock_id = p.stock_id '
            f'LEFT JOIN "{schema}"."stock_group" g ON g.stock_id = p.stock_id '
            "WHERE p.date = %s ORDER BY p.line, p.rank",
            (day,),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    picks = [(str(sid), str(name) if name else None) for sid, name, _ in rows]
    groups = {str(sid): str(grp) for sid, _, grp in rows if grp}
    return hot_groups(picks, groups, flows, min_picks)


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
            f"SELECT endpoint, p256dh, auth, email FROM {WEB_SCHEMA}.push_subscriptions ORDER BY created_at"
        )
        return [Subscription(str(e), str(p), str(a), str(m)) for e, p, a, m in cur.fetchall()]


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
