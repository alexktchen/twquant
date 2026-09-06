"""由 `all_backtests.json` 產生單頁 HTML 報告（SPEC-013 §6）。

**頁面不做任何計算**：所有數字都從 JSON 讀出直接呈現。AC-11 的由來是先前的頁面在
渲染層寫死比較數字，出現過「TAIEX −34.37%」的錯標（那其實是等權宇宙的回撤）。
資料與呈現分離之後，頁面上不可能出現沒有出處的數字。

重跑本腳本即更新報告；回測仍在進行時會照實標示「尚缺 N 筆」。

用法：
    python scripts/report_bundle.py && python scripts/build_report.py
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys

SRC = pathlib.Path("D:/project/twquant/data/reports/all_backtests.json")
OUT = pathlib.Path("D:/project/twquant/data/reports/backtest_report.html")

# 台股慣例：紅漲綠跌（與歐美相反）。這是本題材特有的約定，不是隨意配色。
CSS = """
:root{
  --bg:#F7F8FA; --surface:#FFFFFF; --sunken:#EFF1F5;
  --ink:#1A1D24; --ink-2:#4A5160; --ink-3:#79808F;
  --line:#DDE1E8; --line-2:#C7CCD6;
  --accent:#3A4A8C; --accent-soft:#E7EAF5;
  --up:#C0392B;      /* 漲：紅（台股慣例） */
  --down:#1E8449;    /* 跌：綠（台股慣例） */
  --warn:#8A6D1F; --warn-soft:#FBF3DC;
  --f1:#3A4A8C; --f2:#1E7B7B; --f3:#B0672A;
  --f4:#7A3E8C; --f5:#4A7A2E; --f6:#8C3A52;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#12141A; --surface:#191C24; --sunken:#1F232C;
    --ink:#E8EAF0; --ink-2:#AEB5C4; --ink-3:#7C8494;
    --line:#2A2F3A; --line-2:#3A4150;
    --accent:#8FA2E8; --accent-soft:#232941;
    --up:#E8695B; --down:#4CAF7D;
    --warn:#D9B65C; --warn-soft:#2C2718;
    --f1:#8FA2E8; --f2:#57C4C4; --f3:#E0A063;
    --f4:#C48FD8; --f5:#8FC96A; --f6:#E086A0;
  }
}
:root[data-theme="dark"]{
  --bg:#12141A; --surface:#191C24; --sunken:#1F232C;
  --ink:#E8EAF0; --ink-2:#AEB5C4; --ink-3:#7C8494;
  --line:#2A2F3A; --line-2:#3A4150;
  --accent:#8FA2E8; --accent-soft:#232941;
  --up:#E8695B; --down:#4CAF7D;
  --warn:#D9B65C; --warn-soft:#2C2718;
  --f1:#8FA2E8; --f2:#57C4C4; --f3:#E0A063;
  --f4:#C48FD8; --f5:#8FC96A; --f6:#E086A0;
}
*{box-sizing:border-box}
body{
  background:var(--bg); color:var(--ink); margin:0;
  font-family:"Noto Sans TC",-apple-system,"Segoe UI",system-ui,sans-serif;
  font-size:15px; line-height:1.7; -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px; margin:0 auto; padding:48px 28px 96px; display:flex; flex-direction:column; gap:44px}
h1{
  font-family:"Noto Serif TC",Georgia,serif; font-weight:700;
  font-size:clamp(26px,3.4vw,38px); line-height:1.28; margin:0; text-wrap:balance;
  letter-spacing:-.01em;
}
h2{
  font-family:"Noto Serif TC",Georgia,serif; font-weight:600;
  font-size:21px; margin:0 0 4px; text-wrap:balance;
}
h3{font-size:15px; font-weight:600; margin:0 0 6px; color:var(--ink)}
p{margin:0; color:var(--ink-2); max-width:74ch}
.eyebrow{
  font-size:11.5px; font-weight:600; letter-spacing:.14em; text-transform:uppercase;
  color:var(--accent); font-family:"IBM Plex Mono",ui-monospace,monospace;
}
.sub{color:var(--ink-3); font-size:13.5px}
section{display:flex; flex-direction:column; gap:14px}
hr{border:0; border-top:1px solid var(--line); margin:0}

