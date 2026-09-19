"""監控 API（SPEC-007）。

FastAPI/JSON 服務，供 React 前端即時讀 clean 資料湖（取代靜態 data.json 快照，
並取代已退役的 Streamlit 儀表板）。查詢全部 GET；唯一的操作端點是資料後台的
「一鍵補抓」（POST /api/ingest-jobs，D-018：白名單指令 + 單一併發，其餘操作仍走
CLI + DECISIONS）。以 `twquant serve` 啟動；docker-compose 的 `api` 服務常駐。

不快取：每個請求都即時查資料源（個股端點走 PG serving、儀表板段落現算資料湖），
資料一更新下一個請求就反映，不必等換日失效。代價是重運算端點（全因子 IC + 回測 +
容量）每請求重算。
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from twquant.config import ServingConfig, SystemConfig, load_config
from twquant.data.warehouse import TableSource, Warehouse
from twquant.errors import DataError, TwquantError
from twquant.monitoring import jobs, screener, stock_api
from twquant.monitoring import snapshot as snap
from twquant.reporting import strategy_targets as strategy_targets_mod
from twquant.reporting.insight_evidence import select_packs
from twquant.reporting.insight_run import insights_for_run
from twquant.reporting.platform_artifacts import (
    ArtifactCatalog,
    backtest_detail,
    index_backtests,
    index_models,
)
from twquant.reporting.recommendation_snapshot import (
    select_candidates,
    select_snapshots,
    stock_events,
)

_CONFIG = snap.CONFIGS_DIR / "system.yaml"


def _data_root() -> Path:
    """Resolve the configured data root independently of the server working directory."""
    cfg = load_config(_CONFIG, SystemConfig)
    data_path = Path(cfg.data_dir)
    if not data_path.is_absolute():
        data_path = snap.CONFIGS_DIR.parent / data_path
    return data_path


def get_warehouse() -> Warehouse:
    """由 configs/system.yaml 建 Warehouse；data_dir 相對路徑以 repo 根為基準（CWD 無關）。"""
    return Warehouse(_data_root())


WarehouseDep = Annotated[Warehouse, Depends(get_warehouse)]


@dataclass(frozen=True)
class PlatformArtifactRoots:
    """Configured, server-owned roots for platform artifact reading."""

    models_root: Path
    backtest_roots: Mapping[str, Path]


_BACKTEST_ROOTS = (
    "ladder",
    "ladder10m",
    "tracks",
    "tracks_c",
    "tracks_m",
    "tracks_g",
    "tracks_mg",
)
_OPAQUE_ID = re.compile(r"^(model|backtest)_[0-9a-f]{20}$")
# Stock identity stays a string with leading zeroes; no separator can reach the filesystem.
_PLATFORM_SID = re.compile(r"^[A-Za-z0-9]{1,16}$")


def get_platform_artifact_roots() -> PlatformArtifactRoots:
    """Return fixed artifact roots; clients never select source files or directories."""
    data_root = _data_root()
    return PlatformArtifactRoots(
        models_root=data_root / "models",
        backtest_roots={name: data_root / name for name in _BACKTEST_ROOTS},
    )


PlatformArtifactRootsDep = Annotated[PlatformArtifactRoots, Depends(get_platform_artifact_roots)]
RecommendationDataRootDep = Annotated[Path, Depends(_data_root)]


def _artifact_catalog_response(catalog: ArtifactCatalog, source: str) -> dict[str, object]:
    """Serialize a list response from a safe artifact catalog projection."""
    return {"items": catalog.items, "source": source, "issues": catalog.issues}


def _artifact_detail(catalog: ArtifactCatalog, artifact_id: str, kind: str) -> dict[str, object]:
    """Return a catalog item by opaque ID, never interpreting the ID as a file path."""
    if _OPAQUE_ID.fullmatch(artifact_id) is None or not artifact_id.startswith(f"{kind}_"):
        raise HTTPException(status_code=422, detail="artifact_id 格式錯誤")
    for item in catalog.items:
        if item.get("artifact_id") == artifact_id:
            return {"item": item, "issues": catalog.issues}
    raise HTTPException(status_code=404, detail="找不到指定的 artifact")


def _validate_artifact_id(artifact_id: str, kind: str) -> None:
    """Reject malformed opaque IDs before an artifact catalog is read."""
    if _OPAQUE_ID.fullmatch(artifact_id) is None or not artifact_id.startswith(f"{kind}_"):
        raise HTTPException(status_code=422, detail="artifact_id 格式錯誤")


def _artifact_storage_error() -> HTTPException:
    """Return a path-safe server error for unavailable artifact storage."""
    return HTTPException(status_code=503, detail="artifact 儲存區暫時無法讀取")


# openapi_url 也放 /api/ 下，讓 /api/docs 經 nginx/vite 反代時仍能載到 schema。
app = FastAPI(
    title="twquant 監控 API", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
)
# 查詢 GET + 補抓 POST（本機工具）：放行跨來源（dev vite proxy / 直開單檔皆可）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health(wh: WarehouseDep) -> dict[str, object]:
    """存活探針：回最新行情日。"""
    return {"status": "ok", "as_of": wh.latest_date("prices_daily")}


@app.get("/api/platform/models")
def get_platform_models(roots: PlatformArtifactRootsDep) -> dict[str, object]:
    """List model metadata projections without loading model weights or starting training."""
    try:
        return _artifact_catalog_response(index_models(roots.models_root), "artifact_metadata")
    except OSError as exc:
        raise _artifact_storage_error() from exc


@app.get("/api/platform/models/{artifact_id}")
def get_platform_model(artifact_id: str, roots: PlatformArtifactRootsDep) -> dict[str, object]:
    """Return one model projection resolved only from a server-side opaque-ID catalog."""
    try:
        return _artifact_detail(index_models(roots.models_root), artifact_id, "model")
    except OSError as exc:
        raise _artifact_storage_error() from exc


@app.get("/api/platform/backtests")
def get_platform_backtests(roots: PlatformArtifactRootsDep) -> dict[str, object]:
    """List persisted backtest summaries without recalculating a strategy or metric."""
    try:
        return _artifact_catalog_response(index_backtests(roots.backtest_roots), "result_bundle")
    except OSError as exc:
        raise _artifact_storage_error() from exc


@app.get("/api/platform/backtests/{artifact_id}")
def get_platform_backtest(artifact_id: str, roots: PlatformArtifactRootsDep) -> dict[str, object]:
    """Return one persisted backtest projection resolved only from an opaque ID."""
    try:
        _validate_artifact_id(artifact_id, "backtest")
        catalog = index_backtests(roots.backtest_roots)
        if not any(item.get("artifact_id") == artifact_id for item in catalog.items):
            raise HTTPException(status_code=404, detail="找不到指定的 artifact")
        detail = backtest_detail(roots.backtest_roots, artifact_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="找不到指定的 artifact")
        return {"item": detail, "issues": catalog.issues}
    except OSError as exc:
        raise _artifact_storage_error() from exc


@app.get("/api/platform/recommendations")
def get_platform_recommendations(
    data_dir: RecommendationDataRootDep,
    date: Annotated[dt.date | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
    run: Annotated[str | None, Query()] = None,
    bucket: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, object]:
    """Read persisted ML recommendation runs; this endpoint never executes inference."""
    try:
        catalog = select_snapshots(data_dir, date=date, model_id=model, run_id=run)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="推薦快照儲存區暫時無法讀取") from exc
    runs: list[dict[str, object]] = []
    start = (page - 1) * page_size
    for item in catalog.runs:
        # 洞悉在讀取時 join 進來（PLAN §3.3）；已發布的快照檔案不因此改寫。
        insights = insights_for_run(data_dir, item.run_id)
        # Bucket/search filters run over the whole saved run before any page slice,
        # so a later page can never surface a candidate an earlier filter removed.
        kept = select_candidates(item, bucket=bucket, query=q)
        projection = item.model_dump(mode="json")
        projection["candidate_total"] = len(kept)
        # 篩選**之前**的全貌（T-234）。只有篩選後的數量時，前端分不出三件不同的事：
        # 這個 run 合法地沒有候選／有候選但不在這個分組／這是沒有晉級分組的舊快照。
        # D-111 把預設分類改成「前 20 名」之後，一個真的空的 run 曾被誤報成舊快照。
        projection["candidate_total_all"] = len(item.candidates)
        projection["bucket_counts"] = dict(
            sorted(Counter(candidate.bucket for candidate in item.candidates).items())
        )
        projection["candidates"] = [
            {**candidate.model_dump(mode="json"), "insight": insights.get(candidate.stock_id)}
            for candidate in kept[start : start + page_size]
        ]
        runs.append(projection)
    return {
        "runs": runs,
        "issues": catalog.issues,
        "query": {
            "date": date.isoformat() if date else None,
            "model": model,
            "run": run,
            "bucket": bucket,
            "q": q,
            "page": page,
            "page_size": page_size,
        },
    }


@app.get("/api/platform/strategy-targets")
def get_platform_strategy_targets(
    data_dir: RecommendationDataRootDep,
    date: Annotated[dt.date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 1,
) -> dict[str, object]:
    """讀取已保存的策略持股快照（D-112）。本端點不做任何計算，也不觸發策略執行。

    每支策略的 `vault` 區塊（金庫期實測成績）跟著名單一起回傳——
    D-107 判定無策略可部署，名單不得在沒有成績的情況下單獨呈現。
    """
    try:
        snapshots, issues = strategy_targets_mod.select_snapshots(data_dir, date=date, limit=limit)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="策略持股快照儲存區暫時無法讀取") from exc
    return {
        "snapshots": [snap.model_dump(mode="json") for snap in snapshots],
        "issues": list(issues),
        "query": {"date": date.isoformat() if date else None, "limit": limit},
    }


@app.get("/api/platform/stocks/{sid}/recommendations")
def get_platform_stock_recommendations(
    sid: str, data_dir: RecommendationDataRootDep
) -> dict[str, object]:
    """Return one stock's saved recommendation events; unknown stocks are legitimately empty."""
    if _PLATFORM_SID.fullmatch(sid) is None:
        raise HTTPException(status_code=422, detail="股票代號格式錯誤")
    try:
        events = stock_events(data_dir, sid)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="推薦快照儲存區暫時無法讀取") from exc
    return {"sid": sid, "events": events}


