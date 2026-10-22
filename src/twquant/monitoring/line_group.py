"""LINE 群組每日圖卡（T-423／D-204）：用一支備用 LINE 帳號在一般群組的**聊天**發一張圖卡，點圖打開網站。
四種：每日名單、族群資金流入、盤前晨報、盤後快訊。做法來自 lt-foods/new_erp（linejs 登入備用帳號）。

分工是「排隊 → 撿著送」：排程寫好名單／晨報後 `line-group enqueue` 畫好圖卡（web.line_cards，網站
/pub/line-card/<token> 公開給 LINE 抓）、排進 web.line_posts（同一天同一種同一群組只排一次）；`line-group send`
撿到期的送出。要發哪些群組在網站「LINE 群組」分頁勾（web.line_groups，有權限的人才看得到）。跟 LINE 講話的是
Node 轉接器 line-poster/（linejs 只有 JS 版），這裡用子程序呼叫：stdin 一份 JSON、stdout 一行一個事件。

⚠ linejs 是 LINE **非官方**客戶端協定，違反 LINE 使用條款、帳號可能被停權——只能用備用帳號。
登入憑證存在 web.line_account：access token 7 天、refresh token 一年且每次換新都會輪替，所以要能寫回，
不能放 GitHub secret。上半部是純函數（組卡片、Flex 訊息、解析事件、決定每筆的下一個狀態），下半部連資料庫。
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import json
import secrets
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from twquant.config import LineGroupConfig
from twquant.errors import ConfigError, DataError, ExecutionError
from twquant.monitoring.line_card import Card, CardRow, CardSection
from twquant.monitoring.push_notify import HotGroup

KINDS = ("picks", "groups", "morning", "evening")
BRIEF_KINDS = {"morning": ("morning_brief", "盤前晨報"), "evening": ("evening_brief", "盤後快訊")}
ACCOUNT_ID = "default"  # 只有一支發文帳號；表留 id 欄位，之後要分帳號不用改表
_WEEKDAYS = "一二三四五六日"
# LINE 叫停（被暫時限制／701）時寫在貼文上的說明：不是失敗，下一輪自動再送
BACKOFF_HINT = "LINE 要求暫緩（暫時限制或請稍後再發），帳號先停一陣子，之後自動重送"
# 轉接器說「要開始送了」卻沒有結果：可能已經發出去，不重送（寧可漏一則也不要同一張圖發兩次）
UNKNOWN_HINT = "送到一半轉接器中斷，不確定有沒有發出去；不自動重送，請看群組"
ALT_TEXT_MAX = 400  # LINE Flex 訊息 altText 上限
TEXT_MAX = 5000  # LINE 文字訊息上限


# ── 卡片內容 ───────────────────────────────────────────────────────────────


def day_label(day: dt.date) -> str:
    """10/21（三）。"""
    return f"{day.month}/{day.day}（{_WEEKDAYS[day.weekday()]}）"


@dataclass(frozen=True)
class PickRow:
    """名單上的一檔：哪條線、第幾名、代號、股名、族群。"""

    line: str
    rank: int
    stock_id: str
    name: str | None
    group: str | None


def picks_sections(
    lines: Sequence[tuple[str, str]],
    picks: Sequence[PickRow],
    previous: Mapping[str, set[str]],
    *,
    rows_per_line: int,
) -> tuple[CardSection, ...]:
    """每日名單：照設定的線別順序，每條線列前幾名（代號、股名、族群、新進標 NEW），其餘寫「…另 N 檔」。

    previous＝各線上一個有名單的日子的代號（這條線之前沒有名單就不在 dict 裡 → 不標新進）。
    """
    by_line: dict[str, list[PickRow]] = {}
    for p in sorted(picks, key=lambda r: (r.line, r.rank)):
        by_line.setdefault(p.line, []).append(p)
    out: list[CardSection] = []
    for code, name in lines:
        rows = by_line.get(code, [])
        if not rows:
            out.append(CardSection(name, "今天沒有符合條件的股票"))
            continue
        prev = previous.get(code)
        new = {r.stock_id for r in rows if prev is not None and r.stock_id not in prev}
        note = f"{len(rows)} 檔" + (f" · 新進 {len(new)}" if new else "")
        shown = tuple(
            CardRow(
                str(r.rank),
                r.stock_id,
                r.name or r.stock_id,
                r.group or "",
                "NEW" if r.stock_id in new else "",
            )
            for r in rows[:rows_per_line]
        )
        more = f"…另 {len(rows) - rows_per_line} 檔" if len(rows) > rows_per_line else ""
        out.append(CardSection(name, note, shown, more=more))
    return tuple(out)


def groups_sections(hot: Sequence[HotGroup], *, max_listed: int) -> tuple[CardSection, ...]:
    """族群資金流入：一個族群一塊（資金倍數、漲跌家數、名單上的股票），條件同網頁推播（T-354）。"""
    if not hot:
        raise ValueError("沒有資金流入的族群，不該做圖卡")
    out = [
        CardSection(
            h.group,
            f"資金 {h.ratio:.1f} 倍 · {h.n} 檔 {h.up} 漲",
            paragraphs=(f"名單 {len(h.picks)} 檔：{'、'.join(h.picks)}",),
        )
        for h in hot[:max_listed]
    ]
    if len(hot) > max_listed:
        out[-1] = replace(out[-1], more=f"…另 {len(hot) - max_listed} 個族群")
    return tuple(out)


@dataclass(frozen=True)
class BriefRow:
    """晨報／盤後快訊的一列：模型版（標題、要點、風險）或規則版（rule_text，模型缺席時用）。"""

    headline: str | None
    bullets: tuple[str, ...]
    risk_note: str | None
    rule_text: str | None


def brief_sections(day: dt.date, kind: str, brief: BriefRow) -> tuple[CardSection, ...]:
    """晨報／盤後快訊：有模型版用標題＋要點＋風險，沒有就用規則那一句（跟網站首頁同一份）。"""
    if brief.headline:
        out = [CardSection(brief.headline, paragraphs=tuple(f"• {b}" for b in brief.bullets))]
        if brief.risk_note:
            out.append(CardSection("風險", paragraphs=(brief.risk_note,)))
        return tuple(out)
    if brief.rule_text:
        return (CardSection("", paragraphs=(brief.rule_text,)),)
    raise ValueError(f"{day} 的{BRIEF_KINDS[kind][1]}沒有內容")


_TITLES = {"picks": "今日名單", "groups": "名單上的族群資金流入"} | {
    k: v[1] for k, v in BRIEF_KINDS.items()
}


def card_title(day: dt.date, kind: str) -> str:
    """圖卡標題。"""
    return f"{day_label(day)}{_TITLES[kind]}"


def make_card(title: str, sections: tuple[CardSection, ...], cfg: LineGroupConfig) -> Card:
    """組成一整張卡（底部的「點圖看…」、網站、免責聲明照設定）。"""
    host = urlsplit(cfg.site_url).hostname or cfg.site_url
    return Card(title, sections, cfg.cta, host, cfg.footer)


def summary_lines(card: Card) -> list[str]:
    """卡片的文字摘要：標題＋每個區塊的標題與旁註（altText 與備案文字用）。"""
    out = [card.title]
    for s in card.sections:
        head = "　".join(x for x in (s.heading, s.note) if x)
        if head:
            out.append(head)
        elif s.paragraphs:
            out.append(s.paragraphs[0])
    return out


def alt_text(card: Card) -> str:
    """Flex 訊息的 altText（通知與聊天列表看到的那行），也是備案文字的內容。"""
    lines = summary_lines(card)
    text = lines[0] + ("：" + "；".join(lines[1:]) if len(lines) > 1 else "")
    return text if len(text) <= ALT_TEXT_MAX else text[: ALT_TEXT_MAX - 1] + "…"


def fallback_text(alt: str, link: str) -> str:
    """圖卡、圖片都發不出去時的文字版：摘要＋連結（LINE 會把網址做成連結預覽）。"""
    return f"{alt}\n\n{link}"


def image_caption(caption: str, link: str) -> str:
    """改發圖片時，圖後面那一行：一般圖片點了不會開網站，網址要另外給。"""
    return f"{caption}\n{link}"


def flex_message(alt: str, image_url: str, width: int, height: int, link: str) -> dict[str, Any]:
    """一則 Flex 訊息：整張是圖，點圖打開網站。"""
    if not image_url.startswith("https://") or not link.startswith("https://"):
        raise ValueError(f"Flex 的圖與連結都要 https：{image_url} {link}")
    if height > 3 * width:
        raise ValueError(f"圖太長（{width}×{height}）：Flex 圖片高度不能超過寬度三倍")
    return {
        "type": "flex",
        "altText": alt,
        "contents": {
            "type": "bubble",
            "size": "giga",
            "hero": {
                "type": "image",
                "url": image_url,
                "size": "full",
                "aspectRatio": f"{width}:{height}",
                "aspectMode": "fit",
                "action": {"type": "uri", "label": "打開 twquant", "uri": link},
            },
        },
    }


def is_square(home_id: str) -> bool:
    """社群（OpenChat）的聊天 m…；一般群組是 c…。"""
    return home_id.startswith("m")


def chat_label(home_id: str) -> str:
    """清單上標的種類（社群／群組）。"""
    return "社群" if is_square(home_id) else "群組"


def new_token() -> str:
    """圖卡網址裡的亂數（猜不到；32 個十六進位字）。"""
    return secrets.token_hex(16)


# ── 轉接器事件 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Credential:
    """LINE 登入憑證。前三個是給網站顯示／refresh 的；storage＝整份 linejs storage 的 JSON 字串
    （端對端加密金鑰等），跨 run 接續 session 用，只在資料庫與轉接器之間流動、不給網站讀。"""

    access_token: str
    refresh_token: str | None
    expire: int | None
    storage: str | None = None


@dataclass(frozen=True)
class AdapterOutput:
    """轉接器跑一次的結果。credential＝程序中吐出的最後一組（沒吐 → 呼叫時給的那組）。"""

    returncode: int
    credential: Credential | None = None
    profile: dict[str, str] = field(default_factory=dict)
    groups: tuple[dict[str, str], ...] = ()
    sending: tuple[int, ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    fatal: str | None = None


def parse_events(lines: Iterable[str], returncode: int, base: Credential | None) -> AdapterOutput:
    """把轉接器 stdout 的 NDJSON 事件收起來。credential 事件是「有換的欄位」，依序疊在 base 上。

    不是 JSON 的行（套件自己印到 stdout 的訊息）轉到 stderr，不吞掉也不當事件。
    """
    cred = base
    profile: dict[str, str] = {}
    groups: list[dict[str, str]] = []
    sending: list[int] = []
    results: list[dict[str, Any]] = []
    fatal: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            print(f"[line-poster stdout] {line}", file=sys.stderr)
            continue
        kind = ev.get("type")
        if kind == "credential":
            token = ev.get("accessToken") or (cred.access_token if cred else "")
            if not token:
                raise ExecutionError(f"轉接器回的憑證沒有 access token：{sorted(ev)}")
            cred = Credential(
                access_token=str(token),
                refresh_token=ev["refreshToken"]
                if ev.get("refreshToken")
                else (cred.refresh_token if cred else None),
                expire=int(ev["expire"])
                if ev.get("expire") is not None
                else (cred.expire if cred else None),
                storage=cred.storage if cred else None,
            )
        elif kind == "storage":
            data = str(ev.get("data") or "")
            if data and cred is not None:
                cred = replace(cred, storage=data)
        elif kind == "profile":
            profile = {k: str(v) for k, v in ev.items() if k != "type" and v is not None}
        elif kind == "group":
            groups.append({"homeId": str(ev["homeId"]), "name": str(ev.get("name") or "")})
        elif kind == "sending":
            sending.append(int(ev["id"]))
        elif kind == "result":
            results.append(ev)
        elif kind in ("qr", "pin"):
            pass  # 登入進度：串流時由 on_event 處理（web_login），收尾時不用
        elif kind == "fatal":
            fatal = str(ev.get("error") or "轉接器失敗（沒有原因）")
        else:
            raise ExecutionError(f"轉接器回了不認得的事件：{line[:200]}")
    if returncode != 0 and fatal is None:
        fatal = f"轉接器結束碼 {returncode}"
    return AdapterOutput(
        returncode, cred, profile, tuple(groups), tuple(sending), tuple(results), fatal
    )


AdapterRunner = Callable[[str, dict[str, Any], Credential | None], AdapterOutput]
EventSink = Callable[[dict[str, Any]], None]


def run_node(
    cfg: LineGroupConfig,
    repo_root: Path,
    command: str,
    request: dict[str, Any],
    base: Credential | None,
    on_event: EventSink | None = None,
) -> AdapterOutput:
    """跑一次 Node 轉接器，一行一行讀事件（登入時 QR、PIN 要邊跑邊寫進資料庫給網站顯示）。

    stderr 直接接到終端機（QR、PIN、錯誤細節要讓人看到）；跑超過時限就砍掉，已讀到的事件照算。
    """
    timeout = cfg.login_timeout_seconds if command == "login" else cfg.adapter_timeout_seconds
    lines: list[str] = []
    killed = threading.Event()
    # with：結束時關掉三條管線並等程序收掉（逾時被砍時也不留沒關的檔案）
    with subprocess.Popen(
        [cfg.node_bin, str(repo_root / cfg.adapter), command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        cwd=str(repo_root),
    ) as proc:
        if proc.stdin is None or proc.stdout is None:
            raise ExecutionError("轉接器的 stdin／stdout 沒接上")

        def kill() -> None:
            killed.set()
            proc.kill()

        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            proc.stdin.write(json.dumps(request, ensure_ascii=False))
            proc.stdin.close()
            for line in proc.stdout:
                lines.append(line)
                if on_event is not None:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 雜訊行：parse_events 會轉到 stderr
                    if isinstance(ev, dict):
                        on_event(ev)
            proc.wait()
        except BaseException:
            proc.kill()
            raise
        finally:
            timer.cancel()
    res = parse_events(lines, proc.returncode, base)
    return replace(res, fatal=f"轉接器跑超過 {timeout:.0f} 秒被中止") if killed.is_set() else res


def node_runner(cfg: LineGroupConfig, repo_root: Path) -> AdapterRunner:
    """send／groups 用的轉接器（不需要邊跑邊處理事件）。"""

    def run(command: str, request: dict[str, Any], base: Credential | None) -> AdapterOutput:
        return run_node(cfg, repo_root, command, request, base)

    return run


# linejs 登不進去的兩種訊息：token 被拒、access token 過期又沒有能用的 refresh token
_LOGIN_ERRORS = ("AUTHENTICATION_FAILED", "MUST_REFRESH_V3_TOKEN", "cached token rejected")


def needs_login(error: str) -> bool:
    """這個錯誤是不是要重新掃 QR 登入。"""
    return any(k in error for k in _LOGIN_ERRORS)


# ── 每筆貼文的下一個狀態 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Job:
    """web.line_posts 裡到期要送的一列（含圖卡的尺寸，組 Flex 訊息用）。"""

    id: int
    kind: str
    home_id: str
    card_token: str
    alt_text: str
    link_url: str
    attempts: int
    width: int
    height: int

    def request(self, cfg: LineGroupConfig) -> dict[str, Any]:
        """給轉接器的那一筆：Flex 圖卡；備案依序是直接發圖（card，圖在請求的 cards 裡）、摘要文字。

        社群不發 Flex（message=None）：LIFF 分享不支援社群，直接從「發圖＋一行網址」開始。
        """
        return {
            "id": self.id,
            "homeId": self.home_id,
            "message": None
            if is_square(self.home_id)
            else flex_message(
                self.alt_text,
                f"{cfg.card_url_base}{self.card_token}.png",
                self.width,
                self.height,
                self.link_url,
            ),
            "card": self.card_token if cfg.fallback_image else "",
            "imageCaption": image_caption(cfg.image_caption, self.link_url),
            "fallbackText": fallback_text(self.alt_text, self.link_url)
            if cfg.fallback_text
            else "",
        }


_VIA_NOTE = {"image": "改發圖片", "text": "改發文字"}


def next_state(
    job: Job,
    result: Mapping[str, Any] | None,
    *,
    sending: bool,
    now: dt.datetime,
    max_attempts: int,
) -> dict[str, Any]:
    """依轉接器的結果決定這一列要改哪些欄位（純函數）。

    - 送出：status=sent（via＝flex、image 或 text；改發時把前面失敗的錯誤留著）
    - 沒結果但說了要送（送到一半中斷）：status=unknown，不重送
    - 沒結果也沒開始送：原樣（{}）
    - LINE 叫停：維持 queued，不算一次失敗；其他錯誤 attempts+1，到上限才標 failed
    """
    if result is None:
        return {"status": "unknown", "last_error": UNKNOWN_HINT} if sending else {}
    if result.get("sent"):
        via = str(result.get("via") or "flex")
        notes = [
            f"{label}{result[key]}"
            for key, label in (
                ("flexError", f"圖卡發不出去，{_VIA_NOTE.get(via, '改發文字')}："),
                ("imageError", "圖片也發不出去："),
                ("captionError", "圖片後面那行網址沒發出去："),
            )
            if result.get(key)
        ]
        return {
            "status": "sent",
            "via": via,
            "sent_at": now,
            "last_error": "；".join(notes)[:1000] if notes else None,
        }
    error = result.get("error")
    if not error:
        raise ExecutionError(f"line_posts #{job.id}：轉接器說沒送出卻沒有錯誤：{result}")
    if result.get("backoffMs") is not None:
        return {"last_error": BACKOFF_HINT}
    attempts = job.attempts + 1
    return {
        "attempts": attempts,
        "status": "failed" if attempts >= max_attempts else "queued",
        "last_error": str(error)[:1000],
    }


def backoff_until(results: Sequence[Mapping[str, Any]], now: dt.datetime) -> dt.datetime | None:
    """這一輪有沒有被 LINE 叫停；有的話帳號停到什麼時候。"""
    waits = [int(r["backoffMs"]) for r in results if r.get("backoffMs") is not None]
    return now + dt.timedelta(milliseconds=max(waits)) if waits else None


# ── 資料庫 ─────────────────────────────────────────────────────────────────


def ensure_tables(conn: Any, schema: str) -> None:
    """建帳號、登入進度、群組、圖卡、排隊五張表（第一次用時自動建；沒有備份，丟了只是重新掃 QR、重新勾群組）。"""
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_account ('
            "id text PRIMARY KEY, mid text, display_name text, storage_json text, "
            "access_token text NOT NULL, refresh_token text, token_expire bigint, "
            "updated_at timestamptz NOT NULL, paused_until timestamptz, busy_until timestamptz, "
            "last_error text)"
        )
        # storage_json 是後加的欄位（T-423 修 E2EE）：舊表（先前已登入過）要補上
        cur.execute(
            f'ALTER TABLE "{schema}".line_account ADD COLUMN IF NOT EXISTS storage_json text'
        )
        # enabled：網站「LINE 群組」分頁勾的（新加入的群組預設不發）；present：帳號現在還在不在這個群組
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_groups ('
            "home_id text PRIMARY KEY, name text NOT NULL, enabled boolean NOT NULL DEFAULT false, "
            "present boolean NOT NULL DEFAULT true, synced_at timestamptz NOT NULL, "
            "updated_by text, updated_at timestamptz)"
        )
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_login ('
            "id text PRIMARY KEY, status text NOT NULL, requested_by text, requested_at timestamptz, "
            "updated_at timestamptz NOT NULL, run_url text, qr_url text, qr_png text, pin text, "
            "display_name text, error text)"
        )
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_cards ('
            "token text PRIMARY KEY, date date NOT NULL, kind text NOT NULL, png bytea NOT NULL, "
            "width integer NOT NULL, height integer NOT NULL, created_at timestamptz NOT NULL)"
        )
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_posts ('
            "id bigserial PRIMARY KEY, date date NOT NULL, kind text NOT NULL, home_id text NOT NULL, "
            f'card_token text NOT NULL REFERENCES "{schema}".line_cards(token) ON DELETE CASCADE, '
            "alt_text text NOT NULL, link_url text NOT NULL, status text NOT NULL, via text, "
            "enqueued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, sent_at timestamptz, "
            "attempts integer NOT NULL DEFAULT 0, last_error text, UNIQUE (date, kind, home_id))"
        )
    conn.commit()


def _exists(cur: Any, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f'"{schema}"."{table}"',))
    row = cur.fetchone()
    return bool(row and row[0])


def load_picks(
    conn: Any, schema: str, day: dt.date, lines: Sequence[tuple[str, str]]
) -> tuple[list[PickRow], dict[str, set[str]]] | None:
    """當天名單（含股名、族群）與各線上一個有名單的日子的代號；當天沒有名單 → None。

    族群照網站：族群表上有就用表上的，沒有（重抓之間新上市）用官方產業別。
    """
    with conn.cursor() as cur:
        groups = _exists(cur, schema, "stock_group")
        cur.execute(
            "SELECT p.line, p.rank, p.stock_id, m.name, "
            + ("coalesce(g.grp, m.industry) " if groups else "m.industry ")
            + f'FROM "{schema}"."daily_picks" p '
            f'LEFT JOIN "{schema}"."stock_meta" m ON m.stock_id = p.stock_id '
            + (
                f'LEFT JOIN "{schema}"."stock_group" g ON g.stock_id = p.stock_id '
                if groups
                else ""
            )
            + "WHERE p.date = %s ORDER BY p.line, p.rank",
            (day,),
        )
        rows = [
            PickRow(str(ln), int(rk), str(sid), nm or None, grp or None)
            for ln, rk, sid, nm, grp in cur.fetchall()
        ]
        if not rows:
            return None
        previous: dict[str, set[str]] = {}
        for code, _ in lines:
            cur.execute(
                f'SELECT stock_id FROM "{schema}"."daily_picks" WHERE line = %s AND date = '
                f'(SELECT max(date) FROM "{schema}"."daily_picks" WHERE line = %s AND date < %s)',
                (code, code, day),
            )
            prev = {str(r[0]) for r in cur.fetchall()}
            if prev:
                previous[code] = prev
    return rows, previous


def load_brief(conn: Any, schema: str, day: dt.date, kind: str) -> BriefRow | None:
    """那天的晨報／盤後快訊；還沒寫 → None。"""
    table = BRIEF_KINDS[kind][0]
    with conn.cursor() as cur:
        if not _exists(cur, schema, table):
            return None
        cur.execute(
            f'SELECT headline, bullets, risk_note, rule_text FROM "{schema}"."{table}" WHERE date = %s',
            (day,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    headline, bullets, risk, rule = row
    if isinstance(bullets, str):
        bullets = json.loads(bullets)
    return BriefRow(
        headline or None, tuple(str(b) for b in bullets or ()), risk or None, rule or None
    )


def enabled_groups(conn: Any, schema: str) -> list[str]:
    """網站上勾了、帳號也還在裡面的群組。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT home_id FROM "{schema}".line_groups WHERE enabled AND present ORDER BY home_id'
        )
        return [str(r[0]) for r in cur.fetchall()]


