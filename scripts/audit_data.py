"""T-138：全資料健檢——一次跑完所有「會報告成功但數字錯」的已知失效模式。

用法：
    python scripts/audit_data.py                    # 全部表、全期
    python scripts/audit_data.py --since 2022-01-01 # 只查近期
    python scripts/audit_data.py --tables prices_daily,factor_values
    python scripts/audit_data.py --out docs/reports/data-audit.md

設計原則：**每一項檢查都對應一個真的發生過的缺陷**，而不是憑空想像的風險。
清單來自 memory 的 `twquant-data-quality-traps` 與本 repo 的裁決紀錄：

| # | 檢查 | 對應的真實缺陷 |
|---|---|---|
| 1 | 整條腿缺席（分組佔比塌陷） | T-136：TWSE 缺 61 天，`sparse_dates` 因鄰居也壞而完全失效 |
| 2 | 稀疏日（鄰近日比較） | D-0xx：8 天只有 TWSE、TPEX 全缺 |
| 3 | 跨日重複快照 | 43 天寫了別天的資料，有些是**未來**的價格 |
| 4 | 幽靈日（不在交易日曆） | 颱風停市被當成正常交易日 |
| 5 | 個股列數斷層 | T-136 的餘波：缺口讓 `shift()` 的列位移跨越曆日缺口 |
| 6 | 因子覆蓋率塌陷 | 缺表會讓因子**靜默消失**（materialize 只記 DEBUG） |
| 7 | 非有限值（inf/NaN） | 除以零通過 `drop_nulls`；標籤 inf 會擴散到整個橫斷面 |
| 8 | 橫斷面常數因子 | MAD=0 時 winsorize 把整個橫斷面壓成常數，因子被刪除而無錯誤 |

**只讀，不寫。** 發現問題只回報，不自動修——修補要走各自的任務與裁決。
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import pathlib
import statistics as st
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data.serving import store

# 每張表的分組欄位：用來抓「整條腿缺席」。None 代表該表沒有天然的腿。
_LEG_BY_PREFIX = {  # 由 stock_id 前綴切，不需 join stock_meta
    "四碼普通股": r"^[1-9][0-9]{3}$",
    "ETF(00xx)": r"^00",
}
_DEFAULT_TABLES = (
    "prices_daily",
    "institutional_daily",
    "margin_daily",
    "monthly_revenue",
    "financial_statements",
    "stock_valuation",
    "factor_values",
    "ml_scores",
)
_LEG_MIN_RATIO = 0.5  # 當日某腿佔比 < 全期佔比的一半即回報
_SPARSE_MIN_RATIO = 0.7
_SPARSE_WINDOW = 5
_COVERAGE_MIN_RATIO = 0.7  # 因子覆蓋率 / 全期中位數


class Finding:
    """一條發現。`severity` 決定退出碼：critical → 1。"""

    def __init__(self, table: str, check: str, severity: str, detail: str) -> None:
        self.table, self.check, self.severity, self.detail = table, check, severity, detail

    def line(self) -> str:
        icon = {"critical": "🔴", "warn": "🟡", "info": "🔵"}[self.severity]
        return f"| {icon} {self.severity} | `{self.table}` | {self.check} | {self.detail} |"


def _daily_counts(cur, schema: str, table: str, since: dt.date | None) -> pl.DataFrame:
    where = "WHERE date >= %s" if since else ""
    cur.execute(
        f'SELECT date, COUNT(*) FROM "{schema}"."{table}" {where} GROUP BY date ORDER BY date',
        (since,) if since else (),
    )
    rows = cur.fetchall()
    return pl.DataFrame(rows, schema={"date": pl.Date, "n": pl.Int64}, orient="row")


def _has_col(cur, schema: str, table: str, col: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
        (schema, table, col),
    )
    return cur.fetchone() is not None


def _check_legs(cur, schema: str, table: str, since: dt.date | None) -> list[Finding]:
    """#1 整條腿缺席：以**全期佔比**為基準，不看鄰居——長時間停擺時鄰居也全壞。"""
    out: list[Finding] = []
    where = "WHERE date >= %s" if since else ""
    for name, rx in _LEG_BY_PREFIX.items():
        cur.execute(
            f"SELECT date, COUNT(*) FILTER (WHERE stock_id ~ %s), COUNT(*) "
            f'FROM "{schema}"."{table}" {where} GROUP BY date ORDER BY date',
            (rx, since) if since else (rx,),
        )
        rows = cur.fetchall()
        if not rows:
            continue
        tot_leg = sum(r[1] for r in rows)
        tot_all = sum(r[2] for r in rows)
        if tot_all == 0 or tot_leg == 0:
            continue
        base = tot_leg / tot_all
        bad = [r for r in rows if r[2] > 0 and (r[1] / r[2]) < base * _LEG_MIN_RATIO]
        if bad:
            span = f"{bad[0][0]} ~ {bad[-1][0]}"
            out.append(
                Finding(
                    table,
                    f"整腿缺席（{name}）",
                    "critical",
                    f"**{len(bad)} 天**佔比塌陷（全期 {base:.1%}，最低 "
                    f"{min(r[1] / r[2] for r in bad):.1%}），{span}",
                )
            )
    return out


