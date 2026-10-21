"""名單四面向 10 分制的規則評分（T-426／D-206 §二）。

每個面向 5 個小項，每項偏多 +1、中性 0、偏空 −1，從 `rules.base` 起算 → 0～10（5＝中性）。
「偏多／偏空」大多跟同一天全市場（四碼普通股）比百分位。**描述現況，不是預測**——
除了波動（D-132）與投信連買（D-143），其他小項沒有驗證過能預測報酬；主力賣超扣分只代表「主力在賣」（D-203：之後並沒有比較差）。
資料缺＝該項 0 分並寫「資料不足」，不猜。
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import asdict, dataclass
from typing import Any

import polars as pl

from twquant.config import PickScoreRules

SHARES_PER_LOT = 1000  # 台股一張＝1,000 股（制度，不是參數）
NO_DATA = "資料不足"


@dataclass(frozen=True)
class Item:
    """一個小項：名稱、+1／0／−1、白話理由。"""

    name: str
    points: int
    text: str


def total(items: list[Item], rules: PickScoreRules) -> int:
    """面向分數＝base ＋ 各項加總，夾在 0～2×base。"""
    return max(0, min(2 * rules.base, rules.base + sum(i.points for i in items)))


def items_json(items: dict[str, list[Item]]) -> list[dict[str, Any]]:
    """存進 pick_reviews.items 的格式：[{dim, name, points, text}]。"""
    return [{"dim": k, **asdict(i)} for k, lst in items.items() for i in lst]


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def _lots(shares: float) -> str:
    return f"{abs(round(shares / SHARES_PER_LOT)):,} 張"


def _beat(p: float) -> str:
    """百分位 → 白話：「強過全市場 85% 的股票」。"""
    return f"強過全市場 {round(p * 100)}% 的股票"


def _band(p: float, rules: PickScoreRules, *, reverse: bool = False) -> int:
    if p >= rules.hi_pct:
        return -1 if reverse else 1
    if p <= rules.lo_pct:
        return 1 if reverse else -1
    return 0


# ── 全市場比較基準 ──────────────────────────────────────────────────────────


@dataclass
class Market:
    """當天全市場的百分位與產業中位數；key＝股票代號。百分位 0～1（越大越高）。"""

    mom: dict[str, tuple[float, float]]  # (近 N 日漲跌, 百分位)
    vol: dict[str, tuple[float, float]]  # (日報酬標準差, 百分位)
    foreign: dict[str, tuple[float, float, float]]  # (近 N 日淨買股數, 占成交量, 百分位)
    main_force: dict[str, tuple[float, float, float]]
    dy: dict[str, tuple[float, float]]  # (殖利率, 百分位)
    industry: dict[str, str]
    per_median: dict[str, float]  # 產業 → 本益比中位數
    pbr_median: dict[str, float]


def percentile(values: dict[str, float]) -> dict[str, float]:
    """平均名次 ÷ 家數（同 D-203 的切法）。"""
    if not values:
        return {}
    s = pl.DataFrame({"k": list(values), "v": list(values.values())}).with_columns(
        p=pl.col("v").rank("average") / pl.len()
    )
    return dict(zip(s["k"].to_list(), s["p"].to_list(), strict=True))


def price_stats(
    px: pl.DataFrame, rules: PickScoreRules
) -> tuple[dict[str, float], dict[str, float]]:
    """px＝(stock_id, date, close) 近一段，舊到新 → (近 mom_days 日漲跌, 近 vol_days 日日報酬標準差)。"""
    mom: dict[str, float] = {}
    vol: dict[str, float] = {}
    for (sid,), g in px.sort("stock_id", "date").group_by("stock_id", maintain_order=True):
        c = g["close"].to_list()
        if len(c) > rules.mom_days and c[-1 - rules.mom_days] > 0:
            mom[str(sid)] = c[-1] / c[-1 - rules.mom_days] - 1
        if len(c) > rules.vol_days:
            r = [
                c[i] / c[i - 1] - 1 for i in range(len(c) - rules.vol_days, len(c)) if c[i - 1] > 0
            ]
            if len(r) == rules.vol_days:
                vol[str(sid)] = statistics.pstdev(r)
    return mom, vol


def flow_ratio(
    flows: pl.DataFrame, volume: pl.DataFrame, sessions: list[dt.date]
) -> dict[str, tuple[float, float]]:
    """flows＝(date, stock_id, net)、volume＝(date, stock_id, volume) → 近 sessions 的 (淨買股數, 占成交量)。

    每個交易日都要有那檔的 flow 才算（缺一天不補 0，那檔就不算，D-206 §二「資料缺不猜」）。"""
    n = len(sessions)
    f = (
        flows.filter(pl.col("date").is_in(sessions))
        .group_by("stock_id")
        .agg(pl.col("net").sum(), pl.len().alias("_n"))
    )
    v = (
        volume.filter(pl.col("date").is_in(sessions))
        .group_by("stock_id")
        .agg(pl.col("volume").sum(), pl.len().alias("_m"))
    )
    j = f.join(v, on="stock_id").filter(
        (pl.col("_n") == n) & (pl.col("_m") == n) & (pl.col("volume") > 0)
    )
    return {
        str(r["stock_id"]): (float(r["net"]), float(r["net"]) / float(r["volume"]))
        for r in j.iter_rows(named=True)
    }


def with_pct(d: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float, float]]:
    p = percentile({k: v[1] for k, v in d.items()})
    return {k: (v[0], v[1], p[k]) for k, v in d.items()}


def industry_medians(
    val: dict[str, tuple[float | None, float | None, float | None]],
    industry: dict[str, str],
    min_peers: int,
) -> tuple[dict[str, float], dict[str, float]]:
    """產業的本益比、股價淨值比中位數（只用 > 0 的值；少於 min_peers 檔不算）。"""
    per: dict[str, list[float]] = {}
    pbr: dict[str, list[float]] = {}
    for sid, (pe, pb, _) in val.items():
        ind = industry.get(sid)
        if not ind:
            continue
        if pe is not None and pe > 0:
            per.setdefault(ind, []).append(pe)
        if pb is not None and pb > 0:
            pbr.setdefault(ind, []).append(pb)
    return (
        {k: statistics.median(v) for k, v in per.items() if len(v) >= min_peers},
        {k: statistics.median(v) for k, v in pbr.items() if len(v) >= min_peers},
    )


# ── 四個面向 ────────────────────────────────────────────────────────────────


def tech_items(
    sid: str,
    closes: list[float],
    volumes: list[float],
    m: Market,
    rules: PickScoreRules,
    high_days: int,
) -> list[Item]:
    """均線、漲幅、高低點、量價、波動。closes／volumes 舊到新，最後一筆＝當天。"""
    out: list[Item] = []
    c = closes[-1] if closes else None
    # 均線
    if c is None or len(closes) < max(rules.ma_days):
        out.append(Item("均線", 0, NO_DATA))
    else:
        above = [n for n in rules.ma_days if c >= sum(closes[-n:]) / n]
        names = "、".join(str(n) for n in rules.ma_days)
        if len(above) == len(rules.ma_days):
            out.append(Item("均線", 1, f"收盤站在 {names} 日均線之上"))
        elif not above:
            out.append(Item("均線", -1, f"收盤在 {names} 日均線之下"))
        else:
            out.append(Item("均線", 0, f"收盤只站上 {'、'.join(str(n) for n in above)} 日均線"))
    # 漲幅
    if sid in m.mom:
        r, p = m.mom[sid]
        word = "漲" if r >= 0 else "跌"
        out.append(
            Item(
                "漲幅", _band(p, rules), f"近 {rules.mom_days} 日{word} {_pct(abs(r))}，{_beat(p)}"
            )
        )
    else:
        out.append(Item("漲幅", 0, NO_DATA))
    # 高低點
    if c is None or len(closes) < 2:
        out.append(Item("高低點", 0, NO_DATA))
    else:
        hi = max(closes[-high_days:])
        gap = 1 - c / hi if hi > 0 else 1.0
        low = len(closes) >= rules.vol_days and c <= min(closes[-rules.vol_days :])
        if gap <= rules.near_high:
            out.append(
                Item("高低點", 1, "創 52 週新高" if c >= hi else f"離 52 週高點只差 {_pct(gap)}")
            )
        elif low:
            out.append(Item("高低點", -1, f"今天是 {rules.vol_days} 日新低"))
        else:
            out.append(Item("高低點", 0, f"離 52 週高點還差 {_pct(gap)}"))
    # 量價
    n = rules.vol_days
    if c is None or len(closes) < 2 or len(volumes) <= n or volumes[-1] <= 0:
        out.append(Item("量價", 0, NO_DATA))
    else:
        avg = sum(volumes[-1 - n : -1]) / n
        ratio = volumes[-1] / avg if avg > 0 else 0.0
        chg = c / closes[-2] - 1 if closes[-2] > 0 else 0.0
        burst = ratio >= rules.volume_burst
        pts = (1 if chg > 0 else -1 if chg < 0 else 0) if burst else 0
        move = "上漲" if chg > 0 else "下跌" if chg < 0 else "平盤"
        out.append(Item("量價", pts, f"今天{move}，成交量是前 {n} 日平均的 {ratio:.1f} 倍"))
    # 波動
    if sid in m.vol:
        _, p = m.vol[sid]
        cmp = (
            f"比全市場 {round((1 - p) * 100)}% 的股票穩"
            if p < 0.5
            else f"比全市場 {round(p * 100)}% 的股票大"
        )
        out.append(Item("波動", _band(p, rules, reverse=True), f"近 {rules.vol_days} 日波動{cmp}"))
    else:
        out.append(Item("波動", 0, NO_DATA))
    return out


def _streak(values: list[float]) -> int:
    if not values or values[-1] == 0:
        return 0
    sign = 1 if values[-1] > 0 else -1
    n = 0
    for v in reversed(values):
        if v * sign > 0:
            n += 1
        else:
            break
    return n * sign


def _flow_item(
    name: str, who: str, d: tuple[float, float, float] | None, rules: PickScoreRules
) -> Item:
    if d is None:
        return Item(name, 0, NO_DATA)
    net, ratio, p = d
    word = "買超" if net > 0 else "賣超" if net < 0 else "持平"
    pts = 1 if p >= rules.hi_pct and net > 0 else -1 if p <= rules.lo_pct and net < 0 else 0
    return Item(
        name,
        pts,
        f"{who}近 {rules.flow_days} 日{word} {_lots(net)}，占成交量 {_pct(abs(ratio))}，"
        + (
            f"買得比全市場 {round(p * 100)}% 的股票多"
            if net > 0
            else f"賣得比全市場 {round((1 - p) * 100)}% 的股票多"
        ),
    )


def chip_items(
    sid: str,
    trust: list[float],
    margin: list[float],
    closes: list[float],
    holders: list[tuple[dt.date, float]],
    m: Market,
    rules: PickScoreRules,
) -> list[Item]:
    """外資、投信、主力、融資、千張大戶。trust／margin／closes 舊到新；holders＝(週, 千張大戶比例) 舊到新。"""
    out = [_flow_item("外資", "外資", m.foreign.get(sid), rules)]
    s = _streak(trust)
    if not trust:
        out.append(Item("投信", 0, NO_DATA))
    elif abs(s) >= rules.trust_streak:
        out.append(
            Item("投信", 1 if s > 0 else -1, f"投信連續{'買超' if s > 0 else '賣超'} {abs(s)} 天")
        )
    else:
        out.append(Item("投信", 0, "投信沒有連續買賣"))
    out.append(_flow_item("主力", "主力（前 15 大券商）", m.main_force.get(sid), rules))
    n = rules.flow_days
    if len(margin) <= n or margin[-1 - n] <= 0 or len(closes) <= n or closes[-1 - n] <= 0:
        out.append(Item("融資", 0, NO_DATA))
    else:
        dm = margin[-1] / margin[-1 - n] - 1
        dp = closes[-1] / closes[-1 - n] - 1
        txt = f"融資近 {n} 日{'增加' if dm > 0 else '減少' if dm < 0 else '持平'} {_pct(abs(dm))}，股價{'漲' if dp >= 0 else '跌'} {_pct(abs(dp))}"
        pts = 1 if dm < 0 and dp > 0 else -1 if dm >= rules.margin_jump and dp < 0 else 0
        out.append(Item("融資", pts, txt))
    if len(holders) < 2:
        out.append(Item("千張大戶", 0, NO_DATA))
    else:
        diff = (holders[-1][1] - holders[-2][1]) * 100
        pts = 1 if diff >= rules.holders_pp else -1 if diff <= -rules.holders_pp else 0
        out.append(
            Item(
                "千張大戶",
                pts,
                f"千張大戶持股 {_pct(holders[-1][1])}，比前一週{'多' if diff >= 0 else '少'} {abs(diff):.1f} 個百分點",
            )
        )
    return out


def fund_items(
    sid: str,
    revenue: list[tuple[str, float | None]],
    val: tuple[float | None, float | None, float | None] | None,
    m: Market,
    rules: PickScoreRules,
) -> list[Item]:
    """月營收、營收趨勢、本益比、股價淨值比、殖利率。revenue＝(年月, 年增率) 新到舊。"""
    out: list[Item] = []
    rev = [(ym, y) for ym, y in revenue if y is not None]
    if not rev:
        out.append(Item("月營收", 0, NO_DATA))
    else:
        ym, y = rev[0]
        pts = 1 if y >= rules.rev_hot else -1 if y <= -rules.rev_cold else 0
        out.append(
            Item("月營收", pts, f"{int(ym[5:7])} 月營收年{'增' if y >= 0 else '減'} {_pct(abs(y))}")
        )
    k = rules.rev_trend_months
    if len(revenue) < k or any(y is None for _, y in revenue[:k]):
        out.append(Item("營收趨勢", 0, NO_DATA))
    else:
        ys = [float(y) for _, y in revenue[:k] if y is not None]
        if all(y > 0 for y in ys):
            out.append(Item("營收趨勢", 1, f"營收連 {k} 個月年增"))
        elif all(y < 0 for y in ys):
            out.append(Item("營收趨勢", -1, f"營收連 {k} 個月年減"))
        else:
            out.append(Item("營收趨勢", 0, f"近 {k} 個月營收有增有減"))
    ind = m.industry.get(sid)
    pe, pb, dy = val if val is not None else (None, None, None)
    for name, v, med, unit in (
        ("本益比", pe, m.per_median.get(ind or ""), "倍"),
        ("股價淨值比", pb, m.pbr_median.get(ind or ""), "倍"),
    ):
        if val is None:
            out.append(Item(name, 0, NO_DATA))
        elif name == "本益比" and (v is None or v <= 0):
            out.append(Item(name, -1, "沒有本益比（可能虧損）"))
        elif v is None or v <= 0 or med is None:
            out.append(
                Item(
                    name, 0, NO_DATA if v is None else f"{name} {v:.1f} {unit}（同產業太少，不比）"
                )
            )
        else:
            pts = 1 if v <= med * rules.cheap else -1 if v >= med * rules.dear else 0
            out.append(Item(name, pts, f"{name} {v:.1f} {unit}，同產業中位數 {med:.1f} {unit}"))
    if dy is None:
        out.append(Item("殖利率", 0, NO_DATA))
    elif dy <= 0:
        out.append(Item("殖利率", -1, "沒有配息（殖利率 0）"))
    elif sid not in m.dy:
        out.append(Item("殖利率", 0, f"殖利率 {_pct(dy)}"))
    else:
        p = m.dy[sid][1]
        out.append(
            Item(
                "殖利率",
                1 if p >= rules.hi_pct else 0,
                f"殖利率 {_pct(dy)}，高過全市場 {round(p * 100)}% 的股票",
            )
        )
    return out


def news_items(
    disposition: list[str],
    attention: bool,
    insider: int,
    insider_days: int,
    announcements: list[str],
    events: list[str],
    rules: PickScoreRules,
) -> list[Item]:
    """處置／注意、內部人、重大訊息利多、利空、行事曆（行事曆只顯示不計分）。"""
    out: list[Item] = []
    if disposition or attention:
        out.append(
            Item(
                "處置／注意",
                -1,
                "；".join([*disposition, *(["今天是注意股"] if attention else [])]),
            )
        )
    else:
        out.append(Item("處置／注意", 0, "不是處置股或注意股"))
    if insider > 0:
        out.append(Item("內部人", -1, f"近 {insider_days} 天有 {insider} 筆內部人申報轉讓持股"))
    else:
        out.append(Item("內部人", 0, "近期沒有內部人申報轉讓"))
    good = [a for a in announcements if any(w in a for w in rules.good_news)]
    bad = [a for a in announcements if any(w in a for w in rules.bad_news)]
    out.append(
        Item("重大訊息利多", 1, f"公告：{good[0]}")
        if good
        else Item("重大訊息利多", 0, "近期公告沒有利多字眼")
    )
    out.append(
        Item("重大訊息利空", -1, f"公告：{bad[0]}")
        if bad
        else Item("重大訊息利空", 0, "近期公告沒有利空字眼")
    )
    out.append(Item("行事曆", 0, "、".join(events) if events else "近期沒有法說會或除權息"))
    return out


# ── 讀 PG：當天的全市場基準 ──────────────────────────────────────────────────


def load_market(
    conn: Any, schema: str, day: dt.date, rules: PickScoreRules
) -> tuple[Market, list[str]]:
    """day（含）以前的全市場基準；回 (Market, 缺了哪類資料)。只讀不寫。"""
    s = f'"{schema}"'
    errors: list[str] = []
    span = max(rules.mom_days, rules.vol_days) + 1

    def read(table: str, sql: str, params: dict[str, Any]) -> pl.DataFrame | None:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",))
            if cur.fetchone()[0] is None:
                errors.append(f"缺 {table}，相關小項記資料不足")
                return None
        return pl.read_database(
            sql, conn, execute_options={"params": params}, infer_schema_length=None
        )

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT date FROM {s}.prices_daily WHERE date <= %s ORDER BY date DESC LIMIT %s",
            (day, span),
        )
        sess = sorted(r[0] for r in cur.fetchall())
    flow_sess = sess[-rules.flow_days :]
    px = read(
        "prices_daily",
        f"SELECT date, stock_id, close, volume FROM {s}.prices_daily WHERE date >= %(a)s AND date <= %(d)s "
        "AND stock_id ~ %(p)s AND close IS NOT NULL",
        {"a": sess[0] if sess else day, "d": day, "p": rules.stock_pattern},
    )
    mom: dict[str, float] = {}
    vol: dict[str, float] = {}
    volume = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.Utf8, "volume": pl.Float64})
    if px is not None and not px.is_empty():
        mom, vol = price_stats(
            px.select("stock_id", "date", pl.col("close").cast(pl.Float64)), rules
        )
        volume = px.select("date", "stock_id", pl.col("volume").cast(pl.Float64))
    mp, vp = percentile(mom), percentile(vol)

    def flows(table: str, col: str) -> dict[str, tuple[float, float, float]]:
        df = read(
            table,
            f"SELECT date, stock_id, {col}::double precision AS net FROM {s}.{table} "
            "WHERE date = ANY(%(d)s) AND stock_id ~ %(p)s AND " + col + " IS NOT NULL",
            {"d": flow_sess, "p": rules.stock_pattern},
        )
        if df is None or df.is_empty() or len(flow_sess) < rules.flow_days:
            if df is not None:
                errors.append(f"{table} 近 {rules.flow_days} 個交易日沒有資料")
            return {}
        have = set(df["date"].to_list())
        missing = [d for d in flow_sess if d not in have]
        if missing:
            errors.append(f"{table} 缺 {', '.join(map(str, missing))}，相關小項記資料不足")
            return {}
        return with_pct(flow_ratio(df, volume, flow_sess))

    val_df = read(
        "stock_valuation",
        f"SELECT DISTINCT ON (stock_id) stock_id, per, pbr, dividend_yield FROM {s}.stock_valuation "
        "WHERE date <= %(d)s AND date > %(a)s AND stock_id ~ %(p)s ORDER BY stock_id, date DESC",
        {"d": day, "a": day - dt.timedelta(days=14), "p": rules.stock_pattern},
    )
    val: dict[str, tuple[float | None, float | None, float | None]] = {}
    if val_df is not None:
        for r in val_df.iter_rows():
            val[str(r[0])] = tuple(None if x is None else float(x) for x in r[1:])  # type: ignore[assignment]
    meta = read("stock_meta", f"SELECT stock_id, industry FROM {s}.stock_meta", {})
    industry = {str(a): str(b) for a, b in meta.iter_rows() if b} if meta is not None else {}
    per_med, pbr_med = industry_medians(val, industry, rules.min_peers)
    dy = {k: v[2] for k, v in val.items() if v[2] is not None}
    dyp = percentile(dy)
    return (
        Market(
            mom={k: (mom[k], mp[k]) for k in mom},
            vol={k: (vol[k], vp[k]) for k in vol},
            foreign=flows("institutional_daily", "foreign_net"),
            main_force=flows("main_force_daily", "net_shares"),
            dy={k: (dy[k], dyp[k]) for k in dy},
            industry=industry,
            per_median=per_med,
            pbr_median=pbr_med,
        ),
        errors,
    )