def save_groups(
    conn: Any, schema: str, groups: Sequence[Mapping[str, str]], now: dt.datetime
) -> None:
    """記下帳號現在在哪些群組（新的預設不發；不在清單上的標 present=false，不刪，網站上的勾選留著）。"""
    ids = [g["homeId"] for g in groups]
    with conn.cursor() as cur:
        for g in groups:
            cur.execute(
                f'INSERT INTO "{schema}".line_groups (home_id, name, synced_at) VALUES (%s, %s, %s) '
                "ON CONFLICT (home_id) DO UPDATE SET name = EXCLUDED.name, present = true, "
                "synced_at = EXCLUDED.synced_at",
                (g["homeId"], g["name"], now),
            )
        cur.execute(
            f'UPDATE "{schema}".line_groups SET present = false, synced_at = %s '
            "WHERE NOT (home_id = ANY(%s))",
            (now, ids),
        )
    conn.commit()


def save_card(
    conn: Any,
    schema: str,
    day: dt.date,
    kind: str,
    png: bytes,
    size: tuple[int, int],
    now: dt.datetime,
) -> str:
    """存一張圖卡，回它的 token（網址用）。"""
    token = new_token()
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".line_cards (token, date, kind, png, width, height, created_at) '
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (token, day, kind, png, size[0], size[1], now),
        )
    conn.commit()
    return token


