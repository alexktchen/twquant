"""由回測結果包產出「換股進出清單」HTML（T-230）。

    uv run --all-groups python scripts/build_trade_journal.py \
        --bundles D:/project/twquant/data/tracks_vault_nodiv \
        --out D:/project/twquant/data/reports/trade_journal_vault.html

只讀既有結果包的 ledger，**不重跑回測**，所以不增加 DSR 試驗數，也碰不到
第二保留期（D-109）。`--out` 必須是絕對路徑（CLAUDE.md §0.2 的兩個 data/ 陷阱）。
股名取自 PG `stock_meta`；PG 不可達即失敗，不退回「只有代號」的半成品。
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any

import psycopg

from twquant.reporting.trade_journal import build_journal, journal_to_dicts

_TEMPLATE = Path(__file__).with_name("trade_journal_template.html")


def _names(dsn: str, schema: str) -> dict[str, str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(f'SELECT stock_id, name FROM "{schema}".stock_meta').fetchall()
    return {str(sid): str(name) for sid, name in rows if name}


def _entry(path: Path) -> dict[str, Any]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    strategy = bundle["strategy"]
    return {
        "code": path.name.split("_")[0],
        "file": path.name,
        "name": strategy.get("name"),
        "description": strategy.get("description"),
        "start": bundle["start"],
        "end": bundle["end"],
        "initial_capital": bundle["initial_capital"],
        "universe": strategy.get("universe"),
        "rebalance": strategy.get("rebalance"),
        "top_n": (strategy.get("selection") or {}).get("top_n"),
        "metrics": bundle["metrics"],
        "n_sessions": len(bundle["equity"]),
        "days": journal_to_dicts(build_journal(bundle)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True, nargs="+", help="結果包 JSON 或其所在目錄")
    ap.add_argument("--out", required=True, help="輸出 HTML（絕對路徑）")
    ap.add_argument("--title", default="換股進出清單")
    ap.add_argument("--schema", default="serving")
    args = ap.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        raise SystemExit(f"--out 必須是絕對路徑，得到 {args.out}")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("缺 TWQUANT_PG_DSN（股名取自 PG stock_meta）")

    paths: list[Path] = []
    for raw in args.bundles:
        p = Path(raw)
        paths.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    if not paths:
        raise SystemExit("沒有找到任何結果包")

    payload = {
        "title": args.title,
        "strategies": [_entry(p) for p in paths],
        "names": _names(dsn, args.schema),
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (
        _TEMPLATE.read_text(encoding="utf-8")
        .replace("__TITLE__", html.escape(args.title))
        .replace("__DATA__", blob)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    n_days = sum(len(s["days"]) for s in payload["strategies"])
    print(f"[OK] {len(paths)} 個結果包、{n_days} 個成交日 → {out}")


if __name__ == "__main__":
    main()
