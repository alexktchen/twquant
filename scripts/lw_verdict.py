"""D-088 的判讀：長窗口三條線對事前門檻的成績，含**逐年 IC**。

逐年 IC 不是附錄而是判準的一部分（D-088 明文）：長樣本的價值在體制數，
若 IC 全部來自 2020-2021 那兩年而 2018／2022 為負，那是「多一段順風期」
而不是「多幾個體制」，該視同未通過。

不改門檻、不挑窗——門檻硬編在 `_GATES`，抄自 D-088 寫定當時的文字。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

# D-088 事前寫定。IC 門檻沿用短窗口（效果量不隨窗數變）；
# 窗比例提高（顯著性隨窗數變：短窗口 16 窗、長窗口 33~38 窗）。
_GATES: dict[str, tuple[str, float, float]] = {
    "ml_h20_cost_aware_lw": ("基礎 H20", 0.03, 0.65),
    "ml_f_revenue_surprise_lw": ("F 營收驚奇", 0.02, 0.65),
    "ml_r_drawdown60_lw": ("R 60 日回撤", 0.05, 0.75),
}
# 短窗口對照（既有模型目錄名 → 那條線的 lw 名稱）
_SHORT: dict[str, str] = {
    "ml_h20_cost_aware_lw": "lgbm_net_h20_20260225_6a798962",
    "ml_f_revenue_surprise_lw": "lgbm_rev_h15_20260225_a3227e06",
    "ml_r_drawdown60_lw": "lgbm_dd_h60_20260225_1a2c3a8e",
}


def _rank_ic_by(scored: pl.DataFrame, key: pl.Expr) -> pl.DataFrame:
    """逐日 spearman(score, label) 後按 key 取均值——與 `oos_rank_ic` 同一個定義。

    先逐日再彙總，不是把全年混在一起算一個相關：後者會被跨日的水準差主導，
    那不是我們宣稱的「同一天內排序對不對」。
    """
    per_day = (
        scored.filter(pl.col("label").is_not_null())
        .group_by("date")
        .agg(
            pl.corr(pl.col("score").rank("average"), pl.col("label").rank("average")).alias("ic"),
            pl.len().alias("n"),
        )
        .drop_nulls("ic")
    )
    return (
        per_day.with_columns(key.alias("_k"))
        .group_by("_k")
        .agg(
            pl.col("ic").mean().alias("ic"),
            pl.col("ic").gt(0).mean().alias("pos_days"),
            pl.len().alias("days"),
            pl.col("n").mean().alias("avg_stocks"),
        )
        .sort("_k")
    )


def _find(models: Path, cfg_name: str) -> Path | None:
    """以 `lgbm_lw_` 前綴找長窗口模型目錄；同前綴多個時取 train_end 最新的。"""
    kind = {"ml_h20_cost_aware_lw": "net_h20", "ml_f_revenue_surprise_lw": "rev_h15"}.get(
        cfg_name, "dd_h60"
    )
    hits = sorted(models.glob(f"lgbm_lw_{kind}_*"))
    return hits[-1] if hits else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default="D:/project/twquant/data/models")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    models = Path(args.models)
    lines: list[str] = []

    def say(s: str = "") -> None:
        print(s)
        lines.append(s)

    say("# D-088 判讀：長窗口三條線")
    say()
    say("門檻抄自 D-088 寫定當時的文字，本腳本不得調整。")
    for cfg_name, (label, ic_gate, win_gate) in _GATES.items():
        d = _find(models, cfg_name)
        say()
        say(f"## {label}（{cfg_name}）")
        if d is None:
            say(f"- **尚未產出**（`{models}` 下找不到對應的 `lgbm_lw_*` 目錄）")
            continue
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        ics = [float(x) for x in meta["window_ics"]]
        overall = float(meta["oos_rank_ic"])
        pos = sum(1 for x in ics if x > 0) / len(ics)
        ok_ic, ok_win = overall >= ic_gate, pos >= win_gate
        say(f"- 模型 `{d.name}`；訓練資料 {meta['data_from']} ~ {meta['data_to']}、{len(ics)} 窗")
        say(
            f"- **OOS RankIC {overall:+.4f}**（門檻 ≥ {ic_gate:+.2f}）"
            f" → {'通過' if ok_ic else '**未過**'}"
        )
        say(
            f"- **{pos:.0%} 窗為正**（{sum(1 for x in ics if x > 0)}/{len(ics)}，"
            f"門檻 ≥ {win_gate:.0%}） → {'通過' if ok_win else '**未過**'}"
        )

        scored = pl.read_parquet(d / "oos_scores.parquet")
        by_year = _rank_ic_by(scored, pl.col("date").dt.year())
        say()
        say("| 年 | RankIC | 為正的日數 | 交易日 | 平均檔數 |")
        say("|---|---:|---:|---:|---:|")
        for r in by_year.iter_rows(named=True):
            say(
                f"| {r['_k']} | {r['ic']:+.4f} | {r['pos_days']:.0%} "
                f"| {r['days']} | {r['avg_stocks']:.0f} |"
            )
        neg = [int(r["_k"]) for r in by_year.iter_rows(named=True) if r["ic"] <= 0]
        say()
        # D-088 的額外要求：體制數才是長樣本的價值所在。
        if neg:
            say(
                f"- **逐年為負的年份：{neg}** → 依 D-088，IC 若集中在少數順風年"
                "即視同未通過，需逐年檢視而不是只看總和。"
            )
        else:
            say("- 逐年 IC **全為正** → 不是單一順風期的產物。")

        short = models / _SHORT[cfg_name]
        if (short / "meta.json").exists():
            sm = json.loads((short / "meta.json").read_text(encoding="utf-8"))
            sic = [float(x) for x in sm["window_ics"]]
            say(
                f"- 短窗口對照 `{short.name}`：RankIC {float(sm['oos_rank_ic']):+.4f}、"
                f"{sum(1 for x in sic if x > 0) / len(sic):.0%} 窗為正（{len(sic)} 窗）"
            )
            # **兩個總和不可直接相減。** 長窗口的測試期由 2017 起、短窗口由 2022 起，
            # 重疊只有尾段——總和的差裡混著「多測了幾個比較好賺的年份」。
            # 唯一可比的是**共同測試期**上的差，故一律算它，並把不重疊的年份標出來。
            s_scored = pl.read_parquet(short / "oos_scores.parquet")
            lo = max(scored["date"].min(), s_scored["date"].min())
            hi = min(scored["date"].max(), s_scored["date"].max())
            l_days = _rank_ic_by(scored.filter(pl.col("date").is_between(lo, hi)), pl.lit(1))
            s_days = _rank_ic_by(s_scored.filter(pl.col("date").is_between(lo, hi)), pl.lit(1))
            if l_days.height and s_days.height:
                li, si = float(l_days["ic"][0]), float(s_days["ic"][0])
                say(
                    f"- **共同測試期 {lo} ~ {hi}（{int(l_days['days'][0])} 個交易日）**："
                    f"長 {li:+.4f} vs 短 {si:+.4f} → **{li - si:+.4f}**"
                )
                say(
                    f"  - 長窗口另有 {scored.filter(pl.col('date') < lo)['date'].n_unique()}"
                    " 個交易日的測試期是短窗口沒有的"
                    "（2017 起）。**總和的差不可直接歸因於樣本長度**——那一段混著年份難易度。"
                )
        say()
        say(
            f"**判定：{'通過' if (ok_ic and ok_win and not neg) else '未通過'}**（IC／窗比例／逐年三項全過才算）"
        )

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已寫出 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
