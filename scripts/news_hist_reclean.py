"""一次性資料修復（T-412，2026-10-21）：news_hist.items 鉅亨網內文原本留著 <p> 等標籤（cnyes_archive.strip_html 解實體與去標籤的順序錯了），
用修好的 strip_html 就地重洗 content／summary。冪等；可重跑。

用法：uv run python scripts/news_hist_reclean.py  （環境變數 TWQUANT_PG_DSN）
"""

from __future__ import annotations

import os
import time

import psycopg

from twquant.data.sources.cnyes_archive import strip_html

BATCH = 5000


def main() -> None:
    t0 = time.monotonic()
    done = changed = 0
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        last = ""
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, summary, content from news_hist.items where origin = 'cnyes' and id > %s order by id limit %s",
                    (last, BATCH),
                )
                rows = cur.fetchall()
            if not rows:
                break
            upd = []
            for item_id, summary, content in rows:
                s2 = strip_html(summary, 1000) if summary else None
                c2 = strip_html(content, 2000) if content else None
                if s2 != summary or c2 != content:
                    upd.append((s2, c2, item_id))
            if upd:
                with conn.cursor() as cur:
                    cur.executemany(
                        "update news_hist.items set summary = %s, content = %s where id = %s", upd
                    )
                conn.commit()
            done += len(rows)
            changed += len(upd)
            last = rows[-1][0]
            print(f"  {done} 則、改了 {changed}；{time.monotonic() - t0:.0f} 秒", flush=True)
    print(f"[OK] 看過 {done} 則、重洗 {changed} 則；{time.monotonic() - t0:.0f} 秒")


if __name__ == "__main__":
    main()
