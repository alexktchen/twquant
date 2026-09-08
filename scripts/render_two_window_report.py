"""把 two_window_payload.json ＋ 版面組成單頁 HTML 報告。

數字全部由頁面內嵌的 JSON 塊渲染——**不手抄**，也讓這份報告日後可被 AI 共編：
改資料塊即可，不必動版面（與 `台股ML選股實驗總覽.html` 同一個做法）。

版面拆成三個檔案（head/body/script）純粹是為了避開 shell heredoc 的引號問題；
組出來的成品是單一自足的 HTML。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRATCH = Path(
    "C:/Users/Alex/AppData/Local/Temp/claude/"
    "D--project-twquant--claude-worktrees-strange-kalam-3ec702/"
    "5681af54-7901-4325-b1a7-122e152a538d/scratchpad"
)
PAYLOAD = Path("D:/project/twquant/data/reports/two_window_payload.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(SCRATCH / "two_window_report.html"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    head = (SCRATCH / "report_head.html").read_text(encoding="utf-8")
    body = (SCRATCH / "report_body.html").read_text(encoding="utf-8")
    script = (SCRATCH / "report_script.js").read_text(encoding="utf-8")

    # `</` 會提前關掉內嵌的 JSON 塊。本專案的資料裡沒有它，但仍照規矩擋——
    # 哪天有策略描述寫了 HTML 片段，這一行就是唯一擋得住的地方。
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    html = (
        head
        + body
        + '\n<script id="payload" type="application/json">'
        + blob
        + "</script>\n<script>\n"
        + script
        + "\n</script>\n"
    )
    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"→ {out}　{len(html) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
