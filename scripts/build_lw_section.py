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
    # ── 營收線（D-074 的第二段，長窗口版）──────────────────────────
    {
        "code": "LW-F1",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線",
        "base": "LW-C1",
        "name": "營收驚奇池 × 技術排序",
        "what": "先用**營收驚奇模型**把宇宙縮到分數最高的 30%（預測下月營收會超出自己趨勢的那些），"
        "再用技術面突破的四個訊號在池子裡排序、選 20 檔等權。",
        "edge": "**這是整條營收線的主角。** 判定不是看它自己好不好，是看 F1 − F2。",
    },
    {
        "code": "LW-F2",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線",
        "base": "LW-F1",
        "name": "動能池 × 技術排序（對照）",
        "what": "與 F1 **逐字相同，只把池子換成三個月動能前 30%**。",
        "edge": "**F 系列的隔離對照。** F1 − F2 才是「營收驚奇比動能多值多少」；"
        "少了這格，F1 好看也可能只是因為「先篩一次」本身有效，與營收無關。",
    },
    {
        "code": "LW-F3",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線",
        "base": "LW-F1",
        "name": "52 週高點池 × 技術排序（更嚴對照）",
        "what": "與 F1 逐字相同，只把池子換成 52 週高點前 30%。",
        "edge": "更嚴的對照：池子用的是排序訊號裡權重最高的那個因子，"
        "F1 要贏過它才代表營收驚奇帶進了技術面沒有的資訊。",
    },
    {
        "code": "LW-MG1",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線",
        "base": None,
        "name": "ML 訊號池 ＋ 營收成長濾網",
        "what": "以成本感知模型的分數當池子，再加一道月營收年增率的濾網。",
        "edge": "營收在這裡是**濾網**不是訊號——與 F1 把營收當池子是兩種用法。",
    },
    {
        "code": "LW-G1",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線",
        "base": None,
        "name": "ML 訊號 ＋ 營收成長濾網",
        "what": "與上一支的差別在基座：這支直接用 ML 分數排序，不先過技術面。",
        "edge": "同一道營收濾網掛在不同基座上，效果一不一致。",
    },
    {
        "code": "LW-L2",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線",
        "base": None,
        "name": "階梯 L2：宇宙加營收篩",
        "what": "診斷階梯的第二階——在 ML 基座上，唯一的變更是宇宙加營收篩選。",
        "edge": "階梯是累積的，這一階量的是「營收篩選在宇宙層值多少」。",
    },
    {
        "code": "LW-F1abs",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線 × 擇時（P6）",
        "base": "LW-F1",
        "name": "營收驚奇 ＋ Antonacci 絕對動能",
        "what": "與 F1 逐字相同，只多一層：加權指數過去 12 個月報酬 ≤ 0 就全部空手。",
        "edge": "Antonacci (2014)《Dual Momentum》的絕對動能那一半。",
    },
    {
        "code": "LW-F1trend",
        "dir": "tracks_lw_revenue_nodiv",
        "batch": "營收線 × 擇時（P6）",
        "base": "LW-F1",
        "name": "營收驚奇 ＋ Faber 均線擇時",
        "what": "與 F1 逐字相同，只多一層：加權指數跌破 200 日均線就全部空手。",
        "edge": "**與上一格必須並排**：Faber 比「現價 vs 過去 N 日平均」、Antonacci 比「現價 vs N 日前價格」，"
        "同一段行情可給出相反訊號。少了對照就分不出「擇時有用」與「這條規則有用」。",
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

_BENCH_LONG = {"cagr": 0.1791, "mdd": -0.3638, "sharpe": 0.96}  # 0050 不含息，2017 視窗


def _bench_by_window() -> dict[str, dict[str, float]]:
    """每個視窗各自的 0050（不含息，與策略同口徑）。

    **每一格都要跟自己視窗的基準比**。拿 2017 視窗的基準去比 2019 視窗的策略，
    等於把「那幾年大盤比較好賺」算成策略的功勞——本輪量到的差是 CAGR 最多 8.4pp。
    """
    f = R / "reports/benchmarks_two_windows.json"
    if not f.exists():
        return {"2017-01-03": _BENCH_LONG}
    b = json.loads(f.read_text(encoding="utf-8")).get("0050_px", {})
    out: dict[str, dict[str, float]] = {}
    for win, key in (("2017-01-03", "long"), ("2019-05-02", "w2019")):
        m = b.get(key)
        if m:
            out[win] = {"cagr": m["cagr"], "mdd": m["mdd"], "sharpe": m["sharpe"]}
    return out or {"2017-01-03": _BENCH_LONG}


def _metrics(cell: dict[str, object]) -> dict[str, float] | None:
    d = R / str(cell["dir"])
    if not d.exists():
        return None
    for f in d.glob("*_1x.json"):
        if f.stem.split("_", 1)[0] == cell["code"]:
            m = json.loads(f.read_text(encoding="utf-8"))["metrics"]
            return {k: m[k] for k in ("cagr", "mdd", "sharpe", "turnover", "n_days")}
    return None


# 每個輸出目錄 → (視窗, 這批在問什麼)。**視窗不同的格不可直接相比**（D-093）：
# 2019 視窗少了 2018 Q4 那個壓力體制，MDD 會系統性偏淺。
_DIRS: dict[str, tuple[str, str]] = {
    "tracks_lw_nodiv": ("2017-01-03", "D-087 公開因子四格"),
    "tracks_lw_risk_nodiv": ("2017-01-03", "D-091 風控三格"),
    "tracks_lw_turnover_nodiv": ("2017-01-03", "D-092 P1 降周轉"),
    "tracks_lw_timing_nodiv": ("2017-01-03", "D-092 P4 擇時"),
    "tracks_lw_all_nodiv": ("2017-01-03", "D-092 P5a 其餘公開因子"),
    "tracks_lw_matrix_nodiv": ("2017-01-03", "D-092 P7 六規則 × 公開因子基座"),
    "tracks_lw_revenue_nodiv": ("2017-01-03", "營收線（需 ML 分數的格已改列 2019）"),
    "tracks_lw_w2019_nodiv": ("2019-05-02", "D-093 配對對照（公開因子四格）"),
    "tracks_lw_risk_w2019_nodiv": ("2019-05-02", "D-093 配對對照（風控三格）"),
    "tracks_lw_ml_w2019_nodiv": ("2019-05-02", "D-092 P2 ML 訊號進各載具"),
    "tracks_lw_revenue_w2019_nodiv": ("2019-05-02", "營收線"),
    "tracks_lw_revmom_w2019_nodiv": ("2019-05-02", "D-092 P8 營收 × 動能"),
    "tracks_lw_all_ml_w2019_nodiv": ("2019-05-02", "D-092 P5b 其餘 ML 策略"),
    "tracks_lw_f1_timing_w2019_nodiv": ("2019-05-02", "D-092 P6 F1 × 兩條擇時"),
    "tracks_lw_matrix_ml_w2019_nodiv": ("2019-05-02", "D-092 P7 六規則 × ML 基座"),
}


def _tier(cagr: float, mdd: float) -> str:
    """D-087 的分層。甲層原文是「MDD ≤ 30% **且 CAGR > 0**」——只看 MDD 會把
    「常態空手所以回撤淺」的事件型策略誤判為甲（LW-C2b／C2c／M3 三格 CAGR 都是負的）。
    虧錢不是站得住。"""
    if cagr <= 0:
        return "丙"
    return "甲" if abs(mdd) <= 0.30 else "乙" if abs(mdd) <= 0.40 else "丙"


def _discover_all() -> list[dict[str, object]]:
    """掃過所有輸出目錄，把**每一格**都列出來——不只上面手寫說明的那些。

    手寫清單負責敘事（名稱、在做什麼、對照誰），這裡負責**完整性**：
    使用者要的是「全部跑過的策略都盤點完」，而手寫清單永遠會落後於實際跑的格數。
    """
    bench = _bench_by_window()
    rows: list[dict[str, object]] = []
    for dname, (window, batch) in _DIRS.items():
        d = R / dname
        if not d.exists():
            continue
        for f in sorted(d.glob("*_1x.json")):
            try:
                m = json.loads(f.read_text(encoding="utf-8"))["metrics"]
            except (json.JSONDecodeError, KeyError, OSError):
                # 只吞這三種：正在寫入的半成品 JSON、缺 metrics 的舊格式、讀檔失敗。
                # **不吞其他例外**——那會讓真的壞掉靜默變成「這一格沒跑」。
                continue
            rows.append(
                {
                    "code": f.stem.split("_", 1)[0],
                    "window": window,
                    "batch": batch,
                    "cagr": m["cagr"],
                    "mdd": m["mdd"],
                    "sharpe": m["sharpe"],
                    "turnover": m.get("turnover"),
                    "tier": _tier(m["cagr"], m["mdd"]),
                    "vs_bench": (
                        None
                        if window not in bench
                        else {
                            "cagr_pp": (m["cagr"] - bench[window]["cagr"]) * 100,
                            "mdd_pp": (abs(bench[window]["mdd"]) - abs(m["mdd"])) * 100,
                            "sharpe": m["sharpe"] - bench[window]["sharpe"],
                        }
                    ),
                }
            )
    return sorted(rows, key=lambda r: (str(r["window"]), -float(r["sharpe"])))


def _window_calibration() -> dict[str, object]:
    """同一份設定在兩個視窗下的差——**這是跨視窗比較能不能做的唯一依據**。

    D-093 只能事前說「2019 視窗少了 2018 Q4，MDD 應該偏淺」；配對對照跑完之後
    才知道偏多少。若 MDD 幾乎不動而 CAGR 動很大，那結論是
    「回撤可以跨視窗比、報酬不行」——比籠統地說「都不可比」精確得多。
    """
    pairs: list[dict[str, object]] = []
    for a_dir, b_dir in (
        ("tracks_lw_nodiv", "tracks_lw_w2019_nodiv"),
        ("tracks_lw_risk_nodiv", "tracks_lw_risk_w2019_nodiv"),
    ):
        da, db = R / a_dir, R / b_dir
        if not (da.exists() and db.exists()):
            continue
        for fa in sorted(da.glob("*_1x.json")):
            fb = db / fa.name
            if not fb.exists():
                continue
            try:
                ma = json.loads(fa.read_text(encoding="utf-8"))["metrics"]
                mb = json.loads(fb.read_text(encoding="utf-8"))["metrics"]
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            pairs.append(
                {
                    "code": fa.stem.split("_", 1)[0],
                    "cagr_2017": ma["cagr"],
                    "cagr_2019": mb["cagr"],
                    "mdd_2017": ma["mdd"],
                    "mdd_2019": mb["mdd"],
                    "d_cagr_pp": (mb["cagr"] - ma["cagr"]) * 100,
                    "d_mdd_pp": (abs(ma["mdd"]) - abs(mb["mdd"])) * 100,  # 正＝2019 較淺
                }
            )
    if not pairs:
        return {"pairs": []}
    dm = sorted(float(p["d_mdd_pp"]) for p in pairs)
    dc = sorted(float(p["d_cagr_pp"]) for p in pairs)
    mid = len(pairs) // 2
    return {
        "pairs": pairs,
        "mdd_median_pp": dm[mid],
        "mdd_max_pp": dm[-1],
        "cagr_median_pp": dc[mid],
        "cagr_max_pp": dc[-1],
    }


# 跑不出來、而「跑不出來」本身就是結果的格。**不是待修的錯**——
# 放寬設定讓它跑得出來，等於改配置去迎合結果（D-092 明文不調 keep_pct／top_n）。
_INFEASIBLE: list[dict[str, str]] = [
    {
        "code": "LW-MGALLm",
        "window": "2017-01-03",
        "name": "動能池 ＋ 五道濾網全上",
        "why": "五道濾網疊起來後實際入選只剩 **3 檔**，單股上限 0.15 讓權重加不到 1，引擎直接 raise。"
        "**這就是答案**：把所有濾網疊在一起，在長窗口的宇宙上沒有東西通得過。",
    },
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    got = {str(c["code"]): _metrics(c) for c in CELLS}
    rows = []
    for c in CELLS:
        m = got[str(c["code"])]
        base = got.get(str(c["base"])) if c["base"] else None
        row = dict(c)
        row["metrics"] = m
        # D-087 的甲層原文是「MDD ≤ 30% **且 CAGR > 0**」——只看 MDD 會把
        # 「常態空手所以回撤淺」的事件型策略誤判為甲層（LW-C2b/C2c/M3 三格
        # CAGR 都是負的卻拿到甲）。虧錢不是站得住。
        row["tier"] = None if m is None else _tier(m["cagr"], m["mdd"])
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

    every = _discover_all()
    payload = {
        "cells": rows,
        "benchmark": _BENCH_LONG,
        "done": sum(1 for r in rows if r["metrics"]),
        "total": len(rows),
        "all_results": every,
        "infeasible": _INFEASIBLE,
        "bench_by_window": _bench_by_window(),
        "calibration": _window_calibration(),
        "windows": {
            w: sum(1 for e in every if e["window"] == w)
            for w in sorted({str(e["window"]) for e in every})
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"敘事格 {payload['done']}／{payload['total']}；**全部結果 {len(every)} 格** → {OUT}")
    print(f"  各視窗格數：{payload['windows']}\n")
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
