"""LINE 群組記事本貼文（T-423／D-204）：照 lt-foods/new_erp 的做法，用一支備用 LINE 帳號在一般群組的記事本發文，
再把貼文卡片分享到群組聊天。四種貼文：每日名單、族群資金流入、盤前晨報、盤後快訊。

分工跟 new_erp 一樣是「排隊 → worker 撿」：排程寫好名單／晨報後 `line-group enqueue` 把整篇文字排進
web.line_posts（同一天同一種同一群組只排一次），`line-group send` 撿到期的送出。跟 LINE 講話的是
Node 轉接器 line-poster/（linejs 只有 JS 版），這裡用子程序呼叫：stdin 一份 JSON、stdout 一行一個事件。

⚠ linejs 是 LINE **非官方**客戶端協定，違反 LINE 使用條款、帳號可能被停權——只能用備用帳號。
登入憑證存在 web.line_account：access token 7 天、refresh token 一年且每次換新都會輪替，所以要能寫回，
不能放 GitHub secret。上半部是純函數（組文字、解析事件、決定每筆的下一個狀態），下半部連資料庫。
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from twquant.config import LineGroupConfig
from twquant.errors import ConfigError, DataError, ExecutionError
from twquant.monitoring.push_notify import HotGroup

KINDS = ("picks", "groups", "morning", "evening")
BRIEF_KINDS = {"morning": ("morning_brief", "盤前晨報"), "evening": ("evening_brief", "盤後快訊")}
ACCOUNT_ID = "default"  # 只有一支發文帳號；表留 id 欄位，之後要分帳號不用改表
_WEEKDAYS = "一二三四五六日"
# LINE 叫停（被暫時限制／701）時寫在貼文上的說明：不是失敗，下一輪自動再送
BACKOFF_HINT = "LINE 要求暫緩（暫時限制或請稍後再發），帳號先停一陣子，之後自動重送"


# ── 組文字 ─────────────────────────────────────────────────────────────────


def day_label(day: dt.date) -> str:
    """10/21（二）。"""
    return f"{day.month}/{day.day}（{_WEEKDAYS[day.weekday()]}）"


def finish(body: str, *, site_url: str, footer: str, max_chars: int) -> str:
    """附上網站連結與免責聲明；超過字數上限 raise（不偷偷截斷）。"""
    text = f"{body.rstrip()}\n\n{site_url}\n{footer}".strip()
    if len(text) > max_chars:
        raise DataError(
            "line_posts", None, f"LINE 貼文 {len(text)} 字超過上限 {max_chars}：{text[:40]}…"
        )
    return text


@dataclass(frozen=True)
class PickRow:
    """名單上的一檔：哪條線、第幾名、代號、股名、族群、名單一句話（沒有就 None）。"""

    line: str
    rank: int
    stock_id: str
    name: str | None
    group: str | None
    blurb: str | None


def compose_picks(
    day: dt.date,
    lines: Sequence[tuple[str, str]],
    picks: Sequence[PickRow],
    previous: Mapping[str, set[str]],
    *,
    max_per_line: int,
) -> str:
    """每日名單：照設定的線別順序，每條線列名次、代號、股名、族群，新進的標出來，下面附一句話。

    previous＝各線上一個有名單的日子的代號（這條線之前沒有名單就不在 dict 裡 → 不標新進）。
    """
    by_line: dict[str, list[PickRow]] = {}
    for p in sorted(picks, key=lambda r: (r.line, r.rank)):
        by_line.setdefault(p.line, []).append(p)
    parts = [f"{day_label(day)}今日名單"]
    for code, name in lines:
        rows = by_line.get(code, [])
        if not rows:
            parts.append(f"【{name}】今天沒有符合條件的股票")
            continue
        prev = previous.get(code)
        new = [r for r in rows if prev is not None and r.stock_id not in prev]
        head = f"【{name}】{len(rows)} 檔" + (f"，新進 {len(new)}" if new else "")
        out = [head]
        for r in rows[:max_per_line]:
            label = f"{r.stock_id} {r.name}" if r.name else r.stock_id
            group = f"（{r.group}）" if r.group else ""
            mark = " 新進" if r in new else ""
            out.append(f"{r.rank}. {label}{group}{mark}")
            if r.blurb:
                out.append(f"　{r.blurb}")
        if len(rows) > max_per_line:
            out.append(f"另 {len(rows) - max_per_line} 檔請看網站")
        parts.append("\n".join(out))
    return "\n\n".join(parts)


def compose_groups(day: dt.date, hot: Sequence[HotGroup], *, max_listed: int) -> str:
    """族群資金流入：一個族群一行（資金倍數、漲跌家數、名單上的股票），條件同網頁推播（T-354）。"""
    if not hot:
        raise ValueError("沒有資金流入的族群，不該組貼文")
    shown = hot[:max_listed]
    rows = [
        f"{h.group}：資金 {h.ratio:.1f} 倍、{h.n} 檔 {h.up} 漲｜名單 {len(h.picks)} 檔：{'、'.join(h.picks)}"
        for h in shown
    ]
    if len(hot) > max_listed:
        rows.append(f"另 {len(hot) - max_listed} 個族群請看網站「盤勢」")
    return f"{day_label(day)}名單上的族群資金流入\n\n" + "\n".join(rows)


@dataclass(frozen=True)
class BriefRow:
    """晨報／盤後快訊的一列：模型版（標題、要點、風險）或規則版（rule_text，模型缺席時用）。"""

    headline: str | None
    bullets: tuple[str, ...]
    risk_note: str | None
    rule_text: str | None


def compose_brief(day: dt.date, kind: str, brief: BriefRow) -> str:
    """晨報／盤後快訊：有模型版用標題＋要點＋風險，沒有就用規則那一句（跟網站首頁同一份）。"""
    title = f"{day_label(day)}{BRIEF_KINDS[kind][1]}"
    if brief.headline:
        body = [brief.headline]
        if brief.bullets:
            body.append("\n".join(f"• {b}" for b in brief.bullets))
        if brief.risk_note:
            body.append(f"風險：{brief.risk_note}")
        return f"{title}\n\n" + "\n\n".join(body)
    if brief.rule_text:
        return f"{title}\n\n{brief.rule_text}"
    raise ValueError(f"{day} 的{BRIEF_KINDS[kind][1]}沒有內容")


# ── 轉接器事件 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Credential:
    """LINE 登入憑證（三個都要存，見模組說明）。"""

    access_token: str
    refresh_token: str | None
    expire: int | None


@dataclass(frozen=True)
class AdapterOutput:
    """轉接器跑一次的結果。credential＝程序中吐出的最後一組（沒吐 → None）。"""

    returncode: int
    credential: Credential | None = None
    profile: dict[str, str] = field(default_factory=dict)
    groups: tuple[dict[str, str], ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    fatal: str | None = None


def parse_events(lines: Iterable[str], returncode: int, base: Credential | None) -> AdapterOutput:
    """把轉接器 stdout 的 NDJSON 事件收起來。credential 事件是「有換的欄位」，依序疊在 base 上。

    不是 JSON 的行（套件自己印到 stdout 的訊息）轉到 stderr，不吞掉也不當事件。
    """
    cred = base
    profile: dict[str, str] = {}
    groups: list[dict[str, str]] = []
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
            )
        elif kind == "profile":
            profile = {k: str(v) for k, v in ev.items() if k != "type" and v is not None}
        elif kind == "group":
            groups.append({"homeId": str(ev["homeId"]), "name": str(ev.get("name") or "")})
        elif kind == "result":
            results.append(ev)
        elif kind == "fatal":
            fatal = str(ev.get("error") or "轉接器失敗（沒有原因）")
        else:
            raise ExecutionError(f"轉接器回了不認得的事件：{line[:200]}")
    if returncode != 0 and fatal is None:
        fatal = f"轉接器結束碼 {returncode}"
    return AdapterOutput(returncode, cred, profile, tuple(groups), tuple(results), fatal)


AdapterRunner = Callable[[str, dict[str, Any], Credential | None], AdapterOutput]


def node_runner(cfg: LineGroupConfig, repo_root: Path) -> AdapterRunner:
    """用子程序跑 Node 轉接器。stderr 直接接到終端機（QR、PIN、錯誤細節要讓人看到）。"""

    def run(command: str, request: dict[str, Any], base: Credential | None) -> AdapterOutput:
        timeout = cfg.login_timeout_seconds if command == "login" else cfg.adapter_timeout_seconds
        proc = subprocess.Popen(
            [cfg.node_bin, str(repo_root / cfg.adapter), command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            cwd=str(repo_root),
        )
        try:
            out, _ = proc.communicate(json.dumps(request, ensure_ascii=False), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            res = parse_events(out.splitlines(), proc.returncode or -9, base)
            return replace(res, fatal=f"轉接器跑超過 {timeout:.0f} 秒被中止")
        return parse_events(out.splitlines(), proc.returncode, base)

    return run


# ── 每筆貼文的下一個狀態 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Job:
    """web.line_posts 裡到期要處理的一列。"""

    id: int
    home_id: str
    text: str
    enqueued_at: dt.datetime
    line_post_id: str | None
    status: str  # queued（還沒發）／posted（發了、分享還沒成功）
    share_state: str
    attempts: int

    def request(self) -> dict[str, Any]:
        """給轉接器的那一筆。"""
        return {
            "id": self.id,
            "homeId": self.home_id,
            "text": self.text,
            "sinceMs": int(self.enqueued_at.timestamp() * 1000),
            "linePostId": self.line_post_id,
            "share": self.share_state == "pending",
        }


def next_state(
    job: Job, result: Mapping[str, Any], *, now: dt.datetime, max_attempts: int
) -> dict[str, Any]:
    """依轉接器的結果決定這一列要改哪些欄位（純函數）。

    - 發成功：status=posted；要分享的看分享結果（LINE 叫停 → 留 pending 下輪再分享；其他錯誤 → failed）
    - 發失敗、LINE 叫停：維持 queued，不算一次失敗
    - 發失敗、其他錯誤：attempts+1，到上限才標 failed
    """
    error = result.get("error")
    backoff = result.get("backoffMs") is not None
    if result.get("posted"):
        upd: dict[str, Any] = {
            "status": "posted",
            "line_post_id": result.get("linePostId") or job.line_post_id,
            "last_error": None,
        }
        if job.status != "posted":
            upd["posted_at"] = now
        if job.share_state == "pending":
            if result.get("shared"):
                upd.update(share_state="shared", shared_at=now)
            elif not upd["line_post_id"]:
                upd.update(
                    share_state="failed",
                    last_error="發文成功但沒拿到 LINE 貼文 id，沒分享到聊天",
                )
            elif error and backoff:
                upd["last_error"] = f"分享到聊天：{BACKOFF_HINT}"
            elif error:
                upd.update(share_state="failed", last_error=f"分享到聊天失敗：{error}"[:1000])
            else:
                raise ExecutionError(
                    f"line_posts #{job.id}：要分享卻沒有分享結果也沒有錯誤：{result}"
                )
        return upd
    if not error:
        raise ExecutionError(f"line_posts #{job.id}：轉接器說沒發成功卻沒有錯誤：{result}")
    if backoff:
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


# linejs 登不進去的兩種訊息：token 被拒、access token 過期又沒有能用的 refresh token
_LOGIN_ERRORS = ("AUTHENTICATION_FAILED", "MUST_REFRESH_V3_TOKEN", "cached token rejected")


def needs_login(error: str) -> bool:
    """這個錯誤是不是要重新掃 QR 登入。"""
    return any(k in error for k in _LOGIN_ERRORS)


def group_mids(cfg: LineGroupConfig, env: Mapping[str, str]) -> list[str]:
    """要發的群組（環境變數，逗號分隔）。只收一般群組 c…（社群 s…／m… 的路徑不同，D-204 不做）。"""
    raw = env.get(cfg.group_mids_env, "")
    mids = [m.strip() for m in raw.split(",") if m.strip()]
    if not mids:
        raise ConfigError(
            f"缺要發的 LINE 群組：環境變數 {cfg.group_mids_env}（`twquant line-group groups` 列得出 c… 的 id）"
        )
    bad = [m for m in mids if not m.startswith("c")]
    if bad:
        raise ConfigError(f"{cfg.group_mids_env} 只收一般群組（c… 開頭），拿到 {bad}")
    return mids


# ── 資料庫 ─────────────────────────────────────────────────────────────────


def ensure_tables(conn: Any, schema: str) -> None:
    """建排隊表與帳號表（第一次用時自動建；沒有備份，丟了只是重新掃 QR）。"""
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_account ('
            "id text PRIMARY KEY, mid text, display_name text, "
            "access_token text NOT NULL, refresh_token text, token_expire bigint, "
            "updated_at timestamptz NOT NULL, paused_until timestamptz, busy_until timestamptz, "
            "last_error text)"
        )
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS "{schema}".line_posts ('
            "id bigserial PRIMARY KEY, date date NOT NULL, kind text NOT NULL, home_id text NOT NULL, "
            "text text NOT NULL, status text NOT NULL, share_state text NOT NULL, line_post_id text, "
            "enqueued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, "
            "posted_at timestamptz, shared_at timestamptz, attempts integer NOT NULL DEFAULT 0, "
            "last_error text, UNIQUE (date, kind, home_id))"
        )
    conn.commit()


def _exists(cur: Any, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f'"{schema}"."{table}"',))
    row = cur.fetchone()
    return bool(row and row[0])


def load_picks(
    conn: Any, schema: str, day: dt.date, lines: Sequence[tuple[str, str]], *, with_blurbs: bool
) -> tuple[list[PickRow], dict[str, set[str]]] | None:
    """當天名單（含股名、族群、一句話）與各線上一個有名單的日子的代號；當天沒有名單 → None。

    族群照網站：族群表上有就用表上的，沒有（重抓之間新上市）用官方產業別。
    """
    with conn.cursor() as cur:
        blurbs = with_blurbs and _exists(cur, schema, "pick_blurbs")
        groups = _exists(cur, schema, "stock_group")
        cur.execute(
            "SELECT p.line, p.rank, p.stock_id, m.name, "
            + ("coalesce(g.grp, m.industry)" if groups else "m.industry")
            + (", b.text " if blurbs else ", NULL ")
            + f'FROM "{schema}"."daily_picks" p '
            f'LEFT JOIN "{schema}"."stock_meta" m ON m.stock_id = p.stock_id '
            + (
                f'LEFT JOIN "{schema}"."stock_group" g ON g.stock_id = p.stock_id '
                if groups
                else ""
            )
            + (
                f'LEFT JOIN "{schema}"."pick_blurbs" b '
                "ON b.date = p.date AND b.line = p.line AND b.stock_id = p.stock_id "
                if blurbs
                else ""
            )
            + "WHERE p.date = %s ORDER BY p.line, p.rank",
            (day,),
        )
        rows = [
            PickRow(str(ln), int(rk), str(sid), nm or None, grp or None, bl or None)
            for ln, rk, sid, nm, grp, bl in cur.fetchall()
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


def enqueue(
    conn: Any,
    schema: str,
    day: dt.date,
    kind: str,
    home_ids: Sequence[str],
    text: str,
    *,
    now: dt.datetime,
    stale_hours: float,
    share: bool,
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
                "(date, kind, home_id, text, status, share_state, enqueued_at, expires_at) "
                "VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s) "
                "ON CONFLICT (date, kind, home_id) DO NOTHING",
                (day, kind, home, text, "pending" if share else "skipped", now, expires),
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
            f'SELECT access_token, refresh_token, token_expire FROM "{schema}".line_account WHERE id = %s',
            (ACCOUNT_ID,),
        )
        row = cur.fetchone()
    return Credential(str(row[0]), row[1], row[2]) if row else None


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
            "(id, mid, display_name, access_token, refresh_token, token_expire, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token, "
            "token_expire = EXCLUDED.token_expire, updated_at = EXCLUDED.updated_at, "
            "mid = coalesce(EXCLUDED.mid, line_account.mid), "
            "display_name = coalesce(EXCLUDED.display_name, line_account.display_name)",
            (
                ACCOUNT_ID,
                (profile or {}).get("mid"),
                (profile or {}).get("displayName"),
                cred.access_token,
                cred.refresh_token,
                cred.expire,
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
    """先把過期的作廢，再撿還沒發的與發了但分享還沒成功的（舊的先）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE \"{schema}\".line_posts SET status = 'expired', "
            "share_state = CASE WHEN share_state = 'pending' THEN 'expired' ELSE share_state END "
            "WHERE status = 'queued' AND expires_at <= %s",
            (now,),
        )
        cur.execute(
            f"UPDATE \"{schema}\".line_posts SET share_state = 'expired' "
            "WHERE status = 'posted' AND share_state = 'pending' AND expires_at <= %s",
            (now,),
        )
        cur.execute(
            "SELECT id, home_id, text, enqueued_at, line_post_id, status, share_state, attempts "
            f'FROM "{schema}".line_posts '
            "WHERE expires_at > %s AND (status = 'queued' OR (status = 'posted' AND share_state = 'pending')) "
            "ORDER BY enqueued_at, id",
            (now,),
        )
        jobs = [Job(*r) for r in cur.fetchall()]
    conn.commit()
    return jobs


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

    posted: int = 0
    shared: int = 0
    failed: tuple[str, ...] = ()
    left: int = 0
    note: str = ""


