#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ms_halfyear.py — 網友原版在台指期（小台、大台、微台）往前一段期間的每日損益估算（富邦 1 分 K 還原逐筆，同 studies/ms_tmf 的還原法）。

使用者 2026-10-29：「可以把台指期的復盤往前半年的賺賠都標出來嗎？我只要網友的策略」。
這是 1 分 K 估算，不是逐筆：studies/ms_tmf 量過，1 分 K 對網友原版每筆少算約 8～10 點（偏悲觀），網站上要標「估算」。
輸出 <out>/<商品代號>.json：{"product", "source": "minute_estimate", "days": {日: {"total", "n", "trades": [...]}}}。

用法：python ms_halfyear.py --repo C:/t/Momentum_Scalping --bars D:/.../fubon_1m --out D:/.../ms_halfyear --from 2026-04-28 --to 2026-10-27
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_minute_test as T  # noqa: E402


def compact(t: dict, pv: float) -> dict:
    return {"entry_time": t["entry_time"][11:19], "entry_price": t["entry_price"], "exit_time": t["exit_time"][11:19],
            "exit_price": json.loads(t["exit_legs"])[-1]["price"], "exit_reason": t["final_exit_reason"],
            "points": t["points"], "net_pnl": t["net_pnl"], "setup": f"{t['strategy']}/{t['setup']}"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True)
    p.add_argument("--bars", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--products", nargs="*", default=["MXF", "TXF", "TMF"])
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for prod in args.products:
        code, pv, _ = T.PRODUCTS[prod]
        days, excluded = T.main_contract_days(T.load_minute(Path(args.bars), prod))
        run = [d for d in sorted(days) if args.start <= d <= args.end]
        print(f"{prod}：{run[0]}～{run[-1]} 共 {len(run)} 天；資料不完整排除 {[d for d in excluded if args.start <= d <= args.end]}", flush=True)
        res, _, _ = T.run_days([(d, T.minute_data(days, d)) for d in run], args.repo, prod, args.workers)
        payload = {"product": code, "source": "minute_estimate", "from": run[0], "to": run[-1],
                   "days": {d: {"total": round(sum(t["net_pnl"] for t in res.get(d, [])), 0), "n": len(res.get(d, [])),
                                "trades": [compact(t, pv) for t in res.get(d, [])]} for d in run}}
        (out / f"{code}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tot = sum(v["total"] for v in payload["days"].values())
        print(f"{prod} 完成：{sum(v['n'] for v in payload['days'].values())} 筆、合計 {tot:+,.0f} 元", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
