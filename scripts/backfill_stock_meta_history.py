"""T-164：把長歷史會用到、但 `stock_meta` 沒有的股票補進來（多半是已下市者）。

**為什麼非補不可**：T-143 的 `markets` 濾網用 `stock_meta` 決定一檔股票在不在
可交易市場。`stock_meta` 的來源（TWSE ISIN）只列**現存**上市櫃公司，已下市者整批缺席
——於是就算把它們的行情載進 `prices_daily`，策略層仍會把它們濾掉，
**存活者偏誤在下一層原封不動地回來**，而 T-116 花了 29 小時回補就白做了。

資料來源與規則（實測數字見下）：

| 來源 | 給什麼 | 註 |
|---|---|---|
| FinMind `TaiwanStockInfo` | 市場別（`type`）、名稱、產業別 | **`type` 有 `emerging`（興櫃）** |
| FinMind `TaiwanStockDelisting` | 權威下市日 | 725 列 |

**`emerging` 是這支腳本最重要的發現**：3,007 檔 staging 股票裡有 **387 檔的 type
只有 `emerging`**——它們**從未上市**，整檔不得進 `stock_meta`，也不得載入行情。
實測：套上「排除 emerging-only ＋ `date >= list_date`」兩道規則後，
2015-06~2019-12 的 OHLC 交叉率由 **3.940% 降到 0.065%**（PG 自己 2021 年是 0.29%）。

**FinMind 沒有上市日**（`TaiwanStockInfo` 只有 date／industry_category／stock_id／
stock_name／type），故補進來的股票 `list_date` 留 null。那些股票的興櫃期只能靠
行情恆等式在載入時擋（`load_finmind_history.py`），無法用日期切——這一點必須寫下來，
因為它是這批資料唯一沒有權威依據的地方。

token 只從 `.env` 的 `FINMIND_TOKEN` 讀，永不寫入輸出（黃金規則 9）。

用法：
    python scripts/backfill_stock_meta_history.py --check   # 只報告，不寫
    python scripts/backfill_stock_meta_history.py --write
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl
import requests

from twquant.config import ServingConfig, load_config
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import API, DELISTING_DATASET, STOCK_INFO_DATASET

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
_ENV = pathlib.Path("D:/project/twquant/.env")
STAGING = pathlib.Path("D:/project/twquant/data/staging/finmind_hist_price")
_MARKETS = {"twse": "TWSE", "tpex": "TPEX"}


def _token() -> str:
    """從環境變數或 .env 取 token。**不回傳給任何輸出路徑**。"""
    tok = os.environ.get("FINMIND_TOKEN")
    if not tok and _ENV.exists():
        for line in _ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("FINMIND_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not tok:
        raise SystemExit("FINMIND_TOKEN 未設定（環境變數或 D:/project/twquant/.env）")
    return tok


def _fetch(dataset: str, token: str) -> list[dict[str, object]]:
    r = requests.get(API, params={"dataset": dataset, "token": token}, timeout=180)
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
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只報告，不寫")
    g.add_argument("--write", action="store_true", help="寫入 PG serving.stock_meta")
    args = ap.parse_args()

    token = _token()
    info = _fetch(STOCK_INFO_DATASET, token)
    deli = _fetch(DELISTING_DATASET, token)
    print(f"TaiwanStockInfo {len(info):,} 列、TaiwanStockDelisting {len(deli):,} 列")

    # 一檔可能有多筆分類快照，且轉板者會同時出現 emerging 與 twse/tpex。
    # **只有「所有列都是 emerging」才算從未上市**——曾轉上市櫃的不得誤刪。
    types: dict[str, set[str]] = collections.defaultdict(set)
    names: dict[str, str] = {}
    for d in info:
        sid = d.get("stock_id")
        if isinstance(sid, str) and sid:
            types[sid].add(str(d.get("type") or ""))
            n = d.get("stock_name")
            if isinstance(n, str) and n.strip():
                names[sid] = n.strip()
    emerging_only = {s for s, t in types.items() if t == {"emerging"}}
    delist: dict[str, dt.date] = {}
    for d in deli:
        sid, day = d.get("stock_id"), d.get("date")
        if isinstance(sid, str) and isinstance(day, str):
            delist[sid] = dt.date.fromisoformat(day)

    staged = sorted(p.stem for p in STAGING.iterdir() if p.suffix == ".parquet")
    print(
        f"staging 行情 {len(staged):,} 檔；其中 type 只有 emerging（從未上市）{len(emerging_only & set(staged)):,} 檔"
    )

    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT stock_id, market, delist_date FROM "{cfg.pg_schema}"."stock_meta"')
            existing = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        print(f"stock_meta 現有 {len(existing):,} 檔")

        rows: list[dict[str, object]] = []
        skipped_emerging = skipped_unknown = 0
        for sid in staged:
            if sid in existing or sid in emerging_only:
                skipped_emerging += sid in emerging_only
                continue
            market = next(
                (_MARKETS[t] for t in sorted(types.get(sid, set())) if t in _MARKETS), None
            )
            if market is None:
                # 市場別不明者**不補列**（不得偽造）——它們仍會被 markets 濾網擋掉，
                # 但那是「資料不足」而不是「我們編了一個市場別」。
                skipped_unknown += 1
                continue
            rows.append(
                {
                    "stock_id": sid,
                    "name": names.get(sid),
                    "market": market,
                    "industry": None,  # 產業別留 null：對映規則見 sources/finmind.py，此處不猜
                    "list_date": None,  # FinMind 沒有上市日；興櫃期改由行情恆等式擋
                    "delist_date": delist.get(sid),
                }
            )
        print(
            f"\n將新增 {len(rows):,} 檔（跳過：已存在、興櫃 {skipped_emerging:,} 檔、"
            f"市場別不明 {skipped_unknown:,} 檔）"
        )
        if rows:
            # 全 null 的欄位會被 polars 推成 Null 型別而過不了 schema——顯式給型別
            frame = schemas.validate(
                "stock_meta",
                pl.DataFrame(
                    rows,
                    schema={
                        "stock_id": pl.String,
                        "name": pl.String,
                        "market": pl.String,
                        "industry": pl.String,
                        "list_date": pl.Date,
                        "delist_date": pl.Date,
                    },
                ).sort("stock_id"),
            )
            n_deli = int(frame["delist_date"].is_not_null().sum())
            print(
                f"  其中有下市日的 {n_deli:,} 檔；市場別分布 {dict(collections.Counter(frame['market'].to_list()))}"
            )
            print(frame.head(5))
        else:
            print("  沒有要新增的股票")

        # 既有列的 delist_date 補漏（T-111 同一個目的）
        fills = [
            {"stock_id": s, "delist_date": d}
            for s, d in delist.items()
            if s in existing and existing[s][1] is None and s in set(staged)
        ]
        print(f"既有列可補下市日 {len(fills):,} 檔")

        if args.check:
            print("\n--check，未寫入。")
            return
        if not rows and not fills:
            print("\n無事可做。")
            return
        sch.ensure_schema(conn, cfg.pg_schema)
        spec = sch.spec_of("stock_meta")
        with conn.cursor() as cur:
            if rows:
                upsert(cur, cfg.pg_schema, spec, frame)
            for f in fills:
                cur.execute(
                    f'UPDATE "{cfg.pg_schema}"."stock_meta" SET delist_date = %s WHERE stock_id = %s',
                    (f["delist_date"], f["stock_id"]),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{cfg.pg_schema}"."stock_meta"')
            print(f"\n寫入完成：stock_meta 由 {len(existing):,} 檔變成 {cur.fetchone()[0]:,} 檔")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
