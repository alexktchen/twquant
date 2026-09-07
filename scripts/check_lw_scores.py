"""三個長窗口模型的分數是否都已回填進 ml_scores——是則 exit 0，否則 exit 1（供排隊腳本輪詢）。"""

from __future__ import annotations

import os
import sys

import psycopg

_MODELS = (
    "lgbm_lw_net_h20_20260225_83890036",
    "lgbm_lw_rev_h15_20260225_9d4a149c",
    "lgbm_lw_dd_h60_20260225_1bd7c6a0",
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "select model_id, count(*), min(date), max(date) from serving.ml_scores "
            "where model_id = any(%s) group by model_id",
            (list(_MODELS),),
        )
        got = {r[0]: r[1:] for r in cur.fetchall()}
    ok = True
    for m in _MODELS:
        if m in got:
            print(f"  {m}: {got[m][0]:,} 列 {got[m][1]} ~ {got[m][2]}")
        else:
            print(f"  {m}: 尚未回填")
            ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