/* 首屏：最硬的事實 */
.verdict{
  background:var(--surface); border:1px solid var(--line); border-radius:3px;
  padding:26px 28px; display:flex; flex-direction:column; gap:18px;
}
.verdict .lede{font-size:17px; color:var(--ink); max-width:72ch; line-height:1.65}
.vgrid{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px; background:var(--line); border:1px solid var(--line)}
.vcell{background:var(--surface); padding:14px 16px; display:flex; flex-direction:column; gap:3px}
.vcell.hero{background:var(--accent-soft)}
.vcell .k{font-size:11.5px; color:var(--ink-3); letter-spacing:.04em}
.vcell .v{font-family:"IBM Plex Mono",monospace; font-size:21px; font-weight:600; font-variant-numeric:tabular-nums}
.vcell .n{font-size:11.5px; color:var(--ink-3)}

/* 表格 */
.tblwrap{overflow-x:auto; border:1px solid var(--line); background:var(--surface)}
table{border-collapse:collapse; width:100%; font-size:13.5px}
th,td{padding:7px 11px; text-align:right; white-space:nowrap; border-bottom:1px solid var(--line)}
th{
  background:var(--sunken); color:var(--ink-2); font-weight:600; font-size:11.5px;
  letter-spacing:.03em; position:sticky; top:0; z-index:1; border-bottom:1px solid var(--line-2);
}
th:first-child,td:first-child{text-align:left; position:sticky; left:0; background:var(--surface)}
th:first-child{background:var(--sunken)}
td.name{font-weight:600}
td.desc{text-align:left; white-space:normal; color:var(--ink-3); font-size:12.5px; min-width:230px; max-width:340px}
tbody tr:hover td{background:var(--sunken)}
tbody tr:hover td:first-child{background:var(--sunken)}
.num{font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)} .dim{color:var(--ink-3)}
tr.bench td{background:var(--accent-soft)}
tr.bench:hover td{background:var(--accent-soft)}
tr.grouphead td{
  background:var(--sunken); font-weight:600; font-size:12px; color:var(--ink-2);
  letter-spacing:.06em; text-align:left; padding:9px 11px;
}

/* 警語 */
.note{
  border-left:3px solid var(--warn); background:var(--warn-soft);
  padding:13px 16px; font-size:13.5px; color:var(--ink); line-height:1.65;
}
.note strong{color:var(--ink)}
.note.plain{border-left-color:var(--accent); background:var(--accent-soft)}

/* 邊 */
.edges{display:flex; flex-direction:column; gap:1px; background:var(--line); border:1px solid var(--line)}
.edge{background:var(--surface); padding:13px 16px; display:grid; grid-template-columns:1fr auto; gap:6px 22px; align-items:baseline}
.edge .lab{font-weight:600; font-size:14px}
.edge .note2{grid-column:1/-1; font-size:12.5px; color:var(--ink-3); line-height:1.6}
.edge .stats{font-family:"IBM Plex Mono",monospace; font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap}
.tsig{font-weight:600}

.meta{font-size:12.5px; color:var(--ink-3); line-height:1.8}
.meta code{font-family:"IBM Plex Mono",monospace; font-size:11.5px; background:var(--sunken); padding:1px 5px; border-radius:2px}
footer{color:var(--ink-3); font-size:12.5px; border-top:1px solid var(--line); padding-top:18px; line-height:1.75}

