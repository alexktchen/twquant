"""把「資金」與「資料」兩個變因拆開——先前那組數字做不到，所以不能用它下結論。

先前的對照是「修補前 L0 @ 10M」vs「修補後 L0 @ 80M」，兩個變因同時動，
於是 MDD 由 −40.34% 變 −47.60% 無法歸因。本腳本補上缺的那一格：

                 舊資料（_pre-T142-T149）    新資料（乾淨）
    10M              A（已有）                  C（本次補跑）
    80M                —                        B（已有）

  資金效果 = B − C（同資料，只換資金）
  資料效果 = C − A（同資金，只換資料）
  兩者相加須約等於先前看到的 B − A——若不相等，代表有交互作用（資金與資料
  不可分離地共同作用），那本身就是一個必須據實回報的發現。

D-068 的核心論據也在這裡重驗：它主張 80M 的理由**不是曝險高，而是橫向差異小**
（10M 下 20 檔與 50 檔差 20pp，會汙染「檔數」的比較）。那個主張是在**舊資料**上
量的，乾淨資料上是否仍成立，必須重量而不是假設。

用法：
    python scripts/capital_compare.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.backtest.validation import paired_block_stats

ROOT = pathlib.Path("D:/project/twquant/data")
NEW80 = ROOT / "ladder"  # B：新資料 80M
NEW10 = ROOT / "ladder10m"  # C：新資料 10M
OLD10 = ROOT / "ladder" / "_pre-T142-T149"  # A：舊資料 10M

RUNGS = [
    ("L0", "ml_net_h20_m", "現況：ML 訊號、20 檔等權"),
    ("L1", "ladder_l1_n40", "40 檔"),
    ("L2", "ladder_l2_rev", "營收濾網"),
    ("L3", "ladder_l3_simple", "四因子（條件於 40 檔＋營收篩）"),
    ("L4", "ladder_l4_wt", "分數加權"),
    ("X1", "ladder_x1_simple20", "隔離格：只換訊號"),
    ("L0new", "ladder_l0_new", "T-146 處理組：乾淨模型"),
]


def _load(base: pathlib.Path, rung: str, cfg: str, tag: str = "1x") -> dict | None:
    p = base / f"{rung}_{cfg}_{tag}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _curve(b: dict) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(str(r[0])): float(r[1]) for r in b["equity"]}


def _paired_t(a: dict, b: dict) -> tuple[float | None, int]:
    """a 相對 b 的配對日報酬差，月度區塊 t（D-061／R1）。"""
    ca, cb = _curve(a), _curve(b)
    common = sorted(set(ca) & set(cb))
    days, diffs = [], []
    for i in range(1, len(common)):
        p, c = common[i - 1], common[i]
        if ca[p] <= 0 or cb[p] <= 0:
            continue
        days.append(c)
        diffs.append((ca[c] / ca[p] - 1.0) - (cb[c] / cb[p] - 1.0))
    st = paired_block_stats(days, diffs)
    if st.block_se in (None, 0.0):
        return None, st.n_blocks
    return st.mean / st.block_se, st.n_blocks


def _exposure(b: dict) -> float | None:
    """平均曝險＝1 − 平均現金比重。整張捨去的影響直接顯示在這一欄。"""
    eq = b.get("equity") or []
    vals = [(float(r[1]), float(r[2])) for r in eq if float(r[1]) > 0]
    if not vals:
        return None
    return sum(1.0 - cash / equity for equity, cash in vals) / len(vals)


def _row(label: str, b: dict | None) -> str:
    if b is None:
        return f"{label:<14}{'（缺）':>10}"
    m = b["metrics"]
    ex = _exposure(b)
    exs = "—" if ex is None else f"{ex:.0%}"
    return (
        f"{label:<14}{m['cagr']:>+9.2%}{m['mdd']:>9.2%}{m['sharpe']:>8.2f}"
        f"{m.get('turnover', 0):>9.0%}{exs:>8}{len(b['ledger']):>8,}"
    )


def main() -> None:
    print("=" * 96)
    print("三格對照（1× 成本）：A 舊資料10M ／ C 新資料10M ／ B 新資料80M")
    print("=" * 96)
    hdr = f"{'':<14}{'CAGR':>9}{'MDD':>9}{'Sharpe':>8}{'周轉':>9}{'曝險':>8}{'成交':>8}"
    have_c = 0
    for rung, cfg, desc in RUNGS:
        a, c, b = _load(OLD10, rung, cfg), _load(NEW10, rung, cfg), _load(NEW80, rung, cfg)
        have_c += c is not None
        print(f"\n── {rung}　{desc}")
        print(hdr)
        print(_row("A 舊10M", a))
        print(_row("C 新10M", c))
        print(_row("B 新80M", b))
        if c and b:
            t, nb = _paired_t(b, c)
            d = b["metrics"]["cagr"] - c["metrics"]["cagr"]
            print(
                f"  → 資金效果 B−C：ΔCAGR {d:+.2%}、"
                f"月度區塊 t {'—' if t is None else f'{t:+.2f}'}（{nb} 個月）"
            )
        if c and a:
            d = c["metrics"]["cagr"] - a["metrics"]["cagr"]
            # 舊/新資料的日期集合相同（同窗同引擎），配對成立；不同才要退回非配對
            t, nb = _paired_t(c, a)
            print(
                f"  → 資料效果 C−A：ΔCAGR {d:+.2%}、"
                f"月度區塊 t {'—' if t is None else f'{t:+.2f}'}（{nb} 個月）"
            )

    if not have_c:
        print(
            "\n（新資料 10M 尚未產出，先跑 scripts/run_ladder.py --capital 10000000 --out …ladder10m）"
        )
        return

    # D-068 的核心論據重驗：判準是**橫向差異**，不是絕對曝險
    print("\n" + "=" * 96)
    print("D-068 重驗：它主張 80M 的理由是「橫向差異小」而非「曝險高」")
    print("=" * 96)
    for base, name in ((NEW10, "新資料 10M"), (NEW80, "新資料 80M")):
        exs = []
        for rung, cfg, _ in RUNGS:
            b = _load(base, rung, cfg)
            if b:
                e = _exposure(b)
                if e is not None:
                    exs.append((rung, e))
        if len(exs) < 2:
            print(f"{name}：資料不足")
            continue
        lo, hi = min(exs, key=lambda x: x[1]), max(exs, key=lambda x: x[1])
        print(
            f"{name}：曝險 {lo[1]:.0%}（{lo[0]}）~ {hi[1]:.0%}（{hi[0]}）、"
            f"**跨階差 {(hi[1] - lo[1]) * 100:.1f}pp**"
        )
    print(
        "\n判讀規則：跨階差越大，'檔數' 這個變因就越被'買不買得起一張'汙染。"
        "\n若乾淨資料上 10M 的跨階差已經很小，D-068 選 80M 的理由就被自己的量測削弱——"
        "\n那要據實回報，不得因為已經跑完 80M 就略過。"
    )


if __name__ == "__main__":
    main()
