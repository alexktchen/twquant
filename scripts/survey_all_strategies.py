"""D-134 §八：重新盤點**所有**既有策略結果——依使用者的風險門檻排序，不比 0050。

    uv run --all-groups python scripts/survey_all_strategies.py \
        --out D:/project/twquant/data/reports/strategy_survey.html

**只讀既有結果包重算，不重跑任何回測**，故不增加試驗數（DSR 計數不變）。

**這是盤點，不是判定。** 全部落在已被反覆探勘的開發期資料上；任何一格看起來達標，
都只代表「值得事前登記後在新資料上驗證」，不代表可以部署。

揭露欄位刻意包含**換股結構**（每日補位／每月整批／每週）：使用者 2026-09-21 指出
「怎麼會有持有一批然後一個月再換一次 我又不是 ETF 我就是散戶買個股」——
月頻整批換股是法人／ETF 的做法，與使用者要的每日選股、逐檔進出不同。
一支策略的回撤再漂亮，結構不對就不該推薦，故本表把結構與績效並列。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any

import numpy as np
import yaml

DATA = pathlib.Path("D:/project/twquant/data")
REPO = pathlib.Path(__file__).resolve().parents[1]
DEV_END = dt.date(2026, 8, 31)
MDD_FLOOR = -0.20  # D-134 §四.1：使用者指定的回撤承受度（幅度 20%）
MIN_DAYS = 250  # 少於一年的視窗不可比，排除
# 幾乎空手的配置回撤當然淺——那不是風險控制，是沒參與。不設這條會讓
# 「年化 +0.0%、回撤 +0.0%、曝險 0%」這種從未進場的格名列榜首（實測 25 格全是這樣）。
MIN_EXPOSURE = 0.20


def _structure(cfg: dict[str, Any]) -> tuple[str, str]:
    """回傳（換股結構, 幾檔）。結構是使用者最在意的維度，不是附註。"""
    if cfg.get("slots"):
        n = cfg["slots"].get("n")
        return "每日補位", f"{n} 檔"
    reb = cfg.get("rebalance") or {}
    sched = {"monthly": "每月整批", "weekly": "每週整批", "daily": "每日"}.get(
        str(reb.get("schedule")), str(reb.get("schedule"))
    )
    sel = cfg.get("selection") or {}
    n = sel.get("top_n") or sel.get("entry_rank_pct")
    exits = len(cfg.get("exit_rules") or [])
    if exits:
        sched += f"＋{exits} 條出場"
    return sched, f"{n} 檔" if n else "—"


def _load_configs() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in (REPO / "configs" / "strategies").glob("*.yaml"):
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if name := cfg.get("name"):
            out[str(name)] = cfg
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    if not out.is_absolute():
        raise SystemExit(f"--out 必須是絕對路徑，得到 {args.out}")

    configs = _load_configs()
    rows: list[dict[str, Any]] = []
    skipped_late = 0
    for path in sorted(DATA.glob("tracks_*/*.json")):
        try:
            b = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "equity" not in b or "metrics" not in b:
            continue
        if dt.date.fromisoformat(b["end"]) > DEV_END:
            skipped_late += 1  # 第二保留期不得納入任何績效統計
            continue
        eq = np.array([e[1] for e in b["equity"]], dtype=float)
        if len(eq) < args.min_days:
            continue
        name = b.get("strategy")
        if isinstance(name, dict):
            name = name.get("name")
        cfg = configs.get(str(name), {})
        struct, held = _structure(cfg)
        pos = np.array([e[3] for e in b["equity"]], dtype=float)
        expo = pos / eq
        m = b["metrics"]
        rows.append(
            {
                "name": str(name),
                "track": path.parent.name.replace("tracks_", ""),
                "struct": struct,
                "held": held,
                "start": b["start"],
                "end": b["end"],
                "days": len(eq),
                "cagr": float(m["cagr"]),
                "mdd": float(m["mdd"]),
                "calmar": float(m["calmar"]),
                "expo": float(expo.mean()),
                "low_expo": float((expo < 0.5).mean()),
                "meets": bool(
                    m["mdd"] >= MDD_FLOOR and m["cagr"] > 0.0 and float(expo.mean()) >= MIN_EXPOSURE
                ),
                "idle": bool(float(expo.mean()) < MIN_EXPOSURE),
            }
        )

    rows.sort(key=lambda r: (r["idle"], -r["mdd"], -r["calmar"]))
    meets = [r for r in rows if r["meets"]]
    print(f"{len(rows)} 個結果包（{skipped_late} 個因越過保留期邊界排除）\n")
    idle = [r for r in rows if r["idle"]]
    print(
        f"達標（回撤幅度 ≤ {abs(MDD_FLOOR):.0%}、年化 > 0、"
        f"平均曝險 ≥ {MIN_EXPOSURE:.0%}）：**{len(meets)} 個**"
    )
    print(f"另有 {len(idle)} 個因平均曝險 < {MIN_EXPOSURE:.0%}（幾乎空手）不列入比較")
    print(f"\n{'策略':<22}{'結構':<18}{'年化':>9}{'回撤':>9}{'Calmar':>8}{'曝險':>7}  視窗")
    for r in rows[:18]:
        mark = " ★" if r["meets"] else "  "
        print(
            f"{r['name'][:21]:<22}{r['struct'][:17]:<18}{r['cagr']:>+9.1%}{r['mdd']:>+9.1%}"
            f"{r['calmar']:>8.2f}{r['expo']:>7.0%}{mark}{r['start'][:7]}~{r['end'][:7]} ({r['days']}d)"
        )

    by_struct: dict[str, list[float]] = {}
    for r in rows:
        by_struct.setdefault(r["struct"].split("＋")[0], []).append(r["mdd"])
    print(f"\n{'結構':<14}{'格數':>6}{'回撤中位':>10}{'最淺回撤':>10}")
    for s, v in sorted(by_struct.items(), key=lambda kv: -len(kv[1])):
        print(f"{s:<14}{len(v):>6}{float(np.median(v)):>+10.1%}{max(v):>+10.1%}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(
            {"generated_at": dt.datetime.now().astimezone().isoformat(), "rows": rows},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n[OK] {len(rows)} 列 → {out.with_suffix('.json')}")


if __name__ == "__main__":
    main()