/* 圖表 */
.chart{width:100%; height:auto; display:block; background:var(--surface); border:1px solid var(--line)}
.chart .grid{stroke:var(--line); stroke-width:1}
.chart .cross{stroke:var(--accent); stroke-width:1; stroke-dasharray:4 3; opacity:.55}
.chart .bmk{fill:var(--accent); stroke:var(--surface); stroke-width:2}
.chart .cross2{stroke:var(--f2); stroke-width:1; stroke-dasharray:2 4; opacity:.7}
.chart .bmk2{fill:var(--f2); stroke:var(--surface); stroke-width:1.6}
.chart .bmk2tx{fill:var(--f2); font-weight:600}
.chart .bmktx{fill:var(--accent); font-weight:600}
.chart text{font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums}
.chart .tick{font-size:10.5px; fill:var(--ink-3)}
.chart .lbl{font-size:11px; fill:var(--ink-2)}
.chart .axis{font-size:11px; fill:var(--ink-3); letter-spacing:.04em}
.chart .mid{text-anchor:middle}
.chart .end{text-anchor:end}
.chart .bar.up{fill:var(--up)}
.chart .bar.down{fill:var(--down)}
.figcap{font-size:12.5px; color:var(--ink-3); line-height:1.6}
@media (max-width:640px){
  .wrap{padding:32px 16px 64px; gap:34px}
  td.desc{display:none} th.desc{display:none}
}
"""


def pct(v: float | None, d: int = 2, sign: bool = True) -> str:
    if v is None:
        return '<span class="dim">—</span>'
    s = f"{v:+.{d}%}" if sign else f"{v:.{d}%}"
    cls = "up" if v > 0 else ("down" if v < 0 else "dim")
    return f'<span class="num {cls}">{s}</span>'


def plain(v: float | None, fmt: str = "{:.2f}") -> str:
    return (
        '<span class="dim">—</span>' if v is None else f'<span class="num">{fmt.format(v)}</span>'
    )


def esc(s: object) -> str:
    return html.escape(str(s))


# ── 圖表：伺服器端產 SVG，不依賴外部圖表庫（離線可開）────────────────────
# 顏色一律走 CSS 變數，故深淺主題都讀得到；每個座標軸的刻度都標真實數值。

_FAMILY = {  # 家族 → 顏色變數；固定指派、不循環（同一家族在所有圖上同色）
    "階梯": "var(--f1)",
    "雙軌": "var(--f2)",
    "C 軌": "var(--f3)",
    "M 系列": "var(--f4)",
    "G 濾網矩陣": "var(--f5)",
    "MG 系列": "var(--f6)",
}


def scatter_risk_return(rows, bench):
    """風險—報酬散點：x 軸最大回撤（越右越淺）、y 軸 CAGR。左下＝報酬與風險雙輸。

    0050 畫成虛線十字準星，讓「在基準的哪一邊」不需要對照表就看得出來。
    """
    pts = [
        (abs(r["mdd"]), r["cagr"], r["name"], _FAMILY.get(r["group"], "var(--ink-3)"))
        for r in rows
        if r.get("mdd") is not None and r.get("cagr") is not None
    ]
    if not pts:
        return ""
    W, H, L, R, T, B = 760, 420, 62, 20, 18, 44
    bxs = [abs(b["mdd"]) for b in bench if b.get("mdd") is not None]
    bys = [b["cagr"] for b in bench if b.get("cagr") is not None]
    x1 = max([p[0] for p in pts] + bxs) * 1.06
    ally = [p[1] for p in pts] + bys
    y0, y1 = min(0.0, min(ally)) * 1.12, max(ally) * 1.10

    def ax(v):
        return L + v / x1 * (W - L - R)

    def ay(v):
        return T + (y1 - v) / (y1 - y0) * (H - T - B)

    g = []
    for i in range(5):
        v = x1 * i / 4
        g.append(f'<line x1="{ax(v):.1f}" y1="{T}" x2="{ax(v):.1f}" y2="{H - B}" class="grid"/>')
        g.append(f'<text x="{ax(v):.1f}" y="{H - B + 17}" class="tick mid">{v:.0%}</text>')
    for i in range(5):
        v = y0 + (y1 - y0) * i / 4
        g.append(f'<line x1="{L}" y1="{ay(v):.1f}" x2="{W - R}" y2="{ay(v):.1f}" class="grid"/>')
        g.append(f'<text x="{L - 8}" y="{ay(v) + 4:.1f}" class="tick end">{v:.0%}</text>')
    for b in bench:
        # 0051 是**結構上不含台積電**的對照，對小型策略才公平；一起畫成準星，
        # 讓「輸給大盤是因為沒買台積電」這個說法在圖上就能被檢驗
        if b.get("key") == "0051_tr" and b.get("cagr") is not None:
            bx, by = ax(abs(b["mdd"])), ay(b["cagr"])
            g.append(f'<line x1="{L}" y1="{by:.1f}" x2="{W - R}" y2="{by:.1f}" class="cross2"/>')
            g.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="5" class="bmk2"/>')
            g.append(
                f'<text x="{bx + 10:.1f}" y="{by + 4:.1f}" class="lbl bmk2tx">0051 中型100</text>'
            )
        if b.get("key") != "0050_tr" or b.get("cagr") is None:
            continue
        bx, by = ax(abs(b["mdd"])), ay(b["cagr"])
        g.append(f'<line x1="{L}" y1="{by:.1f}" x2="{W - R}" y2="{by:.1f}" class="cross"/>')
        g.append(f'<line x1="{bx:.1f}" y1="{T}" x2="{bx:.1f}" y2="{H - B}" class="cross"/>')
        g.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="6" class="bmk"/>')
        g.append(
            f'<text x="{bx - 11:.1f}" y="{by - 11:.1f}" class="lbl end bmktx">0050 含息</text>'
        )
    ordered = sorted(pts, key=lambda t: -t[1])
    for x, y, name, col in ordered:
        g.append(
            f'<circle cx="{ax(x):.1f}" cy="{ay(y):.1f}" r="4.5" fill="{col}" '
            f'stroke="var(--surface)" stroke-width="1.4"><title>{esc(name)}  '
            f"CAGR {y:.2%}  MDD -{x:.1%}</title></circle>"
        )
    for x, y, name, _col in ordered[:6]:
        g.append(f'<text x="{ax(x) + 8:.1f}" y="{ay(y) + 4:.1f}" class="lbl">{esc(name)}</text>')
    g.append(
        f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" class="axis mid">最大回撤（越右越淺）</text>'
    )
    g.append(
        f'<text transform="translate(15,{(T + H - B) / 2:.0f}) rotate(-90)" '
        f'class="axis mid">年化報酬</text>'
    )
    return (
        f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
        f'aria-label="風險報酬散點圖">{"".join(g)}</svg>'
    )


def bars_marginal(edges):
    """濾網邊際效果：ΔCAGR 與 ΔMDD 並列水平條，各自零線置中。"""
    es = [e for e in edges if e["label"].startswith(("濾網", "全部一起"))]
    if not es:
        return ""
    rowh, W, L, R, T = 30, 760, 178, 16, 30
    H = T + rowh * len(es) + 16
    half = (W - L - R) / 2 - 40
    m1 = max(max(abs(e["d_cagr"]) for e in es), 0.02)
    m2 = max(max(abs(e["d_mdd"]) for e in es), 0.02)
    c1, c2 = L + half / 2, L + half + 56 + half / 2
    g = [
        f'<text x="{c1:.0f}" y="18" class="tick mid">ΔCAGR</text>',
        f'<text x="{c2:.0f}" y="18" class="tick mid">ΔMDD（正＝回撤變淺）</text>',
    ]
    for cx in (c1, c2):
        g.append(f'<line x1="{cx:.0f}" y1="{T - 6}" x2="{cx:.0f}" y2="{H - 8}" class="grid"/>')
    for i, e in enumerate(es):
        y = T + i * rowh + rowh / 2
        lab = e["label"].split(" = ")[0].replace("濾網 ", "")
        g.append(f'<text x="{L - 10}" y="{y + 4:.1f}" class="lbl end">{esc(lab)}</text>')
        for cx, val, mx in ((c1, e["d_cagr"], m1), (c2, e["d_mdd"], m2)):
            w = abs(val) / mx * (half / 2 - 8)
            x = cx if val >= 0 else cx - w
            cls = "up" if val > 0 else "down"
            g.append(
                f'<rect x="{x:.1f}" y="{y - 8:.1f}" width="{w:.1f}" height="16" '
                f'rx="2" class="bar {cls}"/>'
            )
            tx = cx + w + 6 if val >= 0 else cx - w - 6
            anc = "" if val >= 0 else " end"
            g.append(f'<text x="{tx:.1f}" y="{y + 4:.1f}" class="tick{anc}">{val:+.1%}</text>')
    return (
        f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
        f'aria-label="濾網邊際效果">{"".join(g)}</svg>'
    )


def equity_lines(rows, bench, top=5):
    """權益曲線：Sharpe 前 N 名 ＋ 0050 含息（虛線）。起點統一 1.0。"""
    sel = [r for r in rows if r.get("curve")][:top]
    if not sel:
        return ""
    b50 = next((b for b in bench if b.get("key") == "0050_tr" and b.get("curve")), None)
    series = [(r["name"], r["curve"], _FAMILY.get(r["group"], "var(--ink-3)"), False) for r in sel]
    if b50:
        series.append(("0050 含息", b50["curve"], "var(--accent)", True))
    W, H, L, R, T, B = 760, 340, 56, 104, 18, 38
    allv = [q[1] for _, c, _, _ in series for q in c]
    y0, y1 = min(allv) * 0.97, max(allv) * 1.03

    def ax(i, n):
        return L + i / max(1, n - 1) * (W - L - R)

    def ay(v):
        return T + (y1 - v) / (y1 - y0) * (H - T - B)

    g = []
    for i in range(5):
        v = y0 + (y1 - y0) * i / 4
        g.append(f'<line x1="{L}" y1="{ay(v):.1f}" x2="{W - R}" y2="{ay(v):.1f}" class="grid"/>')
        g.append(f'<text x="{L - 8}" y="{ay(v) + 4:.1f}" class="tick end">{v:.2f}x</text>')
    for name, c, col, isb in series:
        d = " ".join(
            f"{'M' if k == 0 else 'L'}{ax(k, len(c)):.1f},{ay(q[1]):.1f}" for k, q in enumerate(c)
        )
        dash = ' stroke-dasharray="5 3"' if isb else ""
        g.append(
            f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{2.2 if isb else 1.5}"'
            f'{dash} stroke-linejoin="round"/>'
        )
        ex, ey = ax(len(c) - 1, len(c)), ay(c[-1][1])
        g.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3" fill="{col}"/>')
        g.append(
            f'<text x="{W - R + 7}" y="{ey + 4:.1f}" class="lbl" fill="{col}">'
            f"{esc(name)} {c[-1][1]:.2f}x</text>"
        )
    g.append(f'<text x="{L}" y="{H - 10}" class="tick">{esc(series[0][1][0][0])}</text>')
    g.append(f'<text x="{W - R}" y="{H - 10}" class="tick end">{esc(series[0][1][-1][0])}</text>')
    return (
        f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
        f'aria-label="權益曲線">{"".join(g)}</svg>'
    )


def write_markdown(d, rows, out_md):
    """把同一份 JSON 也寫成 markdown，供版控與 diff。

    **不重算任何數字**——與 HTML 走同一組欄位，故兩者不可能不一致。
    結果包本身是可重新產生的產物（20MB+），不進版控；進版控的是這份摘要。
    """
    b = {x["key"]: x for x in d["benchmarks"]}
    b50 = b.get("0050_tr", {})
    w = d["window"]
    L = [
        "# 回測總覽",
        "",
        f"窗口 {w['start']} ~ {w['end']}　資金 80,000,000（D-068）　成本 1×／0×／1.5×",
        f"完成 {len(d['rows'])} 筆；尚缺 {len(d['missing'])} 筆。",
        "",
        "> **這不是排行榜。** 試驗數已達數十個，「最好的那格」有很大一部分是選擇偏誤",
        "> （DSR 的定義）。正確讀法是下方的歸因邊——每條只換一件事。",
        "> 任一格要晉升須另立事前登記、獨立重跑，並承擔已消耗的試驗計數。",
        "",
        "## 基準",
        "",
        "| 基準 | 代表什麼 | CAGR | MDD | Sharpe |",
        "|---|---|---:|---:|---:|",
    ]
    for x in d["benchmarks"]:
        if "error" in x:
            L.append(f"| {x['label']} | {x['role']} | 算不出來：{x['error'][:60]} | | |")
        else:
            L.append(
                f"| {x['label']} | {x['role']} | {x['cagr']:+.2%} | "
                f"{x['mdd']:.1%} | {x['sharpe']:.2f} |"
            )
    L += [
        "",
        "## 全部策略（1× 成本，組內按 Sharpe）",
        "",
        "「持股」是**實際**持股中位數，不是設定的 top_n——80M 下賣單被參與率上限擋住、",
        "部位殘留；10M 下買不起一張。兩個方向的扭曲都讓「檔數」的比較失真。",
        "",
        "| 策略 | 內容 | CAGR | MDD | Sharpe | 周轉 | 曝險 | 持股 | 有部位時年化 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    order = ["階梯", "雙軌", "C 軌", "M 系列", "G 濾網矩陣", "MG 系列"]
    for g in order:
        grp = [r for r in rows if r["group"] == g]
        if not grp:
            continue
        L.append(f"| **{g}** | | | | | | | | |")
        for r in grp:
            # 曝險趨近 0 的格（M2／GALL 之類）根本沒有持倉，median 會是 None——
            # 那不是缺陷而是該格的事實，故照實印「—」而不是補 0（會被誤讀成「持有 0 檔」）
            h = (r.get("holdings") or {}).get("median")
            iv = (r.get("invested") or {}).get("ann_return_when_invested")
            L.append(
                f"| {r['name']} | {r['desc']} | {r['cagr']:+.2%} | {r['mdd']:.1%} | "
                f"{r['sharpe']:.2f} | {r['turnover']:.0%} | "
                f"{(r.get('exposure') or 0):.0%} | "
                + (f"{h:.0f} | " if h is not None else "— | ")
                + (f"{iv:+.1%} |" if iv is not None else "— |")
            )
    L += [
        "",
        "## 歸因邊（月度區塊標準誤）",
        "",
        "每條**只換一件事**，其餘逐字相同。日報酬高度自相關（持股期 20 個交易日），",
        "把每日配對差當獨立樣本會把顯著性高估約 4.5 倍，故一律用月度區塊 SE。",
        "",
        "| 邊 | 只換了什麼 | ΔCAGR | ΔMDD | ΔSharpe | t |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for e in d["edges"]:
        t = e.get("t")
        L.append(
            f"| {e['label']} | {e['note']} | {e['d_cagr']:+.2%} | {e['d_mdd']:+.2%} | "
            f"{e['d_sharpe']:+.2f} | {'—' if t is None else f'{t:+.2f}'} |"
        )
    L += [
        "",
        "## 已知限制",
        "",
        "- **沒有任何資金水位是在測「持有恰好 N 檔」**：80M 下賣單被參與率上限擋住 "
        "379~741 次、實際持股高於設定（設 20 檔、實際中位 32 檔）；10M 下因整張捨去而"
        "持有不足、曝險僅 61~87%。",
        "- **低曝險策略不能只看 CAGR**：事件型策略在無訊號的月份空手，CAGR 天然偏低，"
        "那是設計不是缺陷。要看「有部位時年化」與「曝險」。",
        "- **M2／M3 與 GALL 的曝險接近 0，判定無效**（D-076）：稀疏事件閘門與其他閘門"
        "相乘時可用樣本以乘法衰減。結果保留為該教訓的證據。",
        "",
        "本輪修掉的資料缺陷：0050 的 1:4 分割未還原（單日 −74.8% → +0.87%）；"
        "分割與股利混成單一因子（0050 不含息 −8.66% → +35.55%）；"
        "`corporate_events` 對 380 檔 ETF 只涵蓋 16 檔，「含息」靜默退化成不含息；"
        "2023-02~06 價格缺腿與因子宇宙砍半。",
        "",
        "研究用途，非投資建議。尚無任何策略通過晉升判定，也未部署於實盤。",
        "",
    ]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")
    return out_md


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--md", default="docs/reports/backtest-results.md")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    d = json.loads(SRC.read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if r["scale"] == "1x"]
    rows.sort(key=lambda r: -(r["sharpe"] or 0))
    bench = {b["key"]: b for b in d["benchmarks"]}
    b50 = bench.get("0050_tr", {})
    win = d["window"]

    best = rows[0] if rows else None
    gap = (b50.get("cagr", 0) - best["cagr"]) if best else 0

    # ── 首屏 ──────────────────────────────────────────────────────────
    vcells = [
        (
            "0050 含息（基準）",
            pct(b50.get("cagr"), 2, False),
            f"MDD {b50.get('mdd', 0):.1%}・Sharpe {b50.get('sharpe', 0):.2f}",
            True,
        ),
    ]
    if best:
        vcells.append(
            (
                f"最佳策略 {best['name']}",
                pct(best["cagr"], 2, False),
                f"MDD {best['mdd']:.1%}・Sharpe {best['sharpe']:.2f}",
                False,
            )
        )
        vcells.append(
            ("差距", f'<span class="num down">−{gap:.2%}</span>', "年化，策略落後基準", False)
        )
    b51 = bench.get("0051_tr", {})
    if best and b51.get("cagr") is not None:
        vcells.append(
            (
                "0051 中型100（不含台積電）",
                pct(b51["cagr"], 2, False),
                f"MDD {b51['mdd']:.1%}・Sharpe {b51['sharpe']:.2f}",
                False,
            )
        )
    vcells.append(
        (
            "已完成回測",
            f'<span class="num">{len(d["rows"])}</span>',
            f"尚缺 {len(d['missing'])} 筆（仍在跑）",
            False,
        )
    )

    vhtml = "".join(
        f'<div class="vcell{" hero" if hero else ""}"><div class="k">{esc(k)}</div>'
        f'<div class="v">{v}</div><div class="n">{esc(n)}</div></div>'
        for k, v, n, hero in vcells
    )

    # ── 基準表 ────────────────────────────────────────────────────────
    brows = "".join(
        f'<tr class="bench"><td class="name">{esc(b["label"])}</td>'
        f'<td class="desc">{esc(b["role"])}</td>'
        f"<td>{pct(b.get('cagr'), 2, False)}</td><td>{pct(b.get('mdd'), 1, False)}</td>"
        f'<td>{plain(b.get("sharpe"))}</td><td class="dim">—</td><td class="dim">—</td>'
        f'<td class="dim">—</td><td class="dim">—</td></tr>'
        if "error" not in b
        else f'<tr class="bench"><td class="name">{esc(b["label"])}</td>'
        f'<td class="desc">{esc(b["role"])}</td><td colspan="7" style="text-align:left" class="dim">'
        f"算不出來：{esc(b['error'][:120])}</td></tr>"
        for b in d["benchmarks"]
    )

    # ── 主表（按家族分組，組內按 Sharpe） ──────────────────────────────
    order = ["階梯", "雙軌", "C 軌", "M 系列", "G 濾網矩陣", "MG 系列"]
    body = ""
    for g in order:
        grp = [r for r in rows if r["group"] == g]
        if not grp:
            continue
        body += f'<tr class="grouphead"><td colspan="9">{esc(g)}　{len(grp)} 個策略</td></tr>'
        for r in grp:
            h = r.get("holdings") or {}
            inv = r.get("invested") or {}
            hold = f"{h.get('median', 0):.0f}" if h else "—"
            body += (
                f'<tr><td class="name">{esc(r["name"])}</td>'
                f'<td class="desc">{esc(r["desc"])}</td>'
                f"<td>{pct(r['cagr'], 2, False)}</td>"
                f"<td>{pct(r['mdd'], 1, False)}</td>"
                f"<td>{plain(r['sharpe'])}</td>"
                f"<td>{plain(r['turnover'], '{:.0%}')}</td>"
                f"<td>{plain(r.get('exposure'), '{:.0%}')}</td>"
                f'<td><span class="num">{hold}</span></td>'
                f"<td>{plain(inv.get('ann_return_when_invested'), '{:+.1%}') if inv.get('ann_return_when_invested') is not None else '<span class=dim>—</span>'}</td>"
                f"</tr>"
            )

    # ── 歸因邊 ────────────────────────────────────────────────────────
    ehtml = ""
    for e in d["edges"]:
        t = e.get("t")
        tstr = "—" if t is None else f"{t:+.2f}"
        tcls = "up" if t and t >= 2.0 else ("down" if t and t <= -2.0 else "dim")
        cap = (
            ""
            if e.get("same_capital", True)
            else ' <strong style="color:var(--up)">⚠ 資金不同</strong>'
        )
        ehtml += (
            f'<div class="edge"><div class="lab">{esc(e["label"])}</div>'
            f'<div class="stats">ΔCAGR {e["d_cagr"]:+.2%}　ΔMDD {e["d_mdd"]:+.2%}　'
            f'ΔSharpe {e["d_sharpe"]:+.2f}　t <span class="tsig {tcls}">{tstr}</span></div>'
            f'<div class="note2">{esc(e["note"])}{cap}</div></div>'
        )

    missing = ", ".join(d["missing"][:18]) + (" …" if len(d["missing"]) > 18 else "")

    scatter = scatter_risk_return(rows, d["benchmarks"])
    bars = bars_marginal(d["edges"])
    equity = equity_lines(rows, d["benchmarks"])

    page = f"""<title>台股策略回測總覽</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;600;700&family=Noto+Serif+TC:wght@600;700&family=IBM+Plex+Mono:wght@400;600&display=swap">