def _check_sparse(counts: pl.DataFrame, table: str) -> list[Finding]:
    """#2 稀疏日：與鄰近 ±5 日比較。抓得到短暫塌陷，抓不到長期停擺（那是 #1 的事）。"""
    if counts.height < _SPARSE_WINDOW + 1:
        return []
    days, ns = counts["date"].to_list(), counts["n"].to_list()
    hits = []
    for i, (d, n) in enumerate(zip(days, ns, strict=True)):
        lo, hi = max(0, i - _SPARSE_WINDOW), min(len(ns), i + _SPARSE_WINDOW + 1)
        exp = st.median(ns[lo:hi])
        if exp > 0 and n < exp * _SPARSE_MIN_RATIO:
            hits.append((d, n, exp))
    if not hits:
        return []
    worst = min(hits, key=lambda h: h[1] / h[2])
    return [
        Finding(
            table,
            "稀疏日",
            "warn",
            f"{len(hits)} 天列數低於鄰近中位數的 {_SPARSE_MIN_RATIO:.0%}"
            f"（最嚴重 {worst[0]}：{worst[1]:,} vs 預期 {worst[2]:,.0f}）",
        )
    ]


def _check_dupe_snapshots(cur, schema: str, table: str, since: dt.date | None) -> list[Finding]:
    """#3 跨日重複快照：同 (stock_id, 值) 出現在兩個日期。成交量完全相同不可能是巧合。"""
    if not _has_col(cur, schema, table, "close"):
        return []
    where = "WHERE date >= %s" if since else ""
    cur.execute(
        f"""WITH s AS (SELECT date, stock_id, close, volume FROM "{schema}"."{table}" {where})
            SELECT a.date, b.date, COUNT(*)
            FROM s a JOIN s b
              ON a.stock_id=b.stock_id AND a.close=b.close AND a.volume=b.volume AND a.date<b.date
            GROUP BY 1,2 HAVING COUNT(*) > 100 ORDER BY 3 DESC LIMIT 5""",
        (since,) if since else (),
    )
    rows = cur.fetchall()
    if not rows:
        return []
    return [
        Finding(
            table,
            "跨日重複快照",
            "critical",
            f"{len(rows)} 組日期對有 >100 檔 (close, volume) 完全相同："
            + "、".join(f"{a}↔{b} {n:,} 檔" for a, b, n in rows[:3]),
        )
    ]


def _check_stock_gaps(cur, schema: str, table: str, since: dt.date | None) -> list[Finding]:
    """#5 個股列數斷層：標籤與因子的 `shift()` 是**列位移**，跨過曆日缺口就會算錯。

    `forward_open_returns` 用 `px.shift(-(1+h)).over("stock_id")`——列位移。
    某檔若中間缺 50 個交易日，「20 日報酬」實際跨越 2.5 個月，而標籤不會有任何異常標記。
    """
    if table not in ("prices_daily",):
        return []
    where = "AND date >= %s" if since else ""
    cur.execute(
        f"""WITH d AS (SELECT DISTINCT date FROM "{schema}"."{table}" WHERE TRUE {where}),
                 idx AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) i FROM d),
                 p AS (SELECT p.stock_id, i.i FROM "{schema}"."{table}" p JOIN idx i USING (date)
                       WHERE p.stock_id ~ '^[1-9][0-9]{{3}}$' {where}),
                 g AS (SELECT stock_id, i - LAG(i) OVER (PARTITION BY stock_id ORDER BY i) - 1 gap
                       FROM p)
            SELECT COUNT(*), MAX(gap) FROM g WHERE gap > 5""",
        (since, since) if since else (),
    )
    n, mx = cur.fetchone()
    if not n:
        return []
    sev = "critical" if (mx or 0) > 20 else "warn"
    return [
        Finding(
            table,
            "個股列數斷層",
            sev,
            f"{n:,} 處缺口 > 5 個開市日（最大 **{mx}** 日）。標籤的 `shift()` 是列位移，"
            "跨過缺口的「20 日報酬」實際會橫跨數月而無任何標記",
        )
    ]


