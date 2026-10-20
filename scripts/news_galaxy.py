"""新聞全景（星雲圖）快照（T-418；使用者 2026-10-20：「星雲圖當成可以切換的一個視角就好」）。

    uv run --all-groups python scripts/news_galaxy.py [--dry-run] [--env-file D:/project/twquant/.env]

近 galaxy.days 天、最多 galaxy.max_items 則有向量的新聞 → k-means 主題群（cosine）→ 3 維 t-SNE（同群稍微拉近，排出一團一團）
→ qwen3 看標題替每群取 2～6 字的名字（額度用完或失敗 → 用群裡最常被提到的實體當名字）→ 被提到 ≥ hub_min 次的實體當亮點。
整包寫進新聞庫 news.galaxy_snapshots（只留最近 keep 筆），網站 /api/news/galaxy 讀最新一筆。

連線：TWQUANT_NEWS_DSN（新聞專案的 pooler，GitHub Actions 祕密）；取名：CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_AI_TOKEN（沒設就用實體名）。
取名的 Neurons 記在 news.llm_usage（task news_galaxy_names），跟新聞這邊每天的額度一起算。只讀新聞、只寫快照與用量。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "configs" / "news.yaml"
DEFAULT_ENV = pathlib.Path("D:/project/twquant/.env")


class GalaxyError(RuntimeError):
    """設定、資料或連線有問題（不吞掉，讓排程看得到）。"""


@dataclass(frozen=True)
class GalaxyConfig:
    days: int
    max_items: int
    k: int
    island: float
    perplexity: float
    iters: int
    hub_min: int
    keep: int
    seed: int


def load_config(doc: dict[str, Any]) -> GalaxyConfig:
    """configs/news.yaml 的 galaxy 段 → GalaxyConfig；缺欄位或型別不對 → GalaxyError。"""
    g = doc.get("galaxy")
    if not isinstance(g, dict):
        raise GalaxyError("configs/news.yaml 缺 galaxy 段")
    try:
        cfg = GalaxyConfig(
            days=int(g["days"]),
            max_items=int(g["max_items"]),
            k=int(g["k"]),
            island=float(g["island"]),
            perplexity=float(g["perplexity"]),
            iters=int(g["iters"]),
            hub_min=int(g["hub_min"]),
            keep=int(g["keep"]),
            seed=int(g["seed"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise GalaxyError(f"configs/news.yaml galaxy 段格式不對：{e}") from e
    if (
        min(cfg.days, cfg.max_items, cfg.k, cfg.iters, cfg.hub_min, cfg.keep) < 1
        or cfg.perplexity <= 1
    ):
        raise GalaxyError(f"configs/news.yaml galaxy 段數值不合理：{cfg}")
    return cfg


# ── 演算法（純函式）────────────────────────────────────────────────────


def normalize(x: np.ndarray) -> np.ndarray:
    """每列除以長度（cosine 用）；零向量 → GalaxyError。"""
    n = np.linalg.norm(x, axis=1, keepdims=True)
    if (n == 0).any():
        raise GalaxyError("有零向量，無法算 cosine")
    out: np.ndarray = x / n
    return out


def kmeans(x: np.ndarray, k: int, seed: int, restarts: int = 6, iters: int = 80) -> np.ndarray:
    """cosine k-means（k-means++ 起點，多跑幾次挑最緊的）；回每列的群號。決定性（固定 seed）。"""
    n = len(x)
    k = min(k, n)
    best_lab, best = np.zeros(n, dtype=int), -np.inf
    for s in range(restarts):
        r = np.random.default_rng(seed + s)
        cen = [x[r.integers(n)]]
        for _ in range(k - 1):
            d = np.maximum(0, 1 - np.max(np.array(cen) @ x.T, axis=0)) ** 2
            tot = d.sum()
            cen.append(x[r.choice(n, p=d / tot)] if tot > 0 else x[r.integers(n)])
        c = np.array(cen)
        lab = np.argmax(x @ c.T, axis=1)
        for _ in range(iters):
            lab = np.argmax(x @ c.T, axis=1)
            new = np.array([x[lab == j].mean(0) if (lab == j).any() else c[j] for j in range(k)])
            new /= np.linalg.norm(new, axis=1, keepdims=True)
            if np.allclose(new, c):
                break
            c = new
        score = float((x * c[lab]).sum())
        if score > best:
            best, best_lab = score, lab
    return best_lab


def tsne(x: np.ndarray, labels: np.ndarray, cfg: GalaxyConfig, dims: int = 3) -> np.ndarray:
    """精確 t-SNE（n ≤ 幾千夠快）；同群用 island 拉近一點只影響排版。回 (n, dims)，以 97 百分位縮到半徑約 1、極端點收回 1.15。"""
    n = len(x)
    if n < 4:
        raise GalaxyError(f"新聞太少（{n} 則），排不出星雲圖")
    k = int(labels.max()) + 1
    z = np.hstack([x, np.eye(k)[labels] * cfg.island])
    z /= np.linalg.norm(z, axis=1, keepdims=True)
    dm = np.maximum(0, 2 - 2 * (z @ z.T))
    perp = min(cfg.perplexity, (n - 1) / 3)
    target = math.log(perp)
    p = np.zeros((n, n))
    for i in range(n):
        d = np.delete(dm[i], i)
        d = d - d.min()
        lo, hi, beta = 0.0, math.inf, 1.0
        row = np.exp(-d * beta)
        for _ in range(50):
            row = np.exp(-d * beta)
            sp = row.sum()
            h = math.log(sp) + beta * float((d * row).sum()) / sp
            if abs(h - target) < 1e-4:
                break
            if h > target:
                lo = beta
                beta = beta * 2 if hi == math.inf else (beta + hi) / 2
            else:
                hi = beta
                beta = (beta + lo) / 2
        p[i, np.arange(n) != i] = row / row.sum()
    p = np.maximum((p + p.T) / (2 * n), 1e-12)
    rng = np.random.default_rng(cfg.seed)
    y = rng.normal(0, 1e-4, (n, dims))
    gains, upd = np.ones_like(y), np.zeros_like(y)
    for it in range(cfg.iters):
        sq = (y**2).sum(1)
        num = 1 / (1 + sq[:, None] + sq[None, :] - 2 * y @ y.T)
        np.fill_diagonal(num, 0)
        q = np.maximum(num / num.sum(), 1e-12)
        w = (p * (12 if it < 200 else 1) - q) * num
        g = 4 * (w.sum(1)[:, None] * y - w @ y)
        gains = (gains + 0.2) * ((g > 0) != (upd > 0)) + gains * 0.8 * ((g > 0) == (upd > 0))
        gains = np.maximum(gains, 0.01)
        upd = (0.5 if it < 250 else 0.8) * upd - 200 * gains * g
        y += upd
        y -= y.mean(0)
    s = float(np.percentile(np.linalg.norm(y, axis=1), 97)) or 1.0
    y = y / s
    nrm = np.linalg.norm(y, axis=1, keepdims=True)
    out: np.ndarray = np.where(nrm > 1.15, y / nrm * 1.15, y)
    return out


_THINK = re.compile(r"<think>.*?</think>", re.S)


def clean_name(raw: str, max_chars: int = 10) -> str | None:
    """模型回的群名 → 乾淨的名字：去掉思考段、引號標點、只取第一段（空白或換行前）、最多 max_chars 字；空的 → None。"""
    txt = _THINK.sub("", raw).replace("</think>", "").strip()
    txt = txt.strip("「」『』\"'“”。．.：:　 ")
    first = re.split(r"[\s\n]+", txt, maxsplit=1)[0] if txt else ""
    first = first.strip("「」『』\"'“”。．.：:")
    return first[:max_chars] or None


@dataclass(frozen=True)
class Item:
    id: str
    source: str
    url: str
    t: str
    title: str
    title_zh: str | None
    ev: str | None


_COMPANY_TYPES = ("tw", "us", "company")


def _distinct_name(
    name: str | None,
    ranked: list[tuple[str, int]],
    entities: dict[str, dict[str, Any]],
    taken: list[str],
    k: int,
) -> str:
    """模型給不出不撞的名字時：原名（或群裡最常提到的實體）接上群裡最常提到、名字裡沒有的公司（再不然其他實體），
    例「AI・台積電」；都撞才加群號。不會跟已用的重複。"""
    labels = [str(entities[e]["label"]) for e, _ in ranked]
    companies = [
        str(entities[e]["label"]) for e, _ in ranked if entities[e]["type"] in _COMPANY_TYPES
    ]
    base = name or (labels[0] if labels else f"主題 {k + 1}")
    if base not in taken:
        return base
    for extra in companies + labels:
        if extra not in base and f"{base}・{extra}" not in taken:
            return f"{base}・{extra}"
    return f"{base}（{k + 1}）"


def build_snapshot(
    items: Sequence[Item],
    vecs: np.ndarray,
    mentions: dict[str, list[str]],
    entities: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    cfg: GalaxyConfig,
    namer: Callable[[list[str], list[str]], str | None],
    built: str,
) -> dict[str, Any]:
    """一包星雲圖：items（座標、群、標題、來源、提到的亮點）、clusters（名字、則數、中心）、hubs（實體、則數、位置）。"""
    if len(items) != len(vecs):
        raise GalaxyError(f"新聞 {len(items)} 則、向量 {len(vecs)} 個，對不起來")
    x = normalize(vecs.astype(np.float64))
    lab = kmeans(x, cfg.k, cfg.seed)
    y = tsne(x, lab, cfg)
    shown = [it.title_zh or it.title for it in items]
    clusters: list[dict[str, Any]] = []
    for k in sorted(set(lab.tolist())):
        mem = np.where(lab == k)[0]
        cen = x[mem].mean(0)
        order = mem[np.argsort(-(x[mem] @ cen))]
        ranked = Counter(
            e for i in mem for e in mentions.get(items[i].id, []) if e in entities
        ).most_common(8)
        top = ranked[:3]
        titles = [shown[i] for i in order[:14]]
        taken = [str(c["name"]) for c in clusters]
        name = namer(titles, [])
        if name in taken:
            # 撞名（2026-10-20 實見：三群都叫「AI」）→ 帶著已用過的名字再問一次要更具體
            name = namer(titles, taken)
        if not name or name in taken:
            name = _distinct_name(name, ranked, entities, taken, k)
        c = y[mem].mean(0)
        c = y[mem].mean(0)
        clusters.append(
            {
                "k": int(k),
                "name": name,
                "n": len(mem),
                "x": float(c[0]),
                "y": float(c[1]),
                "z": float(c[2]),
                "top": [entities[e]["label"] for e, _ in top],
            }
        )
    index = {it.id: j for j, it in enumerate(items)}
    cnt = Counter(e for it in items for e in mentions.get(it.id, []) if e in entities)
    hubs: list[dict[str, Any]] = []
    for e, n_e in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])):
        if n_e < cfg.hub_min:
            break
        hub_mem = [index[it.id] for it in items if e in mentions.get(it.id, [])]
        m = y[hub_mem].mean(0) * 1.06
        ent = entities[e]
        hubs.append(
            {
                "id": e,
                "label": ent["label"],
                "type": ent["type"],
                "sub": ent.get("sub"),
                "n": n_e,
                "x": float(m[0]),
                "y": float(m[1]),
                "z": float(m[2]),
            }
        )
    hub_ids = {h["id"] for h in hubs}
    out_items = []
    for j, it in enumerate(items):
        src = sources.get(it.source, {})
        ms = mentions.get(it.id, [])
        out_items.append(
            {
                "id": it.id,
                "x": round(float(y[j, 0]), 4),
                "y": round(float(y[j, 1]), 4),
                "z": round(float(y[j, 2]), 4),
                "k": int(lab[j]),
                "title": shown[j],
                "src": src.get("name", it.source),
                "kind": src.get("kind", "tw"),
                "tr": it.title_zh is not None,
                "t": it.t,
                "url": it.url,
                "ev": it.ev,
                "m": [e for e in ms if e in hub_ids],
                "ml": [entities[e]["label"] for e in ms if e in entities][:6],
            }
        )
    return {
        "built": built,
        "days": cfg.days,
        "items": out_items,
        "clusters": clusters,
        "hubs": hubs,
        "links": sum(len(i["m"]) for i in out_items),
    }


# ── IO：設定、新聞庫、Workers AI ───────────────────────────────────────


def load_env(path: pathlib.Path) -> dict[str, str]:
    """.env（若有）＋環境變數（GitHub Actions 走環境變數）；值不印出。"""
    env: dict[str, str] = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("TWQUANT_NEWS_DSN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_AI_TOKEN"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if not env.get("TWQUANT_NEWS_DSN"):
        raise GalaxyError("沒有 TWQUANT_NEWS_DSN（新聞庫連線），排程要設 GitHub Actions 祕密")
    return env


def make_namer(
    env: dict[str, str],
    model: str,
    neurons_per_1m: tuple[float, float],
    budget_left: float,
    usage: list[dict[str, Any]],
) -> Callable[[list[str], list[str]], str | None]:
    """qwen3 取群名（avoid＝別群已用的名字，要它換更具體的）；沒金鑰、額度不夠、呼叫失敗 → None（呼叫端改用實體名）。用量記在 usage（之後寫進 news.llm_usage）。"""
    acct, token = env.get("CLOUDFLARE_ACCOUNT_ID"), env.get("CLOUDFLARE_AI_TOKEN")
    left = [budget_left]

    def namer(titles: list[str], avoid: list[str]) -> str | None:
        if not acct or not token or left[0] <= 0:
            return None
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": "你幫新聞分群取名。看一群新聞標題，用 2 到 6 個字的台灣繁體中文寫出它們共同的主題（例：記憶體漲價、聯準會利率、AI 伺服器、油價）。只輸出名稱，不加標點、不加解釋。 /no_think",
                },
                {
                    "role": "user",
                    "content": "\n".join(titles)
                    + (
                        f"\n\n這些名字別的群已經用了，不要重複、要更具體：{'、'.join(avoid)}"
                        if avoid
                        else ""
                    ),
                },
            ],
            "max_tokens": 40,
        }
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())["result"]
        except (OSError, ValueError, KeyError) as e:
            print(f"  取名失敗（改用實體名）：{type(e).__name__}: {e}", file=sys.stderr)
            return None
        u = res.get("usage") or {}
        n = (
            u.get("prompt_tokens", 0) * neurons_per_1m[0]
            + u.get("completion_tokens", 0) * neurons_per_1m[1]
        ) / 1_000_000
        left[0] -= n
        usage.append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "task": "news_galaxy_names",
                "model_id": model,
                "input_tokens": int(u.get("prompt_tokens", 0)),
                "output_tokens": int(u.get("completion_tokens", 0)),
                "neurons": n,
            }
        )
        txt = (
            res.get("response")
            or ((res.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        return clean_name(str(txt))

    return namer


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新聞全景（星雲圖）快照 → news.galaxy_snapshots")
    ap.add_argument("--dry-run", action="store_true", help="只算、印統計，不寫新聞庫")
    ap.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV),
        help="本機 .env（值不會印出來）；GitHub Actions 用環境變數",
    )
    args = ap.parse_args(argv)
    import psycopg  # 延遲載入：純函式的測試不需要資料庫套件

    t0 = time.monotonic()
    doc = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    cfg = load_config(doc)
    env = load_env(pathlib.Path(args.env_file))
    rule = doc["mention"]["rule_version"]
    tr = doc["translate"]
    sources = {s["id"]: s for s in doc["sources"]}
    with psycopg.connect(
        env["TWQUANT_NEWS_DSN"], connect_timeout=30, prepare_threshold=None
    ) as conn:
        rows = conn.execute(
            """
            select i.id, i.source, i.url, i.first_seen_at, coalesce(v.title_orig, i.title_orig), tr.title_zh, c.cluster_id, e.vec::text
            from news.raw_items i
            join news.embeddings e on e.item_id = i.id
            left join lateral (select title_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1) v on true
            left join news.translations tr on tr.item_id = i.id and tr.content_sha = coalesce(v.content_sha, i.content_sha) and tr.status = 'ok'
            left join news.clusters c on c.item_id = i.id
            where i.first_seen_at > now() - make_interval(days => %s)
            order by i.first_seen_at desc, i.id
            limit %s""",
            (cfg.days, cfg.max_items),
        ).fetchall()
        if len(rows) < 20:
            raise GalaxyError(f"近 {cfg.days} 天只有 {len(rows)} 則有向量的新聞，不夠排星雲圖")
        rows.reverse()  # 舊到新
        items = [Item(r[0], r[1], r[2], r[3].isoformat(), r[4], r[5], r[6]) for r in rows]
        vecs = np.array([json.loads(r[7]) for r in rows], dtype=np.float64)
        ids = [it.id for it in items]
        mentions: dict[str, list[str]] = defaultdict(list)
        for item_id, ent in conn.execute(
            "select distinct item_id, entity_id from news.mentions where rule_version = %s and item_id = any(%s) order by 1, 2",
            (rule, ids),
        ).fetchall():
            mentions[item_id].append(ent)
        ent_ids = sorted({e for es in mentions.values() for e in es})
        entities = {
            r[0]: {"label": r[1], "type": r[2], "sub": r[3]}
            for r in conn.execute(
                "select id, label, type, sub from news.entities where id = any(%s)", (ent_ids,)
            ).fetchall()
        }
        used = conn.execute(
            "select coalesce(sum(neurons), 0)::float8 from news.llm_usage where ts >= date_trunc('day', now() at time zone 'UTC') at time zone 'UTC'"
        ).fetchone()
        budget_left = float(tr["daily_neurons"]) - float(used[0] if used else 0)
        usage: list[dict[str, Any]] = []
        namer = make_namer(
            env,
            str(tr["model"]),
            (float(tr["input_neurons_per_1m"]), float(tr["output_neurons_per_1m"])),
            budget_left,
            usage,
        )
        built = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        snap = build_snapshot(items, vecs, mentions, entities, sources, cfg, namer, built)
        print(
            f"新聞 {len(items)} 則、主題 {len(snap['clusters'])}、亮點 {len(snap['hubs'])}、連線 {snap['links']}；"
            f"取名 {len(usage)} 次 {sum(u['neurons'] for u in usage):.1f} Neurons；花 {time.monotonic() - t0:.0f} 秒"
        )
        print(
            "  主題："
            + "、".join(
                f"{c['name']}({c['n']})" for c in sorted(snap["clusters"], key=lambda c: -c["n"])
            )
        )
        if args.dry_run:
            print("--dry-run：不寫")
            return 0
        with conn.transaction():
            conn.execute(
                "insert into news.galaxy_snapshots (built_at, items, data) values (%s, %s, %s::jsonb)",
                (built, len(items), json.dumps(snap, ensure_ascii=False)),
            )
            conn.execute(
                "delete from news.galaxy_snapshots where built_at not in (select built_at from news.galaxy_snapshots order by built_at desc limit %s)",
                (cfg.keep,),
            )
            for u in usage:
                conn.execute(
                    "insert into news.llm_usage (ts, task, model_id, input_tokens, output_tokens, neurons) values (%s, %s, %s, %s, %s, %s)",
                    (
                        u["ts"],
                        u["task"],
                        u["model_id"],
                        u["input_tokens"],
                        u["output_tokens"],
                        u["neurons"],
                    ),
                )
    print(f"寫進 news.galaxy_snapshots（{built}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
