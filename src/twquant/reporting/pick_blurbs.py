"""名單每檔一句話（T-393／D-195）。

使用者 2026-10-18：「每日名單裡面的個股也用 LLM 給一句話」。做法同晨報（T-377）：規則先把資料寫成事實句，
模型只負責把一檔的事實濃縮成一句；輸出逐句檢查——數字要出自事實、狀態詞（處置、法說會、連買…）要出自事實、
不准有買賣建議、不能太長。沒過或模型缺席 → 改用規則句（source=rule，網站不標 AI）。只顯示，不改名單、不進模型。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from twquant.config import PickBlurbsConfig
from twquant.conventions import TZ
from twquant.data.serving.schema import PICK_BLURBS_SPEC, ensure_tables
from twquant.data.serving.sync import upsert
from twquant.errors import DataError
from twquant.insight.client import InsightClient
from twquant.insight.prompts import PICK_BLURB_PROMPT_VERSION, PICK_BLURB_SYSTEM
from twquant.insight.schemas import PickBlurbs
from twquant.insight.validation import unverified_numbers
from twquant.reporting.morning_brief import strip_self_reference

TASK = "pick_blurb"
DIV_TEXT = {"cash": "除息", "stock": "除權", "both": "除權息"}
# 一定要有的表；其他表（處置、公告…）不在就少那一類事實，記進 errors
REQUIRED_TABLES = ("daily_picks", "stock_meta", "prices_daily")


@dataclass
class PickFacts:
    """一檔上榜股可以引用的全部資料；None／空＝沒有。"""

    day: dt.date
    line: str
    line_name: str
    stock_id: str
    name: str | None
    rank: int
    close: float | None = None
    chg: float | None = None  # 今天漲跌（小數）
    move: float | None = None  # 近 move_days 日漲跌（小數）
    move_days: int = 0
    reasons: list[dict[str, Any]] = field(default_factory=list)  # [{t, s}]
    down_risk: bool = False
    disposition: list[str] = field(default_factory=list)
    attention: bool = False
    events: list[str] = field(default_factory=list)  # 「10/20 除息」「10/22 法說會」
    insider: int = 0
    insider_days: int = 0
    announcements: list[str] = field(default_factory=list)
    common: frozenset[str] = (
        frozenset()
    )  # 這條線多數檔都有的加分理由（因子代號）：不給模型，免得每句都一樣


def md(d: dt.date) -> str:
    """2026-10-20 → 10/20。"""
    return f"{d.month}/{d.day}"


def price_text(v: float) -> str:
    """價格依台股檔位習慣：≥ 1000 取 0 位、≥ 100 取 1 位、其餘 2 位（同網站 format.ts price）。"""
    return f"{v:.0f}" if v >= 1000 else f"{v:.1f}" if v >= 100 else f"{v:.2f}"


def move_text(x: float) -> str:
    """0.0123 → 「漲 1.2%」；−0.04 → 「跌 4.0%」；0 → 「平盤」。"""
    if x == 0:
        return "平盤"
    return f"{'漲' if x > 0 else '跌'} {abs(x) * 100:.1f}%"


GROUPS = ("看點", "提醒", "行情")
_GAP_HIGH = re.compile(r"離\s*52\s*週高點還差\s*(\d+(?:\.\d+)?)%")
# 規則句不帶「，全市場前 3%」這種排名尾巴（使用者看不懂排的是什麼）
_RANK = re.compile(r"[，,]?\s*在?全市場前\s*\d+(?:\.\d+)?%")


def grouped_facts(p: PickFacts, drop_alert: float, near_high: float = 0.0) -> dict[str, list[str]]:
    """一檔的事實句分三組：看點（加分理由）、提醒（扣分理由、風險、事件、公告、近幾天跌深）、行情。

    v2 起名次、連續上榜不放（網站那一列已經有，放進來模型就拿來湊句子）；v3 起理由的「前提」不放
    （「排序模型分數在全市場前 3%」是每檔都有的上榜條件，模型會把它寫成提醒）。
    """
    g: dict[str, list[str]] = {k: [] for k in GROUPS}
    plus = [r for r in p.reasons if int(r.get("s", 0)) > 0]
    # v6：整條線多數檔都有的理由（潛伏飆股的波動因子）不放；拿掉後一個都不剩就全留（大盤精選四個規則因子人人都有）
    own = [r for r in plus if str(r.get("f", "")) not in p.common]
    g["看點"] = [str(r["t"]) for r in (own or plus)]
    # 「離 52 週高點還差 3%」本來就是貼近；給一條明講的事實，「貼近」「接近」才有根據
    for t in list(g["看點"]):
        m = _GAP_HIGH.search(t)
        if m and float(m.group(1)) / 100 <= near_high:
            g["看點"].append("股價在 52 週高點附近")
            break
    # 提醒依要緊程度排（規則句取第一個）：易跌、處置、注意、事件、內部人、公告、近幾天跌深，扣分理由最後
    if p.down_risk:
        g["提醒"].append("模型標了易跌")
    g["提醒"].extend(p.disposition)
    if p.attention:
        g["提醒"].append("今天是注意股")
    g["提醒"].extend(p.events)
    if p.insider > 0:
        g["提醒"].append(f"近 {p.insider_days} 天有 {p.insider} 筆內部人申報轉讓持股")
    g["提醒"].extend(f"近期公告：{a}" for a in p.announcements)
    if p.move is not None and p.move <= -drop_alert:
        g["提醒"].append(f"近 {p.move_days} 日{move_text(p.move)}")
    g["提醒"].extend(str(r["t"]) for r in p.reasons if int(r.get("s", 0)) < 0)
    if p.close is not None and p.chg is not None:
        g["行情"].append(f"今天收盤 {price_text(p.close)} 元，{move_text(p.chg)}")
    if p.move is not None and p.move > -drop_alert:
        g["行情"].append(f"近 {p.move_days} 日{move_text(p.move)}")
    return g


def facts(p: PickFacts, drop_alert: float, near_high: float = 0.0) -> list[str]:
    """一檔的全部事實句（三組攤平）；輸出的數字與狀態詞只拿這些對。"""
    return [f for k in GROUPS for f in grouped_facts(p, drop_alert, near_high)[k]]


# 數字後面緊接的單位：同一個數字換了單位（前 3% 寫成前 3 名、5 日寫成 5%）也算對不上
_NUM_UNIT = re.compile(
    r"(?<![0-9A-Za-z_.])(\d[\d,]*(?:\.\d+)?)\s*([%倍日天元億萬筆檔名週年月季個點])?"
)


_SID = re.compile(r"\d{4,6}[A-Z]?")
# 「七成」「一倍二」「近六旬」「百分之一」：模型自己換算的中文數字，數字檢查看不到 → 不收
_CN_NUMBER = re.compile(
    r"[一二兩三四五六七八九十百半]+[成倍旬]|百分之[一二兩三四五六七八九十]|[一二兩三四五六七八九十]+個百分"
)
_CLAUSE = re.compile(r"[，,、；;]|但")
_BUT = re.compile(r"[\s，,、；;]*但")
_DATE = re.compile(r"(?<!\d)\d{1,2}/\d{1,2}(?!\d)")


# 期間（近 60 日、52 週、20 日平均）不是數字主張，不算進「最多幾個數字」
_PERIOD_UNITS = frozenset("日天週月年季")


def value_count(text: str) -> int:
    """句子裡有幾個「數字主張」：日期 10/22 算一個；後面接期間單位的（近 60 日、52 週）不算。"""
    rest = _DATE.sub(" ", text)
    n = len(_DATE.findall(text))
    return n + sum(1 for m in _NUM_UNIT.finditer(rest) if (m.group(2) or "") not in _PERIOD_UNITS)


def unit_mismatch(text: str, known: Iterable[str]) -> list[str]:
    """句子裡「數字＋單位」在事實裡找不到同樣組合的；沒帶單位的數字交給 unverified_numbers。"""
    have = {(m.group(1).replace(",", ""), m.group(2)) for f in known for m in _NUM_UNIT.finditer(f)}
    out: list[str] = []
    for m in _NUM_UNIT.finditer(text):
        n, u = m.group(1).replace(",", ""), m.group(2)
        if u and (n, u) not in have and f"{n}{u}" not in out:
            out.append(f"{n}{u}")
    return out


def rule_text(p: PickFacts, drop_alert: float) -> str:
    """規則句（模型缺席或沒過檢查時用）：同模型的格式「看點，但提醒」，各取第一條，排名尾巴拿掉。"""
    g = grouped_facts(p, drop_alert)
    head = _RANK.sub("", g["看點"][0]) if g["看點"] else None
    risk = g["提醒"][0] if g["提醒"] else None
    if risk is not None and risk.startswith("近期公告："):
        risk = "近期有重大訊息"  # 公告主旨原文很長、很官腔；完整的在個股頁「事件」
    if head and risk:
        return f"{head}，但{risk}"
    return head or risk or f"{p.line_name}第 {p.rank} 名"


def check_text(text: str, p: PickFacts, cfg: PickBlurbsConfig) -> tuple[str, list[str]]:
    """清掉自報身分與句號後回 (句子, 問題清單)；問題清單非空＝不能用。"""
    t = strip_self_reference(text).strip().rstrip("。")
    t = _BUT.sub("，但", t)  # 「…5.0% 但近 5 日…」→「…5.0%，但近 5 日…」
    g = grouped_facts(p, cfg.drop_alert, cfg.near_high)
    known = facts(p, cfg.drop_alert, cfg.near_high)
    problems: list[str] = []
    if not t:
        problems.append("空白")
    if len(t) > cfg.max_chars:
        problems.append(f"太長（{len(t)} 字，上限 {cfg.max_chars}）")
    bad = unverified_numbers(t, known)
    if bad:
        problems.append(f"數字不在事實裡 {list(bad)}")
    n_num = value_count(t)
    if n_num > cfg.max_numbers:
        problems.append(f"數字太多（{n_num} 個，上限 {cfg.max_numbers}）")
    units = unit_mismatch(t, known)
    if units:
        problems.append(f"數字的單位跟事實不同 {units}")
    if t.count("但") > 1:
        problems.append("一句裡不只一個「但」")
    if "但" in t and not g["提醒"]:
        problems.append("沒有提醒卻寫了「但」")
    made_up = [w for w in cfg.state_terms if w in t and not any(w in f for f in known)]
    if made_up:
        problems.append(f"事實沒有的說法 {made_up}")
    unsupported = [
        w
        for w, need in cfg.evidence_terms.items()
        if w in t and not any(n in f for n in need for f in known)
    ]
    if unsupported:
        problems.append(f"說法在事實裡找不到根據 {unsupported}")
    cn = _CN_NUMBER.findall(t)
    if cn:
        problems.append(f"把數字換成中文 {cn}")
    jargon = [w for w in cfg.jargon_terms if w.lower() in t.lower()]
    if jargon:
        problems.append(f"術語沒翻成白話 {jargon}")
    parts = [x.strip() for x in _CLAUSE.split(t) if x.strip()]
    if len(parts) != len(set(parts)):
        problems.append("同一段話重複")
    advice = [w for w in cfg.advice_terms if w in t]
    if advice:
        problems.append(f"含買賣建議 {advice}")
    return t, problems


def user_payload(
    line_name: str,
    day: dt.date,
    items: Iterable[PickFacts],
    drop_alert: float,
    near_high: float = 0.0,
) -> str:
    """給模型的訊息：一條線的每一檔，代號名稱底下三組事實（沒有的組寫「無」）。"""
    blocks = [f"名單：{line_name}（{day.isoformat()} 收盤後）"]
    for p in items:
        g = grouped_facts(p, drop_alert, near_high)
        parts = [f"【{k}】" + ("；".join(g[k]) if g[k] else "無") for k in GROUPS]
        blocks.append(f"[{p.stock_id} {p.name or ''}]\n" + "\n".join(parts))
    return "\n\n".join(blocks)


@dataclass
class Blurb:
    line: str
    stock_id: str
    text: str
    source: str  # model｜rule
    model_id: str | None
    facts: list[str]
    checks: list[str]


def compose_line(
    client: InsightClient,
    items: list[PickFacts],
    now: dt.datetime,
    cfg: PickBlurbsConfig,
) -> list[Blurb]:
    """一條線一次呼叫；每檔逐句檢查，沒過或模型缺席的那檔用規則句。"""
    if not items:
        return []
    model_id = client.config.model_for(TASK).id
    res, absent = client.complete_detailed(
        task=TASK,
        system=PICK_BLURB_SYSTEM,
        user=user_payload(items[0].line_name, items[0].day, items, cfg.drop_alert, cfg.near_high),
        schema=PickBlurbs,
        prompt_version=PICK_BLURB_PROMPT_VERSION,
        now=now,
        max_tokens=cfg.max_tokens,
    )
    parsed = res.parsed if res is not None else None
    said: dict[str, str] = {}
    note: str | None = None
    if not isinstance(parsed, PickBlurbs):
        note = f"模型缺席：{absent or '輸出不符 PickBlurbs 結構'}"
    else:
        for it in parsed.items:
            m = _SID.search(it.sid)
            said.setdefault(m.group() if m else it.sid.strip(), it.text)
    out: list[Blurb] = []
    for p in items:
        known = facts(p, cfg.drop_alert, cfg.near_high)
        if note is not None:
            out.append(
                Blurb(
                    p.line,
                    p.stock_id,
                    rule_text(p, cfg.drop_alert),
                    "rule",
                    model_id,
                    known,
                    [note],
                )
            )
            continue
        raw = said.get(p.stock_id)
        if raw is None:
            out.append(
                Blurb(
                    p.line,
                    p.stock_id,
                    rule_text(p, cfg.drop_alert),
                    "rule",
                    model_id,
                    known,
                    [f"模型漏了這檔（回了：{sorted(said)}）"],
                )
            )
            continue
        text, problems = check_text(raw, p, cfg)
        if problems:
            out.append(
                Blurb(
                    p.line,
                    p.stock_id,
                    rule_text(p, cfg.drop_alert),
                    "rule",
                    model_id,
                    known,
                    [*problems, f"原句：{raw}"],
                )
            )
        else:
            out.append(Blurb(p.line, p.stock_id, text, "model", model_id, known, []))
    return out


def _has(cur: Any, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return cur.fetchone() is not None


def gather(
    conn: Any,
    schema: str,
    day: dt.date,
    cfg: PickBlurbsConfig,
    line_names: dict[str, str],
) -> tuple[list[PickFacts], list[str]]:
    """讀當天名單與每檔的事實；回 (每檔資料, 缺了哪類資料)。名單、股名、行情的表不在就 raise。"""
    errors: list[str] = []
    s = f'"{schema}"'
    with conn.cursor() as cur:
        for t in REQUIRED_TABLES:
            if not _has(cur, schema, t):
                raise DataError(f"{schema}.{t}", day, "名單一句話要讀這張表，表不在，無法產生")
        cur.execute(
            f"SELECT line, rank, stock_id FROM {s}.daily_picks WHERE date = %s AND line = ANY(%s) ORDER BY line, rank",
            (day, cfg.lines),
        )
        picks = [(str(r[0]), int(r[1]), str(r[2])) for r in cur.fetchall()]
        if not picks:
            return [], errors
        sids = sorted({sid for _, _, sid in picks})
        cur.execute(f"SELECT stock_id, name FROM {s}.stock_meta WHERE stock_id = ANY(%s)", (sids,))
        names = {str(r[0]): (str(r[1]) if r[1] else None) for r in cur.fetchall()}

        # 行情：今天、前一天、move_days 天前
        cur.execute(
            f"SELECT stock_id, date, close FROM {s}.prices_daily WHERE stock_id = ANY(%s) AND date <= %s "
            f"AND date >= %s ORDER BY stock_id, date",
            (sids, day, day - dt.timedelta(days=cfg.move_days * 3 + 10)),
        )
        closes: dict[str, list[tuple[dt.date, float]]] = {}
        for sid, d, c in cur.fetchall():
            if c is not None:
                closes.setdefault(str(sid), []).append((d, float(c)))

        def rows(table: str, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
            if not _has(cur, schema, table):
                errors.append(f"缺 {table}，少了這類事實")
                return []
            cur.execute(sql, params)
            return list(cur.fetchall())

        ahead = day + dt.timedelta(days=cfg.ahead_days)
        reasons = {
            (str(r[0]), str(r[1])): r[2]
            for r in rows(
                "pick_reasons",
                f"SELECT line, stock_id, reasons FROM {s}.pick_reasons WHERE date = %s",
                (day,),
            )
        }
        down = {
            (str(r[0]), str(r[1]))
            for r in rows(
                "pick_down_risk",
                f"SELECT line, stock_id FROM {s}.pick_down_risk WHERE date = %s AND flagged",
                (day,),
            )
        }
        disp: dict[str, list[str]] = {}
        for sid, start, end in rows(
            "disposition_stocks",
            f"SELECT DISTINCT stock_id, effective_start, effective_end FROM {s}.disposition_stocks "
            f"WHERE stock_id = ANY(%s) AND effective_end >= %s AND effective_start <= %s",
            (sids, day, ahead),
        ):
            text = f"處置中，到 {md(end)}" if start <= day else f"{md(start)} 起處置"
            disp.setdefault(str(sid), []).append(text)
        att = {
            str(r[0])
            for r in rows(
                "attention_stocks",
                f"SELECT DISTINCT stock_id FROM {s}.attention_stocks WHERE date = %s AND stock_id = ANY(%s)",
                (day, sids),
            )
        }
        events: dict[str, list[tuple[dt.date, str]]] = {}
        for sid, d, kind in rows(
            "dividend_calendar",
            f"SELECT stock_id, date, kind FROM {s}.dividend_calendar WHERE stock_id = ANY(%s) AND date > %s AND date <= %s",
            (sids, day, ahead),
        ):
            events.setdefault(str(sid), []).append(
                (d, f"{md(d)} {DIV_TEXT.get(str(kind), '除權息')}")
            )
        for sid, d in rows(
            "conference_calendar",
            f"SELECT DISTINCT stock_id, date FROM {s}.conference_calendar WHERE stock_id = ANY(%s) AND date >= %s AND date <= %s",
            (sids, day, ahead),
        ):
            events.setdefault(str(sid), []).append(
                (d, "今天法說會" if d == day else f"{md(d)} 法說會")
            )
        insider = {
            str(r[0]): int(r[1])
            for r in rows(
                "insider_transfer_notices",
                f"SELECT stock_id, count(DISTINCT row_key) FROM {s}.insider_transfer_notices "
                f"WHERE stock_id = ANY(%s) AND date > %s AND date <= %s GROUP BY stock_id",
                (sids, day - dt.timedelta(days=cfg.insider_days), day),
            )
        }
        since = dt.datetime.combine(day - dt.timedelta(days=cfg.announcement_days), dt.time(), TZ)
        until = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), TZ)
        ann: dict[str, list[str]] = {}
        for sid, _, subject in rows(
            "mops_announcements",
            f"SELECT stock_id, available_at, subject FROM {s}.mops_announcements "
            f"WHERE stock_id = ANY(%s) AND available_at >= %s AND available_at < %s ORDER BY available_at DESC",
            (sids, since, until),
        ):
            lst = ann.setdefault(str(sid), [])
            subj = " ".join(str(subject).split())[: cfg.announcement_chars]
            if subj not in lst and len(lst) < cfg.announcement_max:
                lst.append(subj)

    out: list[PickFacts] = []
    for line, rank, sid in picks:
        px = closes.get(sid, [])
        close = chg = move = None
        if px and px[-1][0] == day:
            close = px[-1][1]
            if len(px) >= 2 and px[-2][1] > 0:
                chg = close / px[-2][1] - 1
            if len(px) > cfg.move_days and px[-1 - cfg.move_days][1] > 0:
                move = close / px[-1 - cfg.move_days][1] - 1
        raw = reasons.get((line, sid))
        rs = json.loads(raw) if isinstance(raw, str) else (raw or [])
        out.append(
            PickFacts(
                day=day,
                line=line,
                line_name=line_names.get(line, line),
                stock_id=sid,
                name=names.get(sid),
                rank=rank,
                close=close,
                chg=chg,
                move=move,
                move_days=cfg.move_days,
                reasons=[r for r in rs if isinstance(r, dict) and r.get("t")],
                down_risk=(line, sid) in down,
                disposition=disp.get(sid, []),
                attention=sid in att,
                events=[t for _, t in sorted(events.get(sid, []))],
                insider=insider.get(sid, 0),
                insider_days=cfg.insider_days,
                announcements=ann.get(sid, []),
            )
        )
    # 每條線：加分理由的因子出現在幾成的檔
    for line in {p.line for p in out}:
        members = [p for p in out if p.line == line]
        count: dict[str, int] = {}
        for p in members:
            for f in {str(r.get("f", "")) for r in p.reasons if int(r.get("s", 0)) > 0}:
                count[f] = count.get(f, 0) + 1
        common = frozenset(
            f for f, n in count.items() if f and n / len(members) >= cfg.common_share
        )
        for p in members:
            p.common = common
    return out, errors


def done_today(conn: Any, schema: str, day: dt.date) -> set[tuple[str, str]]:
    """今天已經由模型寫好的 (線, 代號)；第二班排程沿用、不重呼叫。表還沒建＝空。"""
    with conn.cursor() as cur:
        if not _has(cur, schema, PICK_BLURBS_SPEC.name):
            return set()
        cur.execute(
            f'SELECT line, stock_id FROM "{schema}"."{PICK_BLURBS_SPEC.name}" WHERE date = %s AND source = %s',
            (day, "model"),
        )
        return {(str(r[0]), str(r[1])) for r in cur.fetchall()}


def write(conn: Any, schema: str, day: dt.date, blurbs: list[Blurb], now: dt.datetime) -> None:
    """upsert 這些列（重跑覆蓋同一檔）。"""
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    if not blurbs:
        return
    ensure_tables(conn, (PICK_BLURBS_SPEC,), schema)
    df = pl.DataFrame(
        [
            {
                "date": day,
                "line": b.line,
                "stock_id": b.stock_id,
                "text": b.text,
                "source": b.source,
                "model_id": b.model_id,
                "prompt_version": PICK_BLURB_PROMPT_VERSION,
                "facts": json.dumps(b.facts, ensure_ascii=False),
                "checks": json.dumps(b.checks, ensure_ascii=False),
                "created_at": now,
            }
            for b in blurbs
        ],
        schema={
            "date": pl.Date,
            "line": pl.Utf8,
            "stock_id": pl.Utf8,
            "text": pl.Utf8,
            "source": pl.Utf8,
            "model_id": pl.Utf8,
            "prompt_version": pl.Utf8,
            "facts": pl.Utf8,
            "checks": pl.Utf8,
            "created_at": pl.Datetime("us", "Asia/Taipei"),
        },
    ).select(list(PICK_BLURBS_SPEC.column_names))
    with conn.cursor() as cur:
        upsert(cur, schema, PICK_BLURBS_SPEC, df)
    conn.commit()


def run_for_day(
    conn: Any,
    schema: str,
    day: dt.date,
    client: InsightClient,
    cfg: PickBlurbsConfig,
    line_names: dict[str, str],
    now: dt.datetime,
    *,
    force: bool = False,
) -> tuple[str, list[Blurb], list[str]]:
    """當天每條線每檔一句話 → pick_blurbs。回 (摘要, 這次寫的, 缺了哪類資料)。"""
    items, errors = gather(conn, schema, day, cfg, line_names)
    if not items:
        return f"{day} 沒有名單，略過", [], errors
    skip = set() if force else done_today(conn, schema, day)
    written: list[Blurb] = []
    for line in cfg.lines:
        todo = [p for p in items if p.line == line and (p.line, p.stock_id) not in skip]
        written.extend(compose_line(client, todo, now, cfg))
    write(conn, schema, day, written, now)
    n_model = sum(b.source == "model" for b in written)
    n_rule = len(written) - n_model
    reused = sum((p.line, p.stock_id) in skip for p in items)
    summary = f"模型 {n_model} 檔、規則句 {n_rule} 檔、沿用 {reused} 檔"
    return summary, written, errors
