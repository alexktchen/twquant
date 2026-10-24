"""新聞語料向量的精簡備份（2026-10-24，使用者之後要關本機）：news_hist.embeddings → 二進位檔。

pg_dump 會把 halfvec 寫成文字，72.7 萬則壓完還有 7.9 GB；這裡直接存 float16 矩陣（約 1.5 GB）＋ id 清單。
分批寫進 memmap，不一次載入全部（記憶體只用一批）。

用法：uv run python scripts/news_hist_export_vectors.py --out D:/project/twquant/data/archive/news_hist
產出：news_hist-embeddings-<日期>.npy（float16，列 × 1024）、news_hist-embeddings-<日期>-ids.txt.gz（每列「item_id<tab>模型」，順序同矩陣）、
      news_hist-embeddings-<日期>.json（模型、列數、維度、sha256）。
還原：讀 npy＋ids，逐批 `insert into news_hist.embeddings (item_id, model, vec) values (%s, %s, %s::halfvec)`。
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import psycopg

DIM = 1024
BATCH = 20000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().strftime("%Y%m%d")
    npy = out / f"news_hist-embeddings-{day}.npy"
    ids_path = out / f"news_hist-embeddings-{day}-ids.txt.gz"
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) from news_hist.embeddings")
            (n,) = cur.fetchone()
            cur.execute("select model, count(*) from news_hist.embeddings group by 1 order by 1")
            models = {str(m): int(c) for m, c in cur.fetchall()}
        mat = np.lib.format.open_memmap(npy, mode="w+", dtype=np.float16, shape=(n, DIM))
        row = 0
        with (
            gzip.open(ids_path, "wt", encoding="utf-8") as ids,
            conn.cursor(name="emb", binary=True) as cur,
        ):
            cur.itersize = BATCH
            cur.execute("select item_id, model, vec from news_hist.embeddings order by item_id")
            for item_id, model, raw in cur:
                b = bytes(raw)
                if int.from_bytes(b[:2], "big") != DIM:
                    raise SystemExit(f"{item_id}：維度不是 {DIM}")
                mat[row] = np.frombuffer(b[4:], dtype=">f2")
                ids.write(f"{item_id}\t{model}\n")
                row += 1
        if row != n:
            raise SystemExit(f"列數對不上：預期 {n}、寫了 {row}")
        mat.flush()
        del mat
    h = hashlib.sha256()
    with npy.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    meta = {
        "rows": n,
        "dim": DIM,
        "dtype": "float16",
        # 本機 BAAI/bge-m3 與雲端 @cf/baai/bge-m3 是同一個模型（T-412 驗收 cosine 中位數 1.0000）
        "models": models,
        "order": "item_id",
        "npy_sha256": h.hexdigest(),
    }
    (out / f"news_hist-embeddings-{day}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] {n} 則向量 → {npy}（{npy.stat().st_size / 2**30:.2f} GB）")


if __name__ == "__main__":
    main()
