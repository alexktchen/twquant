"""每日選股看板的 HTML 版（T-329）：把 `pick_board.json` 畫成一頁可以直接打開的報告。

純函數：輸入是已存好的看板 payload（`reporting/pick_board.py`）與各股的收盤／漲幅，輸出 HTML 字串。
頁面上的每個數字都來自這兩個輸入，這裡不另算、不讀 PG；落地由 `save_board_html` 與 CLI 負責。
漲跌顏色照台股慣例：紅漲綠跌。
"""

from __future__ import annotations

import datetime as dt
import html
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

HTML_FILE = "pick_board.html"

# 各股價格資訊的鍵：收盤、當日漲幅、近 N 日漲幅（N＝頁面「近 N 日上榜」的視窗）。缺的放 None。
PRICE_KEYS = ("close", "change_1d", "change_window")
PriceInfo = Mapping[str, float | None]


def price_changes(
    closes: Mapping[str, Mapping[dt.date, float]],
    *,
    as_of: dt.date,
    prev_session: dt.date | None,
    window_start: dt.date | None,
) -> dict[str, dict[str, float | None]]:
    """由各股的收盤價算出頁面要的三個數：收盤、當日漲幅、近 N 日漲幅。

    `closes[sid][date]`＝未還原收盤。當日漲幅＝as_of 收盤 ÷ 前一交易日收盤 − 1；
    近 N 日漲幅＝as_of 收盤 ÷ 視窗第一天收盤 − 1。任一端沒有價格就是 None（頁面顯示「—」），
    不用 0 代替。
    """
    out: dict[str, dict[str, float | None]] = {}
    for sid, by_day in closes.items():
        close = by_day.get(as_of)
        prev = by_day.get(prev_session) if prev_session is not None else None
        first = by_day.get(window_start) if window_start is not None else None
        out[sid] = {
            "close": close,
            "change_1d": (close / prev - 1.0) if close is not None and prev else None,
            "change_window": (close / first - 1.0) if close is not None and first else None,
        }
    return out


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _md(date: str) -> str:
    """'2026-10-08' → '10/8'。"""
    _, m, d = date.split("-")
    return f"{int(m)}/{int(d)}"


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    if v == 0:  # −0.0 也印成 +0.0%
        v = 0.0
    return f"{v * 100:+.1f}%"


def _pct_class(v: float | None) -> str:
    if v is None:
        return "flat"
    return "up" if v > 0 else "down" if v < 0 else "flat"


def _pct_cell(v: float | None) -> str:
    return f'<td class="num {_pct_class(v)}">{_pct(v)}</td>'


def _close_cell(info: PriceInfo | None) -> str:
    close = info.get("close") if info else None
    return f'<td class="num">{"—" if close is None else f"{close:,.2f}"}</td>'


def _sid_of(x: object) -> str:
    return x if isinstance(x, str) else str(x["stock_id"])  # type: ignore[index]


class _Names:
    def __init__(self, stocks: Mapping[str, Mapping[str, str]]) -> None:
        self._stocks = stocks

    def name(self, sid: str) -> str:
        return str(self._stocks.get(sid, {}).get("name") or "?")

    def industry(self, sid: str) -> str:
        return str(self._stocks.get(sid, {}).get("industry") or "")

    def cell(self, sid: str) -> str:
        return f'<td class="stock"><span class="sid">{_e(sid)}</span>{_e(self.name(sid))}</td>'

    def inline(self, sid: str) -> str:
        return f"{_e(self.name(sid))}<span class='sid'>{_e(sid)}</span>"


def _facts(s: Mapping[str, Any], n_holdings: int) -> str:
    if s["kind"] == "rebalance":
        rhythm = "每月第一個交易日換股，期間不動"
        rule = "抱到下個月換股，不設停損停利"
    else:
        rhythm = f"每檔抱滿 {s['hold_sessions']} 個交易日就賣，空出位子才補"
        rule = f"{s['slots']} 個位子，推薦名單有排序"
    facts = [
        ("位子", f"{s['slots']} 檔"),
        ("目前持股", f"{n_holdings} 檔"),
        ("換股節奏", rhythm),
        ("持有規則", rule),
    ]
    return "".join(
        f'<div class="fact"><div class="k">{_e(k)}</div><div class="v">{_e(v)}</div></div>'
        for k, v in facts
    )