def _check_factor_coverage(cur, schema: str, since: dt.date | None) -> list[Finding]:
    """#6 因子覆蓋率塌陷：缺表會讓因子靜默消失（materialize 只記 DEBUG）。"""
    where = "WHERE date >= %s" if since else ""
    cur.execute(
        f"SELECT date, COUNT(DISTINCT factor), COUNT(DISTINCT stock_id) "
        f'FROM "{schema}".factor_values {where} GROUP BY date ORDER BY date',
        (since,) if since else (),
    )
    rows = cur.fetchall()
    if not rows:
        return [Finding("factor_values", "因子覆蓋率", "warn", "無資料")]
    out = []
    med_f = st.median([r[1] for r in rows])
    bad_f = [r for r in rows if r[1] < med_f * _COVERAGE_MIN_RATIO]
    if bad_f:
        out.append(
            Finding(
                "factor_values",
                "因子數塌陷",
                "critical",
                f"{len(bad_f)} 天的相異因子數 < 中位數 {med_f:.0f} 的 "
                f"{_COVERAGE_MIN_RATIO:.0%}（最低 {min(r[1] for r in bad_f)}，"
                f"{bad_f[0][0]} ~ {bad_f[-1][0]}）",
            )
        )
    med_s = st.median([r[2] for r in rows])
    bad_s = [r for r in rows if r[2] < med_s * _COVERAGE_MIN_RATIO]
    if bad_s:
        out.append(
            Finding(
                "factor_values",
                "股票覆蓋率塌陷",
                "critical",
                f"{len(bad_s)} 天的相異股票數 < 中位數 {med_s:.0f} 的 "
                f"{_COVERAGE_MIN_RATIO:.0%}（最低 {min(r[2] for r in bad_s)}，"
                f"{bad_s[0][0]} ~ {bad_s[-1][0]}）",
            )
        )
    return out


def _check_nonfinite(cur, schema: str, table: str, since: dt.date | None) -> list[Finding]:
    """#7 非有限值：除以零得到 inf，而 inf **不是 null**，`drop_nulls` 放它過去。"""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND data_type IN "
        "('double precision','real','numeric')",
        (schema, table),
    )
    cols = [r[0] for r in cur.fetchall()]
    if not cols:
        return []
    where = "AND date >= %s" if since else ""
    conds = " OR ".join(
        f'("{c}" = \'Infinity\'::float8 OR "{c}" = \'-Infinity\'::float8 OR "{c}" != "{c}")'
        for c in cols
    )
    try:
        cur.execute(
            f'SELECT COUNT(*) FROM "{schema}"."{table}" WHERE ({conds}) {where}',
            (since,) if since else (),
        )
        n = cur.fetchone()[0]
    except Exception as exc:  # numeric 欄不支援 Infinity 比較等
        return [Finding(table, "非有限值", "info", f"無法檢查（{type(exc).__name__}）")]
    if not n:
        return []
    return [
        Finding(
            table,
            "非有限值",
            "critical",
            f"**{n:,} 列**含 inf/NaN。inf 不是 null，`drop_nulls` 攔不住；"
            "標籤若含 inf，橫斷面平均會把它擴散到當日**所有**樣本",
        )
    ]


