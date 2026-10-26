#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
futures_signal_dataset.py — 台指期「訊號資料集」：每個進場訊號的當下特徵 + 影子交易結果

與 futures_replay.py 的差別：
    * 不模擬帳戶。每一個新的進場訊號（同一波段同一策略只算一次，依 signal_fingerprint）
      都各自開一筆「影子交易」，出場完全照 intraday 原規則（結構停損、動能衰退、EMA 死叉、
      13:00 清倉、緊急停損），不受每日次數上限、冷卻期、已有持倉的影響。
    * 同時記下 Trading 的送單前驗證（停損距離、追價、波段延伸、結構／VWAP／錨點）是否通過，
      分析時可以只看「系統真的會送單」的那一群。
    * 大盤許可不當閘門，改記成特徵（與日線 MA5 的距離、當日漲跌、開盤跳空）。

輸出 events.csv：一列一個訊號；標籤欄位以 label_ 開頭，其餘都是訊號當下就知道的資訊。

用法：python futures_signal_dataset.py --repo ... --taifex taifex_compact --out signal_dataset --workers 4
"""
from __future__ import annotations

import argparse
import multiprocessing
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_replay as M  # noqa: E402
import futures_replay as F  # noqa: E402

_W: Dict[str, Any] = {}


def _init(repo: str, params_json: Optional[str], overrides: List[str]) -> None:
    _W["mods"] = M.load_modules(repo)
    _W["params"] = M.load_parameters(_W["mods"], params_json, overrides)


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or not b else round((a / b - 1) * 100, 5)


SOFT_EXITS = {"EMA_TREND_BREAK", "MOMENTUM_DECAY_PARTIAL", "MARKET_TREND_INVALIDATED", "RS_FAILURE"}


def mirror(data: Dict[str, Any], anchor: float) -> Dict[str, Any]:
    """放空＝把價格以昨收為中心上下翻轉後做多：p' = 2A − p。VWAP、EMA、高低點、分形都是線性的，
    翻轉後同一套做多規則就是對稱的放空規則；買在 p'+滑價 ＝ 賣在 p−滑價。"""
    return dict(data, ticks=[(t, s, round(2 * anchor - p, 2), q) for t, s, p, q in data["ticks"]])


