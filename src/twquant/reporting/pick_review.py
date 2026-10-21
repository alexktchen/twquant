"""名單四面向評分與 AI 首選（T-394／D-196；10 分制規則評分 T-426／D-206）。

使用者 2026-10-18：「15 檔你最推哪一檔，每檔依照消息、技術、籌碼、基本四個面向來評分…兩個模型各 15 檔都要給，
然後兩個模型給我一檔最推薦的」。做法同一句話（T-393）：規則先把每檔四個面向的資料寫成事實句；
**分數由規則算**（`pick_score`：每面向 5 個小項 ±1，0～10、5＝中性，D-206），模型只寫觀點、立場、結論，
從名單裡挑一檔首選並寫理由；程式核對（首選在名單上、理由的數字與說法出自事實、不准建議）。
首選理由沒過 → 改用四面向加總最高的那檔（source=rule）。**AI 評分與首選都沒有驗證過能預測報酬**；事後怎麼檢驗在 D-196 事前登記。
只顯示，不改名單、不進模型。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from twquant.config import PickBlurbsConfig, PickReviewConfig
from twquant.conventions import TZ
from twquant.data.serving.schema import PICK_REVIEWS_SPEC, add_trailing_columns, ensure_tables
from twquant.data.serving.sync import upsert
from twquant.insight.client import InsightClient
from twquant.insight.prompts import PICK_REVIEW_PROMPT_VERSION, PICK_REVIEW_SYSTEM, PICK_TOP_SYSTEM
from twquant.insight.schemas import PickReviewItem, PickTop
from twquant.insight.validation import unverified_numbers
from twquant.reporting.morning_brief import strip_self_reference
from twquant.reporting.pick_blurbs import (
    _CN_NUMBER,
    _SID,
    PickFacts,
    _has,
    gather,
    move_text,
    unit_mismatch,
)
from twquant.reporting.pick_score import (
    NO_DATA,
    Item,
    Market,
    chip_items,
    fund_items,
    items_json,
    load_market,
    news_items,
    tech_items,
    total,
)

TASK = "pick_review"
DIMS = ("消息", "技術", "籌碼", "基本")
KEYS = ("news", "tech", "chips", "fund")
SHARES_PER_LOT = 1000  # 台股一張＝1,000 股（制度，不是參數）
NO_NEWS = "近期沒有公告、處置、注意、除權息或法說會"
SCALE = 10  # 每個面向滿分（D-206）；舊資料是 1～5（AI 自評），網站依 pick_reviews.scale 分開畫


@dataclass
class Dimensions:
    """一檔四個面向的事實句。"""

    stock_id: str
    name: str | None
    line: str
    rank: int
    facts: dict[str, list[str]] = field(default_factory=dict)
    items: dict[str, list[Item]] = field(default_factory=dict)  # 面向 → 5 個小項（D-206）
    scores: dict[str, int] = field(default_factory=dict)  # news／tech／chips／fund → 0～10
    stance: str | None = None  # 五級立場（程式依總分定，D-206 §六）
    split: bool = False  # 多空分歧

    def all_facts(self) -> list[str]:
        return [f for d in DIMS for f in self.facts.get(d, [])]


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def _lots(shares: float) -> str:
    """股數 → 「1,234 張」。"""
    return f"{abs(round(shares / SHARES_PER_LOT)):,} 張"


def _net(label: str, shares: float) -> str:
    return (
        f"{label}{'買超' if shares > 0 else '賣超' if shares < 0 else '持平'} {_lots(shares)}"
        if shares
        else f"{label}持平"
    )


def _streak(values: list[float]) -> int:
    """最新一天往回連續同方向的天數：正＝連買幾天、負＝連賣幾天、0＝今天持平。"""
    if not values or values[-1] == 0:
        return 0
    sign = 1 if values[-1] > 0 else -1
    n = 0
    for v in reversed(values):
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            n += 1
        else:
            break
    return n * sign


def news_facts(p: PickFacts) -> list[str]:
    out: list[str] = list(p.disposition)
    if p.attention:
        out.append("今天是注意股")
    out.extend(p.events)
    if p.insider > 0:
        out.append(f"近 {p.insider_days} 天有 {p.insider} 筆內部人申報轉讓持股")
    out.extend(f"近期公告：{a}" for a in p.announcements)
    return out or [NO_NEWS]


def tech_facts(
    closes: list[float],
    volumes: list[float],
    cfg: PickReviewConfig,
    down_risk: bool,
    near_high: float = 0.0,
) -> list[str]:
    """行情由舊到新，最後一筆＝當天。"""
    out: list[str] = []
    if len(closes) < 2:
        return ["行情不足，算不出技術面"]
    c = closes[-1]
    moves = []
    for n in cfg.move_days:
        if len(closes) > n and closes[-1 - n] > 0:
            moves.append(f"近 {n} 日{move_text(c / closes[-1 - n] - 1)}")
    if moves:
        out.append(f"今天{move_text(c / closes[-2] - 1)}；" + "；".join(moves))
    mas = []
    for n in cfg.ma_days:
        if len(closes) >= n:
            ma = sum(closes[-n:]) / n
            mas.append(f"{n} 日均線{'之上' if c >= ma else '之下'} {_pct(abs(c / ma - 1))}")
    if mas:
        out.append("收盤在 " + "、".join(mas))
    hi = max(closes[-cfg.high_days :])
    out.append("創 52 週新高" if c >= hi else f"離 52 週高點還差 {_pct(1 - c / hi)}")
    if c < hi and 1 - c / hi <= near_high:
        out.append("股價在 52 週高點附近")  # 「接近一年高點」的根據（同一句話 T-393）
    if len(closes) >= cfg.low_days and c <= min(closes[-cfg.low_days :]):
        out.append(f"今天是 {cfg.low_days} 日新低")
    if len(volumes) > cfg.volume_days and volumes[-1] > 0:
        avg = sum(volumes[-1 - cfg.volume_days : -1]) / cfg.volume_days
        if avg > 0:
            out.append(f"成交量是前 {cfg.volume_days} 日平均的 {volumes[-1] / avg:.1f} 倍")
    if down_risk:
        out.append("模型標了易跌")
    return out


def chip_facts(
    inst: list[tuple[float, float, float]],
    margin: list[float],
    holders: list[tuple[dt.date, float]],
    cfg: PickReviewConfig,
) -> list[str]:
    """inst＝每天（外資, 投信, 自營）買賣超股數，舊到新；margin＝融資餘額股數，舊到新；holders＝（週, 千張大戶比例），舊到新。"""
    out: list[str] = []
    n, m = cfg.chip_days, cfg.chip_long_days
    for i, label in enumerate(("外資", "投信", "自營商")):
        vals = [r[i] for r in inst]
        if not vals:
            continue
        part = [f"{label}近 {n} 日{_net('', sum(vals[-n:]))}"]
        if i == 0 and len(vals) >= m:
            part.append(f"近 {m} 日{_net('', sum(vals[-m:]))}")
        s = _streak(vals)
        if abs(s) >= cfg.streak_min:
            part.append(f"連續{'買超' if s > 0 else '賣超'} {abs(s)} 天")
        out.append("，".join(part))
    if len(margin) > n and margin[-1 - n] > 0:
        d = margin[-1] - margin[-1 - n]
        word = "增加" if d > 0 else "減少" if d < 0 else "持平"
        out.append(f"融資近 {n} 日{word} {_lots(d)}（{_pct(abs(d) / margin[-1 - n])}）")
    if holders:
        wk, r = holders[-1]
        text = f"千張大戶持股 {_pct(r)}（{wk.month}/{wk.day} 那週）"
        if len(holders) >= 2:
            diff = (r - holders[-2][1]) * 100
            text += f"，比前一週{'多' if diff >= 0 else '少'} {abs(diff):.1f} 個百分點"
        out.append(text)
    return out or ["籌碼資料不足"]


def fund_facts(
    revenue: list[tuple[str, float | None]],
    val: tuple[float | None, float | None, float | None] | None,
) -> list[str]:
    """revenue＝（年月, 年增率），新到舊；val＝（本益比, 股價淨值比, 殖利率）。"""
    out: list[str] = []
    rev: list[tuple[str, float]] = [(ym, y) for ym, y in revenue if y is not None]
    if rev:
        head = f"{int(rev[0][0][5:7])} 月營收年{'增' if rev[0][1] >= 0 else '減'} {_pct(abs(rev[0][1]))}"
        rest = [
            f"{int(ym[5:7])} 月年{'增' if y >= 0 else '減'} {_pct(abs(y))}" for ym, y in rev[1:]
        ]
        out.append(head + ("；" + "、".join(rest) if rest else ""))
    if val is not None:
        per, pbr, dy = val
        parts = [f"本益比 {per:.1f} 倍" if per else "本益比無（可能虧損）"]
        if pbr:
            parts.append(f"股價淨值比 {pbr:.1f} 倍")
        if dy is not None:
            parts.append(f"殖利率 {_pct(dy)}")
        out.append("、".join(parts))
    return out or ["基本面資料不足"]


def gather_dimensions(
    conn: Any,
    schema: str,
    day: dt.date,
    cfg: PickReviewConfig,
    blurb_cfg: PickBlurbsConfig,
    line_names: dict[str, str],
) -> tuple[list[Dimensions], list[str]]:
    """當天每條線每檔的四面向事實；回 (每檔, 缺了哪類資料)。消息類沿用一句話（T-393）的查詢。"""
    base_cfg = blurb_cfg.model_copy(update={"lines": cfg.lines})
    picks, errors = gather(conn, schema, day, base_cfg, line_names)
    if not picks:
        return [], errors
    sids = sorted({p.stock_id for p in picks})
    s = f'"{schema}"'
    since = day - dt.timedelta(days=cfg.history_days)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT stock_id, close, volume FROM {s}.prices_daily WHERE stock_id = ANY(%s) AND date <= %s AND date >= %s ORDER BY stock_id, date",
            (sids, day, since),
        )
        px: dict[str, tuple[list[float], list[float]]] = {}
        for sid, c, v in cur.fetchall():
            if c is not None:
                cs, vs = px.setdefault(str(sid), ([], []))
                cs.append(float(c))
                vs.append(float(v or 0))

        def rows(table: str, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
            if not _has(cur, schema, table):
                errors.append(f"缺 {table}，少了這類事實")
                return []
            cur.execute(sql, params)
            return list(cur.fetchall())

        chip_since = day - dt.timedelta(days=cfg.chip_long_days * 2 + 10)
        inst: dict[str, list[tuple[float, float, float]]] = {}
        for sid, f, t, d in rows(
            "institutional_daily",
            f"SELECT stock_id, foreign_net, trust_net, dealer_net FROM {s}.institutional_daily "
            f"WHERE stock_id = ANY(%s) AND date <= %s AND date >= %s ORDER BY stock_id, date",
            (sids, day, chip_since),
        ):
            inst.setdefault(str(sid), []).append((float(f or 0), float(t or 0), float(d or 0)))
        margin: dict[str, list[float]] = {}
        for sid, b in rows(
            "margin_daily",
            f"SELECT stock_id, margin_balance FROM {s}.margin_daily WHERE stock_id = ANY(%s) AND date <= %s AND date >= %s ORDER BY stock_id, date",
            (sids, day, chip_since),
        ):
            if b is not None:
                margin.setdefault(str(sid), []).append(float(b))
        holders: dict[str, list[tuple[dt.date, float]]] = {}
        # 集保週快照週六才公布：只用早於名單日的週（同網站 holders.ts）
        for sid, d, big, total in rows(
            "shareholding_dispersion",
            f"SELECT stock_id, date, sum(shares) FILTER (WHERE level = %s), sum(shares) FROM {s}.shareholding_dispersion "
            f"WHERE stock_id = ANY(%s) AND date < %s AND date > %s GROUP BY stock_id, date ORDER BY stock_id, date",
            (cfg.holders_level, sids, day, day - dt.timedelta(days=cfg.holders_lookback_days)),
        ):
            if big is not None and total:
                holders.setdefault(str(sid), []).append((d, float(big) / float(total)))
        revenue: dict[str, list[tuple[str, float | None]]] = {}
        for sid, ym, yoy in rows(
            "monthly_revenue",
            f"SELECT stock_id, year_month, yoy FROM {s}.monthly_revenue WHERE stock_id = ANY(%s) AND available_at <= %s "
            f"ORDER BY stock_id, year_month DESC",
            (sids, dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), TZ)),
        ):
            lst = revenue.setdefault(str(sid), [])
            if len(lst) < cfg.revenue_months:
                lst.append((str(ym), None if yoy is None else float(yoy)))
        val: dict[str, tuple[float | None, float | None, float | None]] = {}
        for sid, per, pbr, dy in rows(
            "stock_valuation",
            f"SELECT DISTINCT ON (stock_id) stock_id, per, pbr, dividend_yield FROM {s}.stock_valuation "
            f"WHERE stock_id = ANY(%s) AND date <= %s ORDER BY stock_id, date DESC",
            (sids, day),
        ):
            val[str(sid)] = (
                None if per is None else float(per),
                None if pbr is None else float(pbr),
                None if dy is None else float(dy),
            )
    market, m_errors = load_market(conn, schema, day, cfg.rules)
    errors.extend(m_errors)
    out: list[Dimensions] = []
    for p in picks:
        cs, vs = px.get(p.stock_id, ([], []))
        d = Dimensions(
            stock_id=p.stock_id,
            name=p.name,
            line=p.line,
            rank=p.rank,
            facts={
                "消息": news_facts(p),
                "技術": tech_facts(cs, vs, cfg, p.down_risk, blurb_cfg.near_high),
                "籌碼": chip_facts(
                    inst.get(p.stock_id, []),
                    margin.get(p.stock_id, []),
                    holders.get(p.stock_id, []),
                    cfg,
                ),
                "基本": fund_facts(revenue.get(p.stock_id, []), val.get(p.stock_id)),
            },
        )
        score_dims(
            d,
            p,
            Inputs(
                closes=cs,
                volumes=vs,
                trust=[r[1] for r in inst.get(p.stock_id, [])],
                margin=margin.get(p.stock_id, []),
                holders=holders.get(p.stock_id, []),
                revenue=revenue.get(p.stock_id, []),
                val=val.get(p.stock_id),
            ),
            market,
            cfg,
        )
        out.append(d)
    return out, errors


@dataclass
class Inputs:
    """一檔算 20 個小項要的個股資料（舊到新，營收新到舊）。"""

    closes: list[float]
    volumes: list[float]
    trust: list[float]
    margin: list[float]
    holders: list[tuple[dt.date, float]]
    revenue: list[tuple[str, float | None]]
    val: tuple[float | None, float | None, float | None] | None


def score_dims(d: Dimensions, p: PickFacts, x: Inputs, m: Market, cfg: PickReviewConfig) -> None:
    """D-206：四面向 20 個小項 → d.items／d.scores；小項理由併進事實（模型寫觀點時可以引用）。"""
    r = cfg.rules
    d.items = {
        "消息": news_items(
            p.disposition, p.attention, p.insider, p.insider_days, p.announcements, p.events, r
        ),
        "技術": tech_items(p.stock_id, x.closes, x.volumes, m, r, cfg.high_days),
        "籌碼": chip_items(p.stock_id, x.trust, x.margin, x.closes, x.holders, m, r),
        "基本": fund_items(p.stock_id, x.revenue, x.val, m, r),
    }
    for dim, key in zip(DIMS, KEYS, strict=True):
        its = d.items[dim]
        d.scores[key] = total(its, r)
        extra = [i.text for i in its if i.text != NO_DATA and i.text not in d.facts[dim]]
        d.facts[dim] = [*d.facts[dim], *extra]
    d.stance, d.split = stance_of(d.scores, cfg)


def stance_of(scores: dict[str, int], cfg: PickReviewConfig) -> tuple[str, bool]:
    """總分 → 五級立場；某面向 ≥ split_hi 且另一面向 ≤ split_lo＝多空分歧（D-206 §六）。"""
    total_ = sum(scores.values())
    levels = sorted(cfg.stance_levels, key=lambda lv: -lv.min)
    label = next((lv.label for lv in levels if total_ >= lv.min), levels[-1].label)
    vals = list(scores.values())
    split = max(vals) >= cfg.split_hi and min(vals) <= cfg.split_lo
    return label, split


@dataclass
class Review:
    line: str
    stock_id: str
    scores: dict[str, int] | None  # news／tech／chips／fund，0～10（規則算，D-206）；模型缺席＝None
    views: dict[str, str | None] = field(default_factory=dict)  # 每個面向一句觀點；沒過核對＝None
    stance: str | None = None
    split: bool = False
    conclusion: str | None = None
    is_top: bool = False
    reason: str | None = None
    source: str = "model"  # 首選的來源：model｜rule
    model_id: str | None = None
    facts: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(
        default_factory=list
    )  # 20 個小項（存進 pick_reviews.items）


def check_reason(
    text: str,
    d: Dimensions,
    blurb_cfg: PickBlurbsConfig,
    max_chars: int,
    *,
    known: list[str] | None = None,
    forbidden: list[str] | None = None,
    allow: list[str] | None = None,
    other_dims: list[str] | None = None,
) -> tuple[str, list[str]]:
    """文字核對：數字與單位出自事實（known 沒給＝那一檔全部事實）、不准中文換算、狀態詞與白話說法要有根據、
    不准術語、不能太長。forbidden 沒給＝不准任何建議（首選理由）；有給＝可以建議、只擋這些字（觀點與結論，D-196 §二）。"""
    t = strip_self_reference(text).strip().rstrip("。")
    known = known if known is not None else d.all_facts()
    problems: list[str] = []
    if not t:
        problems.append("空白")
    if len(t) > max_chars:
        problems.append(f"太長（{len(t)} 字，上限 {max_chars}）")
    bad = unverified_numbers(t, known)
    if bad:
        problems.append(f"數字不在事實裡 {list(bad)}")
    units = unit_mismatch(t, known)
    if units:
        problems.append(f"數字的單位跟事實不同 {units}")
    cn = _CN_NUMBER.findall(t)
    if cn:
        problems.append(f"把數字換成中文 {cn}")
    made_up = [
        w
        for w in blurb_cfg.state_terms
        if w in t and w not in (allow or []) and not any(w in f for f in known)
    ]
    if made_up:
        problems.append(f"事實沒有的說法 {made_up}")
    cross = [w for w in (other_dims or []) if w in t]
    if cross:
        problems.append(f"談到別的面向 {cross}")
    unsupported = [
        w
        for w, need in blurb_cfg.evidence_terms.items()
        if w in t and not any(n in f for n in need for f in known)
    ]
    if unsupported:
        problems.append(f"說法在事實裡找不到根據 {unsupported}")
    if forbidden is None:
        advice = [w for w in blurb_cfg.advice_terms if w in t]
        if advice:
            problems.append(f"含買賣建議 {advice}")
    else:
        bad_terms = [w for w in forbidden if w in t]
        if bad_terms:
            problems.append(f"喊價或保證 {bad_terms}")
    jargon = [w for w in blurb_cfg.jargon_terms if w.lower() in t.lower()]
    if jargon:
        problems.append(f"術語沒翻成白話 {jargon}")
    return t, problems


VIEW_KEYS = tuple(f"{k}_view" for k in KEYS)


def check_item(
    got: Any, d: Dimensions, cfg: PickReviewConfig, blurb_cfg: PickBlurbsConfig, r: Review
) -> None:
    """一檔的四句觀點、立場、結論；不合格的那一項設成 None 並記原因（不連坐整檔）。分數是規則算的（d.scores）。"""
    sc = dict(d.scores)
    r.scores = sc
    for k, dim, vk in zip(KEYS, DIMS, VIEW_KEYS, strict=True):
        others = [w for o, ws in cfg.dim_words.items() if o != dim for w in ws]
        text, problems = check_reason(
            getattr(got, vk),
            d,
            blurb_cfg,
            cfg.view_chars,
            known=d.facts.get(dim, []),
            forbidden=cfg.forbidden_terms,
            allow=cfg.interpretive_terms,
            other_dims=others,
        )
        if problems:
            r.views[k] = None
            r.checks.append(f"{dim}觀點不用：{'；'.join(problems)}；原句：{getattr(got, vk)}")
        else:
            r.views[k] = text
    r.stance, r.split = d.stance, d.split
    text, problems = check_reason(
        got.conclusion,
        d,
        blurb_cfg,
        cfg.conclusion_chars,
        forbidden=cfg.forbidden_terms,
        allow=[*cfg.interpretive_terms, *cfg.conclusion_allow],
    )
    if problems:
        r.checks.append(f"結論不用：{'；'.join(problems)}；原句：{got.conclusion}")
    else:
        r.conclusion = text


def rule_reason(scores: dict[str, int]) -> str:
    total = sum(scores.values())
    return (
        f"四面向合計 {total} 分最高（"
        + "、".join(f"{n} {scores[k]}" for n, k in zip(DIMS, KEYS, strict=True))
        + "）"
    )


def _head(dim: str, key: str, d: Dimensions) -> str:
    """「【籌碼 4 分】」：分數是規則算的，模型照著寫觀點。"""
    return f"【{dim} {d.scores[key]} 分】" if key in d.scores else f"【{dim}】"


def stock_payload(line_name: str, day: dt.date, d: Dimensions) -> str:
    parts = [
        _head(k, key, d) + "；".join(d.facts.get(k, [])) for k, key in zip(DIMS, KEYS, strict=True)
    ]
    if d.stance:
        parts.append(f"立場：{d.stance}" + ("（多空分歧）" if d.split else ""))
    return (
        f"名單：{line_name}（{day.isoformat()} 收盤後）\n[{d.stock_id} {d.name or ''}]\n"
        + "\n".join(parts)
    )


def top_payload(
    line_name: str, day: dt.date, reviews: list[Review], dims: dict[str, Dimensions]
) -> str:
    blocks = [f"名單：{line_name}（{day.isoformat()} 收盤後），有分數的 {len(reviews)} 檔"]
    for r in reviews:
        sc = r.scores or {}
        head = " ".join(f"{n}{sc[k]}／{SCALE}" for n, k in zip(DIMS, KEYS, strict=True))
        d = dims[r.stock_id]
        facts = "\n".join(f"【{k}】" + "；".join(d.facts.get(k, [])) for k in DIMS)
        blocks.append(
            f"[{r.stock_id} {d.name or ''}] {head}（共 {sum(sc.values())}）｜立場 {r.stance or '-'}｜結論：{r.conclusion or '-'}\n{facts}"
        )
    return "\n\n".join(blocks)


def review_stock(
    client: InsightClient,
    d: Dimensions,
    line_name: str,
    day: dt.date,
    now: dt.datetime,
    cfg: PickReviewConfig,
    blurb_cfg: PickBlurbsConfig,
) -> tuple[Review, str | None]:
    """一檔一次呼叫；回 (評分, 缺席原因)。"""
    model_id = client.config.model_for(TASK).id
    r = Review(
        d.line, d.stock_id, None, model_id=model_id, facts=d.all_facts(), items=items_json(d.items)
    )
    res, absent = client.complete_detailed(
        task=TASK,
        system=PICK_REVIEW_SYSTEM,
        user=stock_payload(line_name, day, d),
        schema=PickReviewItem,
        prompt_version=PICK_REVIEW_PROMPT_VERSION,
        now=now,
        max_tokens=cfg.max_tokens,
    )
    got = res.parsed if res is not None else None
    if not isinstance(got, PickReviewItem):
        reason = f"模型缺席：{absent or '輸出不符 PickReviewItem 結構'}"
        r.checks.append(reason)
        return r, reason
    check_item(got, d, cfg, blurb_cfg, r)
    return r, None


def choose_top(
    client: InsightClient,
    reviews: list[Review],
    items: list[Dimensions],
    line_name: str,
    day: dt.date,
    now: dt.datetime,
    cfg: PickReviewConfig,
    blurb_cfg: PickBlurbsConfig,
) -> None:
    """一條線一次呼叫挑首選；理由沒過或跟分數不一致 → 四面向加總最高（source=rule）。"""
    scored = [r for r in reviews if r.scores is not None]
    if not scored:
        return
    dims = {d.stock_id: d for d in items}
    res, absent = client.complete_detailed(
        task=TASK,
        system=PICK_TOP_SYSTEM,
        user=top_payload(line_name, day, scored, dims),
        schema=PickTop,
        prompt_version=PICK_REVIEW_PROMPT_VERSION,
        now=now,
        max_tokens=cfg.top_max_tokens,
    )
    parsed = res.parsed if res is not None else None
    notes: list[str] = []
    best_total = max(sum(r.scores.values()) for r in scored)  # type: ignore[union-attr]
    if not isinstance(parsed, PickTop):
        notes.append(f"首選缺席：{absent or '輸出不符 PickTop 結構'}")
    else:
        m = _SID.search(parsed.top_sid or "")
        top_sid = m.group() if m else (parsed.top_sid or "").strip()
        top = next((r for r in scored if r.stock_id == top_sid), None)
        if top is None or top.scores is None:
            notes.append(f"首選 {top_sid!r} 不在名單上或沒有分數")
        elif sum(top.scores.values()) < best_total - cfg.top_total_slack:
            notes.append(
                f"首選 {top_sid} 總分 {sum(top.scores.values())}，比最高 {best_total} 低超過 {cfg.top_total_slack} 分（跟自己打的分數不一致）"
            )
        else:
            text, problems = check_reason(
                parsed.top_reason, dims[top_sid], blurb_cfg, cfg.reason_chars
            )
            if problems:
                notes.extend([*problems, f"原句：{parsed.top_reason}"])
            else:
                top.is_top, top.reason, top.source = True, text, "model"
                return
    # 後備：四面向加總最高；同分取名次前面的
    rank = {d.stock_id: d.rank for d in items}
    best = max(scored, key=lambda r: (sum(r.scores.values()), -rank[r.stock_id]))  # type: ignore[union-attr]
    best.is_top, best.source = True, "rule"
    best.reason = rule_reason(best.scores)  # type: ignore[arg-type]
    best.checks.extend(notes)


def compose_line(
    client: InsightClient,
    items: list[Dimensions],
    line_name: str,
    day: dt.date,
    now: dt.datetime,
    cfg: PickReviewConfig,
    blurb_cfg: PickBlurbsConfig,
) -> tuple[list[Review], str | None]:
    """一條線：每檔一次呼叫評分，再一次呼叫挑首選。回 (每檔評分, 整條線都缺席時的原因)。"""
    if not items:
        return [], None
    out: list[Review] = []
    reasons: list[str] = []
    for d in items:
        r, absent = review_stock(client, d, line_name, day, now, cfg, blurb_cfg)
        out.append(r)
        if absent:
            reasons.append(absent)
    if len(reasons) == len(items):
        return out, reasons[0]
    choose_top(client, out, items, line_name, day, now, cfg, blurb_cfg)
    return out, None


def done_today(conn: Any, schema: str, day: dt.date) -> set[str]:
    """今天已有模型分數的線；第二班沿用。"""
    with conn.cursor() as cur:
        if not _has(cur, schema, PICK_REVIEWS_SPEC.name):
            return set()
        cur.execute(
            f'SELECT DISTINCT line FROM "{schema}"."{PICK_REVIEWS_SPEC.name}" WHERE date = %s AND news IS NOT NULL',
            (day,),
        )
        return {str(r[0]) for r in cur.fetchall()}


def write(conn: Any, schema: str, day: dt.date, reviews: list[Review], now: dt.datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    if not reviews:
        return
    ensure_tables(conn, (PICK_REVIEWS_SPEC,), schema)
    add_trailing_columns(conn, PICK_REVIEWS_SPEC, schema)  # scale／items（D-206）加在最後
    df = pl.DataFrame(
        [
            {
                "date": day,
                "line": r.line,
                "stock_id": r.stock_id,
                **{k: (r.scores or {}).get(k) for k in KEYS},
                "total": sum(r.scores.values()) if r.scores else None,
                **{f"{k}_view": r.views.get(k) for k in KEYS},
                "stance": r.stance,
                "split": r.split,
                "conclusion": r.conclusion,
                "is_top": r.is_top,
                "top_reason": r.reason,
                "source": r.source,
                "model_id": r.model_id,
                "prompt_version": PICK_REVIEW_PROMPT_VERSION,
                "facts": json.dumps(r.facts, ensure_ascii=False),
                "checks": json.dumps(r.checks, ensure_ascii=False),
                "created_at": now,
                "scale": SCALE,
                "items": json.dumps(r.items, ensure_ascii=False),
            }
            for r in reviews
        ],
        schema={
            "date": pl.Date,
            "line": pl.Utf8,
            "stock_id": pl.Utf8,
            **dict.fromkeys(KEYS, pl.Int32),
            "total": pl.Int32,
            **dict.fromkeys(VIEW_KEYS, pl.Utf8),
            "stance": pl.Utf8,
            "split": pl.Boolean,
            "conclusion": pl.Utf8,
            "is_top": pl.Boolean,
            "top_reason": pl.Utf8,
            "source": pl.Utf8,
            "model_id": pl.Utf8,
            "prompt_version": pl.Utf8,
            "facts": pl.Utf8,
            "checks": pl.Utf8,
            "created_at": pl.Datetime("us", "Asia/Taipei"),
            "scale": pl.Int32,
            "items": pl.Utf8,
        },
    ).select(list(PICK_REVIEWS_SPEC.column_names))
    with conn.cursor() as cur:
        upsert(cur, schema, PICK_REVIEWS_SPEC, df)
    conn.commit()


def run_for_day(
    conn: Any,
    schema: str,
    day: dt.date,
    client: InsightClient,
    cfg: PickReviewConfig,
    blurb_cfg: PickBlurbsConfig,
    line_names: dict[str, str],
    now: dt.datetime,
    *,
    force: bool = False,
) -> tuple[str, list[Review], list[str]]:
    """當天每條線：四面向評分＋首選 → pick_reviews。回 (摘要, 這次寫的, 缺了哪類資料)。"""
    items, errors = gather_dimensions(conn, schema, day, cfg, blurb_cfg, line_names)
    if not items:
        return f"{day} 沒有名單，略過", [], errors
    skip = set() if force else done_today(conn, schema, day)
    written: list[Review] = []
    parts: list[str] = []
    for line in cfg.lines:
        todo = [d for d in items if d.line == line]
        if not todo:
            continue
        if line in skip:
            parts.append(f"{line} 沿用")
            continue
        reviews, absent = compose_line(
            client, todo, line_names.get(line, line), day, now, cfg, blurb_cfg
        )
        written.extend(reviews)
        top = next((r for r in reviews if r.is_top), None)
        n_ok = sum(r.scores is not None for r in reviews)
        parts.append(
            f"{line} {absent}"
            if absent
            else f"{line} 評分 {n_ok}/{len(reviews)} 檔、首選 {top.stock_id if top else '無'}（{top.source if top else '-'}）"
        )
    write(conn, schema, day, written, now)
    return "；".join(parts), written, errors