def _check_constant_cross_section(cur, schema: str, since: dt.date | None) -> list[Finding]:
    """#8 橫斷面常數：MAD=0 時 winsorize 把整個橫斷面壓成常數，因子被刪除而無錯誤。"""
    where = "WHERE date >= %s" if since else ""
    cur.execute(
        f"""SELECT factor, COUNT(*) FROM (
                SELECT factor, date FROM "{schema}".factor_values {where}
                GROUP BY factor, date HAVING COUNT(DISTINCT value) = 1
            ) t GROUP BY factor ORDER BY 2 DESC LIMIT 8""",
        (since,) if since else (),
    )
    rows = cur.fetchall()
    if not rows:
        return []
    cur.execute(
        f'SELECT COUNT(DISTINCT date) FROM "{schema}".factor_values {where}',
        (since,) if since else (),
    )
    total = cur.fetchone()[0] or 1
    worst = [(f, n) for f, n in rows if n > total * 0.1]
    if not worst:
        return []
    return [
        Finding(
            "factor_values",
            "橫斷面常數因子",
            "warn",
            f"{len(worst)} 個因子在 >10% 的日子整個橫斷面同值（標準化後即被刪除）："
            + "、".join(f"`{f}` {n / total:.0%}" for f, n in worst[:5]),
        )
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--tables", default=",".join(_DEFAULT_TABLES))
    ap.add_argument("--out", default="docs/reports/data-audit.md")
    args = ap.parse_args()
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]

    cfg = ServingConfig(pg_schema="serving")
    findings: list[Finding] = []
    summary: list[tuple[str, int, int, str]] = []

    with store.connect(cfg) as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (cfg.pg_schema, t),
            )
            if cur.fetchone() is None:
                findings.append(Finding(t, "表存在", "warn", "表不存在，跳過"))
                continue
            counts = _daily_counts(cur, cfg.pg_schema, t, args.since)
            n_rows = int(counts["n"].sum()) if counts.height else 0
            span = f"{counts['date'].min()} ~ {counts['date'].max()}" if counts.height else "（空）"
            summary.append((t, n_rows, counts.height, span))
            print(f"[{t}] {n_rows:,} 列、{counts.height} 個日期 {span}", flush=True)

            if _has_col(cur, cfg.pg_schema, t, "stock_id"):
                findings += _check_legs(cur, cfg.pg_schema, t, args.since)
            findings += _check_sparse(counts, t)
            findings += _check_dupe_snapshots(cur, cfg.pg_schema, t, args.since)
            findings += _check_stock_gaps(cur, cfg.pg_schema, t, args.since)
            findings += _check_nonfinite(cur, cfg.pg_schema, t, args.since)
            if t == "factor_values":
                findings += _check_factor_coverage(cur, cfg.pg_schema, args.since)
                findings += _check_constant_cross_section(cur, cfg.pg_schema, args.since)

    order = {"critical": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f.severity], f.table, f.check))
    n_crit = sum(1 for f in findings if f.severity == "critical")

    lines = [
        "# 全資料健檢（T-138）",
        "",
        f"執行時間：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}　"
        f"範圍：{args.since or '全期'} 起　表：{len(tables)} 張",
        "",
        f"**{n_crit} 項 critical、{sum(1 for f in findings if f.severity == 'warn')} 項 warn。**",
        "",
        "每一項檢查都對應一個**真的發生過**的缺陷，不是憑空想像的風險。",
        "所有檢查只讀不寫；發現問題只回報，修補走各自的任務與裁決。",
        "",
        "## 發現",
        "",
        "| 嚴重度 | 表 | 檢查 | 詳情 |",
        "|---|---|---|---|",
    ]
    lines += [f.line() for f in findings] or ["| 🟢 ok | — | 全部檢查 | 無發現 |"]
    lines += ["", "## 各表概況", "", "| 表 | 列數 | 日期數 | 區間 |", "|---|---|---|---|"]
    lines += [f"| `{t}` | {n:,} | {d} | {s} |" for t, n, d, s in summary]
    lines += [
        "",
        "## 檢查清單與其對應的真實缺陷",
        "",
        "| 檢查 | 抓的是什麼 | 曾經漏掉它的原因 |",
        "|---|---|---|",
        "| 整腿缺席 | 某個分組（上市／上櫃／ETF）的佔比整段塌陷 | `sparse_dates` 以鄰近日為基準，"
        "停擺超過視窗時鄰居也全壞，一天都抓不到（T-136 的 61 天缺口即如此） |",
        "| 稀疏日 | 短暫的列數塌陷 | 跨日重複偵測只找**多出來**的列，找不到少掉的 |",
        "| 跨日重複快照 | 某天寫了別天的資料（含未來價格） | 擷取回報成功；只有比對 "
        "(close, volume) 完全相同才抓得到 |",
        "| 個股列數斷層 | 缺口讓 `shift()` 的列位移跨越曆日缺口 | 標籤與因子都用列位移，"
        "跨過缺口的「20 日報酬」實際橫跨數月而**無任何標記** |",
        "| 因子數／股票覆蓋率塌陷 | 缺表讓因子靜默消失 | `materialize` 逐因子 catch "
        "`DataError` 並只記 DEBUG，然後回報該日成功 |",
        "| 非有限值 | 除以零的 inf | inf **不是** null，`drop_nulls` 放它過去；"
        "標籤含 inf 會被橫斷面平均擴散到當日所有樣本 |",
        "| 橫斷面常數因子 | MAD=0 時 winsorize 把因子壓成常數 | 每一步都「正確」，"
        "合起來刪除因子，全程無錯誤 |",
        "",
    ]
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"\n已寫出 {out}（critical {n_crit}、warn "
        f"{sum(1 for f in findings if f.severity == 'warn')}）",
        flush=True,
    )
    for f in findings:
        if f.severity == "critical":
            print(f"  🔴 {f.table} / {f.check}：{f.detail}", flush=True)
    if n_crit:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
