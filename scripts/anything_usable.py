"""「有沒有任何一支可以用」——把這句話拆成可查證的問句，逐一查。

「可以用」在這裡的定義是**打敗單純買 0050**，因為那是任何人都做得到的替代方案：
不必選股、不必再平衡、成本近乎零。贏不過它就沒有存在理由。

三個維度分開看，且**口徑嚴格配對**（含息比含息、價格報酬比價格報酬），
否則憑空多出三到五個百分點（D-085）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

R = Path("D:/project/twquant/data")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    allr = json.loads((R / "reports/all_results.json").read_text(encoding="utf-8"))
    bench = json.loads((R / "reports/benchmarks_two_windows.json").read_text(encoding="utf-8"))

    for win, wlabel in (
        ("short", "短窗口 2022-07-01~2026-02-28"),
        ("long", "長窗口 2017-01-03~2026-02-28"),
    ):
        for tr, tlabel in ((True, "含息"), (False, "價格報酬")):
            b = bench["0050_tr" if tr else "0050_px"][win]
            rows = [
                x
                for x in allr["bundles"]
                if x["file"].endswith("_1x")
                and x["credit_dividends"] is tr
                and x["long_window"] is (win == "long")
                and x["cagr"] is not None
            ]
            if not rows:
                continue
            print(f"\n{'=' * 78}\n{wlabel}　·　{tlabel}　·　1× 成本　·　{len(rows)} 支策略")
            print(
                f"基準 0050：CAGR {b['cagr'] * 100:+.2f}%　MDD {b['mdd'] * 100:.2f}%　Sharpe {b['sharpe']:+.2f}"
            )
            beat_c = [x for x in rows if x["cagr"] > b["cagr"]]
            beat_s = [x for x in rows if x["sharpe"] > b["sharpe"]]
            beat_m = [x for x in rows if abs(x["mdd"]) < abs(b["mdd"])]
            beat_all = [x for x in rows if x in beat_c and x in beat_s and x in beat_m]
            print(f"  年化贏 0050　：{len(beat_c):>3} 支")
            print(f"  Sharpe 贏 0050：{len(beat_s):>3} 支")
            print(f"  回撤淺於 0050　：{len(beat_m):>3} 支")
            print(f"  **三項全贏**　：{len(beat_all):>3} 支" + ("" if beat_all else "　← 沒有"))
            for x in sorted(beat_all, key=lambda y: -y["sharpe"]):
                print(f"      {x['file']}")
            top = sorted(rows, key=lambda y: -y["sharpe"])[:3]
            print("  Sharpe 前三名：")
            for x in top:
                gap = x["sharpe"] - b["sharpe"]
                print(
                    f"      {x['file']:38s} CAGR {x['cagr'] * 100:+7.2f}%  MDD {x['mdd'] * 100:7.2f}%  "
                    f"Sharpe {x['sharpe']:+.2f} （對 0050 {gap:+.2f}）"
                )

    # 哪些策略**從來沒在長窗口跑過**——這才是「都不行」與「測過的都不行」的差別
    print(f"\n{'=' * 78}\n長窗口實際測過的策略：")
    lw = sorted({x["file"].rsplit("_", 1)[0] for x in allr["bundles"] if x["long_window"]})
    for n in lw:
        print(f"  {n}")
    short_names = sorted(
        {
            x["file"].rsplit("_", 1)[0].split("_", 1)[0]
            for x in allr["bundles"]
            if not x["long_window"]
        }
    )
    print(f"\n短窗口跑過但**長窗口沒跑過**的策略共 {len(short_names)} 支：")
    print("  " + "、".join(short_names))


if __name__ == "__main__":
    main()