def prune_cards(conn: Any, schema: str, today: dt.date, keep_days: int) -> int:
    """刪掉太舊的圖卡（連帶它的貼文紀錄）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'DELETE FROM "{schema}".line_cards WHERE date < %s',
            (today - dt.timedelta(days=keep_days),),
        )
        n = int(cur.rowcount)
    conn.commit()
    return n


def already_queued(
    conn: Any, schema: str, day: dt.date, kind: str, home_ids: Sequence[str]
) -> set[str]:
    """這天這種已經排過的群組（排過就不再畫圖、不再排）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT home_id FROM "{schema}".line_posts WHERE date = %s AND kind = %s '
            "AND home_id = ANY(%s)",
            (day, kind, list(home_ids)),
        )
        return {str(r[0]) for r in cur.fetchall()}


def enqueue(
    conn: Any,
    schema: str,
    day: dt.date,
    kind: str,
    home_ids: Sequence[str],
    *,
    card_token: str,
    alt: str,
    link: str,
    now: dt.datetime,
    stale_hours: float,
) -> int:
    """排進 web.line_posts；同一天同一種同一群組已經排過就不動（回新排了幾則）。"""
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    expires = now + dt.timedelta(hours=stale_hours)
    added = 0
    with conn.cursor() as cur:
        for home in home_ids:
            cur.execute(
                f'INSERT INTO "{schema}".line_posts '
                "(date, kind, home_id, card_token, alt_text, link_url, status, enqueued_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s) "
                "ON CONFLICT (date, kind, home_id) DO NOTHING",
                (day, kind, home, card_token, alt, link, now, expires),
            )
            added += cur.rowcount
    conn.commit()
    return added


