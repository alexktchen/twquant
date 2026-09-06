"""T-120 AC-1：量化 2005~2026 各年在**固定絕對門檻**下的宇宙大小。

問題（T-120）：`UniverseConfig` 的兩道主要濾網是絕對值——`min_adv_twd: 30_000_000`
與 `min_price: 10.0`。3,000 萬 TWD 在 2005 年與 2026 年是完全不同的門檻（台股成交
金額二十年間成長數倍），固定門檻會讓早期宇宙被砍到不成比例地小、且**愈早期愈嚴**，
使「早期 vs 晚期」不可比——而我們要長歷史的唯一理由就是拉長樣本解 DSR 的檢定力問題，
結果卻拿到一段不可比的樣本。

資料直接讀 `staging/finmind_hist_price`（回補已完成 3,007 檔）。這是一次性診斷量測，
不是因子計算，故不經 PitView——同 `reconcile_stock_meta.py` 的先例。

輸出兩組數字供 AC-2 裁決：
  (a) 固定絕對門檻下逐年的宇宙大小與砍幅
  (b) 若改用**相對門檻**（成交額百分位）逐年的宇宙大小——定義上不漂移
"""

from __future__ import annotations

import pathlib
import sys

import polars as pl

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

SRC = pathlib.Path("D:/project/twquant/data/staging/finmind_hist_price")
OUT = pathlib.Path(__file__).resolve().parents[1] / "docs/reports/universe-drift.md"

MIN_ADV_TWD = 30_000_000.0  # 現行 UniverseConfig 預設
MIN_PRICE = 10.0
ADV_WINDOW = 20
UNIVERSE_RE = r"^[1-9][0-9]{3}$"  # 四碼普通股，排除 ETF 與權證
PCTL = 0.5  # 相對門檻對照：成交額中位數以上（finlab 用「剔除 60 日均額後 50%」）


def _load() -> pl.DataFrame:
    files = sorted(SRC.glob("*.parquet"))
    if not files:
        raise SystemExit(f"找不到回補的行情檔：{SRC}")
    frames = []
    for f in files:
        df = pl.read_parquet(f)
        if "close" not in df.columns:  # 空股票的佔位檔只有 stock_id
            continue
        frames.append(
            df.select(
                pl.col("date").str.to_date(),
                pl.col("stock_id").cast(pl.String),
                pl.col("close").cast(pl.Float64),
                pl.col("Trading_money").cast(pl.Float64).alias("amount"),
            )
        )
    out = pl.concat(frames)
    return out.filter(pl.col("stock_id").str.contains(UNIVERSE_RE))


def main() -> None:
    px = _load()
    print(
        f"載入 {px.height:,} 列、{px['stock_id'].n_unique():,} 檔、"
        f"{px['date'].min()} ~ {px['date'].max()}",
        flush=True,
    )

    # 20 日成交金額中位數（與 universe.py 同一定義：中位數抗漲停無量）
    px = px.sort(["stock_id", "date"]).with_columns(
        _adv=pl.col("amount").rolling_median(ADV_WINDOW).over("stock_id")
    )
    # **每月**取樣後再按年平均。單日快照太吵：實測相鄰兩年的 ADV 中位數可差 3 倍，
    # 那多半是取樣日落在什麼行情上，不是真的體制轉換。用一年 12 個點才看得出趨勢。
    px = px.with_columns(_y=pl.col("date").dt.year(), _m=pl.col("date").dt.month())
    month_end = px.group_by(["_y", "_m"]).agg(pl.col("date").max().alias("d"))
    snap = px.join(month_end, left_on=["_y", "_m", "date"], right_on=["_y", "_m", "d"], how="inner")

    monthly = []
    for (y, _m), g in snap.group_by(["_y", "_m"]):
        s = g.drop_nulls("_adv")
        if s.height == 0:
            continue
        cut = s["_adv"].quantile(PCTL)
        monthly.append(
            {
                "_y": y,
                "total": s.height,
                "abs_keep": s.filter(
                    (pl.col("_adv") >= MIN_ADV_TWD) & (pl.col("close") >= MIN_PRICE)
                ).height,
                "rel_keep": s.filter(
                    (pl.col("_adv") >= cut) & (pl.col("close") >= MIN_PRICE)
                ).height,
                "cut": float(cut),
            }
        )
    md = pl.DataFrame(monthly)
    rows = []
    for y in sorted(md["_y"].unique().to_list()):
        g = md.filter(pl.col("_y") == y)
        total = g["total"].mean()
        abs_keep = g["abs_keep"].mean()
        rel_keep = g["rel_keep"].mean()
        rows.append(
            {
                "年": y,
                "月數": g.height,
                "有 ADV 的檔數": round(float(total)),
                "絕對門檻留下": round(float(abs_keep)),
                "絕對門檻留存率": float(abs_keep) / float(total),
                "當年 ADV 中位數": float(g["cut"].mean()),
                "相對門檻留下": round(float(rel_keep)),
                "相對門檻留存率": float(rel_keep) / float(total),
            }
        )
    tab = pl.DataFrame(rows)
    print(tab)

    lines = [
        "# T-120 AC-1：宇宙門檻在長歷史上的漂移",
        "",
        f"資料：`staging/finmind_hist_price`（T-116 回補，{px['stock_id'].n_unique():,} 檔四碼普通股，"
        f"{px['date'].min()} ~ {px['date'].max()}）。**每月**取月底交易日為快照、再按年平均——單日快照太吵，實測相鄰兩年的 ADV 中位數可差 3 倍，那是取樣日的噪音而非體制轉換。",
        "",
        f"絕對門檻＝現行 `UniverseConfig` 預設：`min_adv_twd={MIN_ADV_TWD:,.0f}`、"
        f"`min_price={MIN_PRICE}`；ADV 為 {ADV_WINDOW} 日成交金額中位數。",
        f"相對門檻＝當日 ADV 的第 {PCTL:.0%} 百分位（同樣套 `min_price`，以隔離是哪一道在漂）。",
        "",
        "| 年 | 月數 | 有 ADV 檔數 | 絕對門檻留下 | 留存率 | ADV 中位數 | 相對門檻留下 | 留存率 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in tab.to_dicts():
        lines.append(
            f"| {r['年']} | {r['月數']} | {r['有 ADV 的檔數']:,} | {r['絕對門檻留下']:,} | "
            f"{r['絕對門檻留存率']:.1%} | {r['當年 ADV 中位數'] / 1e6:,.1f}M | "
            f"{r['相對門檻留下']:,} | {r['相對門檻留存率']:.1%} |"
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已寫出 {OUT}", flush=True)


if __name__ == "__main__":
    main()