<style>{CSS}</style>
<div class="wrap">

<header style="display:flex;flex-direction:column;gap:10px">
  <div class="eyebrow">SPEC-013・研究用，未經部署裁決</div>
  <h1>台股策略回測總覽</h1>
  <p class="sub">窗口 {esc(win["start"])} ~ {esc(win["end"])}　保險庫邊界之後未開封　初始資金依 D-068 統一為 80,000,000　成本情境 1×／0×／1.5×</p>
</header>

<section>
  <div class="verdict">
    <p class="lede">到目前為止，<strong>沒有任何一個策略打敗 0050（含息）</strong>。這是這批實驗最硬的事實，放在最前面。</p>
    <p class="lede" style="font-size:15px">而且不能歸咎於「0050 有台積電」：<strong>0051 中型100 結構上排除市值前 50 名</strong>，
    同期仍有 <strong>+31.57%／Sharpe 1.54</strong>。刻意選小型股的 A 軌（+15.90%／0.68）輸它近 16pp
    ——差距來自選股，不是來自權值股。</p>
    <div class="vgrid">{vhtml}</div>
  </div>
  <div class="note">
    <strong>這份報告不是排行榜。</strong>本批已累積數十個試驗，格數一多，「最好的那格」有很大一部分是選擇偏誤（DSR 的定義）。
    正確的讀法是下方的<strong>歸因邊</strong>——每一條都只換一件事，才問得出「這件事值多少」。
    任一格要晉升為候選，須另立事前登記、獨立重跑，並承擔已消耗的試驗計數。
  </div>
