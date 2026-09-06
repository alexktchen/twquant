"""把所有回測結果包彙整成單一 JSON，供報告頁渲染（SPEC-013 §6）。

**設計原則：報告只讀這個 JSON，不自己算數字。** 所有指標、對齊、統計都在這裡完成
並落地，理由是 AC-11——先前的頁面因為在渲染層寫死比較數字，出現過「TAIEX −34.37%」
的錯標（那其實是等權宇宙的回撤）。資料與呈現分離之後，頁面上不可能出現沒有出處的數字。

每一列都帶 `capital` 與 `data_tables`：D-070 的教訓是**跨策略比較必須同資金**
（10M 下四因子 +9.50%、80M 下 +27.03%，結論會翻轉），而不同資金的結果放在同一張表
卻不標示，就是在誘導錯誤比較。

基準統計走 `reporting/benchmarks.py`：分割一律還原、股利只在含息時還原、
查無事件卻要求含息即 raise（T-151 的三個修正）。

用法：
    python scripts/report_bundle.py --out D:/project/twquant/data/reports/all_backtests.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl

from twquant.backtest.diagnostics import positions_from_bundle, round_trip_stats
from twquant.backtest.engine import Trade
from twquant.backtest.validation import paired_block_stats
from twquant.config import ServingConfig
from twquant.data.serving import store
from twquant.reporting.benchmarks import (
    bench_stats,
    benchmark_series,
    cumulative_factors,
    index_series,
)

ROOT = pathlib.Path("D:/project/twquant/data")

# (顯示名, 目錄, 檔名前綴, 分組, 一句話說明)
SPECS = [
    ("L0", "ladder", "L0_ml_net_h20_m", "階梯", "現況：ML 訊號、20 檔等權（舊凍結模型）"),
    ("L0-new", "ladder", "L0new_ladder_l0_new", "階梯", "ML 訊號、乾淨重訓模型（T-145）"),
    ("L1", "ladder", "L1_ladder_l1_n40", "階梯", "ML 訊號、40 檔"),
    ("L2", "ladder", "L2_ladder_l2_rev", "階梯", "ML 訊號、40 檔＋營收濾網"),
    (
        "L3",
        "ladder",
        "L3_ladder_l3_simple",
        "階梯",
        "四因子（營收/動能/ROE/低波）、40 檔＋營收濾網",
    ),
    ("L4", "ladder", "L4_ladder_l4_wt", "階梯", "同 L3，改分數加權"),
    ("X1", "ladder", "X1_ladder_x1_simple20", "階梯", "隔離格：只把訊號換成四因子，其餘同 L0"),
    ("A", "tracks", "A_track_a_smallcap", "雙軌", "小型飆股：排除市值前 100 大＋營收成長"),
    (
        "B1",
        "tracks",
        "B1_track_b1_large_enhanced",
        "雙軌",
        "大型增強：市值前 100 大、30 檔市值加權",
    ),
    (
        "B1-ML",
        "tracks",
        "B1ML_track_b1ml_large_enhanced",
        "雙軌",
        "同 B1，只把訊號換成 ML（D-067 的第四格）",
    ),
    ("B2", "tracks", "B2_track_b2_broad_capw", "雙軌", "廣泛市值加權"),
    (
        "C1",
        "tracks_c",
        "C1_track_c1_breakout",
        "C 軌",
        "52 週高點×動能×量能×相對強弱（不排大型股、無獲利門檻）",
    ),
    ("C2", "tracks_c2", "C2_track_c2_wbottom", "C 軌", "W 底事件閘門＋固定格位"),
    (
        "C2b",
        "tracks_c2bc",
        "C2b_track_c2b_boxbreak",
        "C 軌",
        "箱型突破事件＋固定格位（多數日子低曝險）",
    ),
    (
        "C2c",
        "tracks_c2bc",
        "C2c_track_c2c_flaghold",
        "C 軌",
        "旗形有撐事件＋固定格位（多數日子空手）",
    ),
    ("C3", "tracks_c3", "C3_track_c3_chipflow", "C 軌", "投信/外資/融資/券資比＋52 週高點與量能"),
    (
        "C4",
        "tracks_c4",
        "C4_track_c4_voltarget",
        "C 軌",
        "同 C1，加波動率縮放 overlay（Barroso–Santa-Clara）",
    ),
    ("M1", "tracks_m", "M1_track_m1_ml_c1", "M 系列", "ML 前 30% 當池子 × C1 技術排序"),
    ("M2", "tracks_m23", "M2_track_m2_ml_flag", "M 系列", "ML 池 × 回踩有撐才進場（固定格位）"),
    ("M3", "tracks_m23", "M3_track_m3_mom_flag", "M 系列", "**隔離格**：同 M2 但池子改用動能"),
    ("M4", "tracks_m", "M4_track_m4_ml_chip", "M 系列", "ML 池 × C3 籌碼排序"),
    ("M5", "tracks_m", "M5_track_m5_ml_large", "M 系列", "ML 池 × B1 大型股市值加權"),
    ("M6", "tracks_m", "M6_track_m6_ml_voltarget", "M 系列", "M1 ＋ 波動率縮放"),
    ("G1", "tracks_g", "G1_track_g1_ml_revenue", "G 濾網矩陣", "ML ＋ 營收成長濾網"),
    ("G2", "tracks_g", "G2_track_g2_ml_lowvol", "G 濾網矩陣", "ML ＋ 低波動池"),
    ("G3", "tracks_g", "G3_track_g3_ml_voltarget", "G 濾網矩陣", "ML ＋ 波動率縮放"),
    ("G4", "tracks_g", "G4_track_g4_ml_smooth", "G 濾網矩陣", "ML ＋ 分數 EMA 平滑"),
    ("G5", "tracks_g", "G5_track_g5_ml_large", "G 濾網矩陣", "ML ＋ 大型股宇宙"),
    ("G6", "tracks_g", "G6_track_g6_ml_flaggate", "G 濾網矩陣", "ML ＋ 型態進場閘門"),
    ("GALL", "tracks_g", "GALL_track_gall_ml_allgates", "G 濾網矩陣", "六個濾網全上"),
]
SCALES = ["1x", "0x", "1.5x"]


def _load(base: str, prefix: str, scale: str) -> dict | None:
    p = ROOT / base / f"{prefix}_{scale}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _exposure(b: dict) -> float | None:
    eq = [(float(r[1]), float(r[2])) for r in b["equity"] if float(r[1]) > 0]
    return sum(1.0 - c / e for e, c in eq) / len(eq) if eq else None


def _holdings(b: dict) -> dict[str, float]:
    """實際持股檔數——**與設定的 top_n 可能差很多**，那正是要呈現的事實。

    80M 下賣單被參與率上限擋住，想出場的部位殘留；10M 下買不起一張故持有不足。
    只列 top_n 而不列實際持股，會讓「20 檔 vs 40 檔」的比較看起來乾淨，實際不然。
    """
    pos = positions_from_bundle(b)
    if pos.height == 0:
        return {}
    n = pos.group_by("date").len().sort("date")["len"]
    return {"median": float(n.median()), "min": int(n.min()), "max": int(n.max())}


def _invested_stats(b: dict) -> dict[str, float | int]:
    """**有部位時**的報酬統計——低曝險策略只看 CAGR 會被完全誤解。

    C2b（箱型突破）平均曝險只有 17%、CAGR +0.33%、MDD −5.5%：它大部分時間在現金，
    所以既不賠也不賺。這時候「訊號好不好」的問題不是 CAGR，而是
    **實際持有部位的那些日子，賺得比市場多嗎**。

    定義：曝險 > 5% 的交易日視為「有部位」。回傳那些日子的日報酬年化與勝率，
    以及有部位的日子佔比。這三個數字合起來才能判讀事件型策略。
    """
    eq = [(dt.date.fromisoformat(str(r[0])), float(r[1]), float(r[2])) for r in b["equity"]]
    eq = [(d, e, c) for d, e, c in eq if e > 0]
    if len(eq) < 2:
        return {}
    rets, n_in = [], 0
    for i in range(1, len(eq)):
        _, e0, c0 = eq[i - 1]
        _, e1, _ = eq[i]
        if 1.0 - c0 / e0 > 0.05:  # 前一日收盤有部位 → 今日的報酬歸因給它
            n_in += 1
            rets.append(e1 / e0 - 1.0)
    if not rets:
        return {"days_invested": 0, "share_invested": 0.0}
    mean = sum(rets) / len(rets)
    return {
        "days_invested": n_in,
        "share_invested": n_in / (len(eq) - 1),
        "ann_return_when_invested": (1.0 + mean) ** 252 - 1.0,
        "win_rate_when_invested": sum(1 for r in rets if r > 0) / len(rets),
    }


def _yearly(b: dict) -> dict[str, dict[str, float]]:
    """逐年報酬與該年平均曝險——報告的年度長條圖用。"""
    eq = [(dt.date.fromisoformat(str(r[0])), float(r[1]), float(r[2])) for r in b["equity"]]
    eq = [(d, e, c) for d, e, c in eq if e > 0]
    out: dict[str, dict[str, float]] = {}
    by_year: dict[int, list[tuple[dt.date, float, float]]] = {}
    for d, e, c in eq:
        by_year.setdefault(d.year, []).append((d, e, c))
    prev_end = eq[0][1] if eq else None
    for y in sorted(by_year):
        rows_y = by_year[y]
        end = rows_y[-1][1]
        if prev_end and prev_end > 0:
            out[str(y)] = {
                "ret": end / prev_end - 1.0,
                "exposure": sum(1.0 - c / e for _, e, c in rows_y) / len(rows_y),
                "n_days": len(rows_y),
            }
        prev_end = end
    return out


def _unfilled(b: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for u in b.get("unfilled", []) or []:
        r = str(u.get("reason", "?"))
        out[r] = out.get(r, 0) + 1
    return out


def _downsample(b: dict, every: int = 5) -> list[list[object]]:
    """權益曲線降採樣供繪圖：每 N 個交易日取一點，末點必留。

    886 個交易日 × 70 個策略全存會讓 JSON 膨脹到難以處理，而圖上肉眼分辨不出
    5 日以內的差異。末點必留是因為它決定終值，不能被降採樣抹掉。
    """
    eq = b.get("equity") or []
    if not eq:
        return []
    base = float(eq[0][1]) or 1.0
    pts = [[str(r[0]), round(float(r[1]) / base, 5)] for i, r in enumerate(eq) if i % every == 0]
    last = [str(eq[-1][0]), round(float(eq[-1][1]) / base, 5)]
    if not pts or pts[-1][0] != last[0]:
        pts.append(last)
    return pts


def _curve(b: dict) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(str(r[0])): float(r[1]) for r in b["equity"]}


def _paired_t(a: dict, b: dict) -> float | None:
    ca, cb = _curve(a), _curve(b)
    com = sorted(set(ca) & set(cb))
    days, diffs = [], []
    for i in range(1, len(com)):
        p, c = com[i - 1], com[i]
        if ca[p] > 0 and cb[p] > 0:
            days.append(c)
            diffs.append((ca[c] / ca[p] - 1.0) - (cb[c] / cb[p] - 1.0))
    st = paired_block_stats(days, diffs)
    return None if st.block_se in (None, 0.0) else st.mean / st.block_se


def _benchmarks(dates: list[dt.date]) -> list[dict]:
    scfg = ServingConfig(pg_schema="serving")
    conn = store.connect(scfg)
    try:
        px = pl.read_database(
            "select date, stock_id, close from serving.prices_daily "
            "where stock_id in ('0050','0056','0051','006201') and date >= '2022-06-01'",
            conn,
        )
        idx = pl.read_database(
            "select date, index_id, close from serving.index_daily where date >= '2022-06-01'", conn
        )
        ev = pl.read_database(
            "select date, stock_id, event_type, adj_factor from serving.corporate_events", conn
        )
    finally:
        conn.close()
    px = cumulative_factors(px.sort("stock_id", "date"), ev)

    def evd(sid: str) -> set[dt.date]:
        return set(ev.filter(pl.col("stock_id") == sid)["date"].to_list())

    specs = [
        (
            "0050_tr",
            "0050 元大台灣50（含息）",
            "市值型大盤代理——最誠實的對照",
            True,
            lambda: benchmark_series(px, "0050", total_return=True),
            evd("0050"),
        ),
        (
            "0050_px",
            "0050（不含息，分割已還原）",
            "與含息對照，量出股利貢獻",
            False,
            lambda: benchmark_series(px, "0050", total_return=False),
            evd("0050"),
        ),
        (
            "0056_tr",
            "0056 高股息（含息）",
            "高股息型對照",
            True,
            lambda: benchmark_series(px, "0056", total_return=True),
            evd("0056"),
        ),
        (
            "0051_tr",
            "0051 元大中型100（含息）",
            "**結構上不含台積電**（市值 51~150 名）——小型／中型策略的公平對照",
            True,
            lambda: benchmark_series(px, "0051", total_return=True),
            evd("0051"),
        ),
        (
            "006201_px",
            "006201 富邦櫃買（價格）",
            "純上櫃小型股的市場報酬。事件表無其股利，故**只能算價格報酬**，真實含息更高",
            False,
            lambda: benchmark_series(px, "006201", total_return=False),
            evd("006201"),
        ),
        (
            "TAIEX",
            "加權指數（價格指數，不含息）",
            "不含息故系統性低於含息基準",
            False,
            lambda: index_series(idx, "TAIEX"),
            set(),
        ),
    ]
    out = []
    for key, label, role, tr, fn, e in specs:
        try:
            st = bench_stats(
                fn(), dates, key=key, label=label, role=role, total_return=tr, event_dates=e
            )
            from twquant.reporting.benchmarks import align_to

            values = align_to(fn(), dates, name=label, event_dates=e)
            values0 = values[0] or 1.0
            out.append(
                {
                    "key": key,
                    "label": label,
                    "role": role,
                    "total_return": tr,
                    "cagr": st.metrics.cagr,
                    "mdd": st.metrics.mdd,
                    "sharpe": st.metrics.sharpe,
                    "annual": {str(k): v for k, v in sorted(st.annual.items())},
                    "curve": [
                        [str(dd), round(vv / values0, 5)]
                        for i, (dd, vv) in enumerate(zip(dates, values, strict=True))
                        if i % 5 == 0
                    ]
                    + [[str(dates[-1]), round(values[-1] / values0, 5)]],
                    "n_days": st.n_days,
                }
            )
        except Exception as exc:  # noqa: BLE001 — 據實記下哪個基準算不出來，不靜默略過
            out.append({"key": key, "label": label, "role": role, "error": str(exc)[:200]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="D:/project/twquant/data/reports/all_backtests.json")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    rows, missing = [], []
    ref_dates: list[dt.date] | None = None
    for name, base, prefix, group, desc in SPECS:
        for scale in SCALES:
            b = _load(base, prefix, scale)
            if b is None:
                missing.append(f"{name} {scale}")
                continue
            m = b["metrics"]
            if scale == "1x" and ref_dates is None:
                ref_dates = sorted(_curve(b))
            rt = round_trip_stats(
                [
                    Trade(
                        dt.date.fromisoformat(t["date"]),
                        t["stock_id"],
                        t["side"],
                        int(t["shares"]),
                        float(t["price"]),
                        float(t.get("commission", 0.0)),
                        float(t.get("tax", 0.0)),
                        float(t.get("slippage", 0.0)),
                    )
                    for t in b["ledger"]
                ]
            )
            rows.append(
                {
                    "name": name,
                    "group": group,
                    "desc": desc,
                    "scale": scale,
                    "capital": b["initial_capital"],
                    "start": b["start"],
                    "end": b["end"],
                    "cagr": m["cagr"],
                    "mdd": m["mdd"],
                    "sharpe": m["sharpe"],
                    "sortino": m.get("sortino"),
                    "calmar": m.get("calmar"),
                    "turnover": m["turnover"],
                    "win_rate_daily": m["win_rate"],
                    "n_days": m["n_days"],
                    "n_trades": len(b["ledger"]),
                    "exposure": _exposure(b),
                    "invested": _invested_stats(b),
                    "curve": _downsample(b),
                    "yearly": _yearly(b),
                    "holdings": _holdings(b),
                    "unfilled": _unfilled(b),
                    "final_equity": b["final_equity"],
                    "data_tables": {
                        k: [v[0], str(v[1])] for k, v in (b.get("data_tables") or {}).items()
                    },
                    "round_trip": {
                        "n_closed": rt.n_closed,
                        "n_open": rt.n_open,
                        "win_rate": rt.win_rate,
                        "profit_factor": rt.profit_factor,
                        "avg_win": rt.avg_win,
                        "avg_loss": rt.avg_loss,
                        "total_pnl": rt.total_pnl,
                    },
                }
            )
            print(f"  {name:8}{scale:6} CAGR {m['cagr']:+.2%}  MDD {m['mdd']:.1%}", flush=True)

    # 事前登記的配對比較（月度區塊 SE，D-061／R1）
    def get(n: str, s: str = "1x") -> dict | None:
        for name, base, prefix, _, _ in SPECS:
            if name == n:
                return _load(base, prefix, s)
        return None

    edges = []
    for label, a, b, note in (
        ("訊號（小型等權）= X1 − L0-new", "X1", "L0-new", "只換訊號，其餘逐字同"),
        ("架構 = B1-ML − L0-new", "B1-ML", "L0-new", "訊號固定為 ML，只換宇宙與加權"),
        ("訊號（大型加權）= B1 − B1-ML", "B1", "B1-ML", "架構固定，只換訊號"),
        ("因子選擇 = C1 − X1", "C1", "X1", "都是四因子，但選的因子完全不同"),
        ("風險管理 = C4 − C1", "C4", "C1", "逐字相同，只多波動率縮放 overlay"),
        # D-071 的判定邊（唯一乾淨的隔離）
        ("ML 當池子 = M2 − M3", "M2", "M3", "逐字相同，只把閘門從 ML 換成動能"),
        # D-072 的邊際效果：每格 = 基準只加一個濾網
        ("濾網 G1 營收 = G1 − L0-new", "G1", "L0-new", "只加營收成長濾網"),
        ("濾網 G2 低波動 = G2 − L0-new", "G2", "L0-new", "只加低波動池"),
        ("濾網 G3 波動縮放 = G3 − L0-new", "G3", "L0-new", "只加波動率縮放 overlay"),
        ("濾網 G4 EMA = G4 − L0-new", "G4", "L0-new", "只加分數 EMA 平滑"),
        ("濾網 G5 大型股 = G5 − L0-new", "G5", "L0-new", "只換成大型股宇宙"),
        ("濾網 G6 型態閘門 = G6 − L0-new", "G6", "L0-new", "只加型態進場閘門＋固定格位"),
        ("全部一起 = GALL − L0-new", "GALL", "L0-new", "六個濾網全上（**非各邊際效果之和**）"),
    ):
        ba, bb = get(a), get(b)
        if not (ba and bb):
            continue
        edges.append(
            {
                "label": label,
                "note": note,
                "a": a,
                "b": b,
                "d_cagr": ba["metrics"]["cagr"] - bb["metrics"]["cagr"],
                "d_mdd": ba["metrics"]["mdd"] - bb["metrics"]["mdd"],
                "d_sharpe": ba["metrics"]["sharpe"] - bb["metrics"]["sharpe"],
                "t": _paired_t(ba, bb),
                "same_capital": ba["initial_capital"] == bb["initial_capital"],
            }
        )

    bundle = {
        "generated_for": "SPEC-013 §6 回測總覽",
        "window": {
            "start": rows[0]["start"] if rows else None,
            "end": rows[0]["end"] if rows else None,
        },
        "rows": rows,
        "edges": edges,
        "benchmarks": _benchmarks(ref_dates or []),
        "missing": missing,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(
        f"\n已寫出 {out}：{len(rows)} 筆回測、{len(edges)} 條邊、{len(bundle['benchmarks'])} 個基準"
    )
    if missing:
        print(f"尚缺 {len(missing)} 筆（仍在跑或失敗）：{', '.join(missing[:12])}")


if __name__ == "__main__":
    main()