def _plan(s: Mapping[str, Any], names: _Names, last_holdings: Sequence[Mapping[str, Any]]) -> str:
    nxt = s["next"]
    buys: list[str] = [str(x) for x in nxt["buy"]]
    sells: list[Mapping[str, Any]] = list(nxt["sell"])
    if nxt["rebalance"]:
        return (
            f"<p>下一個交易日<strong>換股</strong>：</p><ul>"
            f"<li><strong>買進 {len(buys)} 檔</strong>："
            + ("、".join(names.inline(b) for b in buys) or "—")
            + "</li>"
            f"<li><strong>賣出 {len(sells)} 檔</strong>："
            + ("、".join(names.inline(str(x["stock_id"])) for x in sells) or "—")
            + "</li>"
            "</ul>"
        )
    if s["kind"] == "rebalance" and not buys and not sells:
        return "<p>下一個交易日<strong>不買不賣</strong>。持股抱到下個月的第一個交易日再換。</p>"
    if not buys and not sells:
        return "<p>下一個交易日<strong>不買不賣</strong>：位子滿、沒有一檔抱滿。</p>"
    time_stops = [x for x in sells if x["reason"] == "time_stop"]
    others = [x for x in sells if x["reason"] != "time_stop"]
    items = []
    if time_stops:
        items.append(
            f"<li><strong>開盤賣出 {len(time_stops)} 檔</strong>（抱滿 {s['hold_sessions']} 個交易日）："
            + "、".join(names.inline(str(x["stock_id"])) for x in time_stops)
            + "</li>"
        )
    if others:
        items.append(
            f"<li>其他原因賣出 {len(others)} 檔："
            + "、".join(f"{names.inline(str(x['stock_id']))}（{_e(x['reason'])}）" for x in others)
            + "</li>"
        )
    buy_line = f"<li><strong>買進 {len(buys)} 檔</strong>"
    if buys:
        buy_line += "：" + "、".join(names.inline(b) for b in buys)
    elif sells:
        buy_line += "。位子要等賣掉才算空出來（當天可能跌停賣不掉），新股最早<strong>再下一個交易日</strong>開盤才買，用的是下一個交易日收盤後的名單；下面的推薦名單只是預覽。"
    buy_line += "</li>"
    items.append(buy_line)
    selling = {str(x["stock_id"]) for x in sells}
    hold_n = s["hold_sessions"]
    if hold_n is not None and time_stops:
        later = [
            h
            for h in last_holdings
            if str(h["stock_id"]) not in selling and int(h["held_sessions"]) == int(hold_n) - 1
        ]
        if later:
            items.append(
                "<li>"
                + "、".join(
                    f"{names.inline(str(h['stock_id']))}（{_md(str(h['entry_date']))} 進場）"
                    for h in later
                )
                + " 晚一天抱滿，再下一個交易日開盤賣。</li>"
            )
    return "<p>下一個交易日的計畫：</p><ul>" + "".join(items) + "</ul>"


