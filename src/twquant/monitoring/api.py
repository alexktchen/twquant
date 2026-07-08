"""唯讀監控 API（SPEC-007）。

FastAPI/JSON 服務，供 React 前端即時讀 clean 資料湖（取代靜態 data.json 快照，
並取代已退役的 Streamlit 儀表板）。全部 GET、無操作端點（操作走 CLI + DECISIONS，
避免誤觸）。以 `twquant serve` 啟動；docker-compose 的 `api` 服務常駐。

重運算（全因子 IC + 回測）以「最新行情日」為鍵快取——資料湖日頻更新，同一日內
重覆請求直接命中，換日自動失效重算。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from twquant.config import SystemConfig, load_config
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError, TwquantError
from twquant.monitoring import snapshot as snap

_CONFIG = snap.CONFIGS_DIR / "system.yaml"


def get_warehouse() -> Warehouse:
    """由 configs/system.yaml 建 Warehouse；data_dir 相對路徑以 repo 根為基準（CWD 無關）。"""
    cfg = load_config(_CONFIG, SystemConfig)
    data_path = Path(cfg.data_dir)
    if not data_path.is_absolute():
        data_path = snap.CONFIGS_DIR.parent / data_path
    return Warehouse(data_path)


WarehouseDep = Annotated[Warehouse, Depends(get_warehouse)]

# 以 (段名, 資料湖根, 最新行情日, config 指紋) 為鍵的記憶化——換日/換倉/改配置自動失效。
# 前端逐頁 fetch 各段；重運算（因子 IC、回測、容量）只在該段被請求時才算並快取。
_cache: dict[str, object] = {}


def _latest(wh: Warehouse) -> str:
    d = wh.latest_date("prices_daily")
    return d.isoformat() if d else "none"


def _config_stamp() -> str:
    """策略/成本 YAML 的 mtime 指紋——併入快取鍵，config 一改即失效（不必等換日）。"""
    stamps = []
    for p in (snap.STRATEGY_PATH, snap.COSTS_PATH):
        try:
            stamps.append(str(p.stat().st_mtime_ns))
        except OSError:
            stamps.append("0")
    return "|".join(stamps)


def _memo(name: str, wh: Warehouse, build: Callable[[], object]) -> object:
    """記憶化某段資料。build 失敗不入快取（下次重試）。"""
    key = f"{name}|{wh.root}|{_latest(wh)}|{_config_stamp()}"
    if key not in _cache:
        _cache[key] = build()
    return _cache[key]


# openapi_url 也放 /api/ 下，讓 /api/docs 經 nginx/vite 反代時仍能載到 schema。
app = FastAPI(
    title="twquant 監控 API", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
)
# 唯讀 GET API + 本機工具：放行跨來源（dev vite proxy / 直開單檔皆可）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health(wh: WarehouseDep) -> dict[str, object]:
    """存活探針：回最新行情日。"""
    return {"status": "ok", "as_of": wh.latest_date("prices_daily")}


@app.get("/api/snapshot")
def get_snapshot(wh: WarehouseDep) -> dict[str, object]:
    """儀表板完整資料（總覽/市場/因子/回測/法人/營收/容量）；整包，供除錯與離線匯出對照。

    前端已改逐頁 fetch 各段端點（見下），一般渲染不再打此重端點。
    """
    try:
        return cast("dict[str, object]", _memo("snapshot", wh, lambda: snap.build_snapshot(wh)))
    except (TwquantError, ValueError) as exc:
        # 無行情/缺配置/無交易日等 → 服務暫時無法產出（非使用者輸入層級）
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/meta")
def get_meta(wh: WarehouseDep) -> dict[str, object]:
    """輕量中繼資料：最新行情日、回測窗、營收月、前 8 檔個股、涵蓋（供 shell 與各頁預設）。"""

    def build() -> dict[str, object]:
        as_of = wh.latest_date("prices_daily")
        rev_month, _ = snap.revenue(wh)
        return {
            "as_of": as_of.isoformat() if as_of else None,
            "window": f"{snap.WINDOW_START}~{snap.WINDOW_END}",
            "rev_month": rev_month,
            "top_stocks": snap.liquid_stocks(wh, snap.SNAPSHOT_TOP_N),
            "coverage": snap.coverage(wh),
        }

    return cast("dict[str, object]", _memo("meta", wh, build))


@app.get("/api/market")
def get_market(wh: WarehouseDep) -> dict[str, object]:
    """等權市場指數日 OHLC（總覽/市場頁）。"""
    return {"market": _memo("market", wh, lambda: snap.market_index(wh))}


@app.get("/api/indices")
def get_indices(wh: WarehouseDep) -> dict[str, object]:
    """官方指數：加權指數 TAIEX（OHLC）+ 櫃買指數 TPEX（收盤）（市場頁）。"""
    return {"indices": _memo("indices", wh, lambda: snap.market_indices(wh))}


@app.get("/api/factors")
def get_factors(wh: WarehouseDep) -> dict[str, object]:
    """全因子 RankIC（因子健檢頁）——重運算，僅此端點被請求時才算。"""
    try:
        return {"factors": _memo("factors", wh, lambda: snap.factor_reports(wh))}
    except (TwquantError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/institutional")
def get_institutional(wh: WarehouseDep) -> dict[str, object]:
    """投信買超前 15（法人頁）。"""
    return {"institutional": _memo("institutional", wh, lambda: snap.institutional(wh))}


@app.get("/api/revenue")
def get_revenue(wh: WarehouseDep) -> dict[str, object]:
    """月營收 YoY 成長前 15（營收頁）。"""

    def build() -> dict[str, object]:
        ym, rows = snap.revenue(wh)
        return {"rev_month": ym, "revenue": rows}

    return cast("dict[str, object]", _memo("revenue", wh, build))


@app.get("/api/capacity")
def get_capacity(wh: WarehouseDep) -> dict[str, object]:
    """策略資金容量上限（總覽 KPI/容量頁）——需跑 build_targets。"""
    try:
        return {"capacity": _memo("capacity", wh, lambda: snap.capacity(wh))}
    except (TwquantError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/stocks")
def get_stocks(wh: WarehouseDep) -> dict[str, object]:
    """成交值前 150 檔個股代號（即時選單）。"""
    return {"stocks": snap.liquid_stocks(wh)}


@app.get("/api/stock-meta")
def get_stock_meta(wh: WarehouseDep) -> dict[str, object]:
    """代號 → 中文名 / 產業別（族群）/ 市場別；前端據此把數字股號標成中文。"""
    return {"meta": _memo("stock_meta", wh, lambda: snap.stock_meta_map(wh))}


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


@app.get("/api/stocks/{sid}/factors")
def get_stock_factors(sid: str, wh: WarehouseDep) -> dict[str, object]:
    """單一個股的因子體檢卡（各因子橫斷面 z / 百分位 / 方向 + 綜合排名 + 中文名/族群）。"""
    try:
        return snap.stock_factor_scorecard(wh, sid)
    except DataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TwquantError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/backtest")
def get_backtest(
    wh: WarehouseDep,
    start: Annotated[str | None, Query(description="回測起 YYYY-MM-DD")] = None,
    end: Annotated[str | None, Query(description="回測迄 YYYY-MM-DD")] = None,
) -> dict[str, object]:
    """指定期間的回測（權益曲線＋指標）；未帶參數用預設連續期。"""
    try:
        s = dt.date.fromisoformat(start) if start else snap.WINDOW_START
        e = dt.date.fromisoformat(end) if end else snap.WINDOW_END
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式錯誤：{exc}") from exc
    if s >= e:
        raise HTTPException(status_code=400, detail="起始日需早於結束日")
    try:
        return cast(
            "dict[str, object]",
            _memo(f"backtest|{s}|{e}", wh, lambda: snap.backtest_result(wh, s, e)),
        )
    except (TwquantError, ValueError) as exc:
        # run_backtest 對「區間內無交易日」（如未來日期）raise ValueError → 使用者輸入層級
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ingest-status")
def get_ingest_status(wh: WarehouseDep) -> dict[str, object]:
    """擷取涵蓋、缺口、最後執行時點與最近失敗（即時）。"""
    return snap.ingest_status(wh)


# 擷取後台（唯讀）：矩陣/缺口/heartbeat 即時計算、不走 _memo——失敗一發生就要看得到，
# 且成本低（目錄 glob + 小 parquet）。補抓仍走 CLI，D-016 唯讀原則不變。
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
    """漏抓/失敗缺口清單，含歸併區段與建議補抓 CLI 指令（唯讀，不執行）。"""
    return snap.ingest_gaps(wh)


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
    return {"flows": _memo(f"sector-flows|{window}", wh, lambda: snap.sector_flows(wh, window))}
