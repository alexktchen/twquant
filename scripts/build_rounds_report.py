"""把五輪 S／R 系列的結果彙整成一份自包含 HTML 報告（D-130）。

    uv run --all-groups python scripts/build_rounds_report.py \
        --out D:/project/twquant/data/reports/rounds_report.html

只讀既有結果包與模型目錄，**不重跑任何回測、不重訓任何模型**，故不增加試驗數。
所有數字都由程式從結果包算出，不手抄——手抄的報告會在下一次重跑後變成錯的。
`--out` 必須是絕對路徑（CLAUDE.md §0.2 的兩個 data/ 陷阱）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
from typing import Any

import numpy as np

from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.reporting.trade_journal import build_journal

DATA = pathlib.Path("D:/project/twquant/data")
CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"
TEMPLATE = pathlib.Path(__file__).with_name("rounds_report_template.html")
DEV_END = dt.date(2026, 8, 31)

# 每一格**在它自己登記下**的判定。不得用後來才登記的判準回頭判舊結果——
# 那正是登記制度要防的事後挑選（D-128 §四 的判準只適用於第四、五輪）。
VERDICTS: dict[str, tuple[str, str]] = {
    "LW-M1": ("不可部署", "D-107：金庫期回撤 −35.1%，8/8 策略全輸"),
    "E0": ("不送檢", "D-117：七支出場規則沒有一支長窗口回撤 ≤ 20%"),
    "E3": ("不送檢", "D-117：同上；且讓回撤變深"),
    "E7": ("不送檢", "D-117：同上；七支裡唯一壓低回撤的"),
    "H": ("未通過", "D-125：勝率 81.2% 達標，但 69 筆 < 100 筆"),
    "L": ("通過", "D-125：盈虧比 3.71 ≥ 2、38 筆 ≥ 30、年化 > 0"),
    "H60": ("事後探索", "D-126：門檻是看到結果後挑的，不列入通過與否"),
    "S4n30": ("未通過", "D-130：月區塊 t 1.58 < 2、52 筆 < 300"),
    "S4n40": ("未通過", "D-130：月區塊 t 1.38 < 2、62 筆 < 300"),
    "S4n50": ("未通過", "D-130：月區塊 t 1.44 < 2、72 筆 < 300"),
    "R5a": ("不可判定", "D-130：只有停損 → 已結束交易全是輸家"),
    "R5b": ("未通過", "D-130：月區塊 t 0.48 < 2（筆數與分布皆達標）"),
}

# (代號, 顯示名, 結果包路徑, 輪次, 一句話說明)
CELLS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "LW-M1",
        "第一輪 · 月頻換股（對照）",
        "tracks_lw_ml_w2019_nodiv/LW-M1_lw_m1_ml_c1_1x.json",
        "1",
        "20 檔、每月整批換股，無出場規則",
    ),
    (
        "E0",
        "第一輪 · 15 檔無出場",
        "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json",
        "1",
        "E 系列的對照組",
    ),
    (
        "E3",
        "第一輪 · 跌破 20 日線賣",
        "tracks_e_long_nodiv/E3_lw_e3_ma20_1x.json",
        "1",
        "七種出場規則裡的代表",
    ),
    (
        "E7",
        "第一輪 · 回撤模型說賣",
        "tracks_e_long_nodiv/E7_lw_e7_dd_gate_1x.json",
        "1",
        "七種裡唯一壓低回撤的",
    ),
    (
        "H",
        "第二輪 · 高勝率（門檻 0.70）",
        "tracks_s_long_nodiv/Sh_s_h_tb_1x.json",
        "2",
        "小賺就跑，出場與標籤對齊",
    ),
    (
        "L",
        "第二輪 · 讓獲利奔跑（0.80）",
        "tracks_s_long_nodiv/Sl_s_l_tb_1x.json",
        "2",
        "唯一通過事前判準的一組",
    ),
    (
        "H60",
        "第二輪 · 降門檻到 0.60",
        "tracks_s_long_nodiv/Sh60_s_h60_tb_1x.json",
        "2",
        "事後探索，非事前登記",
    ),
    ("S4n30", "第四輪 · 30 檔", "tracks_s4_long_nodiv/S4n30_1x.json", "4", "41 特徵、門檻 0.55"),
    ("S4n40", "第四輪 · 40 檔", "tracks_s4_long_nodiv/S4n40_1x.json", "4", "41 特徵、門檻 0.55"),
    ("S4n50", "第四輪 · 50 檔", "tracks_s4_long_nodiv/S4n50_1x.json", "4", "41 特徵、門檻 0.55"),
    (
        "R5a",
        "第五輪 · 排序＋僅停損",
        "tracks_r5_long_nodiv/R5a_1x.json",
        "5",
        "逐筆統計不可判定（見報告說明）",
    ),
    (
        "R5b",
        "第五輪 · 排序＋L 的規則",
        "tracks_r5_long_nodiv/R5b_1x.json",
        "5",
        "五輪裡第一個每天都有候選的",
    ),
)


def _round_trip_cost(cfg: CostConfig) -> float:
    fee = float(cfg.commission_rate) * float(cfg.commission_discount) * 2
    return (fee + float(cfg.tax_rate_sell)) * float(cfg.cost_scale)


def _stats(path: pathlib.Path, rt: float) -> dict[str, Any]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if dt.date.fromisoformat(bundle["end"]) > DEV_END:
        raise SystemExit(f"{path.name} 結束於 {bundle['end']}，越過第二保留期邊界")
    eq = np.array([e[1] for e in bundle["equity"]], dtype=float)
    pos = np.array([e[3] for e in bundle["equity"]], dtype=float)
    dates = [e[0] for e in bundle["equity"]]
    ann = (eq[-1] / float(bundle["initial_capital"])) ** (252.0 / len(eq)) - 1.0
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    journal = build_journal(bundle)
    exits = [m for d in journal for m in d.moves if m.action == "exit" and m.ret is not None]
    entries = [d.date for d in journal for m in d.moves if m.action == "enter"]
    net = np.array([float(m.ret) for m in exits]) - rt if exits else np.array([])
    months: dict[str, list[float]] = {}
    for m, r in zip(exits, net, strict=True):
        months.setdefault(str(m.entry_date)[:7], []).append(float(r))
    means = [sum(v) / len(v) for v in months.values()]
    t = float("nan")
    if len(means) > 1:
        mm = sum(means) / len(means)
        var = sum((x - mm) ** 2 for x in means) / (len(means) - 1)
        t = mm / math.sqrt(var / len(means)) if var > 0 else float("nan")
    # 權益曲線降到每月一點（報告用，檔案不要太大）
    curve = [[dates[i], round(float(eq[i] / eq[0]), 4)] for i in range(0, len(eq), 21)]
    if curve[-1][0] != dates[-1]:
        curve.append([dates[-1], round(float(eq[-1] / eq[0]), 4)])
    return {
        "n_trades": len(net),
        "entry_months": len({d[:7] for d in entries}),
        "entry_years": sorted({d[:4] for d in entries}),
        "expectancy": float(net.mean()) if len(net) else None,
        "median_ret": float(np.median(net)) if len(net) else None,
        "win_rate": float((net > 0).mean()) if len(net) else None,
        "avg_win": float(net[net > 0].mean()) if (net > 0).any() else None,
        "avg_loss": float(net[net <= 0].mean()) if (net <= 0).any() else None,
        "month_t": None if math.isnan(t) else round(t, 2),
        "ann": float(ann),
        "mdd": float(mdd),
        "exposure": float(np.mean(pos / eq)),
        "open_positions": len(bundle.get("final_holdings") or []),
        "open_ret_median": (
            float(np.median([h["ret"] for h in bundle["final_holdings"] if h.get("ret")]))
            if bundle.get("final_holdings")
            else None
        ),
        "start": bundle["start"],
        "end": bundle["end"],
        "curve": curve,
        "rets": [round(float(x), 4) for x in net[:4000]],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        raise SystemExit(f"--out 必須是絕對路徑，得到 {args.out}")
    rt = _round_trip_cost(load_config(CONFIGS / "costs.yaml", CostConfig))

    cells = []
    for code, label, rel, rnd, note in CELLS:
        path = DATA / rel
        if not path.exists():
            print(f"  略過（結果包不存在）：{rel}")
            continue
        verdict, why = VERDICTS[code]
        cells.append(
            {
                "code": code,
                "label": label,
                "round": rnd,
                "note": note,
                "verdict": verdict,
                "verdict_why": why,
                **_stats(path, rt),
            }
        )
        print(f"  {code:<8}{cells[-1]['n_trades']:>6} 筆　{cells[-1]['entry_months']:>3} 個月")

    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "round_trip_cost": rt,
        "cells": cells,
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"[OK] {len(cells)} 格 → {out}（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