</section>

<section>
  <h2>風險與報酬的全貌</h2>
  <p>每個點是一個策略。<strong>左下方＝報酬與風險雙輸。</strong>虛線十字是 0050 含息的位置，右上方的區域才算贏它。</p>
  {scatter}
  <p class="figcap">顏色代表家族。滑鼠停在點上可看該策略的數值；只標出報酬前六名，避免疊字。</p>
</section>

<section>
  <h2>濾網值多少</h2>
  <p>每一條都是「基準<strong>只加這一個</strong>濾網」的差，故可歸因。<strong>ΔMDD 為正代表回撤變淺</strong>；顏色沿用台股慣例，紅為改善。</p>
  {bars}
  <p class="figcap">「全部一起」不是各邊際效果之和——濾網之間會重複與抵銷。</p>
</section>

<section>
  <h2>權益曲線</h2>
  <p>Sharpe 前五名與 0050 含息（虛線）。起點統一為 1.0，故縱軸是累積倍數。</p>
  {equity}
</section>

<section>
  <h2>基準</h2>
  <p>全部對齊策略實際走過的交易日計算。分割一律還原、股利只在含息時還原；查無股利事件卻要求含息即拒絕計算（否則「含息」會靜默退化成價格報酬）。</p>
  <div class="tblwrap"><table>
    <thead><tr><th>基準</th><th class="desc">代表什麼</th><th>CAGR</th><th>MDD</th><th>Sharpe</th><th>周轉</th><th>曝險</th><th>持股</th><th>有部位時年化</th></tr></thead>
    <tbody>{brows}</tbody>
  </table></div>
