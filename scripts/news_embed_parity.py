"""T-412 驗收：同一則新聞，本機 sentence-transformers bge-m3 與雲端 Workers AI bge-m3 的向量 cosine 分布；
另附雲端新聞 published_at vs first_seen_at（研究用可得時間）差距分布。

用法：uv run python scripts/news_embed_parity.py --limit 1500 --out docs/reports/news-embed-parity-2026-10-21.md
門檻（TASKS T-412 事前登記）：cosine 中位數 ≥ 0.99。不過就是兩邊文字規則或模型版本不同，要查，不是把門檻調低。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import psycopg
import yaml

from twquant.data.news_hist import embed_text

PAIRS_SQL = """
select i.id, i.title, i.title_zh, i.summary, i.summary_zh, e.model, e.vec::text
from news_hist.items i join news_hist.embeddings e on e.item_id = i.id
where i.origin = 'cloud' and e.model like '@cf/%%'
order by i.published_at desc limit %s
"""
DELAY_SQL = """
select source, count(*) as n,
  percentile_cont(0.5) within group (order by extract(epoch from available_at - published_at) / 60) as med_min,
  percentile_cont(0.9) within group (order by extract(epoch from available_at - published_at) / 60) as p90_min
from news_hist.items where origin = 'cloud' and published_at is not null
group by source order by n desc
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args()
    cfg = yaml.safe_load(Path("configs/news.yaml").read_text(encoding="utf-8"))
    max_chars = int(cfg["embed"]["max_chars"])
    model_name = str(cfg["hist"]["embed_model"])
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(PAIRS_SQL, (a.limit,))
        rows = cur.fetchall()
        cur.execute(DELAY_SQL)
        delays = cur.fetchall()
    if not rows:
        raise SystemExit("本機沒有雲端來的向量（先跑 twquant news pull --with-embeddings）")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device=a.device)
    if a.device.startswith("cuda"):
        model.half()
    texts = [embed_text(r[1], r[2], r[3], r[4], max_chars) for r in rows]
    local = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    cloud = np.array([json.loads(r[6]) for r in rows], dtype=np.float64)
    cloud /= np.linalg.norm(cloud, axis=1, keepdims=True)
    cos = np.sum(local.astype(np.float64) * cloud, axis=1)
    q = {k: float(np.quantile(cos, v)) for k, v in (("p10", 0.1), ("median", 0.5), ("p90", 0.9))}
    worst = sorted(zip(cos, rows, strict=True), key=lambda t: t[0])[:5]
    cloud_model = sorted({r[5] for r in rows})
    today = dt.date.today().isoformat()
    lines = [
        f"# 本機 vs 雲端 bge-m3 向量一致性（T-412 驗收，{today}）",
        "",
        f"- 樣本：雲端來的新聞最近 {len(rows)} 則；雲端模型 {cloud_model}，本機 {model_name}（fp16、normalize）",
        f"- 文字規則兩邊同一條（`embed_text`，截 {max_chars} 字）",
        f"- cosine：中位數 **{q['median']:.4f}**、p10 {q['p10']:.4f}、p90 {q['p90']:.4f}、最低 {cos.min():.4f}；≥ 0.99 的占 {float((cos >= 0.99).mean()):.1%}",
        f"- 門檻（事前）：中位數 ≥ 0.99 → **{'過' if q['median'] >= 0.99 else '不過'}**",
        "",
        "最低的 5 則：",
        "",
        "| cosine | id | 標題 |",
        "|---|---|---|",
    ]
    lines += [f"| {c:.4f} | {r[0]} | {(r[2] or r[1])[:60]} |" for c, r in worst]
    lines += [
        "",
        "## 雲端新聞 published_at → first_seen_at（研究用可得時間）差距",
        "",
        "| 來源 | 則數 | 中位數（分） | p90（分） |",
        "|---|---|---|---|",
    ]
    lines += [f"| {s} | {n} | {m:.0f} | {p:.0f} |" for s, n, m, p in delays]
    lines += [
        "",
        "註：差距大的來源（官方新聞稿、週刊型）published_at 是發布日不是抓到的時間；研究一律用 available_at（= first_seen_at），不用 published_at。",
    ]
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:7]))


if __name__ == "__main__":
    main()