def _holdings_table(
    code: str, last: Mapping[str, Any], names: _Names, prices: Mapping[str, PriceInfo]
) -> str:
    holdings = sorted(
        last["holdings"], key=lambda h: (-(h.get("change") or 0.0), str(h["stock_id"]))
    )
    rows = []
    for h in holdings:
        sid = str(h["stock_id"])
        info = prices.get(sid)
        rows.append(
            f'<tr data-row="holding:{_e(code)}">'
            + names.cell(sid)
            + f'<td class="muted">{_e(names.industry(sid))}</td>'
            + _close_cell(info)
            + _pct_cell(info.get("change_1d") if info else None)
            + f'<td class="num">{_md(str(h["entry_date"]))}</td>'
            + f'<td class="num">{int(h["held_sessions"])}</td>'
            + _pct_cell(h.get("change"))
            + "</tr>"
        )
    n_up = sum(1 for h in holdings if (h.get("change") or 0.0) > 0)
    n_down = sum(1 for h in holdings if (h.get("change") or 0.0) < 0)
    return (
        f"<p class='lede'>共 {len(holdings)} 檔：{n_up} 檔在成本之上、{n_down} 檔在成本之下、"
        f"{len(holdings) - n_up - n_down} 檔持平。「進場至今」是買進成交價到 {_md(str(last['date']))} 收盤，不含股利與費用；紅漲綠跌。</p>"
        "<div class='scroll'><table>"
        "<thead><tr><th>股票</th><th>產業</th><th class='num'>收盤</th><th class='num'>當日</th>"
        "<th class='num'>進場日</th><th class='num'>持有<br>交易日</th><th class='num'>進場至今</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _picks_table(
    s: Mapping[str, Any],
    shown: Sequence[Mapping[str, Any]],
    names: _Names,
    prices: Mapping[str, PriceInfo],
) -> str:
    last = shown[-1]
    picks = last.get("picks")
    if not picks:
        return f"<p class='lede'>{_md(str(last['date']))} <strong>模型沒有打分</strong>，沒有推薦名單。</p>"
    code = str(s["code"])
    member = [(str(d["date"]), {str(p["stock_id"]) for p in (d.get("picks") or [])}) for d in shown]
    rows = []
    for p in picks:
        sid = str(p["stock_id"])
        marks = "".join(
            f"<i class='{'on' if sid in ids else ''}' title='{_md(day)}'></i>"
            for day, ids in member
        )
        n = sum(sid in ids for _, ids in member)
        info = prices.get(sid)
        chips = []
        if p["held"]:
            chips.append("<span class='chip held'>持有中</span>")
        if p["new"]:
            chips.append("<span class='chip new'>新上榜</span>")
        rows.append(
            "<tr>"
            f"<td class='num rank'>{int(p['rank'])}</td>"
            + names.cell(sid)
            + f"<td class='muted'>{_e(names.industry(sid))}</td>"
            + _close_cell(info)
            + _pct_cell(info.get("change_1d") if info else None)
            + _pct_cell(info.get("change_window") if info else None)
            + f'<td class="marks" data-marks="{_e(code)}:{_e(sid)}" data-count="{n}/{len(member)}">'
            f"<span class='dots'>{marks}</span><span class='cnt'>{n}/{len(member)}</span></td>"
            + f'<td class="tags" data-chips="{_e(code)}:{_e(sid)}" data-held="{int(bool(p["held"]))}" data-new="{int(bool(p["new"]))}">'
            + " ".join(chips)
            + "</td></tr>"
        )
    order = "名次是模型分數的順序。" if s["picks_ordered"] else "名單不分先後（名次只是代號排序）。"
    first, lastday = _md(member[0][0]), _md(member[-1][0])
    n_days = len(member)
    return (
        f"<p class='lede'>{_md(str(last['date']))} 收盤後模型的前 {len(picks)} 名。{order}"
        f"「近 {n_days} 日」指 {first}～{lastday} 這 {n_days} 個交易日：上榜是進前 {len(picks)} 名的天數，"
        f"漲幅是 {first} 收盤到 {lastday} 收盤。上榜天數只是參考，不代表模型更有把握。</p>"
        "<div class='scroll'><table class='picks'>"
        f"<thead><tr><th class='num'>#</th><th>股票</th><th>產業</th><th class='num'>收盤</th><th class='num'>當日</th>"
        f"<th class='num'>近 {n_days} 日<br>漲幅</th><th>近 {n_days} 日<br>上榜</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _activity(shown: Sequence[Mapping[str, Any]], names: _Names) -> str:
    acts = []
    for d in shown:
        ent = [_sid_of(x) for x in d["entered"]]
        ext = [_sid_of(x) for x in d["exited"]]
        if not ent and not ext:
            continue
        parts = []
        if ent:
            parts.append(
                f"<div><span class='lbl up'>買進 {len(ent)} 檔</span>"
                + "、".join(names.inline(x) for x in ent)
                + "</div>"
            )
        if ext:
            parts.append(
                f"<div><span class='lbl down'>賣出 {len(ext)} 檔</span>"
                + "、".join(names.inline(x) for x in ext)
                + "</div>"
            )
        acts.append(
            f"<div class='act'><div class='d'>{_md(str(d['date']))}</div><div class='body'>{''.join(parts)}</div></div>"
        )
    if not acts:
        span = f"{_md(str(shown[0]['date']))}～{_md(str(shown[-1]['date']))}"
        holdings = shown[-1]["holdings"]
        since = (
            f"位子從 {_md(min(str(h['entry_date']) for h in holdings))} 起就是滿的。"
            if holdings
            else "手上沒有持股。"
        )
        acts.append(
            f"<div class='act'><div class='d'>{span}</div><div class='body muted'>沒有進出。{since}</div></div>"
        )
    return "".join(acts)


def _section(
    s: Mapping[str, Any], names: _Names, prices: Mapping[str, PriceInfo], window_sessions: int
) -> str:
    days: list[Mapping[str, Any]] = list(s["days"])
    if not days:
        raise ValueError(f"{s['code']}：看板沒有任何交易日")
    shown = days[-window_sessions:]
    last = shown[-1]
    code = str(s["code"])
    return (
        f'<section id="{_e(code.lower())}">'
        f'<div class="sec-head"><h2>{_e(s["name"])}</h2><p class="summary">{_e(s["summary"])}</p></div>'
        f'<div class="facts">{_facts(s, len(last["holdings"]))}</div>'
        f'<div class="plan"><h3>下一個交易日</h3>{_plan(s, names, last["holdings"])}</div>'
        f"<h3>目前持股</h3>{_holdings_table(code, last, names, prices)}"
        f"<h3>{_md(str(last['date']))} 推薦名單</h3>{_picks_table(s, shown, names, prices)}"
        f"<h3>近期進出</h3><div class='acts'>{_activity(shown, names)}</div>"
        "</section>"
    )


_STYLE = """
:root{--bg:#0a0e17;--panel:#101724;--panel2:#0d1320;--line:#1f2a3c;--fg:#e6ebf4;--muted:#8893a7;--accent:#4c8dff;--up:#f0484e;--down:#22b573;--warn:#f5a524;--chip:#16203a;color-scheme:dark}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#f4f6fa;--panel:#fff;--panel2:#eef2f8;--line:#d9dfea;--fg:#111827;--muted:#5b6578;--accent:#2f6fe4;--up:#d7383e;--down:#1a9a62;--warn:#b97400;--chip:#e8eefc;color-scheme:light}}
:root[data-theme="light"]{--bg:#f4f6fa;--panel:#fff;--panel2:#eef2f8;--line:#d9dfea;--fg:#111827;--muted:#5b6578;--accent:#2f6fe4;--up:#d7383e;--down:#1a9a62;--warn:#b97400;--chip:#e8eefc;color-scheme:light}
body{margin:0;background:var(--bg);color:var(--fg);font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;font-size:15px;line-height:1.65}
.wrap{max-width:960px;margin:0 auto;padding-block:28px 56px;padding-inline:16px}
h1,h2{font-family:"Noto Serif TC","Noto Sans TC",serif;text-wrap:balance;margin:0}
h1{font-size:30px;font-weight:700}h2{font-size:24px;font-weight:700}
h3{font-size:15px;font-weight:700;margin:28px 0 8px}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
header{display:flex;flex-wrap:wrap;gap:16px 28px;align-items:flex-end;justify-content:space-between;padding-bottom:18px;border-bottom:1px solid var(--line)}
header .meta{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}header .meta b{color:var(--fg);font-weight:500}
.note{margin:16px 0 0;padding:12px 14px;background:var(--panel2);border-left:3px solid var(--accent);border-radius:0 6px 6px 0;font-size:14px}
section{margin-top:40px}.sec-head{display:grid;gap:4px}.summary{margin:0;color:var(--muted);max-width:66ch}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:16px}
.fact{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.fact .k{font-size:12px;color:var(--muted);letter-spacing:.04em}.fact .v{font-size:15px;font-weight:500;margin-top:2px}
.plan{margin-top:16px;padding:14px 16px;background:var(--panel);border:1px solid var(--line);border-radius:8px}
.plan h3{margin:0 0 6px;color:var(--accent)}.plan p,.plan ul{margin:6px 0}.plan ul{padding-left:20px}.plan li{margin:4px 0}
.lede{color:var(--muted);font-size:14px;margin:0 0 8px;max-width:72ch}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;white-space:nowrap}
th{font-size:12px;font-weight:500;color:var(--muted);letter-spacing:.04em;border-bottom-color:var(--muted)}
th.num,td.num{text-align:right}td.rank{color:var(--muted);width:2.5em}td.stock{font-weight:500}
.sid{display:inline-block;font-size:12px;color:var(--muted);font-weight:400;margin-left:4px}td.stock .sid{margin:0 6px 0 0}
.muted{color:var(--muted)}.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--muted)}
.marks{white-space:nowrap}.dots{display:inline-flex;gap:3px;vertical-align:middle;margin-right:8px}
.dots i{display:inline-block;width:11px;height:11px;border-radius:2px;background:var(--line)}.dots i.on{background:var(--accent)}
.cnt{color:var(--muted);font-size:13px}
.chip{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--chip);color:var(--fg);border:1px solid var(--line)}
.chip.held{border-color:var(--accent);color:var(--accent)}.chip.new{border-color:var(--warn);color:var(--warn)}
.acts{display:grid;gap:10px}.act{display:grid;grid-template-columns:72px 1fr;gap:12px;padding:10px 12px;background:var(--panel);border:1px solid var(--line);border-radius:8px}
.act .d{font-variant-numeric:tabular-nums;font-weight:500}.act .body{display:grid;gap:6px;font-size:14px}.lbl{display:inline-block;font-weight:500;margin-right:8px}
.foot{margin-top:44px;padding-top:18px;border-top:1px solid var(--line);font-size:14px;color:var(--muted)}
.foot code{font-size:13px;background:var(--panel2);padding:1px 5px;border-radius:4px}
@media (max-width:520px){h1{font-size:26px}h2{font-size:21px}.act{grid-template-columns:1fr;gap:4px}}
"""


def render_board_html(
    board: Mapping[str, Any],
    *,
    window_sessions: int,
    prices: Mapping[str, PriceInfo] | None = None,
) -> str:
    """把看板 payload 畫成完整的 HTML 頁（含 <!doctype>，可直接用瀏覽器打開）。

    `window_sessions`：推薦名單「近 N 日上榜／漲幅」看最後幾個交易日（少於 N 天就看有的）。
    `prices`：各股的收盤、當日漲幅、近 N 日漲幅（見 `price_changes`）；沒給或缺的顯示「—」。
    同一輸入永遠產生同一字串。
    """
    if window_sessions < 1:
        raise ValueError(f"window_sessions 須 ≥ 1，得到 {window_sessions}")
    names = _Names(board["stocks"])
    px: Mapping[str, PriceInfo] = prices or {}
    sections = "".join(_section(s, names, px, window_sessions) for s in board["strategies"])
    gen = dt.datetime.fromisoformat(str(board["generated_at"]))
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        "<title>每日選股看板</title>"
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@600;700&display=swap">'
        f"<style>{_STYLE}</style></head><body><div class='wrap'>"
        "<header><div><div class='eyebrow'>大盤精選 · 潛伏飆股</div><h1>每日選股看板</h1></div>"
        f"<div class='meta'>資料到 <b>{_e(board['as_of'])}</b> 收盤<br>看板產生於 {gen:%Y-%m-%d %H:%M}</div></header>"
        f"<p class='note'>持股是系統從 {_e(board['replay_start'])} 起用回測引擎重播出來的名單，不是任何帳戶的實際部位；"
        "這頁不顯示整體報酬。</p>"
        f"{sections}"
        "<div class='foot'>來源 <code>data/pick_board/pick_board.json</code>（每日排程產生）；收盤與漲幅來自供應層 <code>prices_daily</code>。"
        "這頁的每個數字都由程式從資料讀出。</div>"
        "</div></body></html>"
    )


def save_board_html(data_dir: Path, page: str) -> Path:
    """寫到 `<data_dir>/reports/pick_board.html`（每天覆寫；先寫暫存檔再換名，不留半成品）。"""
    root = data_dir / "reports"
    root.mkdir(parents=True, exist_ok=True)
    path = root / HTML_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(path)
    return path
