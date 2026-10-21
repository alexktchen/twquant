"""T-415：新聞因子試驗報告（SPEC-018 §7 事前登記的 H1／H2）→ docs/reports/news-factors-dev-<日期>.md。

H1 單因子：開發期（--from 起、到對照模型 OOS 最後一天）每個 news.* 因子對 h=20 扣成本超額報酬的日橫斷面 RankIC，
          t ＝ 平均 ÷（標準差 ÷ √天數）；門檻 t ≥ 3 且方向跟事前寫的一致（attention 正、novelty 正、sent 正、mops_novelty 正）。
H2 模型：同一天、同一份程式與資料重訓的對照組（lw 設定）vs 新聞組（lw ＋ 26 個 news.*），同折同 seed；
          逐折 OOS RankIC 平均差 ≥ +0.005 且前 20 檔扣成本超額（平均）≥ 對照組。折的邊界從訓練 log 的「窗 k/N start~end」讀。
門檻不在這裡調：寫死在 SPEC-018 §7／D-201，報告只印判定。

用法：uv run python scripts/news_factors_trial.py --base <對照模型目錄> --news <新聞模型目錄> --base-log <對照訓練 log> \
        --from 2023-01-01 --out docs/reports/news-factors-dev-2026-10-22.md  [--skip-h1]
H1 要 TWQUANT_PG_DSN（讀 serving.factor_values 的 news.*）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
from pathlib import Path

import polars as pl

IC_GATE = 0.005
T_GATE = 3.0
TOP_N = 20
# 事前登記的方向（SPEC-018 §7）；沒列的因子只報數字、不判方向
EXPECTED_SIGN = {
    "news.attention_5d": 1,
    "news.attention_20d": 1,
    "news.novelty": 1,
    "news.sent_1d": 1,
    "news.sent_5d": 1,
    "news.sent_shift": 1,
    "news.mops_novelty": 1,
}
_WIN = re.compile(r"窗 (\d+)/(\d+) (\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})")


def windows_from_log(path: Path) -> list[tuple[dt.date, dt.date]]:
    out = []
    for m in _WIN.finditer(path.read_text(encoding="utf-8", errors="replace")):
        out.append((dt.date.fromisoformat(m.group(3)), dt.date.fromisoformat(m.group(4))))
    if not out:
        raise SystemExit(f"{path}：找不到「窗 k/N start~end」")
    return out


def rank_ic_by_date(df: pl.DataFrame, score: str, label: str = "label") -> pl.DataFrame:
    return (
        df.group_by("date")
        .agg(pl.corr(pl.col(score).rank(), pl.col(label).rank()).alias("ic"), pl.len().alias("n"))
        .sort("date")
    )


def top_n_by_date(df: pl.DataFrame, score: str, n: int = TOP_N) -> pl.DataFrame:
    return (
        df.sort(["date", score], descending=[False, True])
        .group_by("date", maintain_order=True)
        .head(n)
        .group_by("date")
        .agg(pl.col("label").mean().alias("top"))
        .sort("date")
    )


def h2(
    base_dir: Path, news_dir: Path, windows: list[tuple[dt.date, dt.date]], start: dt.date
) -> tuple[list[str], bool]:
    b = pl.read_parquet(base_dir / "oos_scores.parquet").rename({"score": "s_base"})
    n = pl.read_parquet(news_dir / "oos_scores.parquet").rename({"score": "s_news"}).drop("label")
    j = b.join(n, on=["date", "stock_id"], how="inner").filter(pl.col("date") >= start)
    if j.height == 0:
        raise SystemExit("兩個模型的 OOS 分數沒有重疊的日期")
    ic = (
        rank_ic_by_date(j, "s_base")
        .rename({"ic": "ic_base"})
        .join(rank_ic_by_date(j, "s_news").select("date", pl.col("ic").alias("ic_news")), on="date")
    )
    top = (
        top_n_by_date(j, "s_base")
        .rename({"top": "top_base"})
        .join(top_n_by_date(j, "s_news").rename({"top": "top_news"}), on="date")
    )
    d = ic.join(top, on="date")
    rows = []
    for k, (a, z) in enumerate(windows, 1):
        w = d.filter((pl.col("date") >= a) & (pl.col("date") <= z))
        if w.height == 0:
            continue
        rows.append(
            (
                k,
                a,
                z,
                w.height,
                float(w["ic_base"].mean()),
                float(w["ic_news"].mean()),
                float(w["top_base"].mean()),
                float(w["top_news"].mean()),
            )
        )
    ic_diff = sum(r[5] - r[4] for r in rows) / len(rows)
    top_diff = sum(r[7] - r[6] for r in rows) / len(rows)
    passed = ic_diff >= IC_GATE and top_diff >= 0
    lines = [
        "## H2 模型：加新聞因子 vs 不加（同折同 seed）",
        "",
        f"- 開發期折數 {len(rows)}（{rows[0][1]}～{rows[-1][2]}），共 {d.height} 個交易日、{j.height:,} 個股票日",
        f"- 逐折 OOS RankIC 平均差 **{ic_diff:+.4f}**（門檻 ≥ +{IC_GATE}）；前 {TOP_N} 檔扣成本超額平均差 **{top_diff:+.4%}**（門檻 ≥ 0）→ **{'過' if passed else '不過'}**",
        "",
        "| 折 | 測試期 | 天 | IC 對照 | IC 新聞 | 差 | 前20 對照 | 前20 新聞 | 差 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {k} | {a}～{z} | {nd} | {ib:+.4f} | {inw:+.4f} | {inw - ib:+.4f} | {tb:+.3%} | {tn:+.3%} | {tn - tb:+.3%} |"
        for k, a, z, nd, ib, inw, tb, tn in rows
    ]
    return lines, passed


def h1(base_dir: Path, start: dt.date) -> tuple[list[str], bool]:
    import psycopg

    lab = (
        pl.read_parquet(base_dir / "oos_scores.parquet")
        .select("date", "stock_id", "label")
        .filter(pl.col("date") >= start)
    )
    end = lab["date"].max()
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "select factor, date, stock_id, value from serving.factor_values where factor like 'news.%%' and date between %s and %s",
            (start, end),
        )
        rows = cur.fetchall()
    fv = pl.DataFrame(
        {
            "factor": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "stock_id": [r[2] for r in rows],
            "value": [r[3] for r in rows],
        },
        schema={"factor": pl.String, "date": pl.Date, "stock_id": pl.String, "value": pl.Float64},
    ).drop_nulls("value")
    j = fv.join(lab, on=["date", "stock_id"], how="inner")
    out = []
    any_pass = False
    for f in sorted(j["factor"].unique().to_list()):
        g = j.filter(pl.col("factor") == f)
        ic = (
            rank_ic_by_date(g.filter(pl.col("value").is_not_null()), "value")
            .filter(pl.col("n") >= 30)
            .drop_nulls("ic")
        )
        if ic.height < 30:
            out.append((f, ic.height, float("nan"), float("nan"), "樣本不足"))
            continue
        mean, sd = float(ic["ic"].mean()), float(ic["ic"].std())
        t = mean / (sd / math.sqrt(ic.height)) if sd > 0 else float("nan")
        exp = EXPECTED_SIGN.get(f)
        if exp is None:
            verdict = "（不判方向）"
        else:
            ok = abs(t) >= T_GATE and (mean > 0) == (exp > 0)
            verdict = "過" if ok else ("方向反了" if abs(t) >= T_GATE else "不過")
            any_pass = any_pass or ok
        out.append((f, ic.height, mean, t, verdict))
    lines = [
        "## H1 單因子：h=20 扣成本超額報酬的日橫斷面 RankIC",
        "",
        f"- 期間 {start}～{end}；門檻 t ≥ {T_GATE:.0f} 且方向跟事前寫的一致 → 至少一個因子過 → **{'過' if any_pass else '不過'}**",
        "",
        "| 因子 | 天 | 平均 RankIC | t | 判定 |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {f} | {nd} | {m:+.4f} | {t:+.2f} | {v} |" for f, nd, m, t, v in out]
    return lines, any_pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--news", required=True)
    ap.add_argument("--base-log", required=True)
    ap.add_argument("--from", dest="start", default="2023-01-01")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-h1", action="store_true")
    a = ap.parse_args()
    start = dt.date.fromisoformat(a.start)
    base, news = Path(a.base), Path(a.news)
    mb = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    mn = json.loads((news / "meta.json").read_text(encoding="utf-8"))
    lines = [
        f"# 新聞因子試驗（T-415／SPEC-018 §7，{dt.date.today().isoformat()}）",
        "",
        f"- 對照 `{mb['model_id']}`（{len(mb['features'])} 個特徵，OOS RankIC {mb['oos_rank_ic']:+.4f}）；"
        f"新聞 `{mn['model_id']}`（{len(mn['features'])} 個特徵，OOS RankIC {mn['oos_rank_ic']:+.4f}）",
        f"- 資料 {mb['data_from']}～{mb['data_to']}、train_end {mb['train_end']}；兩邊 code_hash {mb['code_hash']}／{mn['code_hash']}、"
        f"data_hash {'相同' if mb['data_hash'] == mn['data_hash'] else '**不同**'}",
        "- 門檻事前登記（D-201 §三），本報告只印判定；不過就收線，不調門檻再跑。",
        "",
    ]
    h2_lines, h2_pass = h2(base, news, windows_from_log(Path(a.base_log)), start)
    lines += [*h2_lines, ""]
    h1_pass = None
    if not a.skip_h1:
        h1_lines, h1_pass = h1(base, start)
        lines += [*h1_lines, ""]
    lines.append(
        f"## 結論：H1 {'略過' if h1_pass is None else ('過' if h1_pass else '不過')}、H2 {'過' if h2_pass else '不過'} → "
        + (
            "**兩個都過：凍結版本、登記 D-109 開封清單**"
            if (h1_pass and h2_pass)
            else "**收線**（任一沒過）"
        )
    )
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
