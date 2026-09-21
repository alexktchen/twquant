"""D-133 §三：第七輪 V 系列判定——三條門檻**同時**成立才算通過。

    uv run --all-groups python scripts/v_verdict.py

1. 最大回撤 ≤ −25%
2. Calmar ≥ 0.67（＝E0 基線，不得用犧牲報酬換回撤）
3. 年化 > 0

門檻逐字取自 D-133 §三，事前登記、不得事後修改。對照組 E0 只列不判（它是基線本身）。
另列平均持股比例與「曝險 < 50% 的交易日佔比」——沒有這兩個數，看不出減碼是真的在動還是從未觸發。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import numpy as np

DATA = pathlib.Path("D:/project/twquant/data")
DEV_END = dt.date(2026, 8, 31)
MAX_MDD, MIN_CALMAR = -0.25, 0.67  # D-133 §三

CELLS: tuple[tuple[str, str, str], ...] = (
    ("E0", "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json", "基線：無 overlay"),
    ("V1", "tracks_v_long_nodiv/V1_lw_v1_voltgt_1x.json", "target 0.16／window 60／floor 0.10"),
    ("V2", "tracks_v_long_nodiv/V2_lw_v2_voltgt_1x.json", "target 0.12／window 60／floor 0.00"),
    ("V3", "tracks_v_long_nodiv/V3_lw_v3_voltgt_1x.json", "target 0.12／window 20／floor 0.00"),
    ("V4", "tracks_v_long_nodiv/V4_lw_v4_voltgt_1x.json", "target 0.08／window 20／floor 0.00"),
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"判準（D-133 §三，事前登記）：MDD ≤ {MAX_MDD:.0%}　Calmar ≥ {MIN_CALMAR}　年化 > 0\n")
    print(
        f"{'格':<4}{'年化':>9}{'最大回撤':>11}{'Calmar':>9}{'持股比例':>10}{'低曝險日':>10}  判定"
    )
    passed, missing = [], []
    for code, rel, note in CELLS:
        path = DATA / rel
        if not path.exists():
            # 「還沒跑」不是「沒通過」——混為一談會讓未完成的批次看起來像有結論
            print(f"{code:<4}{'（結果包不存在，尚未跑完）':>34}")
            missing.append(code)
            continue
        b = json.loads(path.read_text(encoding="utf-8"))
        if dt.date.fromisoformat(b["end"]) > DEV_END:
            raise SystemExit(f"{path.name} 結束於 {b['end']}，越過第二保留期邊界")
        m = b["metrics"]
        eq = np.array([e[1] for e in b["equity"]], dtype=float)
        pos = np.array([e[3] for e in b["equity"]], dtype=float)
        expo = pos / eq
        ok = m["mdd"] <= MAX_MDD and m["calmar"] >= MIN_CALMAR and m["cagr"] > 0.0
        if code == "E0":
            verdict = "基線（不判）"
        else:
            verdict = "通過" if ok else "未通過"
            if ok:
                passed.append(code)
        print(
            f"{code:<4}{m['cagr']:>+9.1%}{m['mdd']:>+11.1%}{m['calmar']:>9.2f}"
            f"{expo.mean():>10.0%}{float((expo < 0.5).mean()):>10.0%}  {verdict}　{note}"
        )
    print()
    if missing:
        raise SystemExit(
            f"**尚未判定**：{'、'.join(missing)} 的結果包還沒產生，先跑完 `scripts/run_vol_target.py`。"
        )
    if passed:
        print(f"**通過：{'、'.join(passed)}。**")
    else:
        print("**四格全部未通過。** 依 D-133 §五，不得加第五格、不得改參數重掃。")


if __name__ == "__main__":
    main()
