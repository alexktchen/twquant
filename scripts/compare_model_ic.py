"""D-139 §三：兩個模型的樣本外 IC 配對比較（同一組 walk-forward 測試窗）。

    uv run --all-groups python scripts/compare_model_ic.py \
        --base <基準模型目錄> --new <新模型目錄>

**依 D-134：統計照報，不當通過門檻。** 本腳本只輸出證據，不判通過與否。

配對比較的前提是兩個模型用同一組測試窗——切分參數不同就不可比，故先逐項核對
標籤、切分、超參，任一不同即中止。否則會拿兩件不同的事相減，而那個錯誤在表面上看不出來。
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from typing import Any

import lightgbm as lgb


def _meta(d: pathlib.Path) -> dict[str, Any]:
    return json.loads((d / "meta.json").read_text(encoding="utf-8"))


def _t_stat(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    se = math.sqrt(var / len(xs))
    return m / se if se > 0 else None


def _importance(d: pathlib.Path) -> dict[str, float]:
    files = [p for p in d.glob("*") if p.suffix in (".txt", ".lgb", ".model")]
    if not files:
        return {}
    b = lgb.Booster(model_file=str(files[0]))
    gain = b.feature_importance("gain")
    total = float(gain.sum()) or 1.0
    return {n: float(g) / total for n, g in zip(b.feature_name(), gain, strict=True)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    bd, nd = pathlib.Path(args.base), pathlib.Path(args.new)
    bm, nm = _meta(bd), _meta(nd)

    # 可比性：除了特徵，其他一律要相同。不同就中止——相減會得到一個看不出錯的錯數字。
    for key in ("label", "split", "lgbm", "horizon_days", "data_from", "data_to"):
        if bm.get(key) != nm.get(key):
            raise SystemExit(
                f"兩個模型的 `{key}` 不同，測試窗不可比，拒絕比較：\n"
                f"  基準 {bm.get(key)}\n  新版 {nm.get(key)}"
            )
    a, b = bm["window_ics"], nm["window_ics"]
    if len(a) != len(b):
        raise SystemExit(f"窗數不同（基準 {len(a)}、新版 {len(b)}），不可配對")

    added = sorted(set(nm["features"]) - set(bm["features"]))
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    mean = sum(diffs) / len(diffs)
    t = _t_stat(diffs)
    imp = _importance(nd)
    added_share = sum(imp.get(f, 0.0) for f in added)

    print(f"基準 {bd.name}\n新版 {nd.name}")
    print(f"新增特徵 {len(added)} 支：{', '.join(added)}\n")
    print(f"{'':<18}{'OOS RankIC':>12}{'窗數':>7}")
    print(f"{'基準':<18}{bm['oos_rank_ic']:>+12.4f}{len(a):>7}")
    print(f"{'新版':<18}{nm['oos_rank_ic']:>+12.4f}{len(b):>7}")
    print(f"{'差':<18}{nm['oos_rank_ic'] - bm['oos_rank_ic']:>+12.4f}\n")
    print(
        f"逐窗配對：平均差 {mean:+.4f}　t = {'—' if t is None else f'{t:+.2f}'}"
        f"　正窗 {sum(1 for d in diffs if d > 0)}/{len(diffs)}"
    )
    print(f"每窗差值（新 − 基準）：{' '.join(f'{d:+.3f}' for d in diffs)}\n")
    print(f"新增特徵重要度合計：**{added_share:.1%}**")
    for f in sorted(added, key=lambda x: -imp.get(x, 0.0)):
        print(f"  {f:<30}{imp.get(f, 0.0):>7.1%}")
    print("\n原有特徵前五：")
    for f, v in sorted(((f, v) for f, v in imp.items() if f not in added), key=lambda x: -x[1])[:5]:
        print(f"  {f:<30}{v:>7.1%}")

    # D-139 §三 事前寫死的解讀規則（不是通過門檻，是防止事後自圓其說）
    if added_share < 0.03 and mean <= 0.0:
        print(
            "\n**依 D-139 §三：重要度合計 < 3% 且 IC 未提升 → 濾網的效果沒有轉移到模型。**"
            "\nD-138 §一 那張濾網表不得再被引用主張震盪指標有效。"
        )


if __name__ == "__main__":
    main()
