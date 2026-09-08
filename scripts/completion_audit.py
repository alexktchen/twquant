"""每一格的狀態盤點：跑完／不可行／設計上不在這個視窗。

先前把「同一個 set 對兩個視窗都算一次」當成缺口，得到 118/128 的假象。
實際上 ML 相關的格**依 D-093 只在 2019 視窗跑**，它們在 2017 視窗的「缺」是設計，不是漏。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

D = pathlib.Path("D:/project/twquant/data")

# 依 D-093：需要 ML 分數的格只跑 2019 視窗（模型的樣本外分數最早 2019-04-30）
_ML_ONLY = {"ml", "ml_all", "f1_timing", "revmom", "matrix_ml"}
# revenue 是混合的：F2/F3/G1/L2 不需要 ML 分數當池子，其餘四格需要
_REVENUE_2019_ONLY = {"LW-F1", "LW-MG1", "LW-F1abs", "LW-F1trend"}

# 跑不出來、而「跑不出來」本身就是結果——不是待修（D-092 明文不調 keep_pct／top_n）
_INFEASIBLE = {
    "LW-MGALLm": "五道濾網疊起來實際入選 3 檔，單股上限 0.15 讓權重加不到 1",
    "LW-MGALL": "同上，M1 基座版只剩 2 檔",
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    spec = importlib.util.spec_from_file_location("r", "scripts/run_long_window.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    done: list[str] = []
    infeasible: list[str] = []
    by_design: list[str] = []
    missing: list[str] = []

    for k, cells in m.CELL_SETS.items():
        for win, suffix in (("2017", "_nodiv"), ("2019", "_w2019_nodiv")):
            d = D / ("tracks_lw" + m.OUT_SUFFIX[k] + suffix)
            for name, cfg in cells:
                tag = f"{name}@{win}"
                if (d / f"{name}_{cfg}_1x.json").exists():
                    done.append(tag)
                    continue
                if name in _INFEASIBLE:
                    # 只在它實際被嘗試的那個視窗記一次
                    if (k == "public_all" and win == "2017") or (k == "ml_all" and win == "2019"):
                        infeasible.append(tag)
                    continue
                if win == "2017" and (
                    k in _ML_ONLY or (k == "revenue" and name in _REVENUE_2019_ONLY)
                ):
                    by_design.append(tag)
                    continue
                if win == "2019" and k not in _ML_ONLY and k != "revenue":
                    by_design.append(tag)  # 公開因子只在 2017 跑（配對對照另外指定）
                    continue
                missing.append(tag)

    print(f"跑完　　　　{len(done):>4} 格")
    print(f"不可行　　　{len(infeasible):>4} 格（結果，非缺陷）")
    for t in infeasible:
        print(f"    {t}：{_INFEASIBLE[t.split('@')[0]]}")
    print(
        f"設計上不跑　{len(by_design):>4} 格（D-093：ML 格只在 2019；公開因子只在 2017 ＋指定的配對對照）"
    )
    print(f"**真的缺　　{len(missing):>4} 格**" + ("" if missing else "　← 沒有"))
    for t in missing:
        print(f"    {t}")


if __name__ == "__main__":
    main()