@app.get("/api/platform/insights")
def get_platform_insights(
    data_dir: RecommendationDataRootDep,
    stock: Annotated[str | None, Query()] = None,
    run: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """Read saved insight evidence packs; this endpoint never calls a language model."""
    if stock is not None and _PLATFORM_SID.fullmatch(stock) is None:
        raise HTTPException(status_code=422, detail="股票代號格式錯誤")
    try:
        catalog = select_packs(data_dir, stock_id=stock, insight_run_id=run)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="洞悉證據包儲存區暫時無法讀取") from exc
    return {
        "packs": [pack.model_dump(mode="json") for pack in catalog.packs],
        "issues": catalog.issues,
        "query": {"stock": stock, "run": run},
    }


@app.get("/api/snapshot")
def get_snapshot(wh: WarehouseDep) -> dict[str, object]:
    """儀表板完整資料（總覽/市場/因子/回測/法人/營收/容量）；整包，供除錯與離線匯出對照。

    前端已改逐頁 fetch 各段端點（見下），一般渲染不再打此重端點。
    """
    try:
        return snap.build_snapshot(wh)
    except (TwquantError, ValueError) as exc:
        # 無行情/缺配置/無交易日等 → 服務暫時無法產出（非使用者輸入層級）
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/meta")
def get_meta(wh: WarehouseDep) -> dict[str, object]:
    """輕量中繼資料：最新行情日、回測窗、營收月、前 8 檔個股、涵蓋（供 shell 與各頁預設）。"""
    as_of = wh.latest_date("prices_daily")
    rev_month, _ = snap.revenue(wh)
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "window": f"{snap.WINDOW_START}~{snap.WINDOW_END}",
        "rev_month": rev_month,
        "top_stocks": snap.liquid_stocks(wh, snap.SNAPSHOT_TOP_N),
        "coverage": snap.coverage(wh),
    }


