"""新聞情緒（T-413／SPEC-018 §6、D-201 §四）：對「這則新聞對這檔股票」貼三類（−1 壞／0 中性／+1 好）。

兩段：(a) 本機 Ollama qwen3 對分層抽樣貼標（news_hist.sentiment_labels）；(b) 在 bge-m3 向量上訓線性探針複製標籤，
全語料打分（news_hist.sentiment_scores）。探針事前門檻（D-201 §四）：驗證 macro-F1 ≥ 0.70 且正負二分 AUC ≥ 0.85，不達就全語料用 Ollama 貼完。

這裡是資料層（可連 PG、可呼叫本機 Ollama）。純函式：抽樣、提示詞、回應解析、探針訓練與評估（numpy）。
標籤只對「新聞怎麼說」，不是對股價的預測（D-200 §二 有關≠會漲跌）。
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from twquant.data.linear_probe import Softmax, train_softmax
from twquant.errors import DataError

SCHEMA = "news_hist"
PROMPT_VERSION = "2026-10-21.1"
LABELS = {"好": 1, "正面": 1, "利多": 1, "壞": -1, "負面": -1, "利空": -1, "中性": 0, "無關": 0}

DDL = f"""
create table if not exists {SCHEMA}.sentiment_labels (
  item_id        text not null references {SCHEMA}.items (id),
  stock_id       text not null,
  label          smallint not null check (label in (-1, 0, 1)),
  model          text not null,
  prompt_version text not null,
  raw            text,
  created_at     timestamptz not null default now(),
  primary key (item_id, stock_id)
);
create table if not exists {SCHEMA}.sentiment_sample (
  item_id   text not null references {SCHEMA}.items (id),
  stock_id  text not null,
  stratum   text not null,
  primary key (item_id, stock_id)
);
create table if not exists {SCHEMA}.sentiment_scores (
  item_id   text not null references {SCHEMA}.items (id),
  stock_id  text not null,
  label     smallint not null check (label in (-1, 0, 1)),
  p_neg     real not null,
  p_neu     real not null,
  p_pos     real not null,
  method    text not null,            -- probe / llm
  version   text not null,
  primary key (item_id, stock_id, method)
);
"""


def ensure_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


# ── 抽樣：每個（年 × 來源）層抽同樣多，不夠的層全拿；同一則多檔股票各算一對 ──
@dataclass(frozen=True)
class Pair:
    item_id: str
    stock_id: str
    stratum: str  # 例 2023|cnyes


def stratified_sample(pairs: Sequence[Pair], n: int, seed: int) -> list[Pair]:
    """各層輪流抽（round-robin），直到 n 對或抽完；同一層內隨機（固定 seed，決定性）。"""
    if n < 1:
        raise ValueError("n 至少 1")
    rng = np.random.default_rng(seed)
    by: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by[p.stratum].append(p)
    queues = []
    for k in sorted(by):
        lst = by[k]
        order = rng.permutation(len(lst))
        queues.append([lst[i] for i in order])
    out: list[Pair] = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop())
    return out


PAIRS_SQL = f"""
select i.id, s.stock_id, extract(year from i.published_at at time zone 'Asia/Taipei')::int as yr, i.origin
from {SCHEMA}.items i
cross join lateral (
  select unnest(i.stocks) as stock_id
  union
  select substr(m.entity_id, 4) from {SCHEMA}.mentions m where m.item_id = i.id and m.entity_id like 'tw:%%' and m.method in ('alias', 'tag')
) s
where i.published_at < %s
"""


def candidate_pairs(conn: Any, before: Any) -> list[Pair]:
    """所有 (新聞, 台股) 對：來源自帶的股票標籤＋別名比對；只用 before 之前的（D-109：研究上限 2026-08-31）。"""
    with conn.cursor() as cur:
        cur.execute(PAIRS_SQL, (before,))
        rows = cur.fetchall()
    return [Pair(str(r[0]), str(r[1]), f"{r[2]}|{r[3]}") for r in rows if r[1]]


def save_sample(conn: Any, sample: Sequence[Pair]) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.sentiment_sample (item_id, stock_id, stratum) values (%s, %s, %s) on conflict do nothing",
            [(p.item_id, p.stock_id, p.stratum) for p in sample],
        )
    conn.commit()
    return len(sample)


# ── 提示詞與解析 ──
def build_prompt(
    stock_name: str, stock_id: str, title: str, body: str | None, max_body: int
) -> str:
    text = (body or "")[:max_body]
    return (
        f"下面是一則新聞。請只就「這則新聞對 {stock_name}（{stock_id}）這家公司」的影響判斷：好消息、壞消息、還是中性／無關。\n"
        "判斷依據是新聞怎麼說（營收、訂單、獲利、評等、監管、意外等），不是你對股價的預測。\n"
        '只回一個 JSON：{"label": "好"} 或 {"label": "壞"} 或 {"label": "中性"}。\n\n'
        f"標題：{title}\n內文：{text}"
    )


_JSON = re.compile(r"\{[^{}]*\}")


def parse_label(raw: str) -> int:
    """模型回應 → −1／0／1；找不到合法標籤 → DataError（記下來，不猜）。"""
    txt = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = _JSON.search(txt)
    if m:
        try:
            v = json.loads(m.group(0)).get("label")
            if isinstance(v, str) and v.strip() in LABELS:
                return LABELS[v.strip()]
        except ValueError:
            pass
    for k, v in LABELS.items():  # 沒照 JSON 回、但只說了一個詞
        if txt.strip().strip("「」\"'。") == k:
            return v
    raise DataError("news_sentiment", None, f"模型回應不是三類之一：{raw[:120]!r}")


def ollama_chat(prompt: str, *, model: str, url: str, timeout: float = 120) -> str:
    """本機 Ollama /api/chat（關掉 qwen3 的思考）；回純文字。"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 40},
    }
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    return str(((out.get("message") or {}).get("content")) or "")