def run_day(job) -> Dict[str, Any]:
    day, data, daily, cfg, side, exit_mode = job
    if side == "short":
        anchor = daily.get("prev_close") or data["ticks"][0][2]
        data = mirror(data, anchor)
        daily = dict(daily, ma5=None if daily.get("ma5") is None else 2 * anchor - daily["ma5"])
    mods, params = _W["mods"], _W["params"]
    intraday, trading, market_mod = mods["intraday"], mods["trading"], mods["market"]
    ip, tp, mp = params["intraday"], params["trading"], params["market"]
    intraday._STRATEGY_STATE_CACHE.clear()
    intraday.prune_calculation_cache()
    buffer = intraday.IncrementalEventBuffer(ip)
    symbol, market = cfg.product, "TAIFEX"
    candidate = {"symbol": symbol, "market": market, "trade_ready": True, "momentum_eligible": True,
                 "trend_eligible": True, "market_relative_strength": 1.0, "day_trade_eligible": True}
    context: Dict[str, Any] = {}
    next_refresh = last_eval = 0
    seen = set()
    events: List[Dict[str, Any]] = []
    open_shadows: List[Dict[str, Any]] = []
    day_open = high = low = None
    prev_close, ma5 = daily.get("prev_close"), daily.get("ma5")

    def close_shadow(shadow, t_us, price, reason):
        fill = price - cfg.slip_ticks * cfg.tick
        entry = shadow["entry_fill"]
        costs = 2 * cfg.commission + round(entry * cfg.point_value * cfg.tax_rate) + round(fill * cfg.point_value * cfg.tax_rate)
        net = (fill - entry) * cfg.point_value - costs
        risk = (entry - shadow["effective_stop"]) * cfg.point_value
        ev = shadow["event"]
        ev.update(label_exit_time=M.us_to_iso(t_us), label_exit_reason=reason, label_exit_fill=fill,
                  label_net_points=round(fill - entry - costs / cfg.point_value, 2),
                  label_gross_points=round(fill - entry, 2), label_net_pnl=round(net, 0),
                  label_net_r=round(net / risk, 4) if risk > 0 else None,
                  label_hold_minutes=round((t_us - shadow["entry_us"]) / 60e6, 2),
                  label_mfe_points=round(shadow["max"] - entry, 2), label_mae_points=round(shadow["min"] - entry, 2))

    for t_us, serial, price, size in data["ticks"]:
        now = M.us_to_dt(t_us)
        while t_us >= next_refresh:
            boundary = max(next_refresh, t_us - t_us % cfg.refresh_us)
            context = F.open_market_context(market_mod, mp, M.us_to_dt(boundary), day)
            next_refresh = boundary + cfg.refresh_us
        buffer.append_sequence([{"symbol": symbol, "market": market, "trade_date": day, "event_time": t_us,
                                 "serial": serial, "price": price, "size": size,
                                 "event_classification": "NEW"}], ip)
        day_open = price if day_open is None else day_open
        high = price if high is None else max(high, price)
        low = price if low is None else min(low, price)

        for shadow in list(open_shadows):
            if shadow["state"] == "PENDING_BUY" and t_us >= shadow["fill_after"]:
                fill = price + cfg.slip_ticks * cfg.tick
                emergency = fill * (1 - tp.emergency_stop_loss_pct / 100)
                shadow.update(state="OPEN", entry_fill=fill, entry_us=t_us, max=price, min=price,
                              effective_stop=max(shadow["structure_stop"], emergency),
                              position={"symbol": symbol, "market": market, "entry_price": fill, "quantity": 1,
                                        "managed_quantity": 1, "strategy_type": shadow["event"]["strategy"],
                                        "strategy_stop_price": shadow["structure_stop"],
                                        "emergency_stop_price": emergency, "entry_time": M.us_to_iso(t_us),
                                        "partial_exit_done": False})
                shadow["event"]["label_entry_fill"] = fill
            elif shadow["state"] == "OPEN":
                shadow["max"] = max(shadow["max"], price)
                shadow["min"] = min(shadow["min"], price)
                if exit_mode == "stop_close" and price <= shadow["effective_stop"]:
                    shadow.update(state="PENDING_SELL", fill_after=t_us + cfg.latency_us, reason="INITIAL_STOP")
            elif shadow["state"] == "PENDING_SELL" and t_us >= shadow["fill_after"]:
                close_shadow(shadow, t_us, price, shadow["reason"])
                open_shadows.remove(shadow)

        if t_us - last_eval < cfg.eval_interval_us:
            continue
        last_eval = t_us
        # 影子持倉：各自用原規則判斷出場
        for shadow in open_shadows:
            if shadow["state"] != "OPEN":
                continue
            exit_signal = intraday.get_signal_context(
                symbol=symbol, market=market, candidate=candidate, market_context=context,
                quote_sequence=buffer, parameters=ip, position=shadow["position"], include_aggregates=False)
            stage = str(exit_signal.get("exit_stage") or "")
            if not exit_signal.get("exit_signal"):
                continue
            # 出場變體：system＝原規則；structure＝忽略「軟出場」只留結構停損；stop_close＝只留初始停損與 13:00
            if exit_mode == "structure" and stage in SOFT_EXITS:
                continue
            if exit_mode == "stop_close" and stage not in {"EMERGENCY_STOP", "TIME_EXIT"}:
                continue
            shadow.update(state="PENDING_SELL", fill_after=t_us + cfg.latency_us, reason=stage)
        # 訊號流：不帶持倉，只看「新的」進場訊號
        signal = intraday.get_signal_context(symbol=symbol, market=market, candidate=candidate,
                                             market_context=context, quote_sequence=buffer, parameters=ip,
                                             position=None, include_aggregates=False)
        if not signal.get("entry_signal"):
            continue
        fingerprint = signal.get("signal_fingerprint")
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        metrics = signal.get("metrics") or {}
        sig = dict(signal, execution_trigger="AUTO")
        executable = price + cfg.slip_ticks * cfg.tick
        stop = float(sig.get("strategy_stop_price") or 0)
        signal_price = float(sig.get("signal_price") or price)
        gates = []
        if stop <= 0 or stop >= executable:
            gates.append("STRUCTURAL_STOP_INVALID")
        elif (executable - stop) / executable * 100 > tp.max_stop_distance_pct:
            gates.append("STRUCTURAL_STOP_TOO_WIDE")
        if (price - signal_price) / signal_price * 100 > tp.max_chase_pct:
            gates.append("CHASE_TOO_HIGH")
        wave = trading._entry_wave_extension_revalidation(sig, executable, tp)
        if not wave.get("ok"):
            gates.append(str(wave.get("rejection_code") or "WAVE_EXTENSION"))
        reval = trading._entry_momentum_revalidation(sig, price, tp, now=now)
        if not reval.get("ok"):
            gates.extend(f"REVAL_{r}" for r in reval.get("reasons") or [])
        vwap = M._to_float(metrics.get("vwap"))
        ema5, ema_slow = M._to_float(metrics.get("ema5")), M._to_float(metrics.get("ema20"))
        slope = M._to_float(metrics.get("ema_fast_slope"))
        event = {
            "day": day, "contract": data["contract"], "time": M.us_to_iso(t_us), "side": side, "exit_mode": exit_mode,
            "strategy": sig.get("strategy_type"), "setup": sig.get("entry_setup_type"),
            "strategy_setup": f"{sig.get('strategy_type')}/{sig.get('entry_setup_type')}",
            "gate_pass": not gates, "gate_reasons": ";".join(dict.fromkeys(gates)),
            "price": price, "structure_stop": stop,
            "f_minute": round((now.hour - 9) * 60 + now.minute + now.second / 60, 2),
            "f_day_change_pct": _pct(price, prev_close),
            "f_gap_pct": _pct(day_open, prev_close),
            "f_ma5_dev_pct": _pct(price, ma5),
            "f_vwap_dev_pct": _pct(price, vwap),
            "f_day_range_pct": None if not prev_close else round((high - low) / prev_close * 100, 5),
            "f_pos_in_range": None if high == low else round((price - low) / (high - low), 4),
            "f_volume_expansion": M._to_float(metrics.get("volume_expansion_ratio")),
            "f_range_expansion": M._to_float(metrics.get("range_expansion_ratio")),
            "f_mom_1m": M._to_float(metrics.get("momentum_1m")),
            "f_mom_3m": M._to_float(metrics.get("momentum_3m")),
            "f_mom_5m": M._to_float(metrics.get("momentum_5m")),
            "f_ema_spread_pct": None if not (ema5 and ema_slow) else round((ema5 - ema_slow) / price * 100, 5),
            "f_ema_slope_pct": None if slope is None else round(slope / price * 100, 5),
            "f_wave_extension_pct": M._to_float(sig.get("entry_wave_extension_pct")),
            "f_wave_age_bars": M._to_float(sig.get("entry_wave_age_bars")),
            "f_stop_distance_pct": None if stop <= 0 else round((executable - stop) / executable * 100, 5),
        }
        events.append(event)
        open_shadows.append({"event": event, "state": "PENDING_BUY", "fill_after": t_us + cfg.latency_us,
                             "structure_stop": stop})

    last_t, _, last_price, _ = data["ticks"][-1]
    for shadow in open_shadows:
        if shadow["state"] in {"OPEN", "PENDING_SELL"}:
            close_shadow(shadow, last_t, last_price, shadow.get("reason") or "EOD_FORCED_CLOSE")
        else:
            shadow["event"]["label_exit_reason"] = "NOT_FILLED"
    return {"day": day, "events": events, "ticks": len(data["ticks"])}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True)
    p.add_argument("--taifex", required=True)
    p.add_argument("--product", default="MTX", choices=sorted(F.PRODUCTS))
    p.add_argument("--params-json")
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--commission", type=float, default=30.0)
    p.add_argument("--tax-rate", type=float, default=0.00002)
    p.add_argument("--slip-ticks", type=int, default=1)
    p.add_argument("--latency-ms", type=float, default=500)
    p.add_argument("--eval-min-interval-ms", type=float, default=1000)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--side", choices=["long", "short"], default="long", help="short＝價格翻轉後套同一套做多規則")
    p.add_argument("--exit-mode", choices=["system", "structure", "stop_close"], default="system")
    p.add_argument("--out", default="signal_dataset")
    args = p.parse_args()

    data = F.read_taifex(Path(args.taifex), args.product)
    days = sorted(data)
    # 日線特徵：前一日收盤（同一合約；沒有就用前一日主力合約）與前 5 日主力合約收盤平均
    daily: Dict[str, Dict[str, Optional[float]]] = {}
    for i, day in enumerate(days):
        if i == 0:
            daily[day] = {"prev_close": None, "ma5": None}
            continue
        previous = data[days[i - 1]]
        prev_close = previous["closes"].get(data[day]["contract"]) or previous["closes"].get(previous["contract"])
        history = [data[d]["closes"][data[d]["contract"]] for d in days[max(0, i - 5):i]]
        daily[day] = {"prev_close": prev_close, "ma5": statistics.fmean(history) if len(history) == 5 else None}
    cfg = F.Config(product=args.product, point_value=F.PRODUCTS[args.product], contracts=1,
                   commission=args.commission, tax_rate=args.tax_rate, slip_ticks=args.slip_ticks, tick=1.0,
                   latency_us=int(args.latency_ms * 1000), eval_interval_us=int(args.eval_min_interval_ms * 1000),
                   refresh_us=30 * M.US)
    jobs = [(day, data[day], daily[day], cfg, args.side, args.exit_mode) for day in days[1:]]  # 第一天沒有昨收，只當歷史
    results = []
    if args.workers > 1:
        with multiprocessing.Pool(args.workers, initializer=_init,
                                  initargs=(args.repo, args.params_json, args.set)) as pool:
            for result in pool.imap(run_day, jobs):
                results.append(result)
                print(f"[{result['day']}] {result['ticks']:,} 筆成交，{len(result['events'])} 個訊號", flush=True)
    else:
        _init(args.repo, args.params_json, args.set)
        for job in jobs:
            result = run_day(job)
            results.append(result)
            print(f"[{result['day']}] {result['ticks']:,} 筆成交，{len(result['events'])} 個訊號", flush=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    events = [e for r in sorted(results, key=lambda r: r["day"]) for e in r["events"]]
    M.write_csv(out / "events.csv", events)
    counts = Counter((e["strategy_setup"], e["gate_pass"]) for e in events)
    print(f"[完成] {len(events)} 個訊號 → {out / 'events.csv'}")
    for (setup, ok), n in sorted(counts.items()):
        print(f"   {setup:40s} 通過閘門={ok}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
