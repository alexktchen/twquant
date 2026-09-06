"""盤點每一層資料的時間跨度：staging 回補 vs PG 供應層 vs 下游產物。

回答「長資料抓到哪邊了」這個問題。**一支腳本跑完**，不要開代理扇出查詢。

三層要分清楚，因為只有第三層會影響任何結果：

1. `data/staging/finmind_hist_*`  —— T-116 抓下來的原始 parquet（每檔一個）
2. `serving.*`（PG）              —— 策略、因子、回測**實際讀**的地方
3. 下游產物（factor_values、模型、ml_scores、回測結果包）

用法：
    python scripts/audit_history_span.py
    python scripts/audit_history_span.py --skip-staging   # 只看 PG（快）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl

from twquant.config import ServingConfig, load_config

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
DATA = pathlib.Path("D:/project/twquant/data")
STAGING = DATA / "staging"
YEARS = (2005, 2008, 2010, 2012, 2015, 2018, 2020, 2022, 2024, 2026)


def _hdr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def audit_staging() -> None:
    _hdr("① staging：T-116 抓下來的原始檔（**尚未進 PG，不影響任何結果**）")
    dirs = sorted(STAGING.glob("finmind_hist_*"))
    if not dirs:
        print("（找不到 finmind_hist_*）")
        return
    print(
        f"{'資料集':16s} {'檔數':>6s} {'最早':>11s} {'最晚':>11s} "
        f"{'總列數':>12s} {'MB':>6s} {'查無資料':>6s}"
    )
    per_year: dict[str, dict[int, int]] = {}
    for d in dirs:
        files = [p for p in d.iterdir() if p.suffix == ".parquet" and p.stat().st_size > 2]
        lo, hi, rows, mb, stubs = None, None, 0, 0.0, 0
        counts: dict[int, int] = {}
        for f in files:
            mb += f.stat().st_size / 1e6
            # FinMind 對「這檔在這個資料集沒有任何資料」回空表，落地成只有 stock_id
            # 一欄的殘檔。那不是壞檔，是「查無資料」——要算出來而不是靜默跳過。
            if "date" not in pl.scan_parquet(f).collect_schema().names():
                stubs += 1
                continue
            # 只讀 date 欄：整份載入 3,007 檔會爆記憶體。
            # FinMind 的 parquet 有些把 date 存成字串、有些存成 Date，故一律轉成字串比較
            # ——不猜格式，兩種都吃得下（ISO 字串的字典序等於日期序）。
            s = pl.read_parquet(f, columns=["date"])["date"]
            if s.is_empty():
                continue
            rows += s.len()
            iso = s.cast(pl.String)
            a, b = iso.min(), iso.max()
            lo = a if lo is None or a < lo else lo
            hi = b if hi is None or b > hi else hi
            for y in {int(x[:4]) for x in iso.to_list() if x}:
                counts[y] = counts.get(y, 0) + 1  # 該年有資料的**股票數**
        per_year[d.name.replace("finmind_hist_", "")] = counts
        print(
            f"{d.name.replace('finmind_hist_', ''):16s} {len(files):6d} "
            f"{lo!s:>11s} {hi!s:>11s} {rows:12,} {mb:6.0f} {stubs:6d}"
        )

    print("\n逐年**有資料的股票檔數**（總宇宙 3,007 檔）——早期是不是稀疏尾巴：")
    print(f"{'資料集':16s} " + " ".join(f"{y:>6d}" for y in YEARS))
    for name, counts in per_year.items():
        print(f"{name:16s} " + " ".join(f"{counts.get(y, 0):6d}" for y in YEARS))


def audit_pg(conn: object, schema: str) -> None:
    cur = conn.cursor()  # type: ignore[attr-defined]
    _hdr("② PG serving：策略／因子／回測**實際讀**的地方")
    cur.execute(
        "select table_name from information_schema.tables "
        "where table_schema=%s order by table_name",
        (schema,),
    )
    tables = [r[0] for r in cur.fetchall()]
    print(f"{'表':26s} {'最早':>12s} {'最晚':>12s} {'列數':>14s}")
    dated = []
    for t in tables:
        cur.execute(
            "select 1 from information_schema.columns where table_schema=%s "
            "and table_name=%s and column_name='date'",
            (schema, t),
        )
        if not cur.fetchone():
            continue
        cur.execute(f'select min(date), max(date), count(*) from "{schema}"."{t}"')
        lo, hi, n = cur.fetchone()
        dated.append((t, lo, hi, n))
        print(f"{t:26s} {lo!s:>12s} {hi!s:>12s} {n:14,}")

    core = [
        "prices_daily",
        "institutional_daily",
        "margin_daily",
        "monthly_revenue",
        "stock_valuation",
        "financial_statements",
        "corporate_events",
    ]
    print("\n核心表的逐年剖面（列數／相異股票數）：")
    print(f"{'表':22s} " + " ".join(f"{y:>13d}" for y in YEARS if y >= 2018))
    for t in core:
        cells = []
        for y in (y for y in YEARS if y >= 2018):
            cur.execute(
                f'select count(*), count(distinct stock_id) from "{schema}"."{t}" '
                "where date >= %s and date < %s",
                (f"{y}-01-01", f"{y + 1}-01-01"),
            )
            n, k = cur.fetchone()
            cells.append(f"{n:>8,}/{k:<4d}")
        print(f"{t:22s} " + " ".join(f"{c:>13s}" for c in cells))


def audit_downstream(conn: object, schema: str) -> None:
    cur = conn.cursor()  # type: ignore[attr-defined]
    _hdr("③ 下游產物：因子、模型、回測")

    cur.execute(
        f"select min(date), max(date), count(distinct date), count(distinct factor) "
        f'from "{schema}"."factor_values"'
    )
    lo, hi, nd, nf = cur.fetchone()
    print(f"factor_values：{lo} ~ {hi}，{nd:,} 個交易日、{nf} 個因子")
    cur.execute(
        f'select min(date) from "{schema}"."factor_values" where date < %s', ("2020-01-02",)
    )
    print(f"  早於 2020-01-02 的最早日期：{cur.fetchone()[0] or '無'}")

    cur.execute(
        f"select model_id, min(date), max(date), count(distinct date) "
        f'from "{schema}"."ml_scores" group by model_id order by model_id'
    )
    print(f"\n{'ml_scores 的 model_id':38s} {'最早':>11s} {'最晚':>11s} {'日數':>6s}")
    for mid, lo2, hi2, nd2 in cur.fetchall():
        print(f"{mid:38s} {lo2!s:>11s} {hi2!s:>11s} {nd2:6d}")

    print(
        f"\n{'模型 artifact':38s} {'data_from':>11s} {'data_to':>11s} {'窗數':>5s} {'OOS IC':>9s}"
    )
    for d in sorted(DATA.glob("models/**/meta.json")):
        m = json.loads(d.read_text(encoding="utf-8"))
        tag = str(d.parent.relative_to(DATA / "models"))
        print(
            f"{tag[:38]:38s} {m.get('data_from')!s:>11s} {m.get('data_to')!s:>11s} "
            f"{len(m.get('window_ics') or []):5d} {m.get('oos_rank_ic', float('nan')):+9.4f}"
        )

    print("\n回測結果包的視窗：")
    windows: dict[tuple[str, str, int], list[str]] = {}
    for p in sorted(DATA.glob("tracks*/*.json")) + sorted(DATA.glob("ladder/*.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - 壞包要據實列出，不得靜默略過
            print(f"  !! 讀不到 {p.name}：{exc}")
            continue
        key = (str(b.get("start")), str(b.get("end")), int(b["metrics"]["n_days"]))
        windows.setdefault(key, []).append(p.parent.name)
    for (s, e, n), where in sorted(windows.items()):
        dirs = sorted(set(where))
        print(f"  {s} ~ {e}（{n} 交易日）：{len(where)} 個包，來自 {', '.join(dirs)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-staging", action="store_true", help="只看 PG（快）")
    args = ap.parse_args()

    if not args.skip_staging:
        audit_staging()
    from twquant.data.serving import store

    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        audit_pg(conn, cfg.pg_schema)
        audit_downstream(conn, cfg.pg_schema)
    finally:
        conn.close()
    print(
        "\n判讀：只有 ② 與 ③ 會影響任何結果。① 是原始檔，"
        "在載入 PG 之前對策略、因子、回測完全沒有作用。"
    )


if __name__ == "__main__":
    main()
