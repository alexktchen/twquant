"""把 docs/reports 的 markdown 報告轉成可直接用瀏覽器打開的 HTML（含目錄頁）。

    uv run --all-groups python scripts/md_reports_to_html.py --out D:/project/twquant/data/reports/html \
        docs/reports/pring-technical-analysis-summary.md docs/reports/core-opportunistic-blend-dev.md ...

網站上公開的報告（https://twquant.pages.dev/reports/）照 docs/reports/public-reports.json 的分區產生：

    uv run --all-groups python scripts/md_reports_to_html.py --public \
        --manifest docs/reports/public-reports.json --data-dir D:/project/twquant/data \
        --out <repo 絕對路徑>/frontend/public/reports

只做排版：內容就是 markdown 原文（數字的來源與口徑都寫在原文裡）。數字欄自動靠右，正負號上色（紅漲綠跌）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
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


# 放上網站分享的頁面：拿到連結就能看，但不讓搜尋引擎收錄
NOINDEX = '<meta name="robots" content="noindex,nofollow">'
INTRO = (
    "2026-10-11 的研究整理。標「開發期」的數字只當參考，正式判讀在第二保留期（最早 2027-03-01）。"
)


def render(
    md_text: str, title: str, src: str, index_name: str | None, noindex: bool = False
) -> str:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    body = _cells(md.render(md_text))
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + (NOINDEX if noindex else "")
        + f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body><div class='wrap'>"
        + (f"<nav class='top'><a href='{index_name}'>← 報告目錄</a></nav>" if index_name else "")
        + f"<article>{body}</article>"
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


def _copy_html(src: pathlib.Path, index_name: str, noindex: bool) -> tuple[str, str]:
    """已經是 HTML 的舊報告：原樣複製，只在 head 加 noindex、在 body 開頭加回目錄的連結。回傳 (內容, 標題)。"""
    page = src.read_text(encoding="utf-8")
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    title = html.unescape(m.group(1).strip()) if m else src.stem
    if noindex:
        # 有 <head> 就放在裡面；沒有（HTML 允許省略）就放最前面，瀏覽器會把開頭的 <meta> 歸進隱含的 head
        page, n = re.subn(
            r"<head([^>]*)>", lambda h: h.group(0) + NOINDEX, page, count=1, flags=re.I
        )
        if n != 1:
            page = NOINDEX + page
    back = (
        "<nav style='max-width:1080px;margin:0 auto;padding:10px 20px;font:15px system-ui,sans-serif'>"
        f"<a href='{index_name}' style='color:#656d76;text-decoration:none'>← 報告目錄</a></nav>"
    )
    # 有 <body> 就放在開頭；沒有就放在第一個內容區塊之前
    page, n = re.subn(r"<body([^>]*)>", lambda b: b.group(0) + back, page, count=1, flags=re.I)
    if n != 1:
        page, n = re.subn(
            r"<(header|main|article|div|h1)\b",
            lambda b: back + b.group(0),
            page,
            count=1,
            flags=re.I,
        )
    if n != 1:
        raise SystemExit(f"{src} 找不到 <body> 或內容區塊，加不了回目錄的連結")
    return page, title


def _index_page(
    sections: list[tuple[str, list[tuple[str, str, str, str]]]], intro: str, noindex: bool
) -> str:
    """目錄頁：每區一個標題，底下每份報告的標題、日期、一句說明。"""
    parts = []
    for sec_title, items in sections:
        lis = "".join(
            f"<li><a href='{n}'>{html.escape(t)}</a>"
            + (f" <span class='date'>{html.escape(d)}</span>" if d else "")
            + f"<p>{html.escape(note)}</p></li>"
            for n, t, d, note in items
        )
        parts.append(
            (f"<h2>{html.escape(sec_title)}</h2>" if sec_title else "")
            + f"<ul class='index'>{lis}</ul>"
        )
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + (NOINDEX if noindex else "")
        + f"<title>研究報告目錄</title><style>{STYLE}"
        "ul.index .date{color:var(--muted);font-size:.85rem;margin-left:.4em;white-space:nowrap}</style></head>"
        f"<body><div class='wrap'><article><h1>研究報告目錄</h1><p>{html.escape(intro)}</p>"
        + "".join(parts)
        + "</article></div></body></html>"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True, help="輸出資料夾（絕對路徑）")
    ap.add_argument(
        "--no-index", action="store_true", help="只轉單一份、不做目錄頁也不放回目錄的連結"
    )
    ap.add_argument(
        "--public",
        action="store_true",
        help="要放上網站分享：每頁加 noindex（拿到連結的人看得到，搜尋引擎不收錄）",
    )
    ap.add_argument(
        "--intro", default=INTRO, help="目錄頁的說明文字（--manifest 時用清單裡的 intro）"
    )
    ap.add_argument(
        "--manifest",
        help="分區清單 JSON（例：docs/reports/public-reports.json）：每區的標題與報告（md 或已是 HTML 的舊報告）",
    )
    ap.add_argument("--data-dir", help="資料正本（絕對路徑）：清單裡 html 項目相對於這裡")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        raise SystemExit(f"--out 要給絕對路徑：{out}")
    out.mkdir(parents=True, exist_ok=True)
    index_name: str | None = None if args.no_index else "index.html"

    if args.manifest:
        if index_name is None:
            raise SystemExit("--manifest 一定要有目錄頁，不能和 --no-index 一起用")
        spec = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
        data_dir = pathlib.Path(args.data_dir) if args.data_dir else None
        sections: list[tuple[str, list[tuple[str, str, str, str]]]] = []
        names: set[str] = set()
        for sec in spec["sections"]:
            items: list[tuple[str, str, str, str]] = []
            for it in sec["items"]:
                if "md" in it:
                    p = pathlib.Path(it["md"])
                    text = p.read_text(encoding="utf-8")
                    page = render(
                        text,
                        _title(text, p.stem),
                        str(p).replace("\\", "/"),
                        index_name,
                        args.public,
                    )
                    title = it.get("title") or _title(text, p.stem)
                    note = it.get("note") or _lede(text)
                else:
                    if data_dir is None or not data_dir.is_absolute():
                        raise SystemExit("清單裡有 html 項目：要給 --data-dir（絕對路徑）")
                    p = data_dir / it["html"]
                    page, own_title = _copy_html(p, index_name, args.public)
                    title = it.get("title") or own_title
                    note = it.get("note", "")
                name = p.stem + ".html"
                if name in names:
                    raise SystemExit(f"兩份報告輸出同一個檔名：{name}")
                names.add(name)
                (out / name).write_text(page, encoding="utf-8")
                items.append((name, title, it.get("date", ""), note))
                print(f"[OK] {out / name}")
            sections.append((sec["title"], items))
        (out / "index.html").write_text(
            _index_page(sections, spec.get("intro", args.intro), args.public), encoding="utf-8"
        )
        print(f"[OK] {out / 'index.html'}（{sum(len(i) for _, i in sections)} 份）")
        return 0

    if not args.files:
        raise SystemExit("沒有要轉的檔案（給 markdown 檔，或用 --manifest）")
    flat: list[tuple[str, str, str, str]] = []
    for f in args.files:
        p = pathlib.Path(f)
        text = p.read_text(encoding="utf-8")
        title = _title(text, p.stem)
        name = p.stem + ".html"
        (out / name).write_text(
            render(text, title, str(p).replace("\\", "/"), index_name, args.public),
            encoding="utf-8",
        )
        flat.append((name, title, "", _lede(text)))
        print(f"[OK] {out / name}")
    if index_name is None:
        return 0
    (out / "index.html").write_text(
        _index_page([("", flat)], args.intro, args.public), encoding="utf-8"
    )
    print(f"[OK] {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