def load_credential(conn: Any, schema: str) -> Credential | None:
    """存好的登入憑證；還沒登入過 → None。"""
    with conn.cursor() as cur:
        if not _exists(cur, schema, "line_account"):
            return None
        cur.execute(
            f"SELECT access_token, refresh_token, token_expire, storage_json "
            f'FROM "{schema}".line_account WHERE id = %s',
            (ACCOUNT_ID,),
        )
        row = cur.fetchone()
    return Credential(str(row[0]), row[1], row[2], row[3]) if row else None


def save_account(
    conn: Any,
    schema: str,
    cred: Credential,
    *,
    now: dt.datetime,
    profile: Mapping[str, str] | None = None,
) -> None:
    """寫回憑證（換新就馬上存：輪替後的 refresh token 丟了就要重新掃 QR）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".line_account '
            "(id, mid, display_name, access_token, refresh_token, token_expire, storage_json, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token, "
            "token_expire = EXCLUDED.token_expire, updated_at = EXCLUDED.updated_at, "
            # storage 有換才更新（send 失敗等情況可能回 None，不要把登入存好的那份蓋掉）
            "storage_json = coalesce(EXCLUDED.storage_json, line_account.storage_json), "
            "mid = coalesce(EXCLUDED.mid, line_account.mid), "
            "display_name = coalesce(EXCLUDED.display_name, line_account.display_name)",
            (
                ACCOUNT_ID,
                (profile or {}).get("mid"),
                (profile or {}).get("displayName"),
                cred.access_token,
                cred.refresh_token,
                cred.expire,
                cred.storage,
                now,
            ),
        )
    conn.commit()


def acquire_lease(conn: Any, schema: str, now: dt.datetime, lease_seconds: float) -> bool:
    """占住帳號（兩個排程同時跑時只能一個發文、換 token）；被別人占著回 False。

    Supabase transaction pooler 不能用 session 層級的 advisory lock，所以用一個到期時間欄位。
    """
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE "{schema}".line_account SET busy_until = %s '
            "WHERE id = %s AND (busy_until IS NULL OR busy_until <= %s)",
            (now + dt.timedelta(seconds=lease_seconds), ACCOUNT_ID, now),
        )
        got = bool(cur.rowcount == 1)
    conn.commit()
    return got


def release_lease(conn: Any, schema: str) -> None:
    """放開帳號。"""
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE "{schema}".line_account SET busy_until = NULL WHERE id = %s', (ACCOUNT_ID,)
        )
    conn.commit()


_ACCOUNT_COLS = (
    "mid",
    "display_name",
    "updated_at",
    "paused_until",
    "busy_until",
    "last_error",
    "token_expire",
)


def account_state(conn: Any, schema: str) -> dict[str, Any]:
    """帳號的暫停／占用狀態與上次錯誤。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT {", ".join(_ACCOUNT_COLS)} FROM "{schema}".line_account WHERE id = %s',
            (ACCOUNT_ID,),
        )
        row = cur.fetchone()
    return dict(zip(_ACCOUNT_COLS, row, strict=True)) if row else {}


