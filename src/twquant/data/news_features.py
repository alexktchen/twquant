"""新聞因子特徵（T-414／SPEC-018 §5／D-201）：news_hist（新聞、提及、向量、情緒）→ 每日逐股特徵 → 主庫 news_features。

資料層：`load_*`／`write_*` 連 PG；計算全是純函式（polars／numpy），測試只打純函式。
因子模組 factors/news.py 只讀 news_features（跟 ml_scores 同一套路，黃金規則 1）。

可得時點：t 日那一列的 available_at ＝ t 日 cutoff_hour（configs/news_factors.yaml），算的時候只用
available_at ≤ 那個時刻的新聞（截點之後的新聞算隔天），而且只算截點已過的日子——永遠不會蓋出未來時間。
缺值規則（SPEC-018 §5）：沒新聞＝null、不補 0；只有 count_1d 的 0 是真值。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl

from twquant.conventions import TZ
from twquant.errors import DataError

HIST = "news_hist"
TABLE = "news_features"
VALUE_COLS = (
    "count_1d",
    "attention_5d",
    "attention_20d",
    "novelty",
    "sent_1d",
    "sent_5d",
    "sent_shift",
    "sector_heat",
    "mops_count_5d",
    "mops_novelty",
)

_EPS = 1e-6  # 滾動標準差在全 0 窗會是 1e-9 級的浮點殘差而不是 0（則數是整數，這麼小的差異沒有意義），視同 0、z 不算

SIM_DDL = f"""
create table if not exists {HIST}.item_stock_sim (
  item_id     text not null references {HIST}.items (id),
  stock_id    text not null,
  kind        text not null check (kind in ('news', 'mops')),
  max_sim     real,
  computed_at timestamptz not null default now(),
  primary key (item_id, stock_id, kind)
);
"""


def topic_cols(k: int) -> list[str]:
    """topic_01 … topic_k。"""
    return [f"topic_{i:02d}" for i in range(1, k + 1)]


# ── 純計算 ──────────────────────────────────────────────────────────────────────


def news_day(available_at: pl.Expr, cutoff_hour: int) -> pl.Expr:
    """新聞算哪一天：available_at ≤ t 日 cutoff_hour 的算 t，之後的算 t+1（跟 t 日那列的截點一致，含等於）。"""
    return (
        (available_at - pl.duration(hours=cutoff_hour, microseconds=1)).dt.date().dt.offset_by("1d")
    )


def daily_counts(mentions: pl.DataFrame, cutoff_hour: int) -> pl.DataFrame:
    """(item_id, stock_id, available_at, is_mops) → 每天每檔：n（一般新聞）、n_mops（公告）。同一則同一檔只算一次。"""
    return (
        mentions.unique(subset=["item_id", "stock_id"])
        .with_columns(news_day(pl.col("available_at"), cutoff_hour).alias("day"))
        .group_by("day", "stock_id")
        .agg(
            (~pl.col("is_mops")).sum().cast(pl.Int64).alias("n"),
            pl.col("is_mops").sum().cast(pl.Int64).alias("n_mops"),
        )
    )


def dense_grid(stocks: Sequence[str], start: dt.date, end: dt.date) -> pl.DataFrame:
    """每檔 × 每個日曆日（含沒新聞的日子；滾動窗以日曆日計）。"""
    days = pl.date_range(start, end, "1d", eager=True).alias("day")
    return pl.DataFrame({"day": days}).join(
        pl.DataFrame({"stock_id": sorted(set(stocks))}), how="cross"
    )


def _rs(col: str, n: int) -> pl.Expr:
    return pl.col(col).rolling_sum(window_size=n, min_samples=1).over("stock_id")


def count_features(
    grid: pl.DataFrame, counts: pl.DataFrame, *, short: int, mid: int, base: int, mops: int
) -> pl.DataFrame:
    """密格 × 每日則數 → count_1d（log1p）、attention_5d／20d（近 N 日則數 ÷ 近 base 日日均，都 +1，取 log）、mops_count_5d。
    前 base 天不足時用「已有天數」當分母（不是 base），熱度才不會在語料開頭被高估。"""
    df = (
        grid.join(counts, on=["day", "stock_id"], how="left")
        .with_columns(
            pl.col("n").fill_null(0), pl.col("n_mops").fill_null(0), pl.lit(1).alias("one")
        )
        .sort(["stock_id", "day"])
        .with_columns(
            _rs("n", short).alias("c_short"),
            _rs("n", mid).alias("c_mid"),
            _rs("n", base).alias("c_base"),
            _rs("one", base).alias("d_base"),
            _rs("n_mops", mops).alias("mops_count_5d"),
        )
    )
    daily_mean = pl.col("c_base") / pl.col("d_base") + 1
    return df.with_columns(
        pl.col("n").cast(pl.Float64).log1p().alias("count_1d"),
        ((pl.col("c_short") + 1) / daily_mean).log().alias("attention_5d"),
        ((pl.col("c_mid") + 1) / daily_mean).log().alias("attention_20d"),
        pl.col("mops_count_5d").cast(pl.Float64),
    ).select("day", "stock_id", "n", "count_1d", "attention_5d", "attention_20d", "mops_count_5d")


def _mean(s: pl.Expr, k: pl.Expr) -> pl.Expr:
    return pl.when(k > 0).then(s / k).otherwise(None)


def sentiment_features(
    grid: pl.DataFrame,
    scores: pl.DataFrame,
    *,
    cutoff_hour: int,
    short: int,
    mid: int,
    shift_base: int,
) -> pl.DataFrame:
    """(item_id, stock_id, available_at, sent=p_pos−p_neg) → sent_1d、sent_5d（窗內平均，沒新聞＝null）、
    sent_shift ＝ sent_5d − 再往前 shift_base 日的平均。"""
    daily = (
        scores.unique(subset=["item_id", "stock_id"])
        .with_columns(news_day(pl.col("available_at"), cutoff_hour).alias("day"))
        .group_by("day", "stock_id")
        .agg(pl.col("sent").sum().alias("s"), pl.len().cast(pl.Int64).alias("k"))
    )
    df = (
        grid.join(daily, on=["day", "stock_id"], how="left")
        .with_columns(pl.col("s").fill_null(0.0), pl.col("k").fill_null(0))
        .sort(["stock_id", "day"])
        .with_columns(
            _rs("s", short).alias("s1"),
            _rs("k", short).alias("k1"),
            _rs("s", mid).alias("s5"),
            _rs("k", mid).alias("k5"),
            _rs("s", mid + shift_base).alias("s25"),
            _rs("k", mid + shift_base).alias("k25"),
        )
    )
    sent_mid = _mean(pl.col("s5"), pl.col("k5"))
    prev = _mean(pl.col("s25") - pl.col("s5"), pl.col("k25") - pl.col("k5"))
    return df.select(
        "day",
        "stock_id",
        _mean(pl.col("s1"), pl.col("k1")).alias("sent_1d"),
        sent_mid.alias("sent_5d"),
        (sent_mid - prev).alias("sent_shift"),
    )


def sector_heat(
    grid_counts: pl.DataFrame,
    groups: pl.DataFrame,
    *,
    short: int,
    base: int,
    min_base: int,
) -> pl.DataFrame:
    """族群近 short 日被提到的則數，對該族群近 base 日的 z 分數（不足 min_base 天或標準差 0 → null）。
    grid_counts 是密格（含 0），所以沒新聞的成員也算進族群日均。"""
    g = (
        grid_counts.join(groups, on="stock_id", how="inner")
        .group_by("day", "grp")
        .agg(pl.col("n").sum().alias("gn"))
        .sort(["grp", "day"])
        .with_columns(
            pl.col("gn").rolling_sum(window_size=short, min_samples=1).over("grp").alias("gs")
        )
        .with_columns(
            pl.col("gs")
            .rolling_mean(window_size=base, min_samples=min_base)
            .over("grp")
            .alias("mu"),
            pl.col("gs")
            .rolling_std(window_size=base, min_samples=min_base)
            .over("grp")
            .alias("sd"),
        )
        .with_columns(
            pl.when(pl.col("sd") > _EPS)
            .then((pl.col("gs") - pl.col("mu")) / pl.col("sd"))
            .otherwise(None)
            .alias("sector_heat")
        )
    )
    return groups.join(g.select("day", "grp", "sector_heat"), on="grp", how="inner").select(
        "day", "stock_id", "sector_heat"
    )


def novelty_features(grid: pl.DataFrame, sims: pl.DataFrame, *, recent: int) -> pl.DataFrame:
    """每則新聞對「這檔過去 base 日的新聞」的最大 cosine（item_max_sims 先算好）→ 近 recent 日取最大 → novelty ＝ 1 − 它。
    窗內沒新聞、或新聞都沒有舊聞可比 → null。"""
    d = sims.group_by("day", "stock_id").agg(pl.col("max_sim").max().alias("ms"))
    return (
        grid.join(d, on=["day", "stock_id"], how="left")
        .sort(["stock_id", "day"])
        .with_columns(
            pl.col("ms")
            .rolling_max(window_size=recent, min_samples=1)
            .over("stock_id")
            .alias("ms_recent")
        )
        .select("day", "stock_id", (1 - pl.col("ms_recent")).alias("novelty"))
    )


def mops_novelty_features(grid: pl.DataFrame, sims: pl.DataFrame, *, max_age: int) -> pl.DataFrame:
    """最新一則公告（≤ max_age 天內）對它過去 base 日公告的最大 cosine → 1 − 它；同一天多則取最大（最不新的那則）。"""
    d = sims.group_by("day", "stock_id").agg(pl.col("max_sim").max().alias("ms"))
    return (
        grid.join(d, on=["day", "stock_id"], how="left")
        .sort(["stock_id", "day"])
        .with_columns(
            pl.col("ms")
            .fill_null(strategy="forward", limit=max_age)
            .over("stock_id")
            .alias("ms_latest")
        )
        .select("day", "stock_id", (1 - pl.col("ms_latest")).alias("mops_novelty"))
    )


def item_max_sims(
    days: np.ndarray, vecs: np.ndarray, need: np.ndarray, base_days: int
) -> np.ndarray:
    """一檔股票的新聞按日期排好（days 升冪、vecs 已正規化）：每則對「同檔 [day−base, day−1] 的新聞」的最大 cosine。
    只算 need 為 True 的列；沒有可比的舊聞 → nan。"""
    if not (len(days) == len(vecs) == len(need)):
        raise ValueError("days、vecs、need 長度要一樣")
    if len(days) > 1 and np.any(np.diff(days) < 0):
        raise ValueError("days 要升冪")
    out = np.full(len(days), np.nan, dtype=np.float32)
    for d in np.unique(days[need]):
        a = int(np.searchsorted(days, d, side="left"))
        b = int(np.searchsorted(days, d, side="right"))
        lo = int(np.searchsorted(days, d - base_days, side="left"))
        if lo >= a:
            continue
        q = np.flatnonzero(need[a:b]) + a
        sims = vecs[q] @ vecs[lo:a].T
        out[q] = sims.max(axis=1)
    return out


def topic_features(
    days: np.ndarray, vecs: np.ndarray, centers: np.ndarray, *, recent: int, stock_id: str
) -> pl.DataFrame:
    """一檔股票：近 recent 日提到它的新聞向量平均，對每個主題中心的 cosine（主題中心已正規化）。
    回 (day, stock_id, topic_01…)，只有窗內有新聞的日子。days 是「新聞算哪一天」的序數（date.toordinal）。"""
    if len(days) == 0:
        return pl.DataFrame()
    d0, d1 = int(days.min()), int(days.max()) + recent - 1
    span = d1 - d0 + 1
    daily = np.zeros((span, vecs.shape[1]), dtype=np.float32)
    np.add.at(daily, (days - d0).astype(np.int64), vecs.astype(np.float32))
    csum = np.cumsum(daily, axis=0)
    window = csum.copy()
    window[recent:] -= csum[:-recent]
    norm = np.linalg.norm(window, axis=1)
    keep = norm > 0
    cos = (window[keep] @ centers.T) / norm[keep][:, None]
    cols = topic_cols(centers.shape[0])
    out = {
        "day": [dt.date.fromordinal(d0 + int(i)) for i in np.flatnonzero(keep)],
        "stock_id": [stock_id] * int(keep.sum()),
    }
    out.update({c: cos[:, j].astype(np.float64).tolist() for j, c in enumerate(cols)})
    return pl.DataFrame(out)


def fit_topics(vecs: np.ndarray, k: int, seed: int) -> np.ndarray:
    """k-means 主題中心（固定 seed、單次初始化 → 決定性），中心正規化。只在第一折訓練期的新聞上擬合並凍結。"""
    from sklearn.cluster import KMeans

    if len(vecs) < k * 10:
        raise DataError("news_topics", None, f"只有 {len(vecs)} 則新聞，不夠擬合 {k} 個主題")
    km = KMeans(n_clusters=k, n_init=1, random_state=seed, max_iter=100).fit(
        vecs.astype(np.float32)
    )
    c = np.asarray(km.cluster_centers_, dtype=np.float32)
    return np.asarray(c / np.linalg.norm(c, axis=1, keepdims=True), dtype=np.float32)


def assemble(
    dates: Sequence[dt.date],
    parts: Sequence[pl.DataFrame],
    *,
    cutoff_hour: int,
    topic_k: int,
    topic_version: str,
) -> pl.DataFrame:
    """把各特徵表（都有 day、stock_id）拼成 news_features 的列：只留交易日 dates，available_at ＝ date 的 cutoff_hour。
    第一張表是密格（決定股票池）；沒新聞的欄位留 null。"""
    base = parts[0].filter(pl.col("day").is_in(list(dates)))
    for p in parts[1:]:
        if p.height == 0:
            continue
        base = base.join(p, on=["day", "stock_id"], how="left")
    cols = [*VALUE_COLS, *topic_cols(topic_k)]
    missing = [c for c in cols if c not in base.columns]
    base = base.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in missing])
    return (
        base.rename({"day": "date"})
        .with_columns(
            pl.col("date")
            .cast(pl.Datetime("us"))
            .dt.replace_time_zone("Asia/Taipei")
            .dt.offset_by(f"{cutoff_hour}h")
            .alias("available_at"),
            pl.lit(topic_version).alias("topic_version"),
        )
        .select("date", "stock_id", "available_at", *cols, "topic_version")
        .sort(["date", "stock_id"])
    )


def cutoff_passed(day: dt.date, cutoff_hour: int, now: dt.datetime) -> bool:
    """這一天的截點過了沒（只算過了的日子，不蓋未來時間）。"""
    return now >= dt.datetime(day.year, day.month, day.day, cutoff_hour, tzinfo=TZ)


# ── IO（PG）────────────────────────────────────────────────────────────────────


def ensure_sim_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(SIM_DDL)
    conn.commit()


def load_mentions(conn: Any, until: dt.datetime) -> pl.DataFrame:
    """台股提及＋新聞可得時間：(item_id, stock_id, available_at, is_mops)。一般新聞取 alias／tag（任何規則版本）；
    公告只取 tag（公告公司本身），內文提到的對手方不算它的公告。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""select distinct m.item_id, substr(m.entity_id, 4), i.available_at, i.origin = 'mops'
                from {HIST}.mentions m join {HIST}.items i on i.id = m.item_id
                where m.entity_id like 'tw:%%' and m.method in ('alias', 'tag') and i.available_at <= %s
                  and (i.origin <> 'mops' or m.method = 'tag')""",
            (until,),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        {
            "item_id": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "available_at": [r[2] for r in rows],
            "is_mops": [bool(r[3]) for r in rows],
        },
        schema={
            "item_id": pl.String,
            "stock_id": pl.String,
            "available_at": pl.Datetime("us", "UTC"),
            "is_mops": pl.Boolean,
        },
    ).with_columns(pl.col("available_at").dt.convert_time_zone("Asia/Taipei"))


def load_scores(conn: Any, version: str, until: dt.datetime) -> pl.DataFrame:
    """情緒探針分數 → (item_id, stock_id, available_at, sent=p_pos−p_neg)。version 空 → 空表（sent_* 全 null）。"""
    if not version:
        return pl.DataFrame(
            schema={
                "item_id": pl.String,
                "stock_id": pl.String,
                "available_at": pl.Datetime("us", "Asia/Taipei"),
                "sent": pl.Float64,
            }
        )
    with conn.cursor() as cur:
        cur.execute(
            f"""select s.item_id, s.stock_id, i.available_at, s.p_pos - s.p_neg from {HIST}.sentiment_scores s
                join {HIST}.items i on i.id = s.item_id where s.version = %s and i.available_at <= %s""",
            (version, until),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        {
            "item_id": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "available_at": [r[2] for r in rows],
            "sent": [float(r[3]) for r in rows],
        },
        schema={
            "item_id": pl.String,
            "stock_id": pl.String,
            "available_at": pl.Datetime("us", "UTC"),
            "sent": pl.Float64,
        },
    ).with_columns(pl.col("available_at").dt.convert_time_zone("Asia/Taipei"))


def load_groups(conn: Any, schema: str) -> pl.DataFrame:
    """主庫 stock_group（D-177）：(stock_id, grp)。"""
    with conn.cursor() as cur:
        cur.execute(f'select stock_id, grp from "{schema}".stock_group where grp is not null')
        rows = cur.fetchall()
    return pl.DataFrame(
        {"stock_id": [r[0] for r in rows], "grp": [r[1] for r in rows]},
        schema={"stock_id": pl.String, "grp": pl.String},
    )


def load_universe(conn: Any, schema: str) -> list[str]:
    """主庫 stock_meta 的四碼普通股（跟 materialize 的股票池同一條規則）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"select stock_id from \"{schema}\".stock_meta where stock_id ~ '^[1-9][0-9]{{3}}$' order by stock_id"
        )
        return [str(r[0]) for r in cur.fetchall()]