def pending_pairs(
    conn: Any, limit: int, names: dict[str, str]
) -> list[tuple[str, str, str, str, str | None]]:
    """還沒貼標的樣本：(item_id, stock_id, 股名, 標題, 內文)。股名由呼叫端給（主庫 stock_meta）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""select s.item_id, s.stock_id, i.title_zh, i.title, i.summary_zh, i.summary, i.content
                from {SCHEMA}.sentiment_sample s join {SCHEMA}.items i on i.id = s.item_id
                left join {SCHEMA}.sentiment_labels l on l.item_id = s.item_id and l.stock_id = s.stock_id
                where l.item_id is null order by s.item_id, s.stock_id limit %s""",
            (limit,),
        )
        rows = cur.fetchall()
    out = []
    for item_id, stock_id, title_zh, title, summary_zh, summary, content in rows:
        body = content or (summary_zh if title_zh else summary)
        out.append(
            (
                str(item_id),
                str(stock_id),
                names.get(str(stock_id), str(stock_id)),
                str(title_zh or title),
                body,
            )
        )
    return out


CORPUS_PENDING_SQL = f"""
select p.item_id, p.stock_id, i.title_zh, i.title, i.summary_zh, i.summary, i.content
from (
  select distinct m.item_id, substr(m.entity_id, 4) as stock_id
  from {SCHEMA}.mentions m join {SCHEMA}.items i on i.id = m.item_id
  where m.entity_id like 'tw:%%' and m.method in ('alias', 'tag') and (i.origin <> 'mops' or m.method = 'tag')
) p
join {SCHEMA}.items i on i.id = p.item_id
left join {SCHEMA}.sentiment_labels l on l.item_id = p.item_id and l.stock_id = p.stock_id
where l.item_id is null
order by i.available_at desc, p.item_id, p.stock_id
limit %s
"""


def pending_corpus(
    conn: Any, limit: int, names: dict[str, str]
) -> list[tuple[str, str, str, str, str | None]]:
    """全語料還沒貼標的（新聞, 台股）對，新的先：配對定義跟新聞因子一樣（alias／tag 提及；公告只算公告公司本身）。
    探針沒過門檻時用（T-413／D-201 §四）。回傳格式同 pending_pairs。"""
    with conn.cursor() as cur:
        cur.execute(CORPUS_PENDING_SQL, (limit,))
        rows = cur.fetchall()
    out = []
    for item_id, stock_id, title_zh, title, summary_zh, summary, content in rows:
        body = content or (summary_zh if title_zh else summary)
        out.append(
            (
                str(item_id),
                str(stock_id),
                names.get(str(stock_id), str(stock_id)),
                str(title_zh or title),
                body,
            )
        )
    return out


