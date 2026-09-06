"""D-067／T-147：把 B1 的優勢拆成「架構」與「訊號」兩條邊——2×2 補完後才拆得開。

|                  | ML 訊號   | 四因子訊號 |
|------------------|-----------|------------|
| 等權小型 20 檔    | L0new     | X1         |
| 大型市值加權 30 檔 | **B1-ML** | B1         |

  架構值多少 = B1-ML − L0new（訊號固定為 ML，只換宇宙與加權）
  訊號值多少 = B1 − B1-ML（架構固定，只換訊號）

兩條邊都用月度區塊 SE（D-061／R1）。**先驗共用表一致**，不比整體指紋
（訊號不同必然讀不同的表，見 t146_verdict.py 的教訓）。

D-067 事前登記的方向性假設：「B1 相對 L0 的優勢**主要來自架構**而非訊號」。
可證偽條件：若 B1-ML 明顯劣於 B1（t ≤ −2.0），代表四因子在大型股上確實優於 ML。

用法：
    python scripts/d067_edges.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.backtest.validation import paired_block_stats

ROOT = pathlib.Path("D:/project/twquant/data")
CELLS = {
    "L0new": ROOT / "ladder" / "L0new_ladder_l0_new_1x.json",
    "X1": ROOT / "ladder" / "X1_ladder_x1_simple20_1x.json",
    "B1ML": ROOT / "tracks" / "B1ML_track_b1ml_large_enhanced_1x.json",
    "B1": ROOT / "tracks" / "B1_track_b1_large_enhanced_1x.json",
    "A": ROOT / "tracks" / "A_track_a_smallcap_1x.json",
    "B2": ROOT / "tracks" / "B2_track_b2_broad_capw_1x.json",
}


def _load(k: str) -> dict:
    p = CELLS[k]
    if not p.exists():
        raise SystemExit(f"缺 {k}：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _curve(b: dict) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(str(r[0])): float(r[1]) for r in b["equity"]}


def _edge(a: dict, b: dict) -> tuple[float, float, float, float | None, int]:
    """a − b：ΔCAGR、ΔMDD、ΔSharpe、月度區塊 t、區塊數。"""
    ca, cb = _curve(a), _curve(b)
    com = sorted(set(ca) & set(cb))
    days, diffs = [], []
    for i in range(1, len(com)):
        p, c = com[i - 1], com[i]
        if ca[p] > 0 and cb[p] > 0:
            days.append(c)
            diffs.append((ca[c] / ca[p] - 1.0) - (cb[c] / cb[p] - 1.0))
    st = paired_block_stats(days, diffs)
    t = None if st.block_se in (None, 0.0) else st.mean / st.block_se
    ma, mb = a["metrics"], b["metrics"]
    return (
        ma["cagr"] - mb["cagr"],
        ma["mdd"] - mb["mdd"],
        ma["sharpe"] - mb["sharpe"],
        t,
        st.n_blocks,
    )


def _assert_same_data(cells: dict[str, dict]) -> None:
    """共用表的列數與水位必須一致；`prices_daily` 是所有格都讀的表。"""
    ref = None
    for k, b in cells.items():
        pdv = (b.get("data_tables") or {}).get("prices_daily")
        if pdv is None:
            raise SystemExit(f"{k} 沒有 prices_daily 的指紋，無從驗證資料一致性")
        if ref is None:
            ref = pdv
        elif pdv != ref:
            raise SystemExit(f"{k} 的 prices_daily {pdv} 與其他格 {ref} 不同——不在同一份資料上")
    print(f"資料一致性：全部 {len(cells)} 格的 prices_daily = {ref[0]:,} 列 @ {ref[1]} ✓")


def main() -> None:
    cells = {k: _load(k) for k in CELLS}
    _assert_same_data(cells)
    cap = {b["initial_capital"] for b in cells.values()}
    if len(cap) != 1:
        raise SystemExit(f"各格資金不一致 {cap}——D-070：跨策略比較必須同資金")
    print(f"資金：全部 {next(iter(cap)):,.0f} ✓\n")

    print(f"{'格':10}{'CAGR':>9}{'MDD':>9}{'Sharpe':>8}{'周轉':>8}{'成交':>7}  說明")
    desc = {
        "L0new": "等權小型 20 檔 × ML（乾淨模型）",
        "X1": "等權小型 20 檔 × 四因子",
        "B1ML": "大型市值加權 30 檔 × ML（乾淨模型）",
        "B1": "大型市值加權 30 檔 × 四因子",
        "A": "A 軌：小型飆股",
        "B2": "B2：廣泛市值加權",
    }
    for k, b in cells.items():
        m = b["metrics"]
        print(
            f"{k:10}{m['cagr']:>+9.2%}{m['mdd']:>9.1%}{m['sharpe']:>8.2f}"
            f"{m['turnover']:>8.0%}{len(b['ledger']):>7,}  {desc[k]}"
        )

    print("\n" + "=" * 78)
    print("D-067 的兩條邊（月度區塊 SE）")
    print("=" * 78)
    for name, a, b, note in (
        ("架構 = B1-ML − L0new", "B1ML", "L0new", "訊號固定為 ML，只換宇宙與加權"),
        ("訊號 = B1 − B1-ML", "B1", "B1ML", "架構固定，只換訊號（正＝四因子較好）"),
        ("對角 = B1 − L0new", "B1", "L0new", "兩者都換，等於原本那條無法拆解的比較"),
        ("小型格對照 = X1 − L0new", "X1", "L0new", "在小型等權架構下只換訊號（T-146 已判）"),
    ):
        dc, dm, ds, t, nb = _edge(cells[a], cells[b])
        ts = "—" if t is None else f"{t:+.2f}"
        print(f"\n{name}\n  {note}")
        print(f"  ΔCAGR {dc:+.2%}  ΔMDD {dm:+.2%}  ΔSharpe {ds:+.2f}  月度區塊 t {ts}（{nb} 個月）")

    # 事前登記的可證偽條件
    _, _, _, t_sig, _ = _edge(cells["B1"], cells["B1ML"])
    print("\n" + "=" * 78)
    if t_sig is not None and t_sig >= 2.0:
        print("D-067 可證偽條件觸發：B1-ML 明顯劣於 B1（t ≥ +2.0）→ 四因子在大型股上確實優於 ML；")
        print("先前「ML 不比簡單因子差」的結論須限縮為「在小型股宇宙上不比它差」。")
    elif t_sig is not None and t_sig <= -2.0:
        print("B1-ML 明顯優於 B1（t ≤ −2.0）→ ML 訊號在大型股架構下有可區分的貢獻。")
    else:
        print("訊號那條邊未能區分（|t| < 2.0）→ 依 D-061 規則不得推出「ML 沒問題」；")
        print("依 D-066 乙層：ML 可留在組合裡當零件，但不再投入開發。")
    print("=" * 78)


if __name__ == "__main__":
    main()