def load_trading_days(conn: Any, schema: str, start: dt.date, end: dt.date) -> list[dt.date]:
    with conn.cursor() as cur:
        cur.execute(
            f'select distinct date from "{schema}".prices_daily where date between %s and %s order by date',
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def load_vectors(conn: Any, item_ids: Sequence[str]) -> tuple[list[str], np.ndarray]:
    """halfvec 用二進位協定抓（pgvector 送出：2 bytes 維度、2 bytes 保留、big-endian fp16）→ (有向量的 id, 正規化 float32 矩陣)。"""
    if not item_ids:
        return [], np.zeros((0, 0), dtype=np.float32)
    with conn.cursor(binary=True) as cur:
        cur.execute(
            f"select item_id, vec from {HIST}.embeddings where item_id = any(%s)", (list(item_ids),)
        )
        rows = cur.fetchall()
    ids: list[str] = []
    mats: list[np.ndarray] = []
    for item_id, raw in rows:
        b = bytes(raw)
        dim = int.from_bytes(b[:2], "big")
        v = np.frombuffer(b[4:], dtype=">f2").astype(np.float32)
        if v.shape[0] != dim:
            raise DataError(
                "news_embeddings", None, f"{item_id}：向量長度 {v.shape[0]} ≠ 標頭 {dim}"
            )
        ids.append(str(item_id))
        mats.append(v)
    if not ids:
        return [], np.zeros((0, 0), dtype=np.float32)
    m = np.stack(mats)
    return ids, m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-6)