def set_account_status(
    conn: Any, schema: str, *, paused_until: dt.datetime | None, last_error: str | None
) -> None:
    """記下帳號要停到什麼時候、上次整個失敗的原因（成功時清掉）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE "{schema}".line_account SET paused_until = %s, last_error = %s WHERE id = %s',
            (paused_until, last_error, ACCOUNT_ID),
        )
    conn.commit()


def due_jobs(conn: Any, schema: str, now: dt.datetime) -> list[Job]:
    """先把過期的作廢，再撿還沒送的（舊的先）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE \"{schema}\".line_posts SET status = 'expired' "
            "WHERE status = 'queued' AND expires_at <= %s",
            (now,),
        )
        cur.execute(
            "SELECT p.id, p.kind, p.home_id, p.card_token, p.alt_text, p.link_url, p.attempts, "
            f'c.width, c.height FROM "{schema}".line_posts p '
            f'JOIN "{schema}".line_cards c ON c.token = p.card_token '
            "WHERE p.status = 'queued' AND p.expires_at > %s ORDER BY p.enqueued_at, p.id",
            (now,),
        )
        jobs = [Job(*r) for r in cur.fetchall()]
    conn.commit()
    return jobs


def card_images(conn: Any, schema: str, tokens: Sequence[str]) -> dict[str, str]:
    """這一輪要用到的圖卡 PNG（base64，給轉接器改發圖片用；同一張圖發很多群組只帶一份）。"""
    want = sorted(set(tokens))
    if not want:
        return {}
    with conn.cursor() as cur:
        cur.execute(f'SELECT token, png FROM "{schema}".line_cards WHERE token = ANY(%s)', (want,))
        got = {str(t): base64.b64encode(bytes(png)).decode("ascii") for t, png in cur.fetchall()}
    conn.commit()
    missing = [t for t in want if t not in got]
    if missing:
        raise DataError(f"{schema}.line_cards", None, f"排隊的貼文找不到圖卡：{missing}")
    return got


