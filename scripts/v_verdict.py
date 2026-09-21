"""D-133 第七輪 V 系列：**只揭露，不判通過與否**（D-135）。

    uv run --all-groups python scripts/v_verdict.py

D-133 §三 原本以「組合最大回撤 ≤ 25%」判定。**該判準已於 D-135 撤銷**——
使用者更正 15%／20% 這類數字指的是**單檔停損**，組合回撤不是他的判準
（「我不要組合 我要單一股票」）。故本腳本不再輸出通過／未通過，只揭露數字。

這一輪值得保留的知識：波動度減碼確實能壓低組合回撤，代價是報酬。
**要看的是 Calmar 有沒有跟著上升**——若各格 Calmar 都停在基線附近，
表示減碼只是沿著風險報酬線移動、沒有創造價值，那就不值得為它增加複雜度。

另列平均曝險與「曝險 < 50% 的交易日佔比」：沒有這兩個數，
看不出減碼是真的在動，還是倍數一直卡在 1.0 從未觸發（`lw_mg3_voltgt` 就是後者）。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import numpy as np

DATA = pathlib.Path("D:/project/twquant/data")
DEV_END = dt.date(2026, 8, 31)
BASELINE_CALMAR = 0.67  # E0 基線，作為「減碼有沒有創造價值」的參照線，**不是門檻**

CELLS: tuple[tuple[str, str, str], ...] = (
    ("E0", "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json", "基線：無 overlay"),
    ("V1", "tracks_v_long_nodiv/V1_lw_v1_voltgt_1x.json", "target 0.16／window 60／floor 0.10"),
    ("V2", "tracks_v_long_nodiv/V2_lw_v2_voltgt_1x.json", "target 0.12／window 60／floor 0.00"),
    ("V3", "tracks_v_long_nodiv/V3_lw_v3_voltgt_1x.json", "target 0.12／window 20／floor 0.00"),
    ("V4", "tracks_v_long_nodiv/V4_lw_v4_voltgt_1x.json", "target 0.08／window 20／floor 0.00"),
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("D-135：組合回撤不是使用者的判準，本表只揭露不判定。")
    print(f"要看 Calmar 有沒有高過基線 {BASELINE_CALMAR}——沒有就表示只是沿風險報酬線移動。\n")
    print(
        f"{'格':<4}{'年化':>9}{'組合回撤':>11}{'Calmar':>9}{'平均曝險':>10}{'低曝險日':>10}  Calmar 增減"
    )
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
        delta = "—" if code == "E0" else f"{m['calmar'] - BASELINE_CALMAR:+.2f}"
        print(
            f"{code:<4}{m['cagr']:>+9.1%}{m['mdd']:>+11.1%}{m['calmar']:>9.2f}"
            f"{expo.mean():>10.0%}{float((expo < 0.5).mean()):>10.0%}  {delta:<8}{note}"
        )
    if missing:
        print(f"\n尚未跑完：{'、'.join(missing)}")


if __name__ == "__main__":
    main()