@app.get("/api/market")
def get_market(wh: WarehouseDep) -> dict[str, object]:
    """等權市場指數日 OHLC（總覽/市場頁）。"""
    return {"market": snap.market_index(wh)}


@app.get("/api/indices")
def get_indices(wh: WarehouseDep) -> dict[str, object]:
    """官方指數：加權指數 TAIEX（OHLC）+ 櫃買指數 TPEX（收盤）（市場頁）。"""
    return {"indices": snap.market_indices(wh)}


@app.get("/api/factors")
def get_factors(wh: WarehouseDep) -> dict[str, object]:
    """全因子 RankIC（因子健檢頁）——重運算，僅此端點被請求時才算。"""
    try:
        return {"factors": snap.factor_reports(wh)}
    except (TwquantError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/institutional")
def get_institutional(wh: WarehouseDep) -> dict[str, object]:
    """投信買超前 15（法人頁）。"""
    return {"institutional": snap.institutional(wh)}


@app.get("/api/revenue")
def get_revenue(wh: WarehouseDep) -> dict[str, object]:
    """月營收 YoY 成長前 15（營收頁）。"""
    ym, rows = snap.revenue(wh)
    return {"rev_month": ym, "revenue": rows}


@app.get("/api/capacity")
def get_capacity(wh: WarehouseDep) -> dict[str, object]:
    """策略資金容量上限（總覽 KPI/容量頁）——需跑 build_targets。"""
    try:
        return {"capacity": snap.capacity(wh)}
    except (TwquantError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/stocks")
def get_stocks(wh: WarehouseDep) -> dict[str, object]:
    """成交值前 150 檔個股代號（即時選單）。"""
    return {"stocks": snap.liquid_stocks(wh)}


@app.get("/api/stock-meta")
def get_stock_meta(wh: WarehouseDep) -> dict[str, object]:
    """代號 → 中文名 / 產業別（族群）/ 市場別（PG serving.stock_meta，毫秒級）；前端據此標中文。

    原讀資料湖（全湖掃描，實測 ~108s）改讀 PG——個股頁選單與追蹤股名稱即時可用。
    """
    return _stock_ep(lambda: stock_api.stock_meta_all(wh, stock_api.serving_cfg()))


@app.get("/api/stocks/{sid}")
def get_stock(sid: str, wh: WarehouseDep) -> dict[str, object]:
    """單一個股即時 K 線（任一資料湖內個股，非只快照的前 8 檔）。"""
    try:
        ohlc = snap.stock_ohlc(wh, sid)
    except DataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ohlc:
        raise HTTPException(status_code=404, detail=f"查無個股 {sid}")
    return {"sid": sid, "ohlc": ohlc}


def _stock_ep(fn: Callable[[], object]) -> dict[str, object]:
    """個股端點共用錯誤處理：非法代號→400、其餘（含 PG 突發不可用）→503。"""
    try:
        return cast("dict[str, object]", fn())
    except DataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 服務層錯誤（PG/資料湖）→ 503，不外洩堆疊
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/stocks/{sid}/factors")
def get_stock_factors(sid: str, wh: WarehouseDep) -> dict[str, object]:
    """單一個股的因子體檢卡（PG factor_values 優先，否則現算；payload 形狀不變）。"""
    return _stock_ep(lambda: stock_api.scorecard(wh, stock_api.serving_cfg(), sid))


@app.get("/api/stocks/{sid}/overview")
def get_stock_overview(sid: str, wh: WarehouseDep) -> dict[str, object]:
    """個股頁首：名稱/產業/最新價/漲跌/52週位置/綜合因子分位（+ source）。"""
    return _stock_ep(lambda: stock_api.overview(wh, stock_api.serving_cfg(), sid))


@app.get("/api/stocks/{sid}/ohlc")
def get_stock_ohlc(
    sid: str, wh: WarehouseDep, days: Annotated[int, Query(ge=1, le=5000)] = 500
) -> dict[str, object]:
    """個股日 K（envelope 版，含 source）；舊 /api/stocks/{sid} 為相容別名。"""
    return _stock_ep(lambda: stock_api.ohlc(wh, stock_api.serving_cfg(), sid, days))


@app.get("/api/stocks/{sid}/institutional")
def get_stock_institutional(
    sid: str, wh: WarehouseDep, days: Annotated[int, Query(ge=1, le=2000)] = 120
) -> dict[str, object]:
    """個股三大法人買賣超 + 外資/投信累計淨買超。"""
    return _stock_ep(lambda: stock_api.institutional(wh, stock_api.serving_cfg(), sid, days))


@app.get("/api/stocks/{sid}/margin")
def get_stock_margin(
    sid: str, wh: WarehouseDep, days: Annotated[int, Query(ge=1, le=2000)] = 120
) -> dict[str, object]:
    """個股融資券餘額 + 日增減 + 券資比。"""
    return _stock_ep(lambda: stock_api.margin(wh, stock_api.serving_cfg(), sid, days))


@app.get("/api/stocks/{sid}/revenue")
def get_stock_revenue(
    sid: str, wh: WarehouseDep, months: Annotated[int, Query(ge=1, le=240)] = 36
) -> dict[str, object]:
    """個股月營收 + YoY + MoM。"""
    return _stock_ep(lambda: stock_api.revenue(wh, stock_api.serving_cfg(), sid, months))


@app.get("/api/stocks/{sid}/brokers")
def get_stock_brokers(
    sid: str, wh: WarehouseDep, window: Annotated[int, Query(ge=1, le=60)] = 5
) -> dict[str, object]:
    """個股主力券商買賣超（富邦DJ 母公司層級 top-15±，近 window 交易日彙總）。"""
    return _stock_ep(lambda: stock_api.brokers(wh, stock_api.serving_cfg(), sid, window))


@app.get("/api/composite-top")
def get_composite_top(
    wh: WarehouseDep, n: Annotated[int, Query(ge=1, le=50)] = 10
) -> dict[str, object]:
    """宇宙綜合因子排名前 n（最新物化日、pct 遞減）；個股因子頁「熱門」列。"""
    return _stock_ep(lambda: stock_api.composite_top(wh, stock_api.serving_cfg(), n))


@app.get("/api/factor-top")
def get_factor_top(
    wh: WarehouseDep,
    factor: Annotated[str, Query(description="因子名，如 price.reversal_5d")] = "price.reversal_5d",
    n: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Annotated[str, Query(description="top＝訊號最強｜bottom＝最弱")] = "top",
) -> dict[str, object]:
    """單因子選股：該因子最新物化日、方向化 z 排序前 n（top 最強/bottom 最弱）。"""
    return _stock_ep(lambda: stock_api.factor_top(wh, stock_api.serving_cfg(), factor, n, order))


@app.get("/api/screener")
def get_screener(
    wh: WarehouseDep,
    n: Annotated[int, Query(ge=1, le=200, description="每池成員上限")] = 30,
    date: Annotated[str | None, Query(description="回溯日 YYYY-MM-DD（省略＝最新即時）")] = None,
) -> dict[str, object]:
    """策略選股池（SPEC-007 §6）：籌碼×技術×基本面高勝率條件的每日成員與連續在池天數。

    date 給定＝回溯：先讀 screener_daily 快照（snapshot=true）、無快照即時回算。
    """
    return _stock_ep(lambda: screener.payload_for(wh, stock_api.serving_cfg(), n, date))


@app.get("/api/stocks/{sid}/broker-flow")
def get_stock_broker_flow(
    sid: str, wh: WarehouseDep, days: Annotated[int, Query(ge=1, le=2000)] = 250
) -> dict[str, object]:
    """個股主力（富邦DJ top-15±）每日淨買超（股）序列；供 K 線副圖日條＋累計線。"""
    return _stock_ep(lambda: stock_api.broker_flow(wh, stock_api.serving_cfg(), sid, days))


@app.get("/api/stocks/{sid}/composite-history")
def get_stock_composite_history(
    sid: str, wh: WarehouseDep, days: Annotated[int, Query(ge=1, le=2000)] = 250
) -> dict[str, object]:
    """個股綜合因子分位歷史（需 PG 物化；無則回空）。"""
    return _stock_ep(lambda: stock_api.composite_history(wh, stock_api.serving_cfg(), sid, days))


@app.get("/api/stocks/{sid}/factors/history")
def get_stock_factor_history(
    sid: str,
    wh: WarehouseDep,
    factors: Annotated[str, Query(description="逗號分隔的因子名")] = "",
    days: Annotated[int, Query(ge=1, le=2000)] = 250,
) -> dict[str, object]:
    """個股各因子的 z/pct 歷史序列（需 PG 物化；無則回空）。"""
    flist = [f.strip() for f in factors.split(",") if f.strip()]
    return _stock_ep(
        lambda: stock_api.factor_history(wh, stock_api.serving_cfg(), sid, flist, days)
    )


_STRATEGIES_DIR = snap.CONFIGS_DIR / "strategies"
# 因子回看上限（營收 YoY 需 ~13 個月 + 緩衝）；PG since 讀取下限 = start − 此值
_BACKTEST_LOOKBACK_DAYS = 420


@app.get("/api/strategies")
def get_strategies() -> list[dict[str, str]]:
    """列出可回測策略（configs/strategies/*.yaml）；壞配置標示無效不擋清單。"""
    from twquant.strategy.config import load_strategy

    out: list[dict[str, str]] = []
    for p in sorted(_STRATEGIES_DIR.glob("*.yaml")):
        try:
            s = load_strategy(p)
        except TwquantError as exc:
            out.append({"id": p.stem, "name": p.stem, "description": f"配置無效：{exc}"})
            continue
        out.append({"id": p.stem, "name": s.name, "description": s.description})
    return out


@app.get("/api/backtest")
def get_backtest(
    wh: WarehouseDep,
    start: Annotated[str | None, Query(description="回測起 YYYY-MM-DD")] = None,
    end: Annotated[str | None, Query(description="回測迄 YYYY-MM-DD")] = None,
    strategy: Annotated[
        str | None, Query(description="策略 id（configs/strategies 檔名，不含 .yaml）")
    ] = None,
) -> dict[str, object]:
    """指定期間/策略的回測（權益曲線＋指標）；未帶參數用預設連續期與預設策略。

    資料源：serving.enabled 時走 PG 鏡射（PgTableSource，cache＋since 下限讀取，
    D-026/T-049）；PG 不可達即 503（D-035 移除 DuckDB 降級——雲端無資料湖）。
    """
    try:
        s = dt.date.fromisoformat(start) if start else snap.WINDOW_START
        e = dt.date.fromisoformat(end) if end else snap.WINDOW_END
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式錯誤：{exc}") from exc
    if s >= e:
        raise HTTPException(status_code=400, detail="起始日需早於結束日")
    strat_path = snap.STRATEGY_PATH
    if strategy:
        p = _STRATEGIES_DIR / f"{strategy}.yaml"
        if Path(strategy).name != strategy or not p.exists():  # 防路徑跳脫 + 白名單目錄
            raise HTTPException(status_code=404, detail=f"無此策略：{strategy}")
        strat_path = p

    src: TableSource = wh
    pg_conn = None
    scfg = load_config(snap.CONFIGS_DIR / "serving.yaml", ServingConfig)
    if scfg.enabled:
        import psycopg

        from twquant.data.serving import store
        from twquant.data.serving.pg_view import PgTableSource

        try:
            pg_conn = store.connect(scfg)
            src = PgTableSource(
                pg_conn,
                scfg.pg_schema,
                cache=True,
                since=s - dt.timedelta(days=_BACKTEST_LOOKBACK_DAYS),
            )
        except psycopg.OperationalError as exc:
            raise HTTPException(status_code=503, detail=f"PG 不可用：{exc}") from exc
    try:
        return snap.backtest_result(src, s, e, strat_path=strat_path)
    except (TwquantError, ValueError) as exc:
        # run_backtest 對「區間內無交易日」（如未來日期）raise ValueError → 使用者輸入層級
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if pg_conn is not None:
            pg_conn.close()


@app.get("/api/ingest-status")
def get_ingest_status(wh: WarehouseDep) -> dict[str, object]:
    """擷取涵蓋、缺口、最後執行時點與最近失敗（即時）。"""
    return snap.ingest_status(wh)


# 擷取後台：矩陣/缺口/heartbeat 即時計算——失敗一發生就要看得到，
# 且成本低（目錄 glob + 小 parquet）。補抓可走 CLI 或一鍵補抓端點（D-018）。
@app.get("/api/ingest-matrix")
def get_ingest_matrix(
    wh: WarehouseDep,
    start: Annotated[str | None, Query(description="起 YYYY-MM-DD（空=最近 N 交易日）")] = None,
    end: Annotated[str | None, Query(description="迄 YYYY-MM-DD（空=最新交易日）")] = None,
) -> dict[str, object]:
    """dataset × 交易日 的擷取涵蓋矩陣（供後台熱力圖）。"""
    try:
        s = dt.date.fromisoformat(start) if start else None
        e = dt.date.fromisoformat(end) if end else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式錯誤：{exc}") from exc
    if s is not None and e is not None and s > e:
        raise HTTPException(status_code=400, detail="起始日需早於或等於結束日")
    return snap.ingest_matrix(wh, s, e)


@app.get("/api/ingest-gaps")
def get_ingest_gaps(wh: WarehouseDep) -> dict[str, object]:
    """漏抓/失敗缺口清單，含歸併區段與建議補抓 CLI 指令。"""
    return snap.ingest_gaps(wh)


@app.get("/api/ingest-tracks")
def get_ingest_tracks() -> dict[str, object]:
    """可手動抓取的資料軌白名單（供「手動抓取歷史資料」表單的資料集選單）。"""
    return {"tracks": jobs.fetchable_tracks()}


@app.get("/api/holiday-calendar")
def get_holiday_calendar(wh: WarehouseDep) -> dict[str, object]:
    """官方開休市日曆（market_holidays）：開市日數、休市清單、官方 vs 觀測核對。"""
    return snap.holiday_calendar(wh)


class IngestJobRequest(BaseModel):
    """一鍵補抓請求：只收缺口識別（指令由白名單推導，不收命令字串）。"""

    table: str
    source: str
    from_: str = Field(alias="from")
    to: str
    force: bool = False  # D-015 護欄：TWSE 行情大區間回補需明示強制


@app.post("/api/ingest-jobs", status_code=201)
def post_ingest_job(req: IngestJobRequest, wh: WarehouseDep) -> dict[str, object]:
    """一鍵補抓（D-018）：以子行程執行 twquant CLI，單一併發；回傳工作供輪詢。"""
    try:
        args = jobs.build_args(req.table, req.source, req.from_, req.to)
        if not req.force and jobs.needs_force(wh, req.table, req.source, req.from_, req.to):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"TWSE 行情回補超過 {snap.TWSE_BACKFILL_WARN} 交易日易觸發封鎖（D-015）："
                    "建議分塊或改走 FinMind；確定要跑請帶 force"
                ),
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return jobs.MANAGER.start(
            jobs.job_label(req.table, req.source, req.from_, req.to),
            jobs.display_command(args),
            jobs.full_argv(args),
        )
    except jobs.JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/ingest-jobs")
def get_ingest_jobs() -> dict[str, object]:
    """補抓工作清單（新在前，含輸出尾行）＋是否有工作進行中（不快取）。"""
    return {"jobs": jobs.MANAGER.jobs(), "busy": jobs.MANAGER.running()}


@app.get("/api/ingest-jobs/{jid}")
def get_ingest_job(jid: str) -> dict[str, object]:
    """單一補抓工作明細（輪詢輸出尾行用）。"""
    job = jobs.MANAGER.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail=f"查無工作 {jid}")
    return job