def load_item_sims(conn: Any, kind: str) -> pl.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            f"select item_id, stock_id, max_sim from {HIST}.item_stock_sim where kind = %s", (kind,)
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        {
            "item_id": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "max_sim": [None if r[2] is None else float(r[2]) for r in rows],
        },
        schema={"item_id": pl.String, "stock_id": pl.String, "max_sim": pl.Float64},
    )


def save_item_sims(conn: Any, kind: str, rows: Sequence[tuple[str, str, float | None]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"insert into {HIST}.item_stock_sim (item_id, stock_id, kind, max_sim) values (%s, %s, %s, %s) on conflict (item_id, stock_id, kind) do update set max_sim = excluded.max_sim, computed_at = now()",
            [(i, s, kind, None if v is None or np.isnan(v) else float(v)) for i, s, v in rows],
        )
    conn.commit()
    return len(rows)


def write_features(conn: Any, schema: str, df: pl.DataFrame) -> int:
    """寫進主庫 news_features（同 (stock_id, date) 覆蓋）。先 COPY 進暫存表再 upsert。"""
    if df.height == 0:
        return 0
    cols = list(df.columns)
    col_sql = ", ".join(f'"{c}"' for c in cols)
    with conn.cursor() as cur:
        cur.execute(f'create temp table _nf (like "{schema}".{TABLE}) on commit drop')
        with cur.copy(f"copy _nf ({col_sql}) from stdin") as cp:
            for row in df.iter_rows():
                cp.write_row(row)
        upd = ", ".join(f'"{c}" = excluded."{c}"' for c in cols if c not in ("stock_id", "date"))
        cur.execute(
            f'insert into "{schema}".{TABLE} ({col_sql}) select {col_sql} from _nf on conflict (stock_id, date) do update set {upd}'
        )
    conn.commit()
    return df.height


def compute_item_sims_and_topics(
    conn: Any,
    items: pl.DataFrame,
    centers: np.ndarray,
    *,
    news_base: int,
    mops_base: int,
    recent: int,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """逐檔：抓這檔新聞的向量 → 還沒算過的 (item, stock) 相似度（新聞對過去 news_base 日、公告對過去 mops_base 日）存表、
    主題 cosine。items：(item_id, stock_id, day, is_mops)，已經只留要算的日期範圍（含回看）。
    回 (新聞相似度, 公告相似度, 主題特徵)，前兩個是 (item_id, stock_id, day, max_sim)。"""
    have = {
        k: set(zip(*(d[c].to_list() for c in ("item_id", "stock_id")), strict=True))
        for k, d in (("news", load_item_sims(conn, "news")), ("mops", load_item_sims(conn, "mops")))
    }
    topic_parts: list[pl.DataFrame] = []
    for (stock_id,), g in items.group_by("stock_id", maintain_order=True):
        sid = str(stock_id)
        for kind, flag, base in (("news", False, news_base), ("mops", True, mops_base)):
            sub_df = g.filter(pl.col("is_mops") == flag).sort(["day", "item_id"])
            if sub_df.height == 0:
                continue
            ids = sub_df["item_id"].to_list()
            need_all = [(i, sid) not in have[kind] for i in ids]
            if kind == "mops" and not any(need_all):
                continue
            got, vecs = load_vectors(conn, ids)
            pos = {i: j for j, i in enumerate(got)}
            keep = [j for j, i in enumerate(ids) if i in pos]
            if not keep:
                continue
            days_ord = np.array([sub_df["day"][j].toordinal() for j in keep], dtype=np.int64)
            v = vecs[[pos[ids[j]] for j in keep]]
            need = np.array([need_all[j] for j in keep])
            if need.any():
                sims = item_max_sims(days_ord, v, need, base)
                save_item_sims(
                    conn,
                    kind,
                    [(ids[j], sid, float(sims[t])) for t, j in enumerate(keep) if need[t]],
                )
            if kind == "news":
                topic_parts.append(
                    topic_features(days_ord, v, centers, recent=recent, stock_id=sid)
                )
    out = []
    for kind, flag in (("news", False), ("mops", True)):
        sims_df = load_item_sims(conn, kind)
        out.append(
            items.filter(pl.col("is_mops") == flag)
            .select("item_id", "stock_id", "day")
            .join(sims_df, on=["item_id", "stock_id"], how="inner")
        )
    parts = [t for t in topic_parts if t.height > 0]
    topics = pl.concat(parts, how="vertical") if parts else pl.DataFrame()
    return out[0], out[1], topics
