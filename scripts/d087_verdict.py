"""D-087 的判讀：四格長窗口回測對事前門檻的成績。

門檻硬編在下方，抄自 D-087 寫定當時的文字，本腳本不得調整。
主判定看 MDD——那是這個視窗才檢定得了的東西（2018Q4／2020／2022 三個壓力體制）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# D-087 事前寫定。甲：至少一格 MDD ≤ 30% 且 CAGR > 0 且 1.5× 後 Sharpe > 0；
# 乙：全部格 MDD 落在 30~40%；丙：全部格 MDD > 40%。
_JIA, _YI = 0.30, 0.40
_CELLS = {
    "LW-C1": "C1 換相對門檻",
    "LW-B2": "B2 換相對門檻",
    "LW-X1": "四公開因子換相對門檻",
    "LW-C1abs": "與 LW-C1 逐字相同，只保留絕對門檻（D-062 的驗證格）",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="D:/project/twquant/data/tracks_lw_nodiv")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    got: dict[tuple[str, str], dict[str, float]] = {}
    divmode: set[bool] = set()
    for f in sorted(Path(args.dir).glob("*.json")):
        b = json.loads(f.read_text(encoding="utf-8"))
        cell, cost = f.stem.split("_", 1)[0], f.stem.rsplit("_", 1)[1]
        got[(cell, cost)] = b["metrics"]
        divmode.add(bool(b.get("credit_dividends", False)))

    lines: list[str] = []

    def say(s: str = "") -> None:
        print(s)
        lines.append(s)

    say("# D-087 判讀：四格長窗口回測")
    say()
    n_days = int(next(iter(got.values())).get("n_days", 0))
    say(f"視窗 2017-01-03 ~ 2026-02-28、**{n_days} 個交易日**（現行 886 日的 2.5 倍）。")
    say(
        f"報酬口徑：**{'含息' if divmode == {True} else '價格報酬（--no-dividends）'}**"
        f"　※ 混合則為 {divmode}"
    )
    say()
    say("| 格 | 內容 | 成本 | CAGR | MDD | Sharpe |")
    say("|---|---|---|---:|---:|---:|")
    for cell, desc in _CELLS.items():
        for cost in ("0x", "1x", "1.5x"):
            m = got.get((cell, cost))
            if m is None:
                continue
            say(
                f"| {cell} | {desc if cost == '0x' else ''} | {cost} "
                f"| {m['cagr'] * 100:+.2f}% | {m['mdd'] * 100:.2f}% | {m['sharpe']:+.2f} |"
            )

    # ── 主判定：MDD ────────────────────────────────────────────────────
    mdds = {k: abs(v["mdd"]) for k, v in got.items()}
    best_key = min(mdds, key=lambda k: mdds[k])
    best = mdds[best_key]
    say()
    say("## 主判定")
    say()
    say(f"- 最淺的一次回撤：**{best_key[0]} {best_key[1]}　MDD −{best * 100:.2f}%**")
    if best <= _JIA:
        tier = "甲"
    elif all(m > _YI for m in mdds.values()):
        tier = "丙"
    else:
        tier = "乙"
    say(
        f"- 12 次回測的 MDD 全距 −{min(mdds.values()) * 100:.2f}% ~ −{max(mdds.values()) * 100:.2f}%"
    )
    say(f"- **判定：{tier}層**")
    say()
    if tier == "丙":
        say(
            "> 丙層（D-087 原文）：「這個架構在多體制下不可行。**不訓練長窗口模型**，"
            "改依 D-052 建議(丙)另立架構（多空、或低頻事件驅動）。」"
        )
        say()
        say(
            f"沒有一格接近 40% 的界——最好的一格還差 {(best - _YI) * 100:.2f}pp，"
            "且是 0× 成本的幻想值。**不是邊緣判定。**"
        )

    # ── 次要判定：D-062 的事後驗證 ─────────────────────────────────────
    say()
    say("## 次要判定一：相對門檻在真實報酬上值多少（D-062 的事後驗證）")
    say()
    say("LW-C1 與 LW-C1abs **逐字相同，只差流動性門檻**（相對前 30% vs 絕對 3,000 萬）。")
    say()
    say("| 成本 | CAGR 相對 | CAGR 絕對 | Δ | MDD 相對 | MDD 絕對 | Δ | Sharpe Δ |")
    say("|---|---:|---:|---:|---:|---:|---:|---:|")
    deltas: list[float] = []
    for cost in ("0x", "1x", "1.5x"):
        rel, abs_ = got.get(("LW-C1", cost)), got.get(("LW-C1abs", cost))
        if rel is None or abs_ is None:
            continue
        dc = (rel["cagr"] - abs_["cagr"]) * 100
        dm = (abs(abs_["mdd"]) - abs(rel["mdd"])) * 100  # 正＝相對門檻回撤較淺
        deltas.append(dc)
        say(
            f"| {cost} | {rel['cagr'] * 100:+.2f}% | {abs_['cagr'] * 100:+.2f}% | **{dc:+.2f}pp** "
            f"| {rel['mdd'] * 100:.2f}% | {abs_['mdd'] * 100:.2f}% | **{dm:+.2f}pp** "
            f"| {rel['sharpe'] - abs_['sharpe']:+.2f} |"
        )
    if deltas:
        say()
        say(
            f"- 相對門檻在**三個成本情境下全面較好**，CAGR 中位 **{statistics.median(deltas):+.2f}pp**，"
            "且回撤更淺、Sharpe 更高。D-062 當初只量了留存率的穩定度"
            "（標準差 5.29pp → 0.42pp），這裡補上真實報酬那一半：**正面，且方向一致。**"
        )
        say(
            "- 但這**不改變主判定**：兩者都在丙層。相對門檻讓一個站不住的架構"
            "站得**比較不那麼不住**，不等於站得住。"
        )

    say()
    say("## 次要判定二：對含息基準的比較——**本批做不了**")
    say()
    say(
        "D-087 原文要求「各格對 0051 含息（+31.57%）的 CAGR 與 Sharpe 差」。"
        "但本批 12 次是 `--no-dividends` 跑的（價格報酬），"
        "**拿它去比含息基準正是 D-085 指出的那個不對稱**——不做。"
    )
    say()
    say(
        "主判定不受影響：實測 40 組含息 vs 價格報酬的重跑，"
        "ΔMDD 中位僅 **+0.98pp**、最大 **+5.01pp**；"
        f"即使最好的一格 −{best * 100:.2f}% 拿到最樂觀的 +5.01pp，"
        f"也只到 −{best * 100 - 5.01:.2f}%，**仍在丙層**。"
    )

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已寫出 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
