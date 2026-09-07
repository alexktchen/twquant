"""LW 系列的報告資料塊：每一格的名稱、白話說明、所屬登記、成績、與基準格的邊。

「邊」是這批實驗的重點：每一格與它的對照格**只差一件事**，
所以 (格 − 對照) 就乾淨地量出那一件事值多少。散開來看只會看到一堆都輸給 0050 的數字。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

R = Path("D:/project/twquant/data")
OUT = R / "reports/lw_series.json"

# 每一格：所屬批次、人話名稱、它實際在做什麼、對照格（＝那條「只差一件事」的邊）
CELLS: list[dict[str, object]] = [
    # ── P0 公開因子四格（D-087）────────────────────────────────────
    {
        "code": "LW-C1",
        "dir": "tracks_lw_nodiv",
        "batch": "D-087 公開因子",
        "base": None,
        "name": "技術面突破",
        "what": "52 週高點 × 三個月動能 × 異常放量 × 相對強弱，選 20 檔等權，每月換股。不看財報。",
        "edge": "這一批的起點。",
    },
    {
        "code": "LW-C1abs",
        "dir": "tracks_lw_nodiv",
        "batch": "D-087 公開因子",
        "base": "LW-C1",
        "name": "技術面突破（固定金額門檻）",
        "what": "與上一支逐字相同，只把流動性門檻換回固定金額（日均成交 3,000 萬）。",
        "edge": "量「相對門檻 vs 固定金額」值多少——固定金額在 2017 與 2026 是不同的嚴格程度。",
    },
    {
        "code": "LW-B2",
        "dir": "tracks_lw_nodiv",
        "batch": "D-087 公開因子",
        "base": None,
        "name": "全市場多因子（市值加權）",
        "what": "月營收年增率 × 動能 × 股東權益報酬率 × 低波動，選 50 檔市值加權，單檔上限 25%。",
        "edge": "最接近一般理解的「多因子選股」，分散度最高。",
    },
    {
        "code": "LW-X1",
        "dir": "tracks_lw_nodiv",
        "batch": "D-087 公開因子",
        "base": "LW-B2",
        "name": "四因子精選 20 檔（等權）",
        "what": "與上一支同樣四個因子，但只選 20 檔、等權、單檔上限 5%。",
        "edge": "量「檔數與加權方式」值多少。",
    },
    # ── P0 風控三格（D-091）────────────────────────────────────────
    {
        "code": "LW-R2",
        "dir": "tracks_lw_risk_nodiv",
        "batch": "D-091 風控",
        "base": "LW-C1",
        "name": "技術面突破 ＋ 剔除高波動",
        "what": "與技術面突破逐字相同，只多一道**事前**濾網：每次選股前先剔除 20 日波動最高的 10%。",
        "edge": "量「事前排除高波動股票」值多少。",
    },
    {
        "code": "LW-C4",
        "dir": "tracks_lw_risk_nodiv",
        "batch": "D-091 風控",
        "base": "LW-C1",
        "name": "技術面突破 ＋ 波動率縮放",
        "what": "與技術面突破逐字相同，只多一層**事後**曝險調節：組合近期波動高於年化 20% 目標時，"
        "整體部位等比例縮小（Barroso–Santa-Clara 2015）。不加槓桿，倍數封頂 1.0。",
        "edge": "量「事後降低整體曝險」值多少。與 R2 只差『事前挑股票』還是『事後縮部位』。",
    },
    {
        "code": "LW-B1",
        "dir": "tracks_lw_risk_nodiv",
        "batch": "D-091 風控",
        "base": None,
        "name": "大型股增強",
        "what": "只在市值前 100 大裡選 30 檔、市值加權、單檔上限 25%。訊號同四因子。",
        "edge": "換載具：「要打敗 0050，先要像 0050」。對照對象是 0050 本身，不是其他格。",
    },
    # ── P1 降周轉（D-092）──────────────────────────────────────────
    {
        "code": "LW-C1band",
        "dir": "tracks_lw_turnover_nodiv",
        "batch": "D-092 P1 降周轉",
        "base": "LW-C1",
        "name": "技術面突破 ＋ 帶狀持有",
        "what": "與技術面突破逐字相同，只改換股規則：新股要進到前 10% 才買，"
        "已持有的要跌出前 30% 才賣（中間那段就抱著不動）。",
        "edge": "量「少換股」值多少。判準：周轉 ≤ 4×／年 且 CAGR ≥ +3pp。",
    },
    {
        "code": "LW-X1band",
        "dir": "tracks_lw_turnover_nodiv",
        "batch": "D-092 P1 降周轉",
        "base": "LW-X1",
        "name": "四因子精選 ＋ 帶狀持有",
        "what": "與四因子精選逐字相同，只改換股規則（同上）。",
        "edge": "同上，但基座是四因子而非技術面。",
    },
    # ── P4 擇時（D-092）────────────────────────────────────────────
    {
        "code": "LW-B2trend",
        "dir": "tracks_lw_timing_nodiv",
        "batch": "D-092 P4 擇時",
        "base": "LW-B2",
        "name": "全市場多因子 ＋ 大盤擇時",
        "what": "與全市場多因子逐字相同，只多一層：加權指數前一日收盤跌破 200 日均線就**全部空手**"
        "（Faber 2007）。均線長度固定 200 不掃。",
        "edge": "量「大盤在跌就不做」值多少。反事實預估 MDD −31.06%。",
    },
    {
        "code": "LW-X1trend",
        "dir": "tracks_lw_timing_nodiv",
        "batch": "D-092 P4 擇時",
        "base": "LW-X1",
        "name": "四因子精選 ＋ 大盤擇時",
        "what": "同上，基座換成四因子精選。",
        "edge": "反事實預估 MDD −31.13%。",
    },
    {
        "code": "LW-B1trend",
        "dir": "tracks_lw_timing_nodiv",
        "batch": "D-092 P4 擇時",
        "base": "LW-B1",
        "name": "大型股增強 ＋ 大盤擇時",
        "what": "同上，基座換成大型股增強。",
        "edge": "兩個「像 0050」的做法疊加。",
    },
    {
        "code": "LW-R2trend",
        "dir": "tracks_lw_timing_nodiv",
        "batch": "D-092 P4 擇時",
        "base": "LW-R2",
        "name": "剔除高波動 ＋ 大盤擇時",
        "what": "同上，基座換成 R2。",
        "edge": "**預期無增量**——波動閘門已經做了類似的事。這一格是為了確認反事實可不可信。",
    },
]

_BENCH_LONG = {"cagr": 0.1791, "mdd": -0.3638, "sharpe": 0.96}  # 0050 不含息，同視窗同口徑


def _metrics(cell: dict[str, object]) -> dict[str, float] | None:
    d = R / str(cell["dir"])
    if not d.exists():
        return None
    for f in d.glob("*_1x.json"):
        if f.stem.split("_", 1)[0] == cell["code"]:
            m = json.loads(f.read_text(encoding="utf-8"))["metrics"]
            return {k: m[k] for k in ("cagr", "mdd", "sharpe", "turnover", "n_days")}
    return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    got = {str(c["code"]): _metrics(c) for c in CELLS}
    rows = []
    for c in CELLS:
        m = got[str(c["code"])]
        base = got.get(str(c["base"])) if c["base"] else None
        row = dict(c)
        row["metrics"] = m
        row["tier"] = (
            None
            if m is None
            else ("甲" if abs(m["mdd"]) <= 0.30 else "乙" if abs(m["mdd"]) <= 0.40 else "丙")
        )
        row["delta"] = (
            None
            if (m is None or base is None)
            else {
                "cagr_pp": (m["cagr"] - base["cagr"]) * 100,
                "mdd_pp": (abs(base["mdd"]) - abs(m["mdd"])) * 100,  # 正＝較淺
                "sharpe": m["sharpe"] - base["sharpe"],
                "turnover": m["turnover"] - base["turnover"],
            }
        )
        row["vs_0050"] = (
            None
            if m is None
            else {
                "cagr_pp": (m["cagr"] - _BENCH_LONG["cagr"]) * 100,
                "mdd_pp": (abs(_BENCH_LONG["mdd"]) - abs(m["mdd"])) * 100,
                "sharpe": m["sharpe"] - _BENCH_LONG["sharpe"],
            }
        )
        rows.append(row)

    payload = {
        "cells": rows,
        "benchmark": _BENCH_LONG,
        "done": sum(1 for r in rows if r["metrics"]),
        "total": len(rows),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{payload['done']}／{payload['total']} 格完成 → {OUT}\n")
    for r in rows:
        m = r["metrics"]
        if not m:
            print(f"  {r['code']:12s} {r['name']:24s} 尚未跑")
            continue
        d = r["delta"]
        edge = f"　邊 {d['cagr_pp']:+.1f}pp／回撤 {d['mdd_pp']:+.1f}pp" if d else ""
        print(
            f"  {r['code']:12s} {r['name']:24s} {m['cagr'] * 100:+6.2f}% "
            f"{m['mdd'] * 100:7.2f}% Sh {m['sharpe']:+.2f} {r['tier']}層{edge}"
        )


if __name__ == "__main__":
    main()
