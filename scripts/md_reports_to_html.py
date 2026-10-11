"""把 docs/reports 的 markdown 報告轉成可直接用瀏覽器打開的 HTML（含目錄頁）。

    uv run --all-groups python scripts/md_reports_to_html.py --out D:/project/twquant/data/reports/html \
        docs/reports/pring-technical-analysis-summary.md docs/reports/core-opportunistic-blend-dev.md ...

只做排版：內容就是 markdown 原文（數字的來源與口徑都寫在原文裡）。數字欄自動靠右，正負號上色（紅漲綠跌）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import pathlib
import re
import sys

from markdown_it import MarkdownIt

STYLE = """
:root{--ink:#1f2328;--muted:#656d76;--line:#d0d7de;--bg:#ffffff;--soft:#f6f8fa;--up:#c62828;--down:#2e7d32;--accent:#b45309}
*{box-sizing:border-box}
body{margin:0;background:var(--soft);color:var(--ink);font:16px/1.7 "Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
article{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:28px 32px}
h1{font-size:1.65rem;line-height:1.35;margin:0 0 .6em}
h2{font-size:1.25rem;margin:1.8em 0 .6em;padding-bottom:.25em;border-bottom:1px solid var(--line)}
h3,h4{font-size:1.05rem;margin:1.4em 0 .4em}
p,li{color:var(--ink)}
blockquote{margin:1em 0;padding:.6em 1em;background:#fff8e6;border-left:4px solid var(--accent);color:#5b3a00}
code{font-family:ui-monospace,Consolas,monospace;font-size:.88em;background:var(--soft);padding:.1em .35em;border-radius:4px}
.scroll{overflow-x:auto;margin:1em 0}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{border:1px solid var(--line);padding:.42em .6em;vertical-align:top}
th{background:var(--soft);font-weight:600;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.pos{color:var(--up)} td.neg{color:var(--down)}
tr:hover td{background:#fbfbfb}
nav.top{margin-bottom:14px;font-size:.92rem}
nav.top a{color:var(--muted);text-decoration:none}
.foot{margin-top:18px;color:var(--muted);font-size:.85rem}
ul.index{list-style:none;padding:0}
ul.index li{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:10px 0}
ul.index a{font-weight:600;color:var(--ink);text-decoration:none}
ul.index p{margin:.3em 0 0;color:var(--muted);font-size:.9rem}
"""

_NUM = re.compile(r"^[−\-+]?[\d,]+(\.\d+)?(%|×|pp| 日| 檔| 筆)?$|^[−\-+]?\d+(\.\d+)?$|^—$")


def _cells(body: str) -> str:
    """表格的數字格靠右、正負上色（只動 <td>，不動文字內容）。"""

    def fix(m: re.Match[str]) -> str:
        inner = m.group(1)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        cls = []
        if _NUM.match(text):
            cls.append("num")
            if text.startswith("+") and text not in ("+0.00%", "+0.0%"):
                cls.append("pos")
            elif text.startswith(("−", "-")):
                cls.append("neg")
        return f'<td class="{" ".join(cls)}">{inner}</td>' if cls else f"<td>{inner}</td>"

    body = re.sub(r"<td>(.*?)</td>", fix, body, flags=re.S)
    return body.replace("<table>", '<div class="scroll"><table>').replace(
        "</table>", "</table></div>"
    )


def render(md_text: str, title: str, src: str, index_name: str) -> str:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    body = _cells(md.render(md_text))
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body><div class='wrap'>"
        f"<nav class='top'><a href='{index_name}'>← 報告目錄</a></nav>"
        f"<article>{body}</article>"
        f"<div class='foot'>來源 <code>{html.escape(src)}</code>（版控的 markdown 原文）；轉檔於 {now}。</div>"
        "</div></body></html>"
    )


def _title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _lede(md_text: str) -> str:
    for line in md_text.splitlines()[1:]:
        t = line.strip()
        if t and not t.startswith(("#", "|", "-", ">")):
            return re.sub(r"[*`]", "", t)[:140]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True, help="輸出資料夾（絕對路徑）")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        raise SystemExit(f"--out 要給絕對路徑：{out}")
    out.mkdir(parents=True, exist_ok=True)
    index_name = "index.html"
    items = []
    for f in args.files:
        p = pathlib.Path(f)
        text = p.read_text(encoding="utf-8")
        title = _title(text, p.stem)
        name = p.stem + ".html"
        (out / name).write_text(
            render(text, title, str(p).replace("\\", "/"), index_name), encoding="utf-8"
        )
        items.append((name, title, _lede(text)))
        print(f"[OK] {out / name}")
    lis = "".join(
        f"<li><a href='{n}'>{html.escape(t)}</a><p>{html.escape(d)}</p></li>" for n, t, d in items
    )
    page = (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>研究報告目錄</title><style>{STYLE}</style></head><body><div class='wrap'>"
        f"<article><h1>研究報告目錄</h1><p>2026-10-11 的研究整理。標「開發期」的數字只當參考，正式判讀在第二保留期（最早 2027-03-01）。</p>"
        f"<ul class='index'>{lis}</ul></article></div></body></html>"
    )
    (out / index_name).write_text(page, encoding="utf-8")
    print(f"[OK] {out / index_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