def apply_update(conn: Any, schema: str, job_id: int, upd: Mapping[str, Any]) -> None:
    """寫回一列的新狀態。"""
    if not upd:
        return
    cols = ", ".join(f"{k} = %s" for k in upd)
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE "{schema}".line_posts SET {cols} WHERE id = %s', (*upd.values(), job_id)
        )
    conn.commit()


@dataclass(frozen=True)
class SendSummary:
    """send 一次的結果。left＝還留在隊裡等下一班的。"""

    sent: int = 0
    as_image: int = 0
    as_text: int = 0
    failed: tuple[str, ...] = ()
    left: int = 0
    note: str = ""


def _left(conn: Any, schema: str, now: dt.datetime) -> int:
    return len(due_jobs(conn, schema, now))


def send_due(
    conn: Any,
    cfg: LineGroupConfig,
    runner: AdapterRunner,
    *,
    now_fn: Callable[[], dt.datetime],
    sleep: Callable[[float], None],
) -> SendSummary:
    """撿到期的貼文送出。沒有要送的就直接結束（還沒登入也不算錯）；帳號被另一班占著就等（最多
    lock_wait_seconds）；LINE 叫停就等到解禁再送（最多 max_backoff_wait_seconds，超過就留給下一班）。憑證有換就寫回。
    """
    schema = cfg.web_schema
    ensure_tables(conn, schema)
    if not due_jobs(conn, schema, now_fn()):
        return SendSummary()
    cred = load_credential(conn, schema)
    if cred is None:
        raise ConfigError("還沒登入 LINE 發文帳號：本機跑 `twquant line-group login` 掃 QR")
    start = now_fn()
    lock_deadline = start + dt.timedelta(seconds=cfg.lock_wait_seconds)
    backoff_deadline = start + dt.timedelta(seconds=cfg.max_backoff_wait_seconds)
    sent = as_image = as_text = 0
    failed: list[str] = []

    def summary(left: int, note: str = "", extra: tuple[str, ...] = ()) -> SendSummary:
        return SendSummary(sent, as_image, as_text, (*failed, *extra), left, note)

    while True:
        now = now_fn()
        if not due_jobs(conn, schema, now):
            return summary(0)
        if not acquire_lease(conn, schema, now, cfg.lease_seconds):
            if now >= lock_deadline:
                left = _left(conn, schema, now)
                return summary(left, "帳號被另一班占著，留給下一班")
            sleep(cfg.poll_seconds)
            continue
        try:
            paused = account_state(conn, schema).get("paused_until")
            if paused is not None and paused > now:
                if paused > backoff_deadline:
                    left = _left(conn, schema, now)
                    note = f"LINE 叫停到 {paused:%H:%M}，留給下一班"
                    return summary(left, note)
                release_lease(conn, schema)
                sleep((paused - now).total_seconds())
                continue
            jobs = due_jobs(conn, schema, now)
            cred = load_credential(conn, schema) or cred
            request = {
                "credential": _cred_json(cred),
                "liffId": cfg.liff_id,
                "cards": card_images(conn, schema, [j.card_token for j in jobs])
                if cfg.fallback_image
                else {},
                "jobs": [j.request(cfg) for j in jobs],
            }
            out = runner("send", request, cred)
            done = now_fn()
            if out.credential is not None and out.credential != cred:
                save_account(conn, schema, out.credential, now=done, profile=out.profile)
                cred = out.credential
            by_id = {int(r["id"]): r for r in out.results}
            for job in jobs:
                upd = next_state(
                    job,
                    by_id.get(job.id),
                    sending=job.id in out.sending,
                    now=done,
                    max_attempts=cfg.max_attempts,
                )
                apply_update(conn, schema, job.id, upd)
                if upd.get("status") == "sent":
                    sent += 1
                    as_image += upd.get("via") == "image"
                    as_text += upd.get("via") == "text"
                if upd.get("status") in ("failed", "unknown"):
                    failed.append(f"#{job.id} {upd.get('last_error')}")
            until = backoff_until(out.results, done)
            set_account_status(conn, schema, paused_until=until, last_error=out.fatal)
            if out.fatal is not None:
                left = _left(conn, schema, done)
                return summary(left, "轉接器失敗", (out.fatal,))
            if until is None:
                return summary(_left(conn, schema, done))
        finally:
            release_lease(conn, schema)