def send_due(
    conn: Any,
    cfg: LineGroupConfig,
    runner: AdapterRunner,
    *,
    now_fn: Callable[[], dt.datetime],
    sleep: Callable[[float], None],
) -> SendSummary:
    """撿到期的貼文送出。帳號被另一班占著就等（最多 lock_wait_seconds）；LINE 叫停就等到解禁再送
    （最多 max_backoff_wait_seconds，超過就留給下一班）。憑證有換就寫回。"""
    schema = cfg.web_schema
    ensure_tables(conn, schema)
    cred = load_credential(conn, schema)
    if cred is None:
        raise ConfigError("還沒登入 LINE 發文帳號：本機跑 `twquant line-group login` 掃 QR")
    start = now_fn()
    lock_deadline = start + dt.timedelta(seconds=cfg.lock_wait_seconds)
    backoff_deadline = start + dt.timedelta(seconds=cfg.max_backoff_wait_seconds)
    posted = shared = 0
    failed: list[str] = []
    while True:
        now = now_fn()
        if not due_jobs(conn, schema, now):
            return SendSummary(posted, shared, tuple(failed), 0)
        if not acquire_lease(conn, schema, now, cfg.lease_seconds):
            if now >= lock_deadline:
                left = len(due_jobs(conn, schema, now))
                return SendSummary(
                    posted, shared, tuple(failed), left, "帳號被另一班占著，留給下一班"
                )
            sleep(cfg.poll_seconds)
            continue
        try:
            paused = account_state(conn, schema).get("paused_until")
            if paused is not None and paused > now:
                if paused > backoff_deadline:
                    left = len(due_jobs(conn, schema, now))
                    return SendSummary(
                        posted,
                        shared,
                        tuple(failed),
                        left,
                        f"LINE 叫停到 {paused:%H:%M}，留給下一班",
                    )
                release_lease(conn, schema)
                sleep((paused - now).total_seconds())
                continue
            jobs = due_jobs(conn, schema, now)
            cred = load_credential(conn, schema) or cred
            out = runner(
                "send", {"credential": _cred_json(cred), "jobs": [j.request() for j in jobs]}, cred
            )
            done = now_fn()
            if out.credential is not None and out.credential != cred:
                save_account(conn, schema, out.credential, now=done, profile=out.profile)
                cred = out.credential
            by_id = {int(r["id"]): r for r in out.results}
            for job in jobs:
                res = by_id.get(job.id)
                if res is None:
                    continue  # 轉接器還沒輪到它（被叫停或中途結束）：原樣留著
                upd = next_state(job, res, now=done, max_attempts=cfg.max_attempts)
                apply_update(conn, schema, job.id, upd)
                if res.get("posted") and job.status != "posted":
                    posted += 1
                if upd.get("share_state") == "shared":
                    shared += 1
                if upd.get("status") == "failed" or upd.get("share_state") == "failed":
                    failed.append(f"#{job.id} {upd.get('last_error')}")
            until = backoff_until(out.results, done)
            set_account_status(conn, schema, paused_until=until, last_error=out.fatal)
            if out.fatal is not None:
                left = len(due_jobs(conn, schema, done))
                return SendSummary(posted, shared, (*failed, out.fatal), left, "轉接器失敗")
            if until is None:
                left = len(due_jobs(conn, schema, done))
                return SendSummary(posted, shared, tuple(failed), left)
        finally:
            release_lease(conn, schema)


def _cred_json(cred: Credential) -> dict[str, Any]:
    return {
        "accessToken": cred.access_token,
        "refreshToken": cred.refresh_token,
        "expire": cred.expire,
    }


def credential_request(cred: Credential | None) -> dict[str, Any]:
    """給轉接器的請求（只帶憑證）。"""
    if cred is None:
        raise ConfigError("還沒登入 LINE 發文帳號：本機跑 `twquant line-group login` 掃 QR")
    return {"credential": _cred_json(cred)}
