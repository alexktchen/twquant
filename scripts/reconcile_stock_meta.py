"""把「有行情但不在 stock_meta」的股票補進來，並補上 delist_date（T-111／D-053）。

用法：
    python scripts/reconcile_stock_meta.py --check     # 只比對與報告，不寫
    python scripts/reconcile_stock_meta.py --write     # 比對通過才寫湖與 PG

為什麼需要：`clean_stock_meta` 的來源（TWSE ISIN）只列**現存**上市櫃公司，已下市者整批缺席。
實測 2,056 檔有行情的股票中有 82 檔不在 stock_meta，而依賴 stock_meta 的三個因子
（`price.residual_reversal_5d`＝25 個 pin 特徵之一、`price.industry_mom_6m`、`price.industry_rs_20d`）
是 inner join，會**靜默**略過它們——缺的正是後來下市的左尾樣本。

資料來源：FinMind `TaiwanStockInfo`（名稱／市場別／產業別）與 `TaiwanStockDelisting`（權威下市日）。
產業別對映與「無法對映即留 null」的規則見 `sources/finmind.py`；市場別缺漏者不補列（不得偽造）。
token 只從環境變數 FINMIND_TOKEN 讀，永不寫入輸出（黃金規則 9）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import polars as pl
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data.clean import reconcile_stock_meta
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import (
    API,
    DELISTING_DATASET,
    STOCK_INFO_DATASET,
    delisting_rows,
    stock_info_rows,
)
from twquant.data.warehouse import Warehouse

_UNIVERSE = r"^[1-9][0-9]{3}$"


def _fetch(dataset: str, token: str) -> list[dict[str, object]]:
    r = requests.get(API, params={"dataset": dataset, "token": token}, timeout=120)
    if r.status_code != 200:
        raise SystemExit(f"{dataset}：HTTP {r.status_code} {r.text[:200]}")
    payload = r.json()
    if payload.get("status") != 200 or "data" not in payload:
        raise SystemExit(
            f"{dataset}：status {payload.get('status')} {str(payload.get('msg'))[:200]}"
        )
    return list(payload["data"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument(
        "--repair-names",
        action="store_true",
        help="以 FinMind 的名稱修補含替換字元 U+FFFD 的中文名（T-117）",
    )
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        raise SystemExit("環境變數 FINMIND_TOKEN 未設定")

    wh = Warehouse(args.lake)
    existing = wh.scan("stock_meta").collect()
    present = (
        wh.scan("prices_daily")
        .select("stock_id")
        .filter(pl.col("stock_id").str.contains(_UNIVERSE))
        .unique()
        .collect()["stock_id"]
        .sort()
        .to_list()
    )
    print(
        f"stock_meta 現有 {existing.height:,} 檔；行情出現過的四位數普通股 {len(present):,} 檔",
        flush=True,
    )

    info = stock_info_rows(_fetch(STOCK_INFO_DATASET, token))
    delistings = delisting_rows(_fetch(DELISTING_DATASET, token))
    print(
        f"FinMind：TaiwanStockInfo {len(info):,} 檔、TaiwanStockDelisting {len(delistings):,} 檔",
        flush=True,
    )

    out = reconcile_stock_meta(existing, present_ids=present, info=info, delistings=delistings)

    if args.repair_names:
        # T-117：ISIN 曾以標準 Big5 解碼，「碁」等字被靜默換成 U+FFFD（14 檔，含 2353 宏碁）。
        # `reconcile_stock_meta` 刻意不覆蓋既有列，故名稱要單獨修。FinMind 的名稱已於 T-111 驗證。
        fm = {str(r["stock_id"]): r.get("name") for r in info}
        broken = out.filter(pl.col("name").str.contains("�", literal=True))
        fixed, unfixable = [], []
        for sid in broken["stock_id"].to_list():
            new_name = fm.get(sid)
            if isinstance(new_name, str) and "�" not in new_name:
                fixed.append((sid, new_name))
            else:
                unfixable.append(sid)
        print("", f"中文名含 U+FFFD：{broken.height} 檔　可修 {len(fixed)} 檔", flush=True)
        for sid, nm in fixed:
            print(f"  {sid}  {broken.filter(pl.col('stock_id') == sid)['name'][0]!r} → {nm!r}")
        if unfixable:
            # 不靜默留著壞名字，也不偽造：明確列出待處理者（黃金規則 5）
            print(f"  無法修（FinMind 也沒有乾淨名稱）：{', '.join(unfixable)}", flush=True)
        if fixed:
            m = pl.DataFrame({"stock_id": [s for s, _ in fixed], "_nm": [n for _, n in fixed]})
            out = (
                out.join(m, on="stock_id", how="left")
                .with_columns(name=pl.coalesce("_nm", "name"))
                .drop("_nm")
            )

    have = set(existing["stock_id"].to_list())
    added = out.filter(~pl.col("stock_id").is_in(list(have)))
    missing_before = [s for s in present if s not in have]
    not_added = [s for s in missing_before if s not in set(added["stock_id"].to_list())]
    with_ind = added.filter(pl.col("industry").is_not_null()).height
    dl_before = (
        existing.filter(pl.col("delist_date").is_not_null()).height if existing.height else 0
    )
    dl_after = out.filter(pl.col("delist_date").is_not_null()).height

    print(
        f"\n缺漏 {len(missing_before)} 檔 → 補列 {added.height} 檔"
        f"（其中 {with_ind} 檔有產業別，三個因子會因此復活）",
        flush=True,
    )
    if not_added:
        print(
            f"  未補列 {len(not_added)} 檔（FinMind 無市場別，不得偽造）：{', '.join(not_added)}",
            flush=True,
        )
    print(f"delist_date：{dl_before} → {dl_after} 檔有值", flush=True)
    if added.height:
        print("\n補列樣本：", flush=True)
        print(added.head(6))

    if args.check:
        print("\n--check 完成，未寫入。", flush=True)
        return

    # 沿用既有的 part-pg.parquet：`_write_partition` 只替換同名檔，寫成別的 source 名會與舊檔
    # 並存 → scan 時 union 出重複 stock_id。這裡的輸出本來就是「完整的一整張表」。
    wh.write("clean", "stock_meta", out, source="pg")
    reread = wh.scan("stock_meta").collect()
    if reread.height != out.height or reread["stock_id"].n_unique() != out.height:
        raise SystemExit(
            f"寫入後湖裡有 {reread.height:,} 列、{reread['stock_id'].n_unique():,} 個相異代號，"
            f"預期各 {out.height:,}——分區檔可能重複，請檢查 clean/stock_meta/date=_all/"
        )

    cfg = ServingConfig(pg_schema="serving")
    spec = sch.spec_of("stock_meta")
    with store.connect(cfg) as conn, conn.cursor() as cur:
        upsert(cur, cfg.pg_schema, spec, out.select([c for c, _ in spec.columns]))
        conn.commit()
    print(f"\n已寫入湖與 PG：{out.height:,} 列（湖已回讀驗證無重複）", flush=True)


if __name__ == "__main__":
    main()