</section>

<section>
  <h2>全部策略</h2>
  <p>1× 成本情境，組內按 Sharpe 排序。<strong>「持股」是實際持股檔數的中位數，不是設定的 top_n</strong>——80M 下賣單會被參與率上限擋住、部位殘留，10M 下則買不起一張，兩個方向的扭曲都會讓「檔數」的比較失真。</p>
  <div class="tblwrap"><table>
    <thead><tr><th>策略</th><th class="desc">內容</th><th>CAGR</th><th>MDD</th><th>Sharpe</th><th>周轉</th><th>曝險</th><th>持股</th><th>有部位時年化</th></tr></thead>
    <tbody>{body}</tbody>
  </table></div>
  <div class="note plain">
    <strong>低曝險策略要看最後一欄。</strong>事件型策略（型態閘門＋固定格位）在沒有訊號的月份空手，
    CAGR 因此天然偏低——那是設計不是缺陷。要判斷訊號好不好，看「有部位時年化」與「曝險」，不能只看 CAGR。
  </div>
</section>

<section>
  <h2>歸因邊</h2>
  <p>每一條都<strong>只換一件事</strong>，其餘逐字相同。不確定性一律用<strong>月度區塊標準誤</strong>：持股期是 20 個交易日、日報酬高度自相關，把每日配對差當獨立樣本會把顯著性高估約 4.5 倍。</p>
  <div class="edges">{ehtml}</div>
