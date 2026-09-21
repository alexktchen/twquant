"""D-140 §五：第八輪 LQ 系列——**只揭露，不判通過與否**。

    uv run --all-groups python scripts/lq_verdict.py

依 D-134（統計照報不當門檻）與 D-135（組合回撤不是使用者的判準）：本表不輸出通過／未通過，
只把 E0 與兩個流動性門檻逐項並排。三格同窗口、同資本、同成本、同口徑，唯一差別是流動性門檻。

另列**篩選強度**（取幾檔 ÷ 當日宇宙）：D-140 §四 事前指出 LQ2 只有約 15/56＝27%，
那表示它幾乎沒在選股、測到的是「大型股本身好不好」。沒有這一欄就看不出這件事。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import numpy as np

DATA = pathlib.Path("D:/project/twquant/data")
DEV_END = dt.date(2026, 8, 31)
BASELINE_CALMAR = 0.67  # E0 的 Calmar，作為參照線，**不是門檻**

CELLS: tuple[tuple[str, str, str], ...] = (
    ("E0", "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json", "對照：ADV 前 30%（約 500 檔）"),
    ("LQ1", "tracks_lq_long_nodiv/LQ1_lw_lq1_liq_1x.json", "日均成交 ≥ 3 億（約 175 檔）"),
    ("LQ2", "tracks_lq_long_nodiv/LQ2_lw_lq2_liq_1x.json", "日均成交 ≥ 10 億（約 56 檔）"),
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("D-135：組合回撤不是使用者的判準，本表只揭露不判定。")
    print(f"要看 Calmar 有沒有高過基線 {BASELINE_CALMAR}——沒有就表示只是沿風險報酬線移動。\n")
    print(f"{'格':<4}{'年化':>9}{'組合回撤':>11}{'Calmar':>9}{'平均曝險':>10}{'年周轉':>9}  說明")
    missing: list[str] = []
    for code, rel, note in CELLS:
        path = DATA / rel
        if not path.exists():
            # 「還沒跑」不是結論——混為一談會讓未完成的批次看起來像有答案
            print(f"{code:<4}{'（結果包不存在，尚未跑完）':>34}")
            missing.append(code)
            continue
        b = json.loads(path.read_text(encoding="utf-8"))
        if dt.date.fromisoformat(b["end"]) > DEV_END:
            raise SystemExit(f"{path.name} 結束於 {b['end']}，越過第二保留期邊界")
        m = b["metrics"]
        eq = np.array([e[1] for e in b["equity"]], dtype=float)
        expo = np.array([e[3] for e in b["equity"]], dtype=float) / eq
        print(
            f"{code:<4}{m['cagr']:>+9.1%}{m['mdd']:>+11.1%}{m['calmar']:>9.2f}"
            f"{expo.mean():>10.0%}{m.get('turnover', float('nan')):>8.1f}x  {note}"
        )
    if missing:
        print(f"\n尚未跑完：{'、'.join(missing)}")


if __name__ == "__main__":
    main()
