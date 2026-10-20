"""新聞類型標籤（T-421；使用者 2026-10-20 選的「新聞類型標籤」）：把新聞分成十來類（財報營收、訂單合作、擴廠投資…）。

做法：本機 Ollama 對一批線上新聞貼類型（news_hist.type_labels）→ 在 bge-m3 向量上訓 softmax 探針（linear_probe）
→ 把權重輸出成 news-service/config/news_types.json，雲端 ingest 對每則新向量算一次內積就有類型（不花 AI 額度）。
類別清單在 configs/news.yaml 的 types（雲端鏡像同一份）。這裡是資料層。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from twquant.data.linear_probe import Softmax
from twquant.errors import DataError

SCHEMA = "news_hist"
PROMPT_VERSION = "2026-10-21.1"

DDL = f"""
create table if not exists {SCHEMA}.type_labels (
  item_id        text primary key references {SCHEMA}.items (id),
  type           text not null,
  model          text not null,
  prompt_version text not null,
  raw            text,
  created_at     timestamptz not null default now()
);
"""


@dataclass(frozen=True)
class TypeClass:
    id: str
    label: str
    hint: str


def load_classes(doc: dict[str, Any]) -> list[TypeClass]:
    """configs/news.yaml types.classes → 類別；id 要唯一、小寫英文；最後一類要是 other。"""
    t = doc.get("types")
    if not isinstance(t, dict) or not isinstance(t.get("classes"), list) or not t["classes"]:
        raise DataError("news_types", None, "configs/news.yaml 缺 types.classes")
    out = [TypeClass(str(c["id"]), str(c["label"]), str(c.get("hint", ""))) for c in t["classes"]]
    ids = [c.id for c in out]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z_]+", i) for i in ids):
        raise DataError("news_types", None, f"types.classes 的 id 要唯一、小寫英文：{ids}")
    if ids[-1] != "other":
        raise DataError("news_types", None, "types.classes 最後一類要是 other（分不出來的放這裡）")
    return out


def ensure_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def build_prompt(classes: Sequence[TypeClass], title: str, body: str | None, max_body: int) -> str:
    menu = "\n".join(
        f"- {c.id}：{c.label}（{c.hint}）" if c.hint else f"- {c.id}：{c.label}" for c in classes
    )
    return (
        "把下面這則財經新聞分到一個類型。類型清單：\n"
        f"{menu}\n"
        '只回一個 JSON：{"type": "<類型 id>"}；分不出來就 other。\n\n'
        f"標題：{title}\n內文：{(body or '')[:max_body]}"
    )


_JSON = re.compile(r"\{[^{}]*\}")


def parse_type(raw: str, ids: Sequence[str]) -> str:
    """模型回應 → 類型 id；不在清單 → DataError（不猜）。"""
    txt = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    m = _JSON.search(txt)
    if m:
        try:
            v = json.loads(m.group(0)).get("type")
            if isinstance(v, str) and v.strip() in ids:
                return v.strip()
        except ValueError:
            pass
    bare = txt.strip().strip("「」\"'。")
    if bare in ids:
        return bare
    raise DataError("news_types", None, f"模型回應不是類型清單之一：{raw[:120]!r}")


def pending_items(
    conn: Any, limit: int, origins: Sequence[str]
) -> list[tuple[str, str, str | None]]:
    """還沒貼類型、有向量的新聞：(id, 標題, 內文)。只拿指定來源（線上新聞為主，跟雲端同一種文字）。新的先。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""select i.id, coalesce(i.title_zh, i.title), coalesce(i.content, case when i.title_zh is not null then i.summary_zh else i.summary end)
                from {SCHEMA}.items i join {SCHEMA}.embeddings e on e.item_id = i.id
                left join {SCHEMA}.type_labels t on t.item_id = i.id
                where t.item_id is null and i.origin = any(%s) order by i.published_at desc limit %s""",
            (list(origins), limit),
        )
        return [(str(r[0]), str(r[1]), r[2]) for r in cur.fetchall()]


def save_label(conn: Any, item_id: str, type_id: str, *, model: str, raw: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"insert into {SCHEMA}.type_labels (item_id, type, model, prompt_version, raw) values (%s, %s, %s, %s, %s) on conflict (item_id) do nothing",
            (item_id, type_id, model, PROMPT_VERSION, raw[:300]),
        )
    conn.commit()


def labeled_vectors(conn: Any) -> tuple[list[str], np.ndarray, list[str]]:
    """有標籤且有向量的：(item_id, 向量矩陣, 類型 id)。"""
    with conn.cursor() as cur:
        cur.execute(
            f"select t.item_id, e.vec::text, t.type from {SCHEMA}.type_labels t join {SCHEMA}.embeddings e on e.item_id = t.item_id order by t.item_id"
        )
        rows = cur.fetchall()
    if not rows:
        raise DataError("news_types", None, "還沒有任何類型標籤")
    return (
        [str(r[0]) for r in rows],
        np.array([json.loads(r[1]) for r in rows], dtype=np.float64),
        [str(r[2]) for r in rows],
    )


def export_weights(
    probe: Softmax,
    classes: Sequence[TypeClass],
    *,
    version: str,
    min_confidence: float,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """給 news-service 的 JSON：權重四捨五入到 4 位（1024×12 約 90 KB），同一條公式在 Edge Function 算（src/types.ts）。"""
    return {
        "version": version,
        "model": "BAAI/bge-m3",
        "classes": [c.id for c in classes],
        "labels": {c.id: c.label for c in classes},
        "min_confidence": min_confidence,
        "metrics": metrics,
        "w": [[round(float(v), 4) for v in row] for row in probe.w.T],  # k × d，每類一列
        "b": [round(float(v), 4) for v in probe.b],
    }


def classify(weights: dict[str, Any], vec: Sequence[float]) -> tuple[str, float]:
    """跟 Edge Function 同一條公式：softmax(w·v+b)，最高的那類與信心；信心不到 min_confidence → other。純函式，給測試對照。"""
    w = np.array(weights["w"], dtype=np.float64)
    b = np.array(weights["b"], dtype=np.float64)
    z = w @ np.asarray(vec, dtype=np.float64) + b
    z -= z.max()
    p = np.exp(z)
    p /= p.sum()
    i = int(p.argmax())
    conf = float(p[i])
    cls = weights["classes"][i] if conf >= float(weights["min_confidence"]) else "other"
    return cls, conf