def scores_from_labels(conn: Any, version: str) -> int:
    """Ollama 標籤 → sentiment_scores（method='llm'、p 為 one-hot）；同 (item, stock, method) 覆蓋。回寫了幾列。
    新聞因子用 p_pos − p_neg，所以 one-hot 就是 −1／0／+1。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""insert into {SCHEMA}.sentiment_scores (item_id, stock_id, label, p_neg, p_neu, p_pos, method, version)
                select item_id, stock_id, label, (label = -1)::int, (label = 0)::int, (label = 1)::int, 'llm', %s
                from {SCHEMA}.sentiment_labels
                on conflict (item_id, stock_id, method) do update
                  set label = excluded.label, p_neg = excluded.p_neg, p_neu = excluded.p_neu, p_pos = excluded.p_pos, version = excluded.version""",
            (version,),
        )
        n = cur.rowcount
    conn.commit()
    return int(n)


def save_label(conn: Any, item_id: str, stock_id: str, label: int, *, model: str, raw: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""insert into {SCHEMA}.sentiment_labels (item_id, stock_id, label, model, prompt_version, raw)
                values (%s, %s, %s, %s, %s, %s) on conflict (item_id, stock_id) do nothing""",
            (item_id, stock_id, label, model, PROMPT_VERSION, raw[:500]),
        )
    conn.commit()


# ── 線性探針：共用 linear_probe（softmax 迴歸）；這裡只把 −1／0／+1 對到索引 ──
_CLASSES = (-1, 0, 1)


@dataclass(frozen=True)
class Probe:
    w: np.ndarray  # (d, 3)
    b: np.ndarray  # (3,)
    classes: tuple[int, ...] = _CLASSES

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return Softmax(self.w, self.b).proba(x)


def train_probe(
    x: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = 1e-3,
    epochs: int = 300,
    lr: float = 0.5,
    seed: int = 7,
) -> Probe:
    """y ∈ {−1,0,1}；類別不平衡用反頻率加權。決定性。"""
    yi = np.array([_CLASSES.index(int(v)) for v in y]) if len(y) else np.zeros(0, dtype=int)
    m = train_softmax(x, yi, 3, l2=l2, epochs=epochs, lr=lr, seed=seed)
    return Probe(w=m.w, b=m.b)


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, classes: Sequence[int] = (-1, 0, 1)) -> float:
    """三類 F1 的平均；真實與預測都沒出現的類別不算（免得測試用的小樣本被拉低）。"""
    f1s = []
    for c in classes:
        tp = float(((y_true == c) & (y_pred == c)).sum())
        fp = float(((y_true != c) & (y_pred == c)).sum())
        fn = float(((y_true == c) & (y_pred != c)).sum())
        if tp + fp + fn == 0:
            continue
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return float(np.mean(f1s)) if f1s else 0.0


def auc(scores: np.ndarray, positive: np.ndarray) -> float:
    """二分 AUC（Mann–Whitney）；只有一類 → DataError。"""
    pos, neg = scores[positive], scores[~positive]
    if len(pos) == 0 or len(neg) == 0:
        raise DataError("news_sentiment", None, "AUC 需要正負兩類都有")
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


@dataclass(frozen=True)
class ProbeEval:
    macro_f1: float
    pos_neg_auc: float
    n_train: int
    n_valid: int
    passed: bool


def evaluate_probe(
    probe: Probe, x: np.ndarray, y: np.ndarray, *, f1_min: float, auc_min: float, n_train: int
) -> ProbeEval:
    """驗證集：三類 macro-F1；正負二分 AUC（只看 y≠0 的列，分數＝p_pos − p_neg）。門檻事前寫死（D-201 §四）。"""
    p = probe.predict_proba(x)
    pred = np.array([(-1, 0, 1)[i] for i in p.argmax(axis=1)])
    f1 = macro_f1(y, pred)
    mask = y != 0
    a = auc((p[:, 2] - p[:, 0])[mask], (y[mask] == 1)) if mask.sum() >= 2 else 0.0
    return ProbeEval(
        macro_f1=f1,
        pos_neg_auc=a,
        n_train=n_train,
        n_valid=len(y),
        passed=(f1 >= f1_min and a >= auc_min),
    )


def split_train_valid(n: int, valid_share: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """固定 seed 的訓練／驗證切分（索引）。"""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    k = round(n * valid_share)
    return idx[k:], idx[:k]


# ── 雲端可用的情緒小模型（T-424／D-205）：「股名：證據片段」向量與訓練資料 ──
PAIR_EMB_DDL = f"""
create table if not exists {SCHEMA}.pair_embeddings (
  item_id    text not null references {SCHEMA}.items (id),
  stock_id   text not null,
  model      text not null,
  text       text not null,
  vec        halfvec(1024) not null,
  created_at timestamptz not null default now(),
  primary key (item_id, stock_id)
);
"""


def ensure_pair_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(PAIR_EMB_DDL)
    conn.commit()


def pending_pair_texts(conn: Any, limit: int, names: dict[str, str]) -> list[tuple[str, str, str]]:
    """有標籤、還沒有 v_pair 的配對：(item_id, stock_id, 「股名：證據片段」)。證據片段取提及比對記下的（alias 優先）。"""
    from twquant.data.sentiment_mlp import pair_text

    with conn.cursor() as cur:
        cur.execute(
            f"""select l.item_id, l.stock_id, coalesce(i.title_zh, i.title),
                  (select m.evidence from {SCHEMA}.mentions m
                    where m.item_id = l.item_id and m.entity_id = 'tw:' || l.stock_id and m.evidence is not null
                    order by m.method limit 1)
                from {SCHEMA}.sentiment_labels l join {SCHEMA}.items i on i.id = l.item_id
                left join {SCHEMA}.pair_embeddings p on p.item_id = l.item_id and p.stock_id = l.stock_id
                where p.item_id is null order by l.item_id, l.stock_id limit %s""",
            (limit,),
        )
        rows = cur.fetchall()
    return [
        (str(r[0]), str(r[1]), pair_text(names.get(str(r[1]), str(r[1])), r[3], str(r[2])))
        for r in rows
    ]


def save_pair_embeddings(
    conn: Any, model: str, rows: Sequence[tuple[str, str, str, Sequence[float]]]
) -> int:
    from twquant.data.news_hist import vec_literal

    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"""insert into {SCHEMA}.pair_embeddings (item_id, stock_id, model, text, vec)
                values (%s, %s, %s, %s, %s::halfvec) on conflict (item_id, stock_id) do nothing""",
            [(i, s, model, t, vec_literal(v)) for i, s, t, v in rows],
        )
    conn.commit()
    return len(rows)


def _halfvec(raw: Any) -> np.ndarray:
    """pgvector halfvec 二進位：2 bytes 維度、2 bytes 保留、big-endian fp16。"""
    b = bytes(raw)
    return np.frombuffer(b[4:], dtype=">f2").astype(np.float16)


def load_mlp_data(conn: Any) -> dict[str, Any]:
    """訓練／驗證資料：有標籤、有新聞向量、有 v_pair 的配對。向量用半精度（float16）存，省一半記憶體。"""
    with conn.cursor(binary=True) as cur:
        cur.execute(
            f"""select l.item_id, l.stock_id, l.label, (s.item_id is not null) as in_sample, e.vec, p.vec
                from {SCHEMA}.sentiment_labels l
                join {SCHEMA}.pair_embeddings p on p.item_id = l.item_id and p.stock_id = l.stock_id
                join {SCHEMA}.embeddings e on e.item_id = l.item_id
                left join {SCHEMA}.sentiment_sample s on s.item_id = l.item_id and s.stock_id = l.stock_id
                order by l.item_id, l.stock_id"""
        )
        items: list[str] = []
        stocks: list[str] = []
        labels: list[int] = []
        sample: list[bool] = []
        vn: list[np.ndarray] = []
        vp: list[np.ndarray] = []
        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            for r in chunk:
                items.append(str(r[0]))
                stocks.append(str(r[1]))
                labels.append(int(r[2]))
                sample.append(bool(r[3]))
                vn.append(_halfvec(r[4]))
                vp.append(_halfvec(r[5]))
    if not items:
        raise DataError(
            "news_sentiment", None, "沒有可訓練的配對（先跑 news sentiment-pair-embed）"
        )
    return {
        "item_id": items,
        "stock_id": stocks,
        "label": np.array(labels, dtype=np.int8),
        "in_sample": np.array(sample, dtype=bool),
        "v_news": np.stack(vn),
        "v_pair": np.stack(vp),
    }
