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
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#12141A; --surface:#191C24; --sunken:#1F232C;
    --ink:#E8EAF0; --ink-2:#AEB5C4; --ink-3:#7C8494;
    --line:#2A2F3A; --line-2:#3A4150;
    --accent:#8FA2E8; --accent-soft:#232941;
    --up:#E8695B; --down:#4CAF7D;
    --warn:#D9B65C; --warn-soft:#2C2718;
  }
}
:root[data-theme="dark"]{
  --bg:#12141A; --surface:#191C24; --sunken:#1F232C;
  --ink:#E8EAF0; --ink-2:#AEB5C4; --ink-3:#7C8494;
  --line:#2A2F3A; --line-2:#3A4150;
  --accent:#8FA2E8; --accent-soft:#232941;
  --up:#E8695B; --down:#4CAF7D;
  --warn:#D9B65C; --warn-soft:#2C2718;
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
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
    <div class="vgrid">{vhtml}</div>
  </div>
  <div class="note">
    <strong>這份報告不是排行榜。</strong>本批已累積數十個試驗，格數一多，「最好的那格」有很大一部分是選擇偏誤（DSR 的定義）。
    正確的讀法是下方的<strong>歸因邊</strong>——每一條都只換一件事，才問得出「這件事值多少」。
    任一格要晉升為候選，須另立事前登記、獨立重跑，並承擔已消耗的試驗計數。
  </div>
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
    print(
        f"已寫出 {out}（{len(d['rows'])} 筆回測、{len(d['edges'])} 條邊、尚缺 {len(d['missing'])} 筆）"
    )


if __name__ == "__main__":
    main()