def sync_groups(
    conn: Any, cfg: LineGroupConfig, runner: AdapterRunner, *, now_fn: Callable[[], dt.datetime]
) -> AdapterOutput | None:
    """問帳號現在在哪些群組（順便接受邀請）存進 web.line_groups，網站上才勾得到。

    還沒登入、或帳號正被另一班占著 → None（不算錯：同步只是讓清單新一點）。
    """
    schema = cfg.web_schema
    ensure_tables(conn, schema)
    cred = load_credential(conn, schema)
    if cred is None or not acquire_lease(conn, schema, now_fn(), cfg.lease_seconds):
        return None
    try:
        out = runner("groups", credential_request(cred), cred)
        done = now_fn()
        if out.credential is not None and out.credential != cred:
            save_account(conn, schema, out.credential, now=done, profile=out.profile)
        if out.fatal is None:
            save_groups(conn, schema, out.groups, done)
        set_account_status(conn, schema, paused_until=None, last_error=out.fatal)
        return out
    finally:
        release_lease(conn, schema)


# ── 從網站登入（T-423） ───────────────────────────────────────────────────
# 網站「LINE 群組」分頁按「登入」→ web.line_login 標 requested、觸發 GitHub Actions line-login.yml →
# 這裡跑轉接器 login，QR 與 PIN 一出來就寫進 web.line_login，網站每幾秒讀一次顯示給使用者掃。

