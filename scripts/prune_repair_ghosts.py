"""T-136 收尾：刪掉修補時誤帶進來的「幽靈股」。

`repair_prices_2023.py` 第一版直接補了 staging 有而 PG 沒有的所有 (date, stock_id)。
staging 的宇宙是 3,007 檔（含已下市、興櫃型代號），比 PG 的價格宇宙大，於是修補窗口
多出 **294 檔只存在於那 50 天**的股票，宇宙在接縫處由 1,767 跳到 2,058。

衛生濾網攔不住它們：那些股票在窗口前沒有任何列，`shift(1)` 為 null、
缺口 `fill_null(0)` 後判為 false，於是會被當成正常股放進宇宙。

刪除範圍是**精確且可驗證**的：只刪「窗口內出現、但窗口前後正常日 PG 完全沒有」的
stock_id 在窗口內的列。這些 (date, stock_id) 在修補前不存在於 PG（`repair` 的
`add` 只取 PG 沒有的鍵），故刪除不會動到任何原有資料。

刪除前一律先匯出備份到 scratchpad。
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data.serving import store
from twquant.data.warehouse import Warehouse

LO, HI = dt.date(2023, 3, 9), dt.date(2023, 5, 23)  # 修補窗口
# 窗口前後各取一段正常日，作為「PG 本來就認得的股票」的參照
REF_LO, REF_HI = dt.date(2023, 2, 1), dt.date(2023, 6, 30)
BACKUP = pathlib.Path(
    "C:/Users/Alex/AppData/Local/Temp/claude/"
    "D--project-twquant--claude-worktrees-strange-kalam-3ec702/"
    "5681af54-7901-4325-b1a7-122e152a538d/scratchpad/pruned_ghost_rows.parquet"
)
_C4 = "^[1-9][0-9]{3}$"


def _ghosts_sql(schema: str) -> str:
    """窗口內出現、但窗口前後正常日完全沒有的四碼普通股。

    用函數而非 `.format`：正則 `^[1-9][0-9]{3}$` 裡的 `{3}` 會被 `.format` 當成佔位符。
    """
    return (
        f'WITH win AS (SELECT DISTINCT stock_id FROM "{schema}".prices_daily '
        f"  WHERE date BETWEEN %(lo)s AND %(hi)s AND stock_id ~ '{_C4}'), "
        f'ref AS (SELECT DISTINCT stock_id FROM "{schema}".prices_daily '
        "  WHERE ((date BETWEEN %(rlo)s AND %(lo)s - 1) OR (date BETWEEN %(hi)s + 1 AND %(rhi)s)) "
        f"    AND stock_id ~ '{_C4}') "
        "SELECT stock_id FROM win WHERE stock_id NOT IN (SELECT stock_id FROM ref)"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    cfg = ServingConfig(pg_schema="serving")
    params = {"lo": LO, "hi": HI, "rlo": REF_LO, "rhi": REF_HI}
    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(_ghosts_sql(cfg.pg_schema), params)
        ghosts = sorted(r[0] for r in cur.fetchall())
        print(f"幽靈股 {len(ghosts)} 檔（窗口內有、窗口前後正常日完全沒有）")
        print(f"  例：{ghosts[:12]}")
        cur.execute(
            f'SELECT COUNT(*) FROM "{cfg.pg_schema}".prices_daily '
            "WHERE date BETWEEN %s AND %s AND stock_id = ANY(%s)",
            (LO, HI, ghosts),
        )
        n = cur.fetchone()[0]
        print(f"  待刪 {n:,} 列")

    if args.check:
        print("\n--check 完成，未刪除。", flush=True)
        return
    if not ghosts:
        print("無幽靈股，結束。")
        return

    # ── 備份 ──────────────────────────────────────────────────────────
    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT date, stock_id, open, high, low, close, volume, amount, available_at "
            f'FROM "{cfg.pg_schema}".prices_daily '
            "WHERE date BETWEEN %s AND %s AND stock_id = ANY(%s)",
            (LO, HI, ghosts),
        )
        rows = cur.fetchall()
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
            "available_at": pl.Datetime(time_zone="UTC"),
        },
        orient="row",
    ).write_parquet(BACKUP)
    print(f"  ✓ 已備份 {len(rows):,} 列 → {BACKUP}")

    # ── 刪 PG ─────────────────────────────────────────────────────────
    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            f'DELETE FROM "{cfg.pg_schema}".prices_daily '
            "WHERE date BETWEEN %s AND %s AND stock_id = ANY(%s)",
            (LO, HI, ghosts),
        )
        deleted = cur.rowcount
        conn.commit()
    print(f"  ✓ PG 已刪 {deleted:,} 列")

    # ── 重寫湖的 finmind_repair 分區（去掉幽靈股）──────────────────────
    wh = Warehouse(args.lake)
    gset = set(ghosts)
    root = pathlib.Path(args.lake) / "clean" / "prices_daily"
    fixed = 0
    for part in sorted(root.glob("date=*/part-finmind_repair.parquet")):
        df = pl.read_parquet(part)
        keep = df.filter(~pl.col("stock_id").is_in(list(gset)))
        if keep.height != df.height:
            keep.write_parquet(part)
            fixed += 1
    print(f"  ✓ 湖已重寫 {fixed} 個 finmind_repair 分區")

    # ── 驗證 ──────────────────────────────────────────────────────────
    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT date, COUNT(*) FROM "{cfg.pg_schema}".prices_daily '
            f"WHERE date BETWEEN '2023-03-06' AND '2023-03-14' AND stock_id ~ '{_C4}' "
            "GROUP BY date ORDER BY date"
        )
        print("\n接縫處逐日四碼普通股數：", [(str(d), n) for d, n in cur.fetchall()])
        cur.execute(
            f'SELECT COUNT(*) FROM "{cfg.pg_schema}".prices_daily '
            "WHERE stock_id = '2330' AND date BETWEEN %s AND %s",
            (LO, HI),
        )
        print(f"2330 在窗口內仍有 {cur.fetchone()[0]} 列（修補的主要目的，不得被誤刪）")
    lake = (
        wh.scan("prices_daily")
        .filter((pl.col("date") >= LO) & (pl.col("date") <= HI))
        .select("date", "stock_id")
        .collect()
    )
    if lake.height != lake.unique().height:
        raise SystemExit("湖出現重複鍵，請檢查分區。")
    print(f"湖回讀 {lake.height:,} 列、無重複鍵")


if __name__ == "__main__":
    main()
