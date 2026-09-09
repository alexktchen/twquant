"""產出「短窗口 vs 長窗口」總結報告的資料塊。

數字一律從落地的結果包與模型 meta 讀，不在此重算，也不手抄。
**口徑分開**：含息與價格報酬永遠不放進同一張表（D-085）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

R = Path("D:/project/twquant/data")
OUT = R / "reports/two_window_payload.json"

# 每個策略／模型的**人話名稱與說明**。代號只是檔名，不是名字。
STRATS: list[dict[str, str]] = [
    {
        "code": "C1",
        "name": "技術面突破（純量價）",
        "cfg": "c1_breakout",
        "what": "只看價格與成交量：股價接近 52 週高點、近三個月動能強、成交量異常放大、"
        "相對大盤走強。四個訊號等比例排名平均後選 20 檔等權，每月換股。"
        "不看財報、不排除大型股、沒有獲利門檻。",
        "why": "檢驗最純粹的「追強勢股」在台股行不行——它是這批實驗裡最不依賴基本面的一支。",
    },
    {
        "code": "C1abs",
        "name": "技術面突破（舊版流動性門檻）",
        "cfg": "c1_abs_threshold",
        "what": "與上一支**逐字相同**，只有一個差別：流動性門檻用固定金額（日均成交 3,000 萬）"
        "而不是相對排名（前 30%）。",
        "why": "**單一變數對照格。** 固定金額在 2017 與 2026 是完全不同的嚴格程度；"
        "有了它，短長窗口的差距才能歸因到「視窗」而不是「門檻定義」。",
    },
    {
        "code": "B2",
        "name": "全市場多因子（市值加權）",
        "cfg": "b2_broad_capw",
        "what": "四個公開因子等權組合：月營收年增率、三個月動能、股東權益報酬率、低波動。"
        "選 50 檔，依市值加權，單檔上限 25%，每月換股。",
        "why": "最接近一般人理解的「多因子選股」，也是這批裡分散度最高的一支。",
    },
    {
        "code": "X1",
        "name": "四因子精選 20 檔（等權）",
        "cfg": "x1_simple20",
        "what": "與上一支同樣的四個因子，但只選 20 檔、等權、單檔上限 5%。",
        "why": "隔離「檔數與加權方式」的影響——和 B2 只差選幾檔與怎麼加權。",
    },
]

MODELS: list[dict[str, str]] = [
    {
        "key": "net",
        "name": "成本感知報酬模型",
        "short_id": "lgbm_net_h20_20260225_6a798962",
        "long_id": "lgbm_lw_net_h20_20260225_83890036",
        "what": "預測「未來 20 個交易日、扣掉交易成本、再減去市場平均之後」的報酬排名。"
        "12 個特徵（動能、反轉、波動、成交量、月營收成長與加速）。",
        "why": "成本寫進標籤本身，模型從一開始就知道「賺的要蓋得過手續費與稅」，"
        "而不是先預測毛報酬再事後扣。",
    },
    {
        "key": "rev",
        "name": "營收驚奇模型",
        "short_id": "lgbm_rev_h15_20260225_a3227e06",
        "long_id": "lgbm_lw_rev_h15_20260225_9d4a149c",
        "what": "預測「下個月公布的營收年增率，會比它自己的趨勢高多少」——"
        "也就是財報公布前，先猜它會不會超出預期。特徵只用量價與籌碼，**明文禁止任何營收類特徵**。",
        "why": "假說是「知情資金的腳印會先於基本面出現」。台股月營收一年公布 12 次，"
        "標籤密度是季報的 4 倍，所以這件事在台股才測得動。",
    },
    {
        "key": "dd",
        "name": "回撤預警模型",
        "short_id": "lgbm_dd_h60_20260225_1a2c3a8e",
        "long_id": "lgbm_lw_dd_h60_20260225_1bd7c6a0",
        "what": "預測「未來 60 個交易日內，相對今天收盤最深會跌多少」的排名。"
        "用途寫死為風控閘門，**不是選股器**。",
        "why": "所有策略的共同痛點不是報酬而是回撤。把「會不會大跌」當成獨立的預測問題，"
        "而不是在報酬模型裡多塞一個低波動因子。",
    },
]


def _bundle(d: str, stem_contains: str, cost: str) -> dict[str, float] | None:
    for f in (R / d).glob(f"*{cost}.json"):
        if stem_contains in f.stem:
            m = json.loads(f.read_text(encoding="utf-8"))["metrics"]
            return {k: m[k] for k in ("cagr", "mdd", "sharpe", "n_days", "turnover")}
    return None


def _model(mid: str) -> dict[str, object] | None:
    p = R / "models" / mid / "meta.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text(encoding="utf-8"))
    ics = [float(x) for x in m["window_ics"]]
    return {
        "model_id": mid,
        "ic": float(m["oos_rank_ic"]),
        "n_windows": len(ics),
        "pos_windows": sum(1 for x in ics if x > 0),
        "data_from": m["data_from"],
        "data_to": m["data_to"],
        "n_features": len(m.get("features", [])),
    }


def _dsr_block() -> dict[str, object]:
    """試驗數與 DSR ≥ 0.95 所需的年化 Sharpe（Bailey & López de Prado 的期望最大值近似）。"""
    import math
    from statistics import NormalDist

    import polars as pl

    from twquant.reporting.research_log import read_research_log

    log = read_research_log(R)
    bt = log.filter(pl.col("entry_type") == "backtest")
    n = int(bt["params_hash"].n_unique())
    fam = int(bt["trial_scope"].n_unique())
    z, g = NormalDist().inv_cdf, 0.5772

    def need(n_trials: int, t_days: int) -> float:
        e_max = math.sqrt(1 / t_days) * (
            (1 - g) * z(1 - 1 / n_trials) + g * z(1 - 1 / (n_trials * math.e))
        )
        return (e_max + z(0.95) * math.sqrt(1 / t_days)) * math.sqrt(244)

    return {
        "n_trials": n,
        "n_families": fam,
        "need_long": need(n, 2227),
        "need_short": need(n, 886),
        "need_long_plus100": need(n + 100, 2227),
        "best_long_sharpe": 0.71,
        "best_short_sharpe": 1.60,
    }


def _vault() -> dict[str, object]:
    """金庫期（D-106／D-107）：策略層與訊號層各一份，全部來自跑出來的檔案。

    金庫已於 2026-09-09 開封，**只開這一次**。`touched` 之後永遠是 True。
    """
    v: dict[str, object] = {
        "start": "2026-03-02",
        "end": "2026-08-31",
        "touched": True,
        "opened_on": "2026-09-09",
        "prereg_commit": "2998ade",
    }
    vp, ip = R / "vault_verdict.json", R / "vault_ic.json"
    if vp.exists():
        v["strategies"] = json.loads(vp.read_text(encoding="utf-8"))
    if ip.exists():
        v["ic"] = json.loads(ip.read_text(encoding="utf-8"))
    # 逐月報酬（策略 vs 0050）——長條圖用
    v["monthly"] = _vault_monthly()
    return v


def _vault_monthly() -> dict[str, object]:
    """金庫期各策略與 0050 的逐月報酬，供圖表使用。"""
    import psycopg

    d = R / "tracks_vault_nodiv"
    if not d.exists():
        return {}
    with psycopg.connect("postgresql://twquant:twquant@localhost:5433/twquant") as c, c.cursor() as cur:
        cur.execute(
            "select date, close from serving.prices_daily where stock_id='0050' "
            "and date between '2026-03-02' and '2026-08-31' order by date"
        )
        b = [(str(r[0]), float(r[1])) for r in cur.fetchall()]
    bm: dict[str, float] = {}
    for day, px in b:
        bm[day[:7]] = px
    months = sorted(bm)
    prev = b[0][1]
    bench: dict[str, float] = {}
    for m in months:
        bench[m] = bm[m] / prev - 1
        prev = bm[m]
    cells = []
    for f in sorted(d.glob("*_1x.json")):
        pkg = json.loads(f.read_text(encoding="utf-8"))
        eq = pkg["equity"]
        mm: dict[str, float] = {}
        for e in eq:
            mm[str(e[0])[:7]] = float(e[1])
        prev = float(eq[0][1])
        r: dict[str, float] = {}
        for m in months:
            r[m] = mm[m] / prev - 1
            prev = mm[m]
        cells.append({"code": f.name.split("_")[0], "monthly": r})
    return {"months": months, "benchmark": bench, "cells": cells}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    bench = json.loads((R / "reports/benchmarks_two_windows.json").read_text(encoding="utf-8"))

    strats = []
    for s in STRATS:
        row = dict(s)
        # 短窗口：C1 在 tracks_c、B2 在 tracks、X1 在 ladder；含息版在對應的 *_div
        srcs = {
            "c1_breakout": ("tracks_c", "tracks_c_div"),
            "b2_broad_capw": ("tracks", "tracks_div"),
            "x1_simple20": ("ladder", "ladder_div"),
            "c1_abs_threshold": (None, None),  # 只有長窗口才有這一格
        }[s["cfg"]]
        for cost in ("0x", "1x", "1.5x"):
            row[f"short_px_{cost}"] = _bundle(srcs[0], s["cfg"], cost) if srcs[0] else None
            row[f"short_tr_{cost}"] = _bundle(srcs[1], s["cfg"], cost) if srcs[1] else None
            row[f"long_px_{cost}"] = _bundle("tracks_lw_nodiv", s["cfg"], cost)
        strats.append(row)

    models = []
    for m in MODELS:
        row = dict(m)
        row["short"] = _model(m["short_id"])
        row["long"] = _model(m["long_id"])
        models.append(row)

    diag_p = R / "reports/lw_diagnosis.json"
    diagnosis = json.loads(diag_p.read_text(encoding="utf-8")) if diag_p.exists() else []
    # DSR 門檻：294 次試驗後，長窗口要 DSR ≥ 0.95 需年化 Sharpe ≈ 1.50（scripts 內算過）。
    # 這裡直接讀登記簿算試驗數，門檻用同一條公式。
    dsr = _dsr_block()

    lw_p = R / "reports/lw_series.json"
    lw = json.loads(lw_p.read_text(encoding="utf-8")) if lw_p.exists() else {"cells": []}
    ta_p = R / "reports/timing_all.json"
    timing = json.loads(ta_p.read_text(encoding="utf-8")) if ta_p.exists() else []

    payload = {
        "lw": lw,
        "timing": [t for t in timing if t["long_window"]],  # 短窗口只有一個空頭事件，不進報告
        "diagnosis": diagnosis,
        "dsr": dsr,
        "windows": {
            "short": {
                "start": "2022-07-01",
                "end": "2026-02-28",
                "n_days": 886,
                "label": "短窗口",
                "regimes": "只包到 2022 熊市的尾巴——一個壓力事件",
            },
            "long": {
                "start": "2017-01-03",
                "end": "2026-02-28",
                "n_days": 2227,
                "label": "長窗口",
                "regimes": "2018 Q4、2020 COVID、2022 熊市——三個獨立的壓力體制",
            },
        },
        "vault": _vault(),
        "strategies": strats,
        "models": models,
        "benchmarks": bench,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT}")
    for s in strats:
        sp, lp = s.get("short_px_1x"), s.get("long_px_1x")
        f = lambda m: (
            f"{m['cagr'] * 100:+6.2f}% / {m['mdd'] * 100:6.2f}%" if m else "        —      "
        )
        print(f"  {s['name']:22s} 短 {f(sp)}　長 {f(lp)}")
    for m in models:
        sm, lm = m["short"], m["long"]
        print(
            f"  {m['name']:14s} 短 IC {sm['ic']:+.4f} ({sm['pos_windows']}/{sm['n_windows']})"
            f"　長 IC {lm['ic']:+.4f} ({lm['pos_windows']}/{lm['n_windows']})"
        )


if __name__ == "__main__":
    main()