LOGIN_ACTIVE = ("requested", "starting", "scan", "pin")
# web.line_login 的欄位（網站 frontend/functions/_lib/line-admin.ts 也會建這張表，兩邊要一樣，測試守著）
LOGIN_COLUMNS = (
    "id",
    "status",
    "requested_by",
    "requested_at",
    "updated_at",
    "run_url",
    "qr_url",
    "qr_png",
    "pin",
    "display_name",
    "error",
)
LoginRunner = Callable[[EventSink], AdapterOutput]


def qr_png_base64(url: str) -> str:
    """把登入網址畫成 QR 碼（PNG、base64），網站直接當 data URL 顯示；不送去任何第三方服務。"""
    import base64
    import io

    import qrcode

    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def login_progress(ev: Mapping[str, Any]) -> dict[str, Any] | None:
    """轉接器的登入事件 → web.line_login 要改的欄位（qr＝等掃描、pin＝等手機輸入；其他事件不管）。"""
    if ev.get("type") == "qr":
        url = str(ev["url"])
        return {"status": "scan", "qr_url": url, "qr_png": qr_png_base64(url), "pin": None}
    if ev.get("type") == "pin":
        return {"status": "pin", "pin": str(ev["pin"]), "qr_url": None, "qr_png": None}
    return None


def set_login(conn: Any, schema: str, now: dt.datetime, fields: Mapping[str, Any]) -> None:
    """更新登入進度那一列（沒有就建；網站每幾秒讀一次）。"""
    if "status" not in fields:
        raise ValueError("set_login 要給 status")
    bad = set(fields) - set(LOGIN_COLUMNS)
    if bad:
        raise ValueError(f"web.line_login 沒有這些欄位：{sorted(bad)}")
    cols = ["id", "updated_at", *fields]
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in ["updated_at", *fields])
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".line_login ({", ".join(cols)}) '
            f"VALUES ({', '.join(['%s'] * len(cols))}) ON CONFLICT (id) DO UPDATE SET {sets}",
            (ACCOUNT_ID, now, *fields.values()),
        )
    conn.commit()


def web_login(
    conn: Any,
    cfg: LineGroupConfig,
    run: LoginRunner,
    *,
    now_fn: Callable[[], dt.datetime],
    run_url: str | None,
) -> AdapterOutput:
    """掃 QR 登入，進度寫進 web.line_login（網站顯示 QR 與 PIN），成功就存憑證。"""
    schema = cfg.web_schema
    ensure_tables(conn, schema)
    clear = {"qr_url": None, "qr_png": None, "pin": None}
    set_login(
        conn, schema, now_fn(), {"status": "starting", "run_url": run_url, "error": None, **clear}
    )

    def on_event(ev: dict[str, Any]) -> None:
        upd = login_progress(ev)
        if upd is not None:
            set_login(conn, schema, now_fn(), upd)

    out = run(on_event)
    done = now_fn()
    if out.fatal is not None or out.credential is None:
        error = out.fatal or "轉接器沒有回憑證"
        set_login(conn, schema, done, {"status": "failed", "error": error[:1000], **clear})
        return out
    save_account(conn, schema, out.credential, now=done, profile=out.profile)
    set_account_status(conn, schema, paused_until=None, last_error=None)
    name = out.profile.get("displayName") or out.profile.get("mid")
    set_login(conn, schema, done, {"status": "done", "display_name": name, "error": None, **clear})
    return out


def _cred_json(cred: Credential) -> dict[str, Any]:
    out: dict[str, Any] = {
        "accessToken": cred.access_token,
        "refreshToken": cred.refresh_token,
        "expire": cred.expire,
    }
    if cred.storage:
        # 壞掉的 storage 就當沒有（頂多少了端對端加密金鑰、改走重登）；不讓它擋下整次發送
        with contextlib.suppress(json.JSONDecodeError):
            out["storage"] = json.loads(cred.storage)
    return out


def credential_request(cred: Credential | None) -> dict[str, Any]:
    """給轉接器的請求（只帶憑證）。"""
    if cred is None:
        raise ConfigError("還沒登入 LINE 發文帳號：本機跑 `twquant line-group login` 掃 QR")
    return {"credential": _cred_json(cred)}