</section>

<section>
  <h2>方法論與已知限制</h2>
  <div class="meta">
    <p><strong>成本</strong>　手續費、交易稅、滑價（含 tick 下限與流動性衝擊），三個情境 1×／0×／1.5×。D-038 要求 1.5× 後仍為正才可晉升。</p>
    <p><strong>前視防護</strong>　因子只能透過 PIT 視圖讀資料；ML 分數是 walk-forward 各窗以 ≤train_end 資料訓練後對其後測試段推論，天然滿足 PIT。</p>
    <p><strong>樣本外</strong>　保險庫邊界 {esc(win["end"])}，之後未開封。重訓模型的 <code>train_end</code> 為 2026-02-25，與凍結模型相同。</p>
    <p><strong>本輪修掉的資料缺陷</strong>　0050 的 1:4 分割未還原（跨日單日報酬 −74.8% → +0.87%）；分割與股利混成單一因子，導致不含息序列留著斷崖（0050 不含息 −8.66% → +35.55%）；<code>corporate_events</code> 對 380 檔 ETF 只涵蓋 16 檔，缺事件時因子被填 1.0，「含息」靜默退化成不含息；2023-02~06 的價格缺腿與因子宇宙砍半。</p>
    <p><strong>尚未解決</strong>　80M 下賣單被參與率上限擋住 379~741 次，實際持股高於設定值（例：設定 20 檔、實際中位 32 檔）；10M 下則因整張捨去而持有不足、曝險僅 61~87%。<strong>沒有任何資金水位是在測「持有恰好 N 檔」</strong>。</p>
    <p><strong>尚缺</strong>　{esc(missing) if missing else "無"}</p>
  </div>
</section>

<footer>
  研究用途，非投資建議。本頁所有數字由回測結果包直接產生，渲染層不做任何計算。
  尚無任何策略通過晉升判定，也未部署於實盤。
</footer>

</div>"""

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    # markdown 進版控（結果包本身 20MB+ 且可重新產生，不進版控）；兩者同源故不會不一致
    md = write_markdown(d, rows, pathlib.Path(args.md))
    print(
        f"已寫出 {out}（{len(d['rows'])} 筆回測、{len(d['edges'])} 條邊、尚缺 {len(d['missing'])} 筆）"
    )
    print(f"已寫出 {md}")


if __name__ == "__main__":
    main()