class PipelineJobRequest(BaseModel):
    """手動補跑每日 pipeline：只收一個交易日（省略＝今日台北），無其他自由輸入。"""

    date: str | None = None


@app.post("/api/pipeline-jobs", status_code=201)
def post_pipeline_job(req: PipelineJobRequest) -> dict[str, object]:
    """手動補跑當日 pipeline（排程錯過時的介面補跑）：以子行程跑 `twquant pipeline --date`。

    與補抓共用同一 JobManager（單一併發：pipeline 內含擷取，不得與補抓並跑）；工作出現
    在同一 `/api/ingest-jobs` 清單供輪詢。
    """
    try:
        day = jobs.resolve_trading_day(req.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    args = jobs.pipeline_args(day)
    try:
        return jobs.MANAGER.start(
            jobs.pipeline_label(day), jobs.display_command(args), jobs.full_argv(args)
        )
    except jobs.JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/heartbeat")
def get_heartbeat(wh: WarehouseDep) -> dict[str, object]:
    """pipeline heartbeat + 最新行情日 + watchdog（供後台狀態條）。"""
    from twquant.pipeline import read_heartbeat, watchdog_check

    hb = read_heartbeat(wh.root)
    latest = wh.latest_date("prices_daily")
    wd = watchdog_check(wh.root, latest) if latest else None
    return {
        "heartbeat": {"day": hb.day, "git_hash": hb.git_hash, "ts": hb.ts} if hb else None,
        "latest": latest.isoformat() if latest else None,
        "watchdog_ok": wd.ok if wd else False,
        "watchdog_reason": wd.reason if wd else "資料湖尚無行情",
    }


@app.get("/api/sector-flows")
def get_sector_flows(
    wh: WarehouseDep,
    window: Annotated[int, Query(ge=1, le=120, description="回看交易日數")] = snap.SECTOR_WINDOW,
) -> dict[str, object]:
    """各產業別（族群）近 window 交易日的三大法人資金淨流入 + 報酬 + 成交值（資金移動板塊）。"""
    return {"flows": snap.sector_flows(wh, window)}
