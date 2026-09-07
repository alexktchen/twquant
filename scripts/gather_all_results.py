"""盤點全部結果，供總結報告使用。輸出一份 JSON，數字只從落地檔案讀，不重算。

刻意把「短窗口」與「長窗口」分開列，且**標明每一批的報酬口徑**
（含息 vs 價格報酬）——兩種口徑的 CAGR 不可放進同一張表比較（D-085）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

R = Path("D:/project/twquant/data")


def _models() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for d in sorted((R / "models").iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        mp = d / "meta.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        ics = [float(x) for x in m.get("window_ics", [])]
        out.append(
            {
                "model_id": m["model_id"],
                "long_window": "_lw_" in m["model_id"],
                "kind": (
                    "net"
                    if "_net_" in m["model_id"]
                    else "rev"
                    if "_rev_" in m["model_id"]
                    else "dd"
                    if "_dd_" in m["model_id"]
                    else "rank"
                ),
                "horizon": m["horizon_days"],
                "data_from": m["data_from"],
                "data_to": m["data_to"],
                "train_end": m["train_end"],
                "oos_rank_ic": float(m["oos_rank_ic"]),
                "n_windows": len(ics),
                "pos_windows": sum(1 for x in ics if x > 0),
                "n_features": len(m.get("features", [])),
            }
        )
    return out


def _bundles() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for d in sorted(R.iterdir()):
        if not d.is_dir() or not d.name.startswith("track"):
            continue
        for f in sorted(d.glob("*.json")):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            m = b.get("metrics")
            if not m:
                continue
            out.append(
                {
                    "dir": d.name,
                    "file": f.stem,
                    "long_window": "_lw" in d.name,
                    # **口徑以目錄名為準，不以欄位為準**：最早跑完的那批結果包在
                    # `credit_dividends` 序列化之前就落地了，欄位缺席會被讀成 False，
                    # 而它們其實是含息的重跑。目錄名 `*_div` 是當時就決定好的、可靠。
                    "credit_dividends": d.name.endswith("_div"),
                    "div_field_present": "credit_dividends" in b,
                    "cagr": m.get("cagr"),
                    "mdd": m.get("mdd"),
                    "sharpe": m.get("sharpe"),
                    "n_days": m.get("n_days"),
                    "start": b.get("start"),
                    "end": b.get("end"),
                    "final_equity": b.get("final_equity"),
                    "initial_equity": b.get("initial_equity"),
                    "turnover": m.get("turnover"),
                    "calmar": m.get("calmar"),
                    "win_rate": m.get("win_rate"),
                    "n_dividends": len(b.get("dividends") or []),
                }
            )
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    models, bundles = _models(), _bundles()
    ends = {b["end"] for b in bundles if b["end"]}
    payload = {
        "models": models,
        "bundles": bundles,
        "backtest_end_dates": sorted(ends),
        "n_bundles": len(bundles),
    }
    out = Path("D:/project/twquant/data/reports/all_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"模型 {len(models)} 個、結果包 {len(bundles)} 份 → {out}\n")
    print("模型：")
    for m in models:
        tag = "長" if m["long_window"] else "短"
        print(
            f"  [{tag}] {m['model_id']:40s} IC {m['oos_rank_ic']:+.4f} "
            f"{m['pos_windows']:>2}/{m['n_windows']:<2} 窗　訓練 {m['data_from']}~{m['data_to']}"
        )
    print(f"\n回測結果包的結束日（**金庫期是否被動過的判據**）：{sorted(ends)}")
    div = {b["credit_dividends"] for b in bundles}
    print(
        f"報酬口徑分布：含息 {sum(1 for b in bundles if b['credit_dividends'])} 份、"
        f"價格報酬 {sum(1 for b in bundles if not b['credit_dividends'])} 份"
    )
    print(f"長窗口結果包 {sum(1 for b in bundles if b['long_window'])} 份")


if __name__ == "__main__":
    main()
