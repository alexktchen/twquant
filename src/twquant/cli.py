"""twquant CLI 進入點。

第一版只提供開發閘門 `check`（CLAUDE.md §2）。資料/回測等指令在後續任務加入。
"""

from __future__ import annotations

import datetime as dt
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import typer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import polars as pl

    from twquant.config import (
        EtfMetaConfig,
        FundFlowConfig,
        MlConfig,
        RecommendationBucketConfig,
        ServingConfig,
        SystemConfig,
    )
    from twquant.data.http import HttpClient
    from twquant.data.ingest.orchestrator import IngestOutcome
    from twquant.data.ingest.registry import SourceSpec
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.data.sources.indices import TpexIndexSource, TwseIndexSource
    from twquant.data.warehouse import TableSource
    from twquant.insight.client import InsightClient
    from twquant.pipeline import PipelineResult
    from twquant.reporting.recommendation_export import BucketPolicy
    from twquant.reporting.recommendation_snapshot import SnapshotWriteResult
    from twquant.reporting.strategy_targets import TargetList
    from twquant.strategy.config import StrategyConfig

app = typer.Typer(add_completion=False, help="台股量化平台 CLI。")

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs"

# 洞悉分析在 pipeline 的參數：N 為 D-081(b) 的事前登記值；窗口與 sector-flows 預設一致。
_INSIGHT_TOP_N = 30
# 紙上交易的行情回看：前收與 20 日 ADV 中位數都在這個窗內（D-142／T-280）。
_PAPER_LOOKBACK_DAYS = 90
_SECTOR_WINDOW = 20


def _force_utf8_output() -> None:
    """Windows 主控台預設 cp950，中文輸出會亂碼；把 stdout/stderr 重設為 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


@app.callback()
def _main() -> None:
    """台股量化平台 CLI。子指令見下。"""
    # 存在此 callback 是為了讓 typer 以「子指令」模式運作（如 `twquant check`），
    # 而非把單一指令收成根指令。
    _force_utf8_output()


# 閘門檢查的目標路徑
_TARGETS = ["src", "tests"]


@dataclass(frozen=True)
class Step:
    name: str
    cmd: list[str]


def _tool_cmd(tool: str, args: list[str]) -> list[str]:
    """組出工具指令：優先用 venv 內的 console script，否則退回 `python -m`。"""
    exe = shutil.which(tool)
    if exe:
        return [exe, *args]
    return [sys.executable, "-m", tool, *args]


def _run(step: Step) -> bool:
    typer.secho(f">> {step.name}", fg=typer.colors.CYAN)
    result = subprocess.run(step.cmd)  # 指令來自本模組固定組裝，非外部輸入
    ok = result.returncode == 0
    mark = "[OK]" if ok else "[FAIL]"
    color = typer.colors.GREEN if ok else typer.colors.RED
    typer.secho(f"{mark} {step.name}", fg=color)
    return ok


@app.command()
def check(
    fast: bool = typer.Option(
        False, "--fast", help="只跑 lint 與測試（跳過 format 檢查與 mypy），開發中用。"
    ),
) -> None:
    """開發閘門：ruff check + ruff format --check + mypy + pytest 全套。

    任何一項失敗即以非零碼結束。--fast 只跑 ruff check + pytest（-x 遇錯即停）。
    """
    if fast:
        steps = [
            Step("ruff check", _tool_cmd("ruff", ["check", *_TARGETS])),
            Step("pytest (-x)", _tool_cmd("pytest", ["-x"])),
        ]
    else:
        steps = [
            Step("ruff check", _tool_cmd("ruff", ["check", *_TARGETS])),
            Step("ruff format --check", _tool_cmd("ruff", ["format", "--check", *_TARGETS])),
            Step("mypy", _tool_cmd("mypy", [])),
            Step("pytest", _tool_cmd("pytest", [])),
        ]

    failed = [step.name for step in steps if not _run(step)]

    typer.echo("")
    if failed:
        typer.secho(f"閘門未過：{', '.join(failed)}", fg=typer.colors.RED, bold=True)
        raise typer.Exit(code=1)
    typer.secho("閘門全綠。", fg=typer.colors.GREEN, bold=True)


#: CLAUDE.md §0.2：`system.yaml` 的 `data_dir` 是**相對路徑**，在 worktree 裡執行時
#: 指向 worktree 自己的 `data/` 而不是主 checkout 的正本。`--out` 只管得到模型檔，
#: `research_log`（＝DSR 的試驗計數）與各式落地仍會分岔——實測本輪的兩筆 `ml_train`
#: 就寫進了 worktree，差點讓試驗帳本一分為二。此環境變數讓所有寫入路徑一次對齊。
_DATA_DIR_ENV = "TWQUANT_DATA_DIR"
_FLOW_FLAG_LOOKBACK_DAYS = 120  # T-341：看板資金流旗標往前讀幾個日曆日（約 80 個交易日）
_SIGNAL_CUTOFF_HOUR = 20  # D-008 訊號截點
_CONTEXT_INDEX_ID = "TAIEX"  # 大盤狀態特徵用的指數（T-248；與 index_trend_gate 同一個）


def _load_system() -> SystemConfig:
    import os

    from twquant.config import SystemConfig, load_config

    cfg = load_config(_CONFIG_DIR / "system.yaml", SystemConfig)
    override = os.environ.get(_DATA_DIR_ENV, "").strip()
    return cfg.model_copy(update={"data_dir": override}) if override else cfg


def _ingest_date(
    day: dt.date, tables: list[str] | None, *, tolerant: bool, failed: list[str] | None = None
) -> int:
    """抓→清洗→存單日每日來源（TWSE+TPEX）。tolerant=True 時單表失敗只警告不中斷。

    來源由註冊表驅動（D-017）：新增每日來源 = data/ingest/builtins.py 一筆 register，
    不改本函數；Warehouse 於寫入站以 IngestSink 埠使用（儲存亦可換）。

    直寫 PG 的表（T-310：當沖、借券、處置、注意）只在 `tables` 明確指定時才抓——每日 pipeline
    由「籌碼」步驟抓它們（前幾個交易日＋缺日，見 `_run_chip_day`）；`backfill` 預設也不碰
    （公告一天一個查詢窗，逐日回補只會重複查同一段、白白逼近證交所 WAF 門檻）。

    回傳「證明當天有交易」的成功表數（公告類不算，見 SourceSpec.proves_trading）。
    `failed` 給了就把沒抓到的表以 "來源/表" 附加進去（D-175 必要表檢查用）。
    """
    import twquant.data.ingest.builtins  # noqa: F401  # 匯入觸發來源註冊
    from twquant.data.ingest.orchestrator import run_ingest, trading_evidence
    from twquant.data.ingest.registry import list_specs
    from twquant.data.serving.schema import DIRECT_PG_TABLES
    from twquant.data.warehouse import Warehouse

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    specs = list_specs(cadence="daily")

    def _announce(tag: str, table: str) -> None:
        typer.secho(f">> [{tag}] 抓取 {table} @ {day}", fg=typer.colors.CYAN)

    retries = 2 if tolerant else 0  # T-276：容錯模式（每日排程）才重試
    outcomes = run_ingest(
        [s for s in specs if s.store == "lake"],
        cfg,
        wh,
        day,
        data_dir=cfg.data_dir,
        tables=tables,
        tolerant=tolerant,
        on_start=_announce,
        retries=retries,
    )
    direct = [t for t in (tables or []) if t in DIRECT_PG_TABLES]
    if direct:
        pg_specs = [s for s in specs if s.store == "pg"]
        outcomes += _ingest_direct(day, pg_specs, direct, tolerant, _announce, retries)
    ok = [o for o in outcomes if o.status == "success"]
    evidence = trading_evidence(outcomes, {s.source_tag: s.proves_trading for s in specs})
    bad = [o for o in outcomes if o.status != "success"]
    if failed is not None:
        failed.extend(f"{o.source_tag}/{o.table}" for o in bad)
    for o in ok:
        typer.secho(f"[OK] {o.source_tag}/{o.table}：{o.rows} 列已存入", fg=typer.colors.GREEN)
    # T-276：**有表成功就代表當天有交易**，那麼失敗的表就不是休市，是真的沒抓到。
    # 兩者原本都印成黃色 [SKIP]，於是 `margin_daily` 連兩天沒進來也沒人發現
    # （2026-09-21／22，停在 09-18）。休市時全部的表都會失敗，用這個差異分開它們。
    # T-310：公告（處置／注意）休市日也查得到東西，它的成功不算證據。
    for o in bad:
        if evidence:
            typer.secho(
                f"[FAILED] {o.source_tag}/{o.table}：{o.detail}"
                f"　← 同日其他 {len(evidence)} 張表有資料，**這天有交易**，不是休市",
                fg=typer.colors.RED,
                bold=True,
            )
        else:
            typer.secho(f"[SKIP] {o.source_tag}/{o.table}：{o.detail}", fg=typer.colors.YELLOW)
    return len(evidence)


def _ingest_direct(
    day: dt.date,
    specs: Sequence[SourceSpec],
    tables: list[str],
    tolerant: bool,
    on_start: Callable[[str, str], None],
    retries: int,
) -> list[IngestOutcome]:
    """直寫 PG 的表（T-310）：同一個註冊表與 run_ingest，sink 換成 PgDirectSink。

    PG 未啟用或連不上：這些表無處可寫 → 容錯模式記失敗（ingest_log 也記），非容錯模式 raise。
    """
    from twquant.data.ingest.orchestrator import IngestOutcome, run_ingest
    from twquant.data.serving import direct_ingest, store
    from twquant.data.serving import schema as sch
    from twquant.errors import DataError
    from twquant.reporting.ingest_log import record

    cfg = _load_system()
    scfg = _load_serving()
    if not scfg.enabled or not store.ping(scfg):
        detail = "PG 未啟用或連不上——直寫表（T-310）無處可寫"
        if not tolerant:
            raise DataError(",".join(tables), day, detail)
        out: list[IngestOutcome] = []
        for spec in specs:
            for table in (t for t in spec.build(cfg).tables if t in tables):
                record(cfg.data_dir, spec.source_tag, table, day, "failed", detail=detail)
                out.append(IngestOutcome(spec.source_tag, table, "failed", 0, detail))
        return out
    with store.connect(scfg) as conn:
        sch.ensure_tables(conn, sch.DIRECT_INGEST_SPECS, scfg.pg_schema)
        sink = direct_ingest.PgDirectSink(conn, scfg.pg_schema)
        outcomes = run_ingest(
            specs,
            cfg,
            sink,
            day,
            data_dir=cfg.data_dir,
            tables=tables,
            tolerant=tolerant,
            on_start=on_start,
            retries=retries,
        )
    for note in sink.notes:
        typer.secho(f"[NOTE] {note}", fg=typer.colors.YELLOW)
    return outcomes


@app.command()
def ingest(
    date: str = typer.Option(..., help="交易日 YYYY-MM-DD"),
    tables: list[str] | None = typer.Option(None, help="要抓的表，預設全部 TWSE 表"),
) -> None:
    """抓 TWSE 當日資料、清洗、存入 clean 資料湖。"""
    _ingest_date(dt.date.fromisoformat(date), tables, tolerant=False)


@app.command()
def daily(
    date: str = typer.Option("today", help="交易日 YYYY-MM-DD 或 today"),
) -> None:
    """每日排程用：抓當日 TWSE 資料，非交易日自動略過（不中斷排程）。"""
    from twquant.conventions import now_tw

    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)
    n = _ingest_date(day, None, tolerant=True)
    typer.secho(f"每日擷取完成：{n} 張表 @ {day}", fg=typer.colors.GREEN, bold=True)


@app.command(name="ingest-dispersion")
def ingest_dispersion() -> None:
    """抓 TDCC 集保戶股權分散表（週頻全市場快照）、清洗、存入 clean 資料湖。

    基準日取自 CSV 的資料日期欄（非參數，opendata 只給最新週快照）。目前 id=1-5 覆蓋
    6 碼證券（ETF/受益證券/債券）；四碼普通股集保分散另循 TDCC 互動站（未來另一來源，
    D-017 的可插拔設計即為此準備）。走與每日相同的來源註冊表 + 編排器（cadence=weekly）。
    """
    import twquant.data.ingest.builtins  # noqa: F401  # 匯入觸發來源註冊
    from twquant.conventions import now_tw
    from twquant.data.ingest.orchestrator import run_ingest
    from twquant.data.ingest.registry import get
    from twquant.data.warehouse import Warehouse

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    typer.secho(">> [tdcc] 抓取集保戶股權分散表", fg=typer.colors.CYAN, bold=True)
    outcomes = run_ingest(
        [get("tdcc")], cfg, wh, now_tw().date(), data_dir=cfg.data_dir, tolerant=False
    )
    for o in outcomes:
        typer.secho(
            f"[OK] {o.source_tag}/{o.table}：{o.rows} 列已存入", fg=typer.colors.GREEN, bold=True
        )


def _daterange(start: dt.date, end: dt.date) -> list[dt.date]:
    """start..end（含端點）的所有日曆日。"""
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


@app.command()
def backfill(
    from_: str = typer.Option(..., "--from", help="起始日 YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="結束日 YYYY-MM-DD"),
    skip_existing: bool = typer.Option(True, help="跳過已抓過的日期（續傳）"),
    tables: list[str] | None = typer.Option(
        None, "--tables", help="只回補指定表（如 prices_daily）；預設全部日表"
    ),
) -> None:
    """回補一段期間的免費來源日資料（TWSE+TPEX）。跳週末、跳假日、可續傳。

    `--tables prices_daily` 只補行情，可避開總請求量觸發 TWSE 封鎖、或繞過個別表的
    來源格式問題，單獨補齊個股 K 線。
    """
    from twquant.data.warehouse import Warehouse

    start = dt.date.fromisoformat(from_)
    end = dt.date.fromisoformat(to)
    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)

    days = [d for d in _daterange(start, end) if d.weekday() < 5]  # 跳週末
    typer.secho(f"回補 {start}~{end}：{len(days)} 個平日", fg=typer.colors.CYAN, bold=True)
    done = skipped = 0
    for i, day in enumerate(days, 1):
        if skip_existing and wh.partition_exists("clean", "prices_daily", day, "twse"):
            skipped += 1
            continue
        n = _ingest_date(day, tables, tolerant=True)
        if n:
            done += 1
        typer.secho(f"  [{i}/{len(days)}] {day}：{n} 表", fg=typer.colors.CYAN)
    typer.secho(
        f"回補完成：{done} 交易日新增、{skipped} 已存在跳過", fg=typer.colors.GREEN, bold=True
    )


@app.command(name="backfill-revenue")
def backfill_revenue(
    from_: str = typer.Option(..., "--from", help="起始月 YYYY-MM"),
    to: str = typer.Option(..., "--to", help="結束月 YYYY-MM"),
) -> None:
    """回補一段期間的月營收（逐月 ingest-revenue）。"""
    fy, fm = (int(x) for x in from_.split("-"))
    ty, tm = (int(x) for x in to.split("-"))
    months: list[str] = []
    y, m = fy, fm
    while (y, m) <= (ty, tm):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    typer.secho(f"回補月營收 {from_}~{to}：{len(months)} 個月", fg=typer.colors.CYAN, bold=True)
    for month in months:
        ingest_revenue(month=month)


def _fubon_universe(wh: object, pattern: str) -> list[str]:
    """從資料湖推導全市場股票池：優先 stock_meta，退回 prices_daily；過濾 pattern、排序去重。

    scan 只讀已存分區的 stock_id 欄（不觸發 schema 驗證），故資料湖有行情/基本資料即可，
    無須另開網路 universe 端點（富邦DJ 亦無此端點）。兩表皆無資料 → 回 []（呼叫端報錯）。
    """
    import polars as pl

    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError

    assert isinstance(wh, Warehouse)
    for table in ("stock_meta", "prices_daily"):
        try:
            df = wh.scan(table).select("stock_id").unique().collect()
        except DataError:
            continue
        sids = df.filter(pl.col("stock_id").str.contains(pattern))["stock_id"].sort().to_list()
        if sids:
            return sids
    return []


def _fetch_fubon_day(
    client: HttpClient,
    sids: list[str],
    day: dt.date,
    workers: int,
    skip_if: Callable[[str], bool] | None = None,
    on_fetched: Callable[[str, bytes], None] | None = None,
    on_done: Callable[[str], None] | None = None,
) -> list[tuple[str, bytes | None, str | None]]:
    """逐股抓某日富邦DJ HTML → [(sid, html, err), ...]，**永遠依 sids 序回傳**（決定性）。

    多程序協調鉤子（D-025，帳本在 PG fubon_fetch_log）：
    - skip_if 在**抓取當下**逐檔呼叫（非日初快照，檢查間隔最小）——回 True 即跳過
      （已被任一程序抓過），結果為 (sid, None, None)（非錯誤）。
    - on_fetched 於抓取成功後在 worker 執行緒呼叫（呼叫端負責解析＋PG 同交易落地；
      帳本列存在 ⇔ 資料已 commit，故靠 skip_if 跳過永不漏抓）。
    DataError（重試耗盡）不中斷整批，記入 err 由呼叫端印 SKIP。workers>1 時以執行緒池
    平行；共用 client 的滑動窗限流節流全域速率，`ThreadPoolExecutor.map` 保序 → 輸出與
    序列版一致。on_done 每檔完成即回呼（成敗/跳過皆算），供進度列；回呼須 thread-safe。
    """
    from twquant.data.sources import fubon_chip
    from twquant.errors import DataError

    def _one(sid: str) -> tuple[str, bytes | None, str | None]:
        try:
            if skip_if is not None and skip_if(sid):
                return sid, None, None  # 任一程序已抓 → 跳過（非錯誤）
            try:
                html = fubon_chip.fetch_broker_trades(client, sid, day)
            except DataError as exc:
                return sid, None, str(exc)
            if on_fetched is not None:
                on_fetched(sid, html)
            return sid, html, None
        finally:
            if on_done is not None:
                on_done(sid)

    if not sids:
        return []
    if workers <= 1:
        return [_one(sid) for sid in sids]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, sids))


def _fubon_status_lines(day_rows: list[tuple[dt.date, int, int, bool]], total: int) -> list[str]:
    """fubon_fetch_log 日彙總 → 顯示行：每日已抓/宇宙總數＋百分比；SENTINEL 日標非交易。"""
    out: list[str] = []
    for d, fetched, with_data, holiday in day_rows:
        if holiday:
            out.append(f"{d}  非交易日（探測標記）")
            continue
        pct = f"{fetched / total * 100:.1f}%" if total else "n/a"
        out.append(f"{d}  已抓 {fetched}/{total}（{pct}）｜有分點資料 {with_data} 檔")
    return out


def _probe_all_empty(results: list[tuple[str, bytes | None, str | None]]) -> bool:
    """探測結果是否「全數抓取成功、但皆無分點表」→ 視為非交易日（假日）。

    任何 err（重試耗盡/被擋）不算證據——回 False 讓整天照抓，避免把「被擋」誤判成假日
    而無聲丟掉一個交易日。空清單（未探測）亦回 False。
    """
    from twquant.data.sources import fubon_chip

    if not results:
        return False
    for _sid, html, err in results:
        if err is not None or html is None:
            return False
        if fubon_chip.parse_broker_rows(html):
            return False
    return True


def _fmt_hms(seconds: float) -> str:
    """秒數 → H:MM:SS。"""
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _make_progress(total: int, tty: bool | None = None) -> Callable[[str, int], None]:
    """建 thread-safe 進度回報。回傳 update(label, advance)。

    TTY（前景終端）：單行 \\r 重繪進度列。非 TTY（`docker logs` / 背景 / 重導向——
    行緩衝的 log driver 吞掉無換行輸出）：改輸出完整換行 log，每 ~0.5% 一行
    （advance>1 的日級事件與收尾必印），docker logs -f 可直接追進度。
    顯示：進度條、整體百分比、完成/總數、目前日期+股號、起始時刻、已用時間、
    預估結束時刻（以平均速率外推）。total=0 時百分比視為 100%（防除零）。
    tty=None 自動偵測 stdout；測試可顯式指定。
    """
    import threading
    import time

    from twquant.conventions import now_tw

    if tty is None:
        tty = sys.stdout.isatty()
    lock = threading.Lock()
    state = {"done": 0}
    t0 = time.monotonic()
    started = now_tw()
    step = max(1, total // 200)  # 非 TTY：~0.5% 粒度（全市場單月 ≈ 每 2 分鐘一行）

    def update(label: str, advance: int) -> None:
        with lock:
            state["done"] += advance
            done = state["done"]
            frac = done / total if total else 1.0
            bar = "█" * int(frac * 20) + "░" * (20 - int(frac * 20))
            elapsed = time.monotonic() - t0
            eta = "--:--:--"
            if done and total:
                remain = elapsed / done * (total - done)
                eta = (now_tw() + dt.timedelta(seconds=remain)).strftime("%H:%M:%S")
            line = (
                f"[{bar}] {frac * 100:5.1f}% {done}/{total}"
                f"｜{label}｜開始 {started:%H:%M:%S} 已用 {_fmt_hms(elapsed)}"
                f" 預估結束 {eta}"
            )
            if tty:
                typer.echo(f"\r{line}".ljust(100), nl=False)
            elif advance != 1 or done % step == 0 or done >= total:
                typer.echo(line)

    return update


@app.command(name="backfill-fubon-chip")
def backfill_fubon_chip(
    from_: str = typer.Option(..., "--from", help="起始日 YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="結束日 YYYY-MM-DD"),
    stocks: str = typer.Option(
        "", "--stocks", help="股票代號逗號分隔（如 2330,2317）；留空＝全市場"
    ),
    universe: str = typer.Option(
        r"^[1-9][0-9]{3}$", help="全市場模式的代號過濾（預設四碼、排除權證/ETF）"
    ),
    data_dir: str | None = typer.Option(
        None,
        "--data-dir",
        help="覆寫資料湖路徑（預設用 system.yaml；全市場模式需已有 stock_meta/行情）",
    ),
    skip_existing: bool = typer.Option(True, help="跳過已抓過的 (日, 股)（讀 PG 帳本續傳）"),
    workers: int | None = typer.Option(
        None, "--workers", help="平行抓取執行緒數；留空＝依限流 max_requests（configs/system.yaml）"
    ),
    probe: int = typer.Option(
        5,
        "--probe",
        min=0,
        help="非交易日探測：每日先抓前 N 檔，全數成功且皆無分點表 → 跳過整天（0=停用；D-024）",
    ),
    yes: bool = typer.Option(False, "--yes", help="全市場大量請求前不再確認"),
) -> None:
    """回補富邦DJ 券商分點買賣超 → **直寫 PG** broker_trades（免費爬蟲，D-019/D-025）。

    --stocks 指定個股；**留空＝全市場**（股票池讀資料湖 stock_meta，退回 prices_daily）。
    逐日→逐股抓取，每檔解析後**當場 upsert 進 PG**（serving.broker_trades）並在同一交易
    寫入抓取帳本 fubon_fetch_log——不落 raw HTML。日終自 PG 讀回該日完整資料補寫資料湖
    分區（clean/broker_trades part-fubon.parquet）：湖仍是因子層（PitView）與
    `serving rebuild` 的真相源，缺了它 rebuild 會清掉 PG 直寫的資料。

    **多程序併跑安全（D-025）**：帳本在 PG——每檔在「抓取當下」查 log（非日初快照，
    間隔最小），已被任一程序 commit 即跳過；帳本列存在 ⇔ 資料已入庫，故跳過永不漏抓；
    競態最壞情況是重抓一檔（upsert 冪等）。進度查詢：`twquant fubon-chip-status`。

    週六日不排入；平日假日由 --probe 探測跳過並標記（D-024）。進度列即時顯示目前
    日期/股號、整體百分比、起始/已用/預估結束時間。
    --workers 開執行緒池平行抓取（D-022），限流仍節流全域速率，故並行度上限＝
    source_rates.fubon.max_requests；留空即取該值。全市場 6 workers（6 req/2s）約 10 分/日。
    ⚠️ 免費公開站，並行度越高被封 IP 風險越高（參 D-015）；多程序各有「自己的」限流窗，
    總請求速率 = 程序數 × 6 req/2s，請自行斟酌。請求量大，>100 檔會先確認。
    """
    import threading

    from twquant.data.serving import chip_ingest, store
    from twquant.data.serving import schema as sch
    from twquant.data.sources import fubon_chip
    from twquant.data.warehouse import Warehouse

    start = dt.date.fromisoformat(from_)
    end = dt.date.fromisoformat(to)
    cfg = _load_system()
    wh = Warehouse(data_dir or cfg.data_dir)
    client = fubon_chip.browser_client(cfg.source_rates["fubon"])

    scfg = _load_serving()
    conn = store.connect(scfg)  # PG 直寫（D-025）；TWQUANT_PG_DSN 未設會 raise ConfigError
    sch.ensure_schema(conn, scfg.pg_schema)
    pg = scfg.pg_schema
    pg_lock = threading.Lock()  # 多 worker 共用單一連線：所有 PG 操作經此鎖序列化

    sids = [s.strip() for s in stocks.split(",") if s.strip()]
    mode = f"{len(sids)} 檔"
    if not sids:  # 全市場：從資料湖推導股票池
        sids = _fubon_universe(wh, universe)
        if not sids:
            typer.secho(
                "資料湖無 stock_meta/prices_daily 可推導股票池；先 backfill 或改用 --stocks 指定。",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        mode = f"全市場 {len(sids)} 檔"

    days = [d for d in _daterange(start, end) if d.weekday() < 5]  # 跳週末（非交易日自然空表）
    rate = cfg.source_rates["fubon"]
    # 限流即全域吞吐上限；並行度預設取 max_requests（超過亦被限流器節流，僅徒增排隊）
    n_workers = max(workers if workers is not None else rate.max_requests, 1)
    est_min = len(sids) * len(days) * (rate.per_seconds / max(rate.max_requests, 1)) / 60
    typer.secho(
        f"富邦DJ 分點回補 {start}~{end}｜{mode} × {len(days)} 平日"
        f"｜{n_workers} 執行緒｜PG 直寫｜約 {est_min:.0f} 分鐘",
        fg=typer.colors.CYAN,
        bold=True,
    )
    if len(sids) > 100 and not yes:
        typer.confirm(f"將對富邦DJ 發出約 {len(sids) * len(days)} 次請求，確定執行？", abort=True)

    progress = _make_progress(len(days) * len(sids))

    with conn:  # 結束（含例外）自動 commit/rollback + close
        for day in days:
            with pg_lock, conn.cursor() as cur:
                logged = chip_ingest.logged(cur, pg, day) if skip_existing else {}
            if chip_ingest.SENTINEL in logged:
                progress(f"{day} 非交易日(已標記)", len(sids))
                typer.echo("")
                typer.secho(f"  [{day}] 已標記非交易日，跳過", fg=typer.colors.YELLOW)
                continue
            if (
                skip_existing
                and not logged
                and wh.partition_exists("clean", "broker_trades", day, "fubon")
            ):
                # 無帳本紀錄但湖分區已在：帳本啟用前的舊回補，視為完成
                progress(f"{day} 已存在", len(sids))
                typer.echo("")
                typer.secho(f"  [{day}] 資料湖已存在（舊回補），跳過", fg=typer.colors.YELLOW)
                continue

            pending = [s for s in sids if s not in logged]
            if len(pending) < len(sids):
                progress(
                    f"{day} 續傳（已抓 {len(sids) - len(pending)} 檔）", len(sids) - len(pending)
                )

            def _tick(sid: str, _day: dt.date = day) -> None:
                progress(f"{_day} {sid}", 1)

            def _seen(sid: str, _day: dt.date = day) -> bool:
                # 抓取當下的逐檔帳本檢查（間隔最小）：他程序剛 commit 的檔立刻可見
                with pg_lock, conn.cursor() as cur:
                    return chip_ingest.is_logged(cur, pg, _day, sid)

            def _commit(sid: str, html: bytes, _day: dt.date = day) -> None:
                # 解析 → 同一交易 upsert 資料＋寫帳本 → commit：帳本列存在 ⇔ 資料已入庫
                df = fubon_chip.rows_to_canonical(fubon_chip.parse_broker_rows(html), sid, _day)
                with pg_lock, conn.cursor() as cur:
                    if not df.is_empty():
                        chip_ingest.upsert_broker_rows(cur, pg, df)
                    chip_ingest.mark(cur, pg, _day, sid, df.height)
                    conn.commit()

            skip_if = _seen if skip_existing else None

            # 假日探測（D-024）：該日尚無任何有資料紀錄時，pending 前 head_n 檔全空 → 非交易日
            has_data = any(n > 0 for n in logged.values())
            head_n = min(probe, len(pending)) if probe > 0 and not has_data else 0
            results = _fetch_fubon_day(
                client, pending[:head_n], day, n_workers, skip_if, _commit, _tick
            )
            if head_n and _probe_all_empty(results):
                with pg_lock, conn.cursor() as cur:
                    chip_ingest.mark(cur, pg, day, chip_ingest.SENTINEL, 0)
                    conn.commit()
                progress(f"{day} 非交易日", len(pending) - head_n)
                typer.echo("")
                typer.secho(
                    f"  [{day}] 探測前 {head_n} 檔皆無分點表（假日/非交易日），標記並跳過整天",
                    fg=typer.colors.YELLOW,
                )
                continue
            results += _fetch_fubon_day(
                client, pending[head_n:], day, n_workers, skip_if, _commit, _tick
            )

            typer.echo("")
            for sid, _html, err in results:
                if err is not None:
                    typer.secho(f"  [SKIP] {day} {sid}：{err}", fg=typer.colors.YELLOW)

            # 日終：自 PG 讀回該日完整資料（含他程序貢獻）→ 補寫資料湖分區（冪等）
            with pg_lock, conn.cursor() as cur:
                full = chip_ingest.read_day(cur, pg, day)
            if full.is_empty():
                typer.secho(f"  [{day}] 無分點資料（非交易日或全空）", fg=typer.colors.YELLOW)
                continue
            with pg_lock, conn.cursor() as cur:
                chip_ingest.clear_sentinel(cur, pg, day)  # 曾誤標非交易而今有資料 → 解除
                conn.commit()
            wh.write("clean", "broker_trades", full, source="fubon")
            typer.secho(
                f"  [OK] {day}：{full.height} 列（{full['stock_id'].n_unique()} 檔）已入 PG＋資料湖",
                fg=typer.colors.GREEN,
            )
    typer.secho("富邦DJ 分點回補完成。", fg=typer.colors.GREEN, bold=True)


@app.command(name="fubon-chip-status")
def fubon_chip_status(
    data_dir: str | None = typer.Option(
        None, "--data-dir", help="覆寫資料湖路徑（宇宙總數自 stock_meta/行情推導）"
    ),
    universe: str = typer.Option(
        r"^[1-9][0-9]{3}$", help="宇宙總數的代號過濾（與 backfill-fubon-chip 預設一致）"
    ),
) -> None:
    """富邦DJ 分點回補進度：每日已抓檔數／宇宙總數＋百分比（讀 PG fubon_fetch_log，D-025）。"""
    from twquant.data.serving import chip_ingest, store
    from twquant.data.serving import schema as sch
    from twquant.data.warehouse import Warehouse

    cfg = _load_system()
    scfg = _load_serving()
    with store.connect(scfg) as conn:
        sch.ensure_schema(conn, scfg.pg_schema)
        with conn.cursor() as cur:
            day_rows = chip_ingest.day_stats(cur, scfg.pg_schema)
    if not day_rows:
        typer.secho("尚無抓取紀錄（fubon_fetch_log 為空）。", fg=typer.colors.YELLOW)
        return
    total = len(_fubon_universe(Warehouse(data_dir or cfg.data_dir), universe))
    for line in _fubon_status_lines(day_rows, total):
        typer.echo(line)
    fetched_days = sum(1 for _, _, _, holiday in day_rows if not holiday)
    holidays = len(day_rows) - fetched_days
    typer.secho(
        f"共 {len(day_rows)} 日（交易日 {fetched_days}、標記非交易日 {holidays}）｜宇宙 {total} 檔",
        fg=typer.colors.CYAN,
        bold=True,
    )


@app.command(name="chip-daily")
def chip_daily(
    date: str = typer.Option("today", help="以哪一天為「今天」YYYY-MM-DD 或 today"),
) -> None:
    """單獨跑每日 pipeline 的「籌碼」步驟（T-310）：官方當沖／借券、處置／注意公告 → 直寫 PG。

    當沖／借券抓這天之前的第 k 個交易日（system.yaml `chip_daily.refetch_offsets`）＋缺日；
    公告抓以這天為準的查詢窗。與 pipeline 同一個函數，平常不用手跑。
    """
    from twquant.conventions import now_tw
    from twquant.errors import DataError

    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)
    try:
        summary = _run_chip_day(day)
    except DataError as exc:
        typer.secho(f"[FAILED] {exc}", fg=typer.colors.RED, bold=True)
        raise typer.Exit(1) from exc
    typer.secho(f"[OK] 籌碼 @ {day}：{summary}", fg=typer.colors.GREEN, bold=True)


def _seconds_until(now: dt.datetime, hh: int, mm: int) -> float:
    """距離下一個 hh:mm（同時區）的秒數；已過今日該時刻則算明日。"""
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


@app.command()
def scheduler(
    at: str = typer.Option("19:00", help="每日觸發時刻 HH:MM（台北）"),
    catch_up: bool = typer.Option(
        True, "--catch-up/--no-catch-up", help="啟動時若今天尚未跑過且已過 at，立刻補跑（T-275）"
    ),
) -> None:
    """常駐排程：每日於 at 時刻跑完整 pipeline（擷取→品質閘門→券商買賣超→訊號→供應層→選股池快照）。

    預設 19:00：券商買賣超（富邦DJ 分點）傍晚才公布，太早跑會抓空（2026-07-16 事件）；
    行情/法人/融資券那時早已齊。若當日 19:00 仍未公布，券商步驟只回「稍後重試」不污染
    當日（不再標 SENTINEL），晚點一鍵補跑即可。券商買賣超/供應層/選股池快照皆 best-effort
    （PG 不可達或來源未公布不影響擷取/訊號主線，見 _run_daily_pipeline）；單日整體失敗不中斷排程。
    """
    import time

    from twquant.conventions import now_tw
    from twquant.pipeline import read_heartbeat

    hh, mm = (int(x) for x in at.split(":"))
    typer.secho(
        f"排程啟動：每日 {at} (Asia/Taipei) 觸發完整 pipeline", fg=typer.colors.CYAN, bold=True
    )
    # T-275：啟動時若今天的 pipeline 還沒跑過就立刻補跑。
    # 原本只算「下一個 hh:mm」，於是容器在觸發時刻之後重啟就等到明天——
    # 那天會有原始資料卻沒有因子／模型分數／選股名單，且隔天只算隔天，洞不會自己補。
    # 2026-09-21、09-22 實際發生（factor_values 停在 09-18，使用者回報「都沒有跑」）。
    # 判據用 heartbeat（pipeline 成功才寫），故重啟多次不會重跑同一天。
    if catch_up:
        today = now_tw().date()
        hb = read_heartbeat(_load_system().data_dir)
        if hb is not None and hb.day == today.isoformat():
            typer.secho(f"啟動補跑：{today} 已有 heartbeat，略過", fg=typer.colors.CYAN)
        elif _seconds_until(now_tw(), hh, mm) > 23 * 3600:
            typer.secho(f"啟動補跑：{today} 尚未跑過且已過 {at}，立即執行", fg=typer.colors.YELLOW)
            try:
                _echo_pipeline_result(_run_daily_pipeline(today))
            except Exception as exc:  # noqa: BLE001  # 補跑失敗不擋排程主線
                typer.secho(f"[啟動補跑失敗] {exc}", fg=typer.colors.RED)
    while True:
        delay = _seconds_until(now_tw(), hh, mm)
        typer.secho(f"下次觸發：{delay / 3600:.1f} 小時後", fg=typer.colors.CYAN)
        time.sleep(delay)
        try:
            _echo_pipeline_result(_run_daily_pipeline(now_tw().date()))
        except Exception as exc:  # noqa: BLE001  # 排程不因單日失敗而死；記錄後續跑
            typer.secho(f"[排程錯誤] {exc}", fg=typer.colors.RED)


@app.command(name="ingest-revenue")
def ingest_revenue(
    month: str = typer.Option(..., help="營收月份 YYYY-MM（例如 2026-05）"),
) -> None:
    """抓某月 MOPS 月營收（上市+上櫃）、清洗、存入 clean 資料湖。"""
    for ok, msg in _ingest_revenue_month(month):
        typer.secho(msg, fg=typer.colors.GREEN if ok else typer.colors.YELLOW)


def _ingest_revenue_month(month: str) -> list[tuple[bool, str]]:
    """月營收（上市＋上櫃）抓→清洗→存 clean 資料湖；回傳每個市場的 (成功, 訊息)。
    單一市場抓不到（例：還沒公布）記進 ingest log、不中斷另一個市場。每日 pipeline 的「月營收」步驟也走這裡。"""
    from twquant.data.clean import clean_monthly_revenue
    from twquant.data.http import HttpClient
    from twquant.data.sources.mops import MopsSource
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.reporting.ingest_log import record

    year, mon = (int(x) for x in month.split("-"))
    roc = year - 1911
    cfg = _load_system()
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36"
    src = MopsSource(client=HttpClient(source="mops", rate=cfg.source_rates["mops"], user_agent=ua))
    wh = Warehouse(cfg.data_dir)
    out: list[tuple[bool, str]] = []
    for market in ["sii", "otc"]:
        typer.secho(f">> [{market}] 月營收 {month}", fg=typer.colors.CYAN)
        try:
            raw = src.fetch_month(roc, mon, market)
            clean = clean_monthly_revenue(raw, roc, mon)
            wh.write("clean", "monthly_revenue", clean, source=f"mops_{market}")
        except DataError as exc:
            record(
                cfg.data_dir, f"mops_{market}", "monthly_revenue", month, "failed", detail=str(exc)
            )
            out.append((False, f"[SKIP] {market}：{exc}"))
            continue
        record(
            cfg.data_dir, f"mops_{market}", "monthly_revenue", month, "success", rows=clean.height
        )
        out.append((True, f"[OK] {market} {month}：{clean.height} 家已存入"))
    return out


@app.command(name="ingest-index")
def ingest_index(
    month: str = typer.Option(..., help="指數月份 YYYY-MM（例如 2026-07）"),
) -> None:
    """抓某月加權指數（TWSE）+ 櫃買指數（TPEx），清洗、存入 index_daily。"""
    from twquant.data.clean import clean_index_tpex, clean_index_twse
    from twquant.data.http import HttpClient
    from twquant.data.sources.indices import TpexIndexSource, TwseIndexSource
    from twquant.data.sources.tpex import browser_client
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.reporting.ingest_log import record

    year, mon = (int(x) for x in month.split("-"))
    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    twse = TwseIndexSource(HttpClient(source="twse_rwd", rate=cfg.source_rates["twse_rwd"]))
    tpex = TpexIndexSource(browser_client(cfg.source_rates["tpex"]))
    plans: list[
        tuple[str, TwseIndexSource | TpexIndexSource, Callable[[pl.DataFrame], pl.DataFrame]]
    ] = [
        ("twse", twse, clean_index_twse),
        ("tpex", tpex, clean_index_tpex),
    ]

    for tag, src, cleaner in plans:
        typer.secho(f">> [{tag}] 指數 {month}", fg=typer.colors.CYAN)
        try:
            raw = src.fetch_month(year, mon)
            clean = cleaner(raw)
            wh.write("clean", "index_daily", clean, source=tag)
        except DataError as exc:
            record(cfg.data_dir, tag, "index_daily", month, "failed", detail=str(exc))
            typer.secho(f"[SKIP] {tag}：{exc}", fg=typer.colors.YELLOW)
            continue
        record(cfg.data_dir, tag, "index_daily", month, "success", rows=clean.height)
        typer.secho(f"[OK] {tag} {month}：{clean.height} 日已存入", fg=typer.colors.GREEN)


@app.command(name="backfill-index")
def backfill_index(
    from_: str = typer.Option(..., "--from", help="起始月 YYYY-MM"),
    to: str = typer.Option(..., "--to", help="結束月 YYYY-MM"),
) -> None:
    """回補一段期間的加權/櫃買指數（逐月 ingest-index）。指數端點不受 D-015 封鎖。"""
    fy, fm = (int(x) for x in from_.split("-"))
    ty, tm = (int(x) for x in to.split("-"))
    months: list[str] = []
    y, m = fy, fm
    while (y, m) <= (ty, tm):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    typer.secho(f"回補指數 {from_}~{to}：{len(months)} 個月", fg=typer.colors.CYAN, bold=True)
    for month in months:
        ingest_index(month=month)


@app.command(name="ingest-meta")
def ingest_meta() -> None:
    """抓上市+上櫃個股基本資料（中文名、產業別/族群），存入 stock_meta（緩變維度表）。"""
    from twquant.data.clean import clean_stock_meta
    from twquant.data.http import HttpClient
    from twquant.data.sources.twse_isin import StockMetaSource
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.reporting.ingest_log import record

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    src = StockMetaSource(HttpClient(source="twse_rwd", rate=cfg.source_rates["twse_rwd"]))
    # (來源標籤, strMode, 市場別)
    plans = [("twse", 2, "TWSE"), ("tpex", 4, "TPEX")]
    for tag, mode, market in plans:
        typer.secho(f">> [{tag}] 個股基本資料 strMode={mode}", fg=typer.colors.CYAN)
        try:
            raw = src.fetch(mode)
            clean = clean_stock_meta(raw, market)
            wh.write("clean", "stock_meta", clean, source=tag)
        except DataError as exc:
            record(cfg.data_dir, tag, "stock_meta", "_all", "failed", detail=str(exc))
            typer.secho(f"[SKIP] {tag}：{exc}", fg=typer.colors.YELLOW)
            continue
        record(cfg.data_dir, tag, "stock_meta", "_all", "success", rows=clean.height)
        typer.secho(f"[OK] {tag}：{clean.height} 檔已存入", fg=typer.colors.GREEN)


@app.command(name="ingest-holidays")
def ingest_holidays() -> None:
    """抓 TWSE 官方『市場開休市日期』（當前公布年度），存入 market_holidays，並與觀測日曆核對。

    此端點無年度參數、只回當前已公布年度；重抓覆蓋同表。抓後印休市概況、近期休市日，以及
    官方交易日曆 vs 觀測行情（prices_daily）在該年度內的差異，直接回答「哪天開市/該補哪天」。
    """
    from typing import cast

    import polars as pl

    from twquant.conventions import now_tw
    from twquant.data import calendar as cal
    from twquant.data.clean import clean_market_holidays
    from twquant.data.http import HttpClient
    from twquant.data.sources.twse_holiday import TwseHolidaySource
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.reporting.ingest_log import record

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    src = TwseHolidaySource(HttpClient(source="twse_holiday", rate=cfg.source_rates["twse_rwd"]))
    typer.secho(">> [twse] 官方市場開休市日期", fg=typer.colors.CYAN, bold=True)
    try:
        clean = clean_market_holidays(src.fetch())
        wh.write("clean", "market_holidays", clean, source="twse")
    except DataError as exc:
        record(cfg.data_dir, "twse", "market_holidays", "_all", "failed", detail=str(exc))
        typer.secho(f"[SKIP] {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    record(cfg.data_dir, "twse", "market_holidays", "_all", "success", rows=clean.height)

    n_closed = clean.filter(pl.col("closed")).height
    years = sorted({int(y) for y in clean["year"].to_list()})
    typer.secho(
        f"[OK] {clean.height} 筆（休市 {n_closed}、交易日註記 {clean.height - n_closed}）· 年度 {years}",
        fg=typer.colors.GREEN,
        bold=True,
    )

    offical = cal.official_calendar(wh)
    typer.secho(
        f"官方交易日曆：{offical.sessions[0]} ~ {offical.sessions[-1]}　{len(offical)} 開市日",
        fg=typer.colors.CYAN,
    )
    today = now_tw().date()
    upcoming = (
        clean.filter(pl.col("closed") & (pl.col("holiday_date") >= today))
        .sort("holiday_date")
        .head(6)
    )
    if not upcoming.is_empty():
        typer.secho("近期休市：", fg=typer.colors.CYAN)
        for r in upcoming.to_dicts():
            typer.echo(f"  {r['holiday_date']}（{r['weekday']}）{r['name']}")

    chk = cal.cross_check_official(wh)
    missing = cast("list[dt.date]", chk["missing"])
    unexpected = cast("list[dt.date]", chk["unexpected"])
    if chk["range"] is None:
        typer.secho("（尚無行情，略過官方 vs 觀測核對）", fg=typer.colors.YELLOW)
    elif not missing and not unexpected:
        typer.secho("官方 vs 觀測：涵蓋年度內完全一致。", fg=typer.colors.GREEN)
    else:
        if missing:
            head = ", ".join(str(d) for d in missing[:5])
            typer.secho(f"官方開市卻無行情 {len(missing)} 日：{head}", fg=typer.colors.YELLOW)
        if unexpected:
            head = ", ".join(str(d) for d in unexpected[:5])
            typer.secho(f"有行情卻官方判休市 {len(unexpected)} 日：{head}", fg=typer.colors.YELLOW)


def _git_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001  # 無 git 時給占位，不影響 pipeline
        return "unknown"


def _run_fubon_day(day: dt.date) -> str:
    """單日富邦DJ 全市場券商分點 → PG broker_trades ＋資料湖（每日 pipeline 尾波共用）。

    `backfill-fubon-chip` 的單日 best-effort 精簡版：非互動（無確認/進度列）、
    skip_existing 續傳（讀 PG 帳本）、探測分點是否已公布——**但不寫 SENTINEL**：太早跑
    （分點未公布）只回「稍後重試」、不污染當日，晚點排程/一鍵會自動補齊（2026-07-16 事件
    修正）。回傳摘要字串；PG 未啟用/不可達或無宇宙 → 回略過訊息（不 raise）。四碼普通股
    ~1960 檔、workers＝限流 max_requests 約 10 分鐘——排在選股池快照前，讓快照吃到當日
    主力買賣超（T-040 五池依賴 broker_trades）。
    """
    if day.weekday() >= 5:  # 週末先擋（避免在無 libpq 環境仍 import psycopg）
        return "週末，跳過"

    import threading

    from twquant.data.serving import chip_ingest, store
    from twquant.data.serving import schema as sch
    from twquant.data.sources import fubon_chip
    from twquant.data.warehouse import Warehouse

    scfg = _load_serving()
    if not scfg.enabled:
        return "未啟用（serving.yaml enabled=false）"
    if not store.ping(scfg):  # DSN 未設或不可達 → best-effort 略過，不擋主線
        return "PG 不可達，略過"
    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    sids = _fubon_universe(wh, r"^[1-9][0-9]{3}$")  # 四碼普通股（排除權證/ETF）
    if not sids:
        return "資料湖無宇宙可推導，略過"
    rate = cfg.source_rates["fubon"]
    n_workers = max(rate.max_requests, 1)
    client = fubon_chip.browser_client(rate)
    pg = scfg.pg_schema
    pg_lock = threading.Lock()  # 多 worker 共用單一連線：PG 操作經此鎖序列化

    with store.connect(scfg) as conn:
        sch.ensure_schema(conn, pg)
        with pg_lock, conn.cursor() as cur:
            logged = chip_ingest.logged(cur, pg, day)
        # 太早跑污染防護（2026-07-16 事件）：pipeline 版**不讀也不寫 SENTINEL**——太早跑
        # （分點尚未公布）只是「稍後重試」，非「非交易日」；舊碼會標 SENTINEL 讓當日永久
        # 跳過，晚點排程/一鍵補跑也被擋。SENTINEL 的假日標記留給明示的 backfill 指令。
        if not logged and wh.partition_exists("clean", "broker_trades", day, "fubon"):
            return "資料湖已存在（舊回補），跳過"
        pending = [s for s in sids if s not in logged]
        if not pending:
            return "當日已全數抓過（續傳完成）"

        def _seen(sid: str) -> bool:
            with pg_lock, conn.cursor() as cur:
                return chip_ingest.is_logged(cur, pg, day, sid)

        def _commit(sid: str, html: bytes) -> None:
            df = fubon_chip.rows_to_canonical(fubon_chip.parse_broker_rows(html), sid, day)
            with pg_lock, conn.cursor() as cur:
                if not df.is_empty():
                    chip_ingest.upsert_broker_rows(cur, pg, df)
                chip_ingest.mark(cur, pg, day, sid, df.height)
                conn.commit()

        # 探測當日分點是否已公布：抓前 N 檔但**不落地**（on_fetched=None，不 mark）；全空＝
        # 尚未公布或非交易日 → 直接返回、不寫任何紀錄，稍後（晚點排程/一鍵）自動重試。
        has_data = any(n > 0 for n in logged.values())
        head_n = 0 if has_data else min(5, len(pending))
        if head_n:
            probe = _fetch_fubon_day(client, pending[:head_n], day, n_workers, _seen)
            if _probe_all_empty(probe):
                return "尚無分點資料（未公布或非交易日），未落地、稍後自動重試"
        # 有資料 → 全量抓並落地（含探測過的前 N 檔，因探測未落地故一併重抓）
        _fetch_fubon_day(client, pending, day, n_workers, _seen, _commit)
        with pg_lock, conn.cursor() as cur:
            full = chip_ingest.read_day(cur, pg, day)
        if full.is_empty():
            return "無分點資料（非交易日或全空）"
        with pg_lock, conn.cursor() as cur:
            chip_ingest.clear_sentinel(cur, pg, day)  # 曾誤標而今有資料 → 解除
            conn.commit()
        wh.write("clean", "broker_trades", full, source="fubon")  # 湖為 PitView/rebuild 真相源
        return f"{full.height} 列（{full['stock_id'].n_unique()} 檔）入 PG＋資料湖"


def _catchup_lake_days(
    gaps: dict[str, list[dt.date]],
    announce_for: Callable[[dt.date], Callable[[str, str], None]],
) -> tuple[list[str], list[str]]:
    """T-327：資料湖表的缺日逐日重抓（舊到新）→ (摘要片段, 失敗)。

    落地照舊走資料湖，同一輪的「供應層」步驟再鏡射進 PG（籌碼步驟排在它前面）。
    缺日由 `direct_ingest.lake_gap_days` 從 PG 算出；這裡只負責抓。
    """
    import twquant.data.ingest.builtins  # noqa: F401  # 匯入觸發來源註冊
    from twquant.data.ingest.orchestrator import run_ingest
    from twquant.data.ingest.registry import list_specs
    from twquant.data.serving.schema import DIRECT_PG_TABLES
    from twquant.data.warehouse import Warehouse
    from twquant.errors import ConfigError

    direct = sorted(set(gaps) & set(DIRECT_PG_TABLES))
    if direct:
        raise ConfigError(
            f"chip_daily.lake_catchup_tables 只收資料湖表；{direct} 是直寫 PG 的表（由籌碼步驟的計畫補）"
        )
    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    specs = [s for s in list_specs(cadence="daily") if s.store == "lake"]
    parts: list[str] = []
    failures: list[str] = []
    for table, days in gaps.items():
        got: list[tuple[dt.date, int]] = []
        for d in days:
            outs = run_ingest(
                specs,
                cfg,
                wh,
                d,
                data_dir=cfg.data_dir,
                tables=[table],
                tolerant=True,
                on_start=announce_for(d),
                retries=2,
            )
            if not outs:
                failures.append(f"{table} {d}（缺日）：沒有任何資料湖來源提供這張表")
            for o in outs:
                if o.status == "success":
                    got.append((d, o.rows))
                else:
                    failures.append(f"{o.table} {d}（缺日）：{o.detail}")
        if got:
            listed = "、".join(str(d) for d, _ in got)
            parts.append(f"{table} 缺日補 {len(got)} 天 {sum(n for _, n in got):,} 列（{listed}）")
    return parts, failures


def _run_chip_day(day: dt.date) -> str:
    """每日 pipeline「籌碼」步驟（T-310）：官方當沖／借券、處置／注意公告 → 直寫 PG。

    - 當沖／借券：抓「前第 k 個交易日」（system.yaml `chip_daily.refetch_offsets`，覆寫）＋最近
      `catchup_trading_days` 個交易日裡某市場一列都沒有的日子。**當天不抓**：借券 20:30／22:30
      才更新、當沖晚間才公布，19:00 的排程抓不到；兩表 available_at 都是同日 21:00，隔天 19:00
      抓進來仍早於 20:00 訊號截點。
    - 當沖在 T+1 那一次另存首次值（daytrade_first_pub；只有 day＝今天那一輪），T+2／T+3 重抓後回報與首次值的差
      （D-154 §三）。
    - 公告：查一個窗（`alerts.alert_window`），窗內舊列 upsert 冪等、內容變了就 raise（D-154 §二.6）。
    - 資料湖表（`chip_daily.lake_catchup_tables`，T-327）：同一個補抓窗裡 PG 一列都沒有的交易日重抓
      進資料湖（融資融券常晚於 19:00 才公布，當天那一次抓不到）。

    任一抓取失敗 → 全部跑完後 raise（步驟標 failed，訊息列出哪幾個）；PG 未啟用／不可達 → 回略過訊息。
    """
    import twquant.data.ingest.builtins as src_builtins
    from twquant.conventions import now_tw
    from twquant.data.ingest.orchestrator import run_ingest
    from twquant.data.ingest.registry import get
    from twquant.data.serving import direct_ingest as di
    from twquant.data.serving import schema as sch
    from twquant.data.serving import store
    from twquant.errors import DataError

    scfg = _load_serving()
    if not scfg.enabled:
        return "未啟用（serving.yaml enabled=false）"
    if not store.ping(scfg):
        return "PG 不可達，略過"
    cfg = _load_system()
    pg = scfg.pg_schema

    def _announce_for(d: dt.date) -> Callable[[str, str], None]:
        def _announce(tag: str, table: str) -> None:
            typer.secho(f">> [{tag}] 抓取 {table} @ {d}", fg=typer.colors.CYAN)

        return _announce

    done: list[tuple[di.ChipFetch, int]] = []
    failures: list[str] = []
    notes: list[str] = []
    alert_rows: dict[str, int] = {}
    with store.connect(scfg) as conn:
        sch.ensure_tables(conn, sch.DIRECT_INGEST_SPECS, pg)
        with conn.cursor() as cur:
            # 只有「今天」那一輪的 T+1 抓取算首次值；手動重跑過去的日子抓到的可能已是定案值
            plan = di.plan_chip_fetches(cur, pg, day, cfg.chip_daily, live=day >= now_tw().date())
        conn.commit()
        batches: dict[tuple[dt.date, str], list[di.ChipFetch]] = {}
        for f in plan:
            batches.setdefault((f.day, f.market), []).append(f)
        for (d, market), fetches in sorted(batches.items()):
            by_table = {f.table: f for f in fetches}
            sink = di.PgDirectSink(
                conn,
                pg,
                keep_first=any(f.first for f in fetches),
                market=di.STOCK_META_MARKET[market],
            )
            outs = run_ingest(
                [get(src_builtins.CHIP_SOURCES[market])],
                cfg,
                sink,
                d,
                data_dir=cfg.data_dir,
                tables=list(by_table),
                tolerant=True,
                on_start=_announce_for(d),
                retries=2,
            )
            notes += sink.notes
            for o in outs:
                f = by_table[o.table]
                if o.status == "success":
                    done.append((f, o.rows))
                else:
                    failures.append(f"{o.table} {d} {market}（{f.reason}）：{o.detail}")
        sink = di.PgDirectSink(conn, pg)
        outs = run_ingest(
            [get(tag) for tag in src_builtins.ALERT_SOURCES.values()],
            cfg,
            sink,
            day,
            data_dir=cfg.data_dir,
            tolerant=True,
            on_start=_announce_for(day),
            retries=2,
        )
        for o in outs:
            if o.status == "success":
                alert_rows[o.table] = alert_rows.get(o.table, 0) + o.rows
            else:
                failures.append(f"{o.table} {o.source_tag}（查詢窗）：{o.detail}")
        refetched = sorted({f.day for f, _ in done if f.table == "daytrade_daily" and not f.first})
        with conn.cursor() as cur:
            revisions = [di.daytrade_revision(cur, pg, d) for d in refetched]
            lake_gaps = {
                t: di.lake_gap_days(cur, pg, t, day, cfg.chip_daily.catchup_trading_days)
                for t in cfg.chip_daily.lake_catchup_tables
            }
        conn.commit()
    lake_parts, lake_failures = _catchup_lake_days(lake_gaps, _announce_for)
    failures += lake_failures

    parts: list[str] = []
    for table, label in (("daytrade_daily", "當沖"), ("sbl_short_daily", "借券")):
        got = [(f, n) for f, n in done if f.table == table]
        if got:
            days = sorted({f.day for f, _ in got})
            span = f"{days[0]}" if len(days) == 1 else f"{days[0]}~{days[-1]}"
            parts.append(f"{label} {len(got)} 批 {sum(n for _, n in got):,} 列（{span}）")
        else:
            parts.append(f"{label} 0 批")
    gaps = [f for f, _ in done if f.reason == "缺日"]
    if gaps:
        parts.append(f"缺日補 {len(gaps)} 批（{sorted({str(f.day) for f in gaps})}）")
    parts.append(
        f"處置 {alert_rows.get('disposition_stocks', 0):,}／注意 {alert_rows.get('attention_stocks', 0):,} 列"
        "（查詢窗，冪等）"
    )
    parts += lake_parts
    for r in revisions:
        if r.compared:
            ex = f"，例 {'、'.join(r.examples)}" if r.examples else ""
            parts.append(
                f"當沖 {r.day} 首次值 {r.compared:,} 列中 {r.changed} 列改過"
                f"（股數差 {r.shares_abs_diff:,}／{r.shares_total:,}{ex}）"
            )
    for note in notes:
        typer.secho(f"[NOTE] {note}", fg=typer.colors.YELLOW)
    summary = "；".join(parts) + (f"；注意：{'；'.join(notes)}" if notes else "")
    if failures:
        raise DataError(
            "chip_daily", day, f"{summary}｜失敗 {len(failures)}：{'；'.join(failures)}"
        )
    return summary


def _load_fund_flow() -> tuple[FundFlowConfig, EtfMetaConfig]:
    """configs/fund_flow.yaml 與它指到的 etf_meta.yaml（SPEC-016）。"""
    from twquant.config import EtfMetaConfig, FundFlowConfig, load_config

    cfg = load_config(_CONFIG_DIR / "fund_flow.yaml", FundFlowConfig)
    meta = load_config(_CONFIG_DIR.parent / cfg.etf_meta_path, EtfMetaConfig)
    return cfg, meta


def _run_etf_units_day(day: dt.date) -> str:
    """每日 pipeline「ETF 單位數」步驟（T-335／SPEC-016）：TWSE MI_QFIIS（ETF 類）→ `etf_units_daily`。

    補 day 之前最近 `units.catchup_trading_days` 個交易日裡 PG 還沒有的日子；當天不抓（21:00 才公布）。
    任一天缺任一檔 configs/etf_meta.yaml 的 ETF → raise、整批不寫（步驟標 failed，不擋訊號主線）。
    """
    from twquant.data.http import HttpClient
    from twquant.data.serving import fund_flow_ingest as ffi
    from twquant.data.serving import store
    from twquant.data.sources.etf_units import fetch_qfiis_units

    scfg = _load_serving()
    if not scfg.enabled:
        return "未啟用（serving.yaml enabled=false）"
    if not store.ping(scfg):
        return "PG 不可達，略過"
    cfg, meta = _load_fund_flow()
    sys_cfg = _load_system()
    client = HttpClient(source="twse_rwd", rate=sys_cfg.source_rates["twse_rwd"])

    def _fetch(d: dt.date) -> list[dict[str, object]]:
        typer.secho(f">> [ETF 單位數] 抓取 MI_QFIIS @ {d}", fg=typer.colors.CYAN)
        return fetch_qfiis_units(client, d)

    with store.connect(scfg) as conn:
        res = ffi.ingest_recent_units(conn, scfg.pg_schema, cfg, meta, day, _fetch)
    if not res.fetched:
        return f"最近 {len(res.targets)} 個交易日都已有（{res.targets[-1] if res.targets else '—'} 止）"
    tail = f"；單日比值異常 {len(res.jumps)} 列（只報不修）：{res.jumps[:3]}" if res.jumps else ""
    return f"補 {len(res.fetched)} 天（{res.fetched[0]}～{res.fetched[-1]}）、{res.rows} 列{tail}"


def _run_etf_holdings_day(day: dt.date) -> str:
    """每日 pipeline「ETF 持股」步驟（T-336／SPEC-016）：元大、統一的每日持股清單 → `etf_holdings_daily`。

    補 day 之前最近 `holdings.catchup_trading_days` 個交易日裡 PG 還沒有的 (ETF, 基準日)。
    全有或全無：任一份抓不到（來源缺那天、權重和不對）→ raise、一列都不寫（步驟標 failed，不擋主線）。
    """
    from twquant.data.http import HttpClient
    from twquant.data.serving import fund_flow_ingest as ffi
    from twquant.data.serving import store
    from twquant.data.sources import etf_pcf

    scfg = _load_serving()
    if not scfg.enabled:
        return "未啟用（serving.yaml enabled=false）"
    if not store.ping(scfg):
        return "PG 不可達，略過"
    cfg, meta = _load_fund_flow()
    yuanta = HttpClient(source="yuanta_pcf", rate=_load_system().source_rates["twse_rwd"])
    ez = etf_pcf.EzmoneySession(pace_seconds=cfg.holdings.pace_seconds)
    fetchers: dict[str, ffi.QueryFetch] = {
        "元大": lambda etf_id, q: etf_pcf.fetch_yuanta(yuanta, etf_id, q),
        "統一": ez.fetch,
    }
    with store.connect(scfg) as conn:
        res = ffi.ingest_recent_holdings(conn, scfg.pg_schema, cfg, meta, day, fetchers)
        if not res.fetched:
            return f"最近 {len(res.window)} 個交易日都已有"
        n_events = ffi.rebuild_rebalance(conn, scfg.pg_schema, meta)  # T-337：換股事件跟著重算
    days = sorted({b for _, b in res.fetched})
    return (
        f"補 {len(res.fetched)} 份（{days[0]}～{days[-1]}）、{res.rows} 列；"
        f"換股事件重算共 {n_events} 筆"
    )


def _run_daily_pipeline(
    day: dt.date, pg_signals: bool = False, cloud: bool = False
) -> PipelineResult:
    """每日營運 pipeline：擷取→品質閘門→券商買賣超→訊號→供應層→選股池快照，寫 heartbeat（LOOP §3）。

    供 `pipeline` CLI 一次性指令與 `scheduler` 常駐迴圈共用（T-023：排程改跑完整
    pipeline，不再只做裸擷取）。券商買賣超／供應層／選股池快照皆 best-effort（display-only，
    PG 不可達或來源失敗不影響擷取/訊號主線）；回傳 twquant.pipeline.PipelineResult。

    pg_signals=True（T-057，GHA 雲端模式）：訊號生成改以 PgTableSource 讀 PG 供應層——
    雲端 runner 的資料湖是 ephemeral（只有當日擷取），歷史回看必須來自 PG 滾動窗口。

    cloud=True（T-332／D-170）：`serving.yaml` 的 `cloud.skip_pipeline_steps` 列到的步驟記為略過。
    """
    from twquant.conventions import now_tw
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.pipeline import PipelineStep, run_pipeline, with_skips
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import load_strategy

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)

    ingest_failed: list[str] = []

    def _ingest_step() -> str:
        return f"{_ingest_date(day, None, tolerant=True, failed=ingest_failed)} 表"

    def _gate_step() -> str:
        from twquant.pipeline import missing_required

        latest = wh.latest_date("prices_daily")
        if latest != day:  # 訊號前硬性斷言：最新行情日 == 今日（D-008）
            raise DataError("prices_daily", day, f"最新行情日 {latest} ≠ 今日 {day}")
        # D-175：提早跑時有的表可能還沒公布；缺必要表就擋下，不拿缺欄特徵打分
        miss = missing_required(cfg.quality_gate.required_tables, ingest_failed)
        if miss:
            raise DataError(
                "ingest", day, f"當天必要的表沒抓到（可能還沒公布）：{miss}；交給下一輪"
            )
        return f"latest={latest}"

    def _signal_step() -> str:
        strat = load_strategy(_CONFIG_DIR / "strategies" / "sample_revenue_trust.yaml")
        if pg_signals:  # 雲端模式：歷史回看來自 PG 滾動窗口（T-046 build 已收 TableSource）
            from twquant.data.serving import store
            from twquant.data.serving.pg_view import PgTableSource

            scfg = _load_serving()
            conn = store.connect(scfg)
            try:
                n = build_targets(strat, PgTableSource(conn, scfg.pg_schema), day).height
            finally:
                conn.close()
            return f"{n} 檔目標（PG 源）"
        return f"{build_targets(strat, wh, day).height} 檔目標"

    def _index_step() -> str:
        # T-227：指數端點是**月頻**（ingest-index --month），從來沒被接進每日迴圈，
        # 於是 index_daily 會一路落後 prices_daily 直到有人手動 backfill-index。
        # 實際後果不是「少一張表」而是**靜默降級**：price.rs_market_20d 整欄空，
        # LightGBM 把缺欄當 NaN 照樣吐分數，ml score 回報 OK。2026-09-02~09-09 就是這樣。
        # 只在該日指數尚未進來時才重抓當月（月頻端點，冪等 upsert），已有就不動網路。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        conn = store.connect(scfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT COUNT(*) FROM "{scfg.pg_schema}"."index_daily" WHERE date = %s',
                    (day,),
                )
                row = cur.fetchone()
                got = int(row[0]) if row else 0
        finally:
            conn.close()
        if got:
            return f"{day} 已有 {got} 筆，不重抓"
        ingest_index(month=f"{day:%Y-%m}")
        return f"已重抓 {day:%Y-%m} 當月指數"

    def _revenue_step() -> str:
        # 本機排程原本沒抓月營收：9 月營收（10-10 公布）進了雲端卻沒進本機，10-12 兩邊名單分歧。
        # 抓上個月（冪等）；某市場還沒公布只記一筆、不擋主線。排在「供應層」前，同步才帶得進 PG。
        from twquant.pipeline import revenue_month

        res = _ingest_revenue_month(revenue_month(day))
        if not any(ok for ok, _ in res):
            raise DataError("monthly_revenue", day, "；".join(m for _, m in res))
        return "；".join(m for _, m in res)

    def _serving_step() -> str:
        # 尾波：鏡射同步 + 物化當日因子 → PG 供應層（display-only，best-effort）。
        # 物化讀取源＝PG 鏡射（PgTableSource，D-026/T-057）：與 CLI serving materialize
        # 一致；本機資料湖歷史不完整（D-026 發現湖空窗），因子回看必須來自 PG。
        from twquant.data.serving import materialize as smat
        from twquant.data.serving import store
        from twquant.data.serving import sync as ssync

        scfg = _load_serving()
        if not scfg.enabled:
            return "未啟用（serving.yaml enabled=false）"
        if not store.ping(scfg):
            return "PG 不可達，略過（display-only）"
        ssync.sync_all(wh, scfg)
        conn = store.connect(scfg)
        try:
            # since＋cache 缺一不可：沒有 since 每張表都全掃（ml_scores 3,283 萬列），
            # 沒有 cache 則每個因子各掃一次。兩者都沒給時，單日物化會卡住（T-278）。
            import twquant.factors.builtins  # noqa: F401  # 觸發註冊，才問得到 lookback
            from twquant.factors.registry import list_factors

            names = [n for n in list_factors() if n not in set(scfg.exclude_factors)]
            m = smat.materialize_day(_daily_pg_source(conn, scfg.pg_schema, day, names), scfg, day)
        finally:
            conn.close()
        return f"鏡射同步 + 物化 {m.factor_rows} 因子列"

    def _broker_step() -> str:
        # 券商買賣超（富邦DJ 全市場分點，~10 分）：排在選股池快照前，讓快照吃到當日主力
        # 買賣超（T-040 五池依賴 broker_trades）。best-effort：非交易日由探測快速跳過、
        # 續傳冪等；PG 未啟用/不可達或來源失敗不擋主線（見 _run_fubon_day）。
        return _run_fubon_day(day)

    def _etf_units_step() -> str:
        # T-335／SPEC-016：ETF 已發行單位數（資金流研究）。不依賴當天行情，非關鍵步驟。
        return _run_etf_units_day(day)

    def _etf_holdings_step() -> str:
        # T-336／SPEC-016：元大、統一的每日持股清單（資金流研究）。非關鍵步驟。
        return _run_etf_holdings_day(day)

    def _chip_step() -> str:
        # T-310：官方當沖／借券（前幾個交易日＋缺日）、處置／注意公告 → 直寫 PG。
        # 不依賴當天行情（交易日取自 PG prices_daily 在今天之前的日子），休市日照跑（冪等）。
        # 抓失敗會 raise（步驟標 failed、列出哪幾個），但不是關鍵步驟，不擋訊號主線。
        return _run_chip_day(day)

    def _screener_step() -> str:
        # 選股池每日快照（SPEC-007 §6 / T-041）：需供應層已同步當日資料，故排在其後；
        # display-only、best-effort，寫入冪等（ON CONFLICT DO NOTHING）。
        from twquant.data.serving import store
        from twquant.monitoring import screener as scr

        scfg = _load_serving()
        if not scfg.enabled:
            return "未啟用（serving.yaml enabled=false）"
        if not store.ping(scfg):
            return "PG 不可達，略過（display-only）"
        res = scr.snapshot(scfg, day, day)
        return f"{res['rows']} 列" if res["rows"] else "無可快照資料（尚無當日供應層資料）"

    def _recommendation_step() -> str:
        # L0 名單（T-225）：對當日全宇宙推論 → 匯出推薦快照。best-effort，不擋主線。
        # 用哪個模型由 configs/ml.yaml 的 production_ranking_model 明寫（T-224）——
        # 未綁定時略過而不是自動選，因為八個模型的 train_end 全同、自動選會挑到風控模型。
        from twquant.errors import DataError
        from twquant.reporting.recommendation_export import export_scores

        bound = _load_ml().production_ranking_model
        if not bound:
            return "未綁定生產模型（configs/ml.yaml production_ranking_model），略過"
        root = Path(cfg.data_dir) / "models"
        mdir = root / bound
        if not (mdir / "meta.json").exists():
            return f"找不到綁定的模型 {bound}，略過"
        ml_cfg = _load_ml()

        def _score_into_pg(model_name: str) -> Path:
            # 生產路徑的分數一律寫進 PG：ML 類策略（D-112）與風控閘門（D-111）都從 ml_scores 讀，
            # 只留 parquet 的話它們每天都會讀不到當日分數。upsert 冪等，重跑安全。
            model_dir = root / model_name
            if not (model_dir / "meta.json").exists():
                raise DataError("ml_score", day, f"找不到模型目錄 {model_name}")
            path = model_dir / f"scores_{day}.parquet"
            if not path.exists() or _ml_scores_in_pg(model_name, day) == 0:
                _ml_score_day(day, model_dir, path, pg=True)
            return path

        scores_path = _score_into_pg(bound)
        # 風控閘門與輔助模型也要有當日分數。訓練期它們是「月底出分數、沿用到下個月底」，
        # 生產改為**每日推論**：D-089 量到回撤模型 94% 是 vol_20d，日頻與月頻差異極小，
        # 而沿用機制在生產環境一旦斷掉會靜默過期——每日推論配上 T-227 的守衛才會大聲失敗。
        gate = ml_cfg.recommendation_buckets
        extra = [gate.risk_gate_model if gate.enabled else "", *ml_cfg.production_aux_models]
        for name in dict.fromkeys(n for n in extra if n and n != bound):
            _score_into_pg(name)
        policy = _bucket_policy(day)
        result = export_scores(scores_path, mdir, Path(cfg.data_dir), day, policy)
        msg = f"{'已保存' if result.created else '內容相同，沿用'} {result.path.name}"
        # D-147：上架研究策略的模型。**在正式名單存檔之後**才跑，失敗寫進摘要、不擋名單。
        # D-150：打分之後也匯出成推薦快照（只套流動性閘門），ML 選股頁才看得到它每天的名單。
        research = [n for n in dict.fromkeys(ml_cfg.research_live_models) if n != bound]
        if research:
            research_policy = _research_bucket_policy(policy)
            saved, failed = _score_and_export_research(
                research,
                _score_into_pg,
                lambda path, name: export_scores(
                    path, root / name, Path(cfg.data_dir), day, research_policy
                ),
            )
            msg += f"；研究模型 {len(saved)}/{len(research)} 已打分並匯出名單"
            if failed:
                msg += f"（⚠ 失敗：{'；'.join(failed)}）"
        return msg

    def _strategy_targets_step() -> str:
        # D-112：已登記策略的當日目標持股快照。best-effort，不擋主線。
        # 排在 ML 推薦名單之後——LW-M1／LW-F1 這類策略要讀當日的 ml_scores。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        return _strategy_targets_for_day(day, Path(cfg.data_dir))

    def _daily_picks_step() -> str:
        # T-332／D-170：兩條線當天的名單寫進 PG daily_picks（永久保留）。排在打分與策略持股之後。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        if not _pg_has_prices(day):
            return f"{day} 沒有行情（休市或尚未擷取），不寫名單"
        return _daily_picks_for_day(day)

    def _benchmark_fill_step() -> str:
        # D-176 修訂三：名單績效對照（0050）缺行情、缺除權息就從 FinMind 補；排在名單績效之前。best-effort。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        return _benchmark_fill(day)

    def _scorecard_step() -> str:
        # D-176 名單績效：照公布的名單重播模擬帳戶＋逐筆。排在每日名單之後（要讀當天剛寫的名單）。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        if not _pg_has_prices(day):
            return f"{day} 沒有行情（休市或尚未擷取），名單績效維持上一個交易日"
        return _scorecard_for_day(day)

    def _stock_groups_step() -> str:
        # D-177 族群標籤：網站名單旁邊的小字。每 refresh_days 天才真的去抓（47 頁）；best-effort。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        return _stock_groups_refresh(force=False)

    def _group_flow_step() -> str:
        # D-177 修訂 族群資金：排在族群標籤之後（要讀族群表）。best-effort，不擋主線。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        if not _pg_has_prices(day):
            return f"{day} 沒有行情（休市或尚未擷取），略過"
        return _group_flow_for_day(day)

    def _paper_trading_step() -> str:
        # D-142：紙上交易帳本。排在策略持股之後——再平衡日的名單直接讀那一步剛存下的快照，
        # 不自己重算（重算＝允許事後補名單）。best-effort，不擋主線。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        return _paper_trading_for_day(day, Path(cfg.data_dir))

    def _quiet_score_step() -> str:
        # D-163 潛伏飆股：安靜股模型的當日分數（池子與大盤狀態同訓練）。排在 ML 推薦名單之後、
        # 看板之前——看板重播要讀當日分數。best-effort，不擋主線。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        if not _pg_has_prices(day):
            return f"{day} 沒有行情（休市或尚未擷取），略過"
        return _quiet_score_range(day, day)

    def _pick_board_step() -> str:
        # D-164 每日選股看板：兩條線重播到今天，存新增／剔除／持股／明日計畫。best-effort。
        from twquant.data.serving import store

        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過"
        if not _pg_has_prices(day):
            return f"{day} 沒有行情（休市或尚未擷取），看板維持上一個交易日"
        return _pick_board_for_day(day, Path(cfg.data_dir))

    def _insight_step() -> str:
        # 洞悉分析（SPEC-014 §6 / T-221）：best-effort，失敗不擋主線——洞悉是增強不是依賴。
        # 前提是當日已有推薦快照；沒有就是正常的「沒有 L0 名單可分析」，不是錯誤。
        from twquant.data.serving import store
        from twquant.insight.client import InsightClient
        from twquant.insight.config import load_llm_config
        from twquant.reporting.insight_run import run_id_for_day, run_insights
        from twquant.reporting.insight_sources import (
            fetch_industry_of,
            fetch_industry_rows,
            fetch_sections,
        )
        from twquant.reporting.recommendation_snapshot import select_snapshots

        data_dir = Path(cfg.data_dir)
        # 只分析生產模型的名單：研究模型的 run 較晚產生，不限定就會被當成當日名單（T-301）。
        run_id = run_id_for_day(data_dir, day, model_id=_load_ml().production_ranking_model or None)
        if run_id is None:
            return "當日無推薦快照，略過"
        scfg = _load_serving()
        if not scfg.enabled or not store.ping(scfg):
            return "PG 不可達，略過（display-only）"
        as_of = select_snapshots(data_dir, run_id=run_id).runs[0].as_of
        conn = store.connect(scfg)
        try:
            industries = fetch_industry_rows(conn, scfg.pg_schema, as_of, _SECTOR_WINDOW)
            summary = run_insights(
                data_dir,
                recommendation_run_id=run_id,
                top_n=_INSIGHT_TOP_N,
                client=InsightClient(load_llm_config(_CONFIG_DIR / "llm.yaml"), data_dir),
                now=now_tw(),
                fetch_sections=lambda sid: fetch_sections(
                    conn,
                    scfg.pg_schema,
                    sid,
                    as_of,
                    industries.get(fetch_industry_of(conn, scfg.pg_schema, sid) or ""),
                ),
            )
        finally:
            conn.close()
        return (
            f"分析 {summary.analysed}、缺席 {summary.absent}、新存 {summary.saved}、"
            f"沿用 {summary.reused}、失敗 {len(summary.failures)}"
        )

    steps = [
        PipelineStep("擷取", _ingest_step),
        PipelineStep("品質閘門", _gate_step, critical=True),
        PipelineStep("籌碼（當沖／借券／處置／注意）", _chip_step),
        PipelineStep("ETF 單位數", _etf_units_step),
        PipelineStep("ETF 持股", _etf_holdings_step),
        PipelineStep("券商買賣超", _broker_step),
        PipelineStep("訊號生成", _signal_step, skip_if_degraded=True),
        PipelineStep("指數", _index_step),
        PipelineStep("月營收", _revenue_step),
        PipelineStep("供應層", _serving_step),
        PipelineStep("選股池快照", _screener_step),
        PipelineStep("ML 推薦名單", _recommendation_step),
        PipelineStep("潛伏飆股打分", _quiet_score_step),
        PipelineStep("策略持股", _strategy_targets_step),
        PipelineStep("每日名單", _daily_picks_step),
        PipelineStep("對照行情", _benchmark_fill_step),
        PipelineStep("名單績效", _scorecard_step),
        PipelineStep("族群標籤", _stock_groups_step),
        PipelineStep("族群資金", _group_flow_step),
        PipelineStep("紙上交易", _paper_trading_step),
        PipelineStep("每日選股看板", _pick_board_step),
        PipelineStep("洞悉分析", _insight_step),
    ]
    if cloud:
        steps = with_skips(steps, _load_serving().cloud.skip_pipeline_steps, "雲端不跑（D-170）")
    return run_pipeline(day, steps, data_dir=cfg.data_dir, git_hash=_git_hash(), now=now_tw())


@app.command("notify-picks")
def notify_picks(
    date: str = typer.Option(..., "--date", help="交易日 YYYY-MM-DD"),
    test: bool = typer.Option(False, "--test", help="試發：不管這天發過沒、不記錄、不刪失效訂閱"),
) -> None:
    """網頁推播（T-348）：當天名單寫好後通知開了通知的裝置；每個交易日只發一次（雲端排程呼叫）。"""
    import os

    from twquant.config import PushConfig, load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.errors import ConfigError
    from twquant.monitoring import push_notify as pn
    from twquant.reporting.pick_board import PickBoardConfig

    day = dt.date.fromisoformat(date)
    cfg = load_config(_CONFIG_DIR / "push.yaml", PushConfig)
    board = load_config(_CONFIG_DIR / "pick_board.yaml", PickBoardConfig)
    key = os.environ.get(cfg.vapid_private_key_env, "")
    if not key:
        raise ConfigError(
            f"缺推播私密金鑰：環境變數 {cfg.vapid_private_key_env}（.env 或 GitHub secret）"
        )
    kind = "picks"
    scfg = _load_serving()
    conn = store.connect_cloud(scfg)
    try:
        if not test and pn.already_sent(conn, day, kind):
            typer.echo(f"{day} 的名單通知已經發過，略過")
            return
        lines = pn.load_line_counts(
            conn, scfg.pg_schema, day, [(s.code, s.name) for s in board.strategies]
        )
        if lines is None:
            typer.echo(f"{day} 還沒有名單，不發")
            return
        subs = pn.load_subscriptions(conn)
        allowed = [s for s in subs if pn.host_allowed(s.endpoint, cfg.allowed_hosts)]
        if len(allowed) != len(subs):
            typer.secho(
                f"[WARN] 略過 {len(subs) - len(allowed)} 個不是已知推播服務的訂閱",
                fg=typer.colors.YELLOW,
            )
        payload = pn.compose(day, lines, cfg.site_url)
        send = pn.webpush_sender(
            key, cfg.vapid_subject, cfg.ttl_seconds, cfg.request_timeout_seconds
        )
        result = pn.send_all(allowed, payload, send)
        if not test:
            pn.record(conn, day, kind, result, now_tw())
    finally:
        conn.close()
    typer.echo(f"{payload['title']}：{payload['body']}")
    typer.echo(f"發出 {result.sent}、失效（已刪） {len(result.gone)}、失敗 {len(result.failed)}")
    if result.failed:
        typer.secho("[FAIL] " + "；".join(result.failed), fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("notify-groups")
def notify_groups(
    date: str = typer.Option(..., "--date", help="交易日 YYYY-MM-DD"),
    test: bool = typer.Option(False, "--test", help="試發：不管這天發過沒、不記錄、不刪失效訂閱"),
) -> None:
    """族群資金推播（T-354）：名單上同族群兩檔以上、而且族群當天資金流入時通知；每個交易日最多一則。"""
    import os

    from twquant.config import PushConfig, load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.errors import ConfigError
    from twquant.monitoring import push_notify as pn

    day = dt.date.fromisoformat(date)
    cfg = load_config(_CONFIG_DIR / "push.yaml", PushConfig)
    key = os.environ.get(cfg.vapid_private_key_env, "")
    if not key:
        raise ConfigError(
            f"缺推播私密金鑰：環境變數 {cfg.vapid_private_key_env}（.env 或 GitHub secret）"
        )
    kind = "groups"
    scfg = _load_serving()
    conn = store.connect_cloud(scfg)
    try:
        if not test and pn.already_sent(conn, day, kind):
            typer.echo(f"{day} 的族群資金通知已經發過，略過")
            return
        hot = pn.load_hot_groups(conn, scfg.pg_schema, day, cfg.group_min_picks)
        if hot is None:
            typer.echo(f"{day} 的名單或族群資金還沒算好，不發")
            return
        if not hot:
            typer.echo(f"{day} 名單上沒有同族群 {cfg.group_min_picks} 檔以上又資金流入的族群，不發")
            return
        subs = [
            s for s in pn.load_subscriptions(conn) if pn.host_allowed(s.endpoint, cfg.allowed_hosts)
        ]
        payload = pn.compose_groups(day, hot, cfg.site_url, cfg.group_max_listed)
        send = pn.webpush_sender(
            key, cfg.vapid_subject, cfg.ttl_seconds, cfg.request_timeout_seconds
        )
        result = pn.send_all(subs, payload, send)
        if not test:
            pn.record(conn, day, kind, result, now_tw())
    finally:
        conn.close()
    typer.echo(f"{payload['title']}：{payload['body']}")
    typer.echo(f"發出 {result.sent}、失效（已刪） {len(result.gone)}、失敗 {len(result.failed)}")
    if result.failed:
        typer.secho("[FAIL] " + "；".join(result.failed), fg=typer.colors.RED)
        raise typer.Exit(1)


def _echo_pipeline_result(res: PipelineResult) -> None:
    typer.secho(f"=== 每日 pipeline @ {res.day} ===", fg=typer.colors.CYAN, bold=True)
    for s in res.steps:
        color = {"ok": typer.colors.GREEN, "failed": typer.colors.RED}.get(
            s.status, typer.colors.YELLOW
        )
        typer.secho(f"  [{s.status.upper()}] {s.name}：{s.detail}", fg=color)
    tag = "降級（沿用前日權重）" if res.degraded else "正常"
    typer.secho(f"結果：{tag}，訊號{'已' if res.signals_produced else '未'}產出", bold=True)


@app.command()
def pipeline(
    date: str = typer.Option("today", help="交易日 YYYY-MM-DD 或 today"),
    pg_signals: bool = typer.Option(
        False, "--pg-signals", help="訊號生成改讀 PG 供應層（GHA 雲端模式，T-057）"
    ),
    fail_on_degraded: bool = typer.Option(
        False,
        "--fail-on-degraded",
        help="降級（關鍵步驟失敗）時以非零碼結束——GHA 無人值守需要紅燈；注意國定假日"
        "（平日休市）也會因無行情而降級",
    ),
    cloud: bool = typer.Option(
        False,
        "--cloud",
        help="雲端模式：略過 serving.yaml cloud.skip_pipeline_steps 列的步驟（D-170）",
    ),
) -> None:
    """每日營運 pipeline：擷取→品質閘門→訊號生成→供應層→選股池快照，寫 heartbeat（LOOP §3）。"""
    from twquant.conventions import now_tw

    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)
    res = _run_daily_pipeline(day, pg_signals=pg_signals, cloud=cloud)
    _echo_pipeline_result(res)
    if fail_on_degraded and res.degraded:
        raise typer.Exit(1)


@app.command(name="daily-picks")
def daily_picks(
    date: str = typer.Option("today", help="交易日 YYYY-MM-DD 或 today"),
) -> None:
    """把兩條線當天的名單寫進 PG daily_picks（T-332；與每日 pipeline 的「每日名單」步驟相同）。"""
    from twquant.conventions import now_tw

    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)
    typer.secho(f"[OK] {day}：{_daily_picks_for_day(day)}", fg=typer.colors.GREEN)


@app.command(name="scorecard")
def scorecard(
    date: str = typer.Option("", help="算到哪個交易日 YYYY-MM-DD；空＝PG 最新行情日"),
) -> None:
    """名單績效（D-176）：每條線照公布的名單自動操作的模擬帳戶＋逐筆統計，寫進 PG `scorecard`（網站讀）。"""
    day = dt.date.fromisoformat(date) if date else _pg_latest_price_day()
    typer.secho(f"[OK] {day}：{_scorecard_for_day(day)}", fg=typer.colors.GREEN)


@app.command(name="stock-groups")
def stock_groups(
    force: bool = typer.Option(False, "--force", help="不管上次什麼時候抓的，現在就重抓"),
) -> None:
    """族群標籤（D-177）：抓櫃買中心產業價值鏈、每檔挑一條當族群，寫進 PG `stock_group`（網站讀）。"""
    typer.secho(f"[OK] {_stock_groups_refresh(force)}", fg=typer.colors.GREEN)


@app.command(name="group-flow")
def group_flow_cmd(
    date: str = typer.Option("", help="算哪個交易日 YYYY-MM-DD；空＝PG 最新行情日"),
    start: str = typer.Option("", "--from", help="回補起日 YYYY-MM-DD（與 --to 一起用）"),
    end: str = typer.Option("", "--to", help="回補迄日 YYYY-MM-DD"),
) -> None:
    """族群資金（D-177 修訂）：每個族群的成交比重、資金倍數、漲跌家數，寫進 PG `group_flow_daily`（網站讀）。"""
    if start or end:
        if not (start and end) or date:
            raise typer.BadParameter("回補要同時給 --from 與 --to，且不能再給 --date")
        days = _pg_price_days(dt.date.fromisoformat(start), dt.date.fromisoformat(end))
    else:
        days = [dt.date.fromisoformat(date) if date else _pg_latest_price_day()]
    for day in days:
        typer.secho(f"[OK] {day}：{_group_flow_for_day(day)}", fg=typer.colors.GREEN)


@app.command(name="group-flow-check")
def group_flow_check_cmd(
    out: str = typer.Option("", "--out", help="另存結果 markdown 的路徑（絕對路徑；空＝只印出來）"),
) -> None:
    """族群資金流入有沒有用（D-178 事前登記）：名單逐筆依進名單那天族群是否資金流入分兩組比報酬，照登記的門檻下結論。"""
    text = _group_flow_check()
    typer.echo(text, nl=False)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        typer.secho(f"[OK] 已存 {out}", fg=typer.colors.GREEN)


@app.command(name="benchmark-fill")
def benchmark_fill_cmd(
    date: str = typer.Option("", help="補到哪一天 YYYY-MM-DD；空＝今天"),
) -> None:
    """名單績效的對照（0050）缺行情、缺除權息就從 FinMind 補（D-176 修訂三）；已有的列只比對、不覆蓋。"""
    from twquant.conventions import now_tw

    day = dt.date.fromisoformat(date) if date else now_tw().date()
    typer.secho(f"[OK] {_benchmark_fill(day)}", fg=typer.colors.GREEN)


@app.command()
def watchdog(date: str = typer.Option("today", help="預期交易日 YYYY-MM-DD 或 today")) -> None:
    """dead-man switch：檢查 heartbeat 是否為預期交易日；缺失/過期 → 非零退出。"""
    from twquant.conventions import now_tw
    from twquant.pipeline import watchdog_check

    cfg = _load_system()
    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)
    r = watchdog_check(cfg.data_dir, day)
    if r.ok:
        typer.secho("heartbeat 正常。", fg=typer.colors.GREEN)
    else:
        typer.secho(f"[CRIT] watchdog：{r.reason}", fg=typer.colors.RED, bold=True)
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """顯示資料擷取狀態：各來源涵蓋範圍、平日缺口、最近失敗、最後執行時點。"""
    import polars as pl

    from twquant.data import calendar as cal
    from twquant.data.warehouse import Warehouse
    from twquant.reporting.ingest_log import last_run, read_ingest_log, recent_failures

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    calendar = cal.load(wh)  # 交易日曆（推導自 prices_daily），缺口以開市日為準、非 weekday

    typer.secho("=== 資料擷取狀態 ===", fg=typer.colors.CYAN, bold=True)
    daily_plan = [
        ("prices_daily", ["twse", "tpex"]),
        ("institutional_daily", ["twse"]),
        ("margin_daily", ["twse"]),
    ]
    for table, sources in daily_plan:
        for source in sources:
            dates = wh.source_dates(table, source)
            if not dates:
                typer.secho(f"  {source}/{table}：無資料", fg=typer.colors.YELLOW)
                continue
            gaps = calendar.missing(dates, dates[0], dates[-1])
            gap_note = f"，缺 {len(gaps)} 交易日" if gaps else "，無缺口"
            color = typer.colors.YELLOW if gaps else typer.colors.GREEN
            typer.secho(
                f"  {source}/{table}：{dates[0]} ~ {dates[-1]}　{len(dates)} 交易日{gap_note}",
                fg=color,
            )
            if gaps:
                head = ", ".join(str(g) for g in gaps[:3])
                more = f" …等 {len(gaps)} 日" if len(gaps) > 3 else ""
                typer.secho(f"      缺口起於：{head}{more}", fg=typer.colors.YELLOW)

    rev_dates = wh.source_dates("monthly_revenue", "mops_sii")
    if rev_dates:
        typer.secho(
            f"  mops/monthly_revenue：{rev_dates[0]} ~ {rev_dates[-1]}　{len(rev_dates)} 個月",
            fg=typer.colors.GREEN,
        )

    lr = last_run(cfg.data_dir)
    typer.secho(f"\n最後擷取時點：{lr or '（無紀錄）'}", fg=typer.colors.CYAN)
    log = read_ingest_log(cfg.data_dir)
    if not log.is_empty():
        nfail = log.filter(pl.col("status") == "failed").height
        typer.secho(f"擷取紀錄：{log.height} 筆，其中失敗 {nfail} 筆", fg=typer.colors.CYAN)
    fails = recent_failures(cfg.data_dir, limit=5)
    if not fails.is_empty():
        typer.secho("最近失敗：", fg=typer.colors.YELLOW)
        for r in fails.to_dicts():
            typer.secho(
                f"  [{r['attempt_ts'][:19]}] {r['source']}/{r['table']} @ {r['date']}",
                fg=typer.colors.YELLOW,
            )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="綁定位址（容器內用 0.0.0.0）"),
    port: int = typer.Option(8000, help="埠號"),
) -> None:
    """啟動監控 API（FastAPI/uvicorn），供 React 前端即時讀資料湖與一鍵補抓。"""
    import uvicorn

    from twquant.monitoring.api import app as api_app

    typer.secho(
        f"監控 API：http://{host}:{port}/api（互動文件：/api/docs）",
        fg=typer.colors.CYAN,
        bold=True,
    )
    uvicorn.run(api_app, host=host, port=port)


@app.command("screener-snapshot")
def screener_snapshot(
    from_: str | None = typer.Option(None, "--from", help="起 YYYY-MM-DD（省略＝最新交易日）"),
    to: str | None = typer.Option(None, "--to", help="迄 YYYY-MM-DD（省略＝最新交易日）"),
) -> None:
    """策略選股池每日快照入 PG（serving.screener_daily，冪等；供 /api/screener?date= 回溯）。"""
    import datetime as _dt

    from twquant.monitoring import screener, stock_api

    try:
        d0 = _dt.date.fromisoformat(from_) if from_ else None
        d1 = _dt.date.fromisoformat(to) if to else None
        res = screener.snapshot(stock_api.serving_cfg(), d0, d1)
    except ValueError as exc:
        typer.secho(f"參數錯誤：{exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    if res["rows"] == 0:
        typer.secho("無可快照的資料（prices_daily 空或區間無交易日）。", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(
        f"選股池快照：{res['from']} ~ {res['to']}，screener_daily 區間內共 {res['rows']} 列",
        fg=typer.colors.GREEN,
        bold=True,
    )


@app.command()
def calendar() -> None:
    """由 prices_daily 推導交易日曆、核對來源缺口、持久化至 clean/trading_calendar。"""
    from twquant.data import calendar as cal
    from twquant.data.warehouse import Warehouse

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    try:
        tc = cal.derive_from_prices(wh)
    except Exception as exc:  # 無行情時給清楚訊息而非 traceback（已 re-raise）
        typer.secho(f"無法推導日曆（先 backfill 行情）：{exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    if not tc.sessions:
        typer.secho("prices_daily 無資料，先 backfill。", fg=typer.colors.RED)
        raise typer.Exit(1)

    cal.persist(wh, tc)
    typer.secho(
        f"交易日曆：{tc.sessions[0]} ~ {tc.sessions[-1]}　{len(tc)} 個開市日，已存入 "
        f"clean/trading_calendar",
        fg=typer.colors.GREEN,
        bold=True,
    )
    checks = cal.cross_check(wh, tc)
    for source, gaps in checks.items():
        if gaps:
            head = ", ".join(str(g) for g in gaps[:3])
            more = f" …等 {len(gaps)} 日" if len(gaps) > 3 else ""
            typer.secho(
                f"  {source}：於自身涵蓋區間內缺 {len(gaps)} 個開市日（{head}{more}）",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(f"  {source}：涵蓋區間內無缺口", fg=typer.colors.GREEN)


@app.command(name="ic-report")
def ic_report(
    factor: str = typer.Option("price.mom_20d", help="因子名"),
    horizons: str = typer.Option("5,10,20", help="前向報酬視窗（逗號分隔）"),
    universe: str = typer.Option(r"^[1-9][0-9]{3}$", help="股票代號過濾（預設四碼、排除權證）"),
    start: str = typer.Option("", help="IC 期間起（YYYY-MM-DD，空=全部）"),
    end: str = typer.Option("", help="as_of 截點（YYYY-MM-DD，空=最新）"),
    event_study: bool = typer.Option(
        False,
        "--event-study",
        help="事件型稀疏因子加跑事件研究（成立日樣本、事件後超額、勝率、漲停占比）",
    ),
    pg: bool = typer.Option(
        True, "--pg/--no-pg", help="讀 PG serving 鏡射表（D-026 預設）；--no-pg 走本機資料湖"
    ),
) -> None:
    """對因子跑 IC 檢驗（RankIC t-stat、多空價差），印出並記入 research_log。"""
    import hashlib
    import math

    import polars as pl

    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.data.warehouse import Warehouse
    from twquant.factors.compute import compute
    from twquant.factors.ic import compute_event_study, compute_ic_report
    from twquant.reporting.research_log import append_research_log, trials_count

    cfg = _load_system()
    if pg:
        from twquant.data.serving import store
        from twquant.data.serving.pg_view import PgTableSource

        scfg = _load_serving()
        pg_conn = store.connect(scfg)
        src: TableSource = PgTableSource(pg_conn, scfg.pg_schema)
    else:
        pg_conn = None
        src = Warehouse(cfg.data_dir)
    as_of = dt.date.fromisoformat(end) if end else src.latest_date("prices_daily")
    if as_of is None:
        if pg_conn is not None:
            pg_conn.close()
        typer.secho("無行情資料——先 ingest/backfill（或 serving sync）。", fg=typer.colors.RED)
        raise typer.Exit(1)

    view = src.view(as_of)
    hz = [int(x) for x in horizons.split(",")]
    factor_df = compute([factor], view).filter(pl.col("stock_id").str.contains(universe))
    prices = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(pl.col("stock_id").str.contains(universe))
        .collect()
    )
    if start:
        s = dt.date.fromisoformat(start)
        factor_df = factor_df.filter(pl.col("date") >= s)

    report = compute_ic_report(factor, factor_df, prices, hz)

    typer.secho(
        f"IC 檢驗 · {factor} · as_of {as_of} · 宇宙 {universe}", fg=typer.colors.CYAN, bold=True
    )
    for h in report.horizons:
        typer.echo(
            f"  {h.horizon:>2}日：RankIC 均值 {h.ic_mean:+.4f}　t={h.t_stat:+.2f}　"
            f"IR={h.ic_ir:+.3f}　多空價差 {h.ls_spread:+.4f}（{h.n_days} 日）"
        )

    phash = hashlib.sha256(f"{factor}|{universe}|{horizons}".encode()).hexdigest()[:12]
    entries: list[dict[str, object]] = []
    for h in report.horizons:
        for name, val in [("ic_mean", h.ic_mean), ("t_stat", h.t_stat), ("ls_spread", h.ls_spread)]:
            entries.append(
                {
                    "entry_type": "ic_test",
                    "strategy": "",
                    "factor": factor,
                    "params_hash": phash,
                    "date": as_of.isoformat(),
                    "metric_name": f"{name}_{h.horizon}d",
                    "metric_value": float(val),
                    "trial_scope": f"ic:{factor}",
                }
            )
    if event_study:
        px_flags = (
            view.table("prices_daily")
            .select("date", "stock_id", "close", "limit_up_price")
            .filter(pl.col("stock_id").str.contains(universe))
            .collect()
        )
        es = compute_event_study(factor, factor_df, px_flags, hz)
        lu = "n/a" if math.isnan(es.limit_up_share) else f"{es.limit_up_share:.1%}"
        typer.secho(
            f"事件研究 · 成立日 {es.n_events} 件 · 成立日漲停占比 {lu}",
            fg=typer.colors.CYAN,
            bold=True,
        )
        for eh in es.horizons:
            typer.echo(
                f"  {eh.horizon:>2}日：超額中位 {eh.median_excess:+.4f}　"
                f"均值 {eh.mean_excess:+.4f}　勝率 {eh.hit_rate:.1%}（n={eh.n_events}）"
            )
        base_entry: dict[str, object] = {
            "entry_type": "ic_test",
            "strategy": "",
            "factor": factor,
            "params_hash": phash,
            "date": as_of.isoformat(),
            "trial_scope": f"ic:{factor}",
        }
        for eh in es.horizons:
            for name, val in [
                ("es_mean_excess", eh.mean_excess),
                ("es_hit_rate", eh.hit_rate),
            ]:
                entries.append(
                    dict(base_entry, metric_name=f"{name}_{eh.horizon}d", metric_value=float(val))
                )
        entries.append(dict(base_entry, metric_name="es_n_events", metric_value=float(es.n_events)))
        entries.append(
            dict(base_entry, metric_name="es_limit_up_share", metric_value=es.limit_up_share)
        )
    append_research_log(cfg.data_dir, entries)
    n = trials_count(cfg.data_dir, f"ic:{factor}")
    typer.secho(
        f"已記入 research_log（trial_scope ic:{factor} 累計 {n} 組參數）", fg=typer.colors.GREEN
    )
    if pg_conn is not None:
        pg_conn.close()


def _best_effort(names: Sequence[str], run: Callable[[str], object]) -> list[str]:
    """逐一執行，回傳失敗清單（`名稱：原因`）。**不是吞錯**：呼叫端必須把清單寫進摘要。

    只用在研究用途（D-147 的上架研究模型）：它失敗不該拖垮同一步裡的生產輸出，
    但失敗本身要看得見——所以回傳而不是記 DEBUG。
    """
    failed: list[str] = []
    for name in names:
        try:
            run(name)
        except Exception as exc:  # noqa: BLE001 - 失敗逐條回傳並寫進步驟摘要，不是靜默略過
            failed.append(f"{name}：{exc}")
    return failed


def _score_and_export_research(
    names: Sequence[str],
    score: Callable[[str], Path],
    export: Callable[[Path, str], SnapshotWriteResult],
) -> tuple[list[str], list[str]]:
    """D-150：研究模型逐一打分、再把分數匯出成推薦快照。回傳（已匯出的檔名, 失敗訊息）。

    失敗逐條回傳、不中斷其餘模型（同 `_best_effort`）：生產名單已經在這之前存好了。
    """
    saved: list[str] = []

    def one(name: str) -> None:
        saved.append(export(score(name), name).path.name)

    return saved, _best_effort(names, one)


def _daily_pg_source(
    conn: object, schema: str, day: dt.date, names: Sequence[str]
) -> PgTableSource:
    """每日路徑讀 PG 的來源：cache＋涵蓋 `names` 最長回看的下界＋`ml_scores` 按表縮窗。

    少了下界就是整張表全掃：`ml_scores` 4,160 萬列整批載入，程序在 15 GB 被 OOM 砍掉。
    2026-09-23 發生兩次——先在物化（T-294），修好之後換成策略持股快照（T-299），
    因為兩處各自組參數、只修了一處。每日路徑一律經過這裡建來源。
    """
    from twquant.data.serving import materialize as smat
    from twquant.data.serving.pg_view import PgTableSource

    return PgTableSource(
        conn,
        schema,
        cache=True,
        since=smat.read_since(day, names),
        # 薄因子只要當天分數，不該被 850 天的 `fundamental.sue` 拖著整批載入
        table_since=smat.read_since_by_table(day, names),
    )


def _live_targets(cfg: StrategyConfig, source: TableSource, day: dt.date) -> TargetList:
    """登記在平台上的一支策略在 `day` 的名單（D-112；每日補位型見 D-147）。

    月頻型走 `build_targets`，給的是目標權重。**每日補位型沒有目標權重**——它的實際持股
    取決於歷史進出場（出場規則、ratchet），而後端沒有帳戶狀態。能誠實給出的只有
    「今天若有空位會買誰」：過門檻的候選前 n 名、每格 1/n。頁面上必須這樣標示。
    補位型另帶排序用的模型分數（T-300）：權重全是 1/n，快照的順位要依分數。
    """
    from twquant.reporting.strategy_targets import TargetList
    from twquant.strategy.build import build_targets
    from twquant.strategy.slots import slot_candidates

    if cfg.slots is not None:
        n = cfg.slots.n
        picks = slot_candidates(cfg, source, day)[:n]
        return TargetList(
            rows=[(sid, 1.0 / n) for sid, _ in picks], kind="slots", scores=dict(picks)
        )
    frame = build_targets(cfg, source, day)
    return TargetList(
        rows=[(str(r["stock_id"]), float(r["target_weight"])) for r in frame.to_dicts()],
        kind="rebalance",
    )


def _strategy_targets_for_day(day: dt.date, data_dir: Path) -> str:
    """對已登記的策略逐支算出 `day` 的目標權重並存成不可變快照（D-112）。回傳一行摘要。

    資料來源與回測相同（PG 供應層）：ML 類策略要讀 `ml_scores`，那張表只在 PG。
    單支失敗會記進快照（status=failed）而不是讓整批中止；整批失敗才往上 raise。
    """
    import twquant.factors.builtins  # noqa: F401  # 觸發註冊，才問得到 lookback
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.factors.registry import list_factors
    from twquant.reporting.strategy_targets import (
        LiveStrategies,
        LiveStrategy,
        build_snapshot,
        save_snapshot,
    )
    from twquant.strategy.config import load_strategy

    registry = load_config(_CONFIG_DIR / "strategies_live.yaml", LiveStrategies)
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        # 全部已註冊因子（不扣物化的排除清單）：TAIL-B 的訊號 ml.lgbm_tail_up_60d 在排除
        # 清單裡，策略照樣要讀它；下界取最長回看，涵蓋宇宙與進場濾網的滾動窗。
        source = _daily_pg_source(conn, scfg.pg_schema, day, list(list_factors()))

        def _compute(strat: LiveStrategy) -> TargetList:
            cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{strat.config}.yaml"))
            # held=空：帶狀持有（banded_top_n）需要「目前持股」才有意義，而後端沒有帳戶狀態。
            # 已登記的 8 支都不是帶狀策略；日後若登記帶狀策略，這裡要先有持股來源。
            return _live_targets(cfg, source, day)

        snapshot = build_snapshot(day, registry.strategies, _compute, generated_at=now_tw())
    finally:
        conn.close()
    result = save_snapshot(data_dir, snapshot)
    ok = sum(1 for r in snapshot.strategies if r.status == "ok")
    flat = sum(1 for r in snapshot.strategies if r.status == "flat")
    failed = [r.code for r in snapshot.strategies if r.status == "failed"]
    tail = f"、失敗 {len(failed)}（{'、'.join(failed)}）" if failed else ""
    return (
        f"{'已保存' if result.created else '內容相同，沿用'} {result.path.name}："
        f"有名單 {ok}、空手 {flat}{tail}"
    )


@app.command(name="strategy-targets")
def strategy_targets(
    date: str = typer.Option("", help="訊號日 YYYY-MM-DD；空＝PG 最新行情日"),
    out: str = typer.Option("", help="快照資料根目錄；空＝configs/system.yaml data_dir"),
) -> None:
    """對 configs/strategies_live.yaml 登記的每支策略產生目標持股快照（D-112，研究輸出）。"""
    from twquant.data.serving import store

    if date:
        day = dt.date.fromisoformat(date)
    else:
        scfg = _load_serving()
        conn = store.connect(scfg)
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT MAX(date) FROM "{scfg.pg_schema}"."prices_daily"')
                row = cur.fetchone()
        finally:
            conn.close()
        if not row or row[0] is None:
            typer.secho("PG 無行情，無法決定訊號日。", fg=typer.colors.RED)
            raise typer.Exit(1)
        day = row[0]
    data_dir = Path(out) if out else Path(_load_system().data_dir)
    typer.secho(f"[OK] {day} {_strategy_targets_for_day(day, data_dir)}", fg=typer.colors.GREEN)


def _quiet_score_range(start: dt.date, end: dt.date) -> str:
    """潛伏飆股（D-163）：安靜股模型對 [start, end] 每個交易日打分並寫進 `ml_scores`。回傳一行摘要。

    通用的 `ml score` 對全宇宙打分、不算大盤狀態——拿來打安靜股模型會落在錯的池子上
    （策略把「當日有分數的股票」當池子），所以走 `ml/quiet_live.py`。upsert 冪等，重跑安全。
    """
    from twquant.config import MlConfig, load_config
    from twquant.data.serving import store
    from twquant.data.serving.ml_store import upsert_ml_scores
    from twquant.errors import DataError
    from twquant.ml.quiet_live import quiet_scores

    ml_cfg = _load_ml()
    if not ml_cfg.quiet_live_model:
        return "未綁定 quiet_live_model，略過"
    if not ml_cfg.quiet_live_config:
        raise typer.BadParameter("quiet_live_model 有綁定但 quiet_live_config 是空的")
    model_cfg = load_config(_CONFIG_DIR / ml_cfg.quiet_live_config, MlConfig)
    model_dir = Path(_load_system().data_dir) / "models" / ml_cfg.quiet_live_model
    if not (model_dir / "model.txt").exists():
        raise DataError("ml_scores", end, f"找不到模型檔 {model_dir / 'model.txt'}")
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        rows = quiet_scores(
            conn,
            scfg.pg_schema,
            cfg=model_cfg,
            model_dir=model_dir,
            stock_pattern=scfg.cloud.stock_pattern,
            start=start,
            end=end,
            lookback_days=ml_cfg.quiet_price_lookback_days,
        )
        n = upsert_ml_scores(conn, scfg.pg_schema, rows)
    finally:
        conn.close()
    days = rows["date"].n_unique()
    return f"{ml_cfg.quiet_live_model}：{days} 個交易日、{rows.height} 列（新增 {n}）"


def _flow_flags_for(day: dt.date, sids: list[str]) -> tuple[dict[str, object] | None, str | None]:
    """看板日的資金流旗標（T-341）：(旗標, 缺漏原因)。資金流三張表任一不存在 → (None, "no_tables")。

    只讀看板日往前 `_FLOW_FLAG_LOOKBACK_DAYS` 個日曆日：因子窗口（ADV 20、事件前置空窗 8、去重 20）都夠。
    """
    import polars as pl

    import twquant.factors.chip  # noqa: F401  # 註冊 chip.margin_velocity_5d
    import twquant.factors.fund_flow as ffac
    from twquant.data.serving import store
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.factors.registry import get
    from twquant.reporting import flow_flags as ffl
    from twquant.reporting import fund_flow_traces as ft

    scfg = _load_serving()
    cfg, meta = _load_fund_flow()
    f = cfg.factors
    with store.connect(scfg) as conn:
        with conn.cursor() as cur:
            for t in ("etf_units_daily", "etf_holdings_daily", "index_rebalance_events"):
                cur.execute("SELECT to_regclass(%s)", (f"{scfg.pg_schema}.{t}",))
                if cur.fetchone()[0] is None:
                    return None, "no_tables"
        since = day - dt.timedelta(days=_FLOW_FLAG_LOOKBACK_DAYS)
        view = PgTableSource(conn, scfg.pg_schema, cache=True, since=since).view(day)

        def run(name: str, **params: object) -> pl.DataFrame:
            fdef = get(name)
            assert fdef.fn is not None
            out = fdef.fn(view, {**fdef.params, **params})
            return out.collect() if isinstance(out, pl.LazyFrame) else out

        pressure = run(
            "chip.etf_flow_pressure_5d", window=f.pressure_window, adv_window=f.adv_window
        )
        streak = run("chip.trust_disc_streak")
        px = (
            view.table("prices_daily")
            .select("date", "stock_id")
            .filter(pl.col("stock_id").str.contains(r"^[1-9][0-9]{3}$"))
            .collect()
        )
        sessions = sorted(set(px["date"].to_list()))
        events = ft.detect_events(
            sessions=sessions,
            rebal=view.table("index_rebalance_events")
            .select("stock_id", "action", "available_at")
            .collect(),
            pressure=pressure,
            disc=ffac.daily_disc(view, 10),
            disc_net=run("chip.trust_disc_net_10d", window=f.disc_window),
            sbl=ffac.sbl_sell_ratio(view, f.pressure_window),
            margin=run("chip.margin_velocity_5d", window=f.pressure_window),
            universe=px,
            cfg=cfg.events,
            cutoff_hour=_SIGNAL_CUTOFF_HOUR,
        )
        recent = set(sessions[-cfg.events.dedup_sessions :])
        active = [e.etf_id for e in meta.etfs if e.is_active]
        hold = (
            view.table("etf_holdings_daily")
            .select("date", "etf_id", "stock_id", "shares")
            .filter(pl.col("etf_id").is_in(active))
            .collect()
        )
    flags = ffl.build_flow_flags(
        sids,
        day,
        pressure=pressure,
        streak=streak,
        events=events.filter(pl.col("date").is_in(sorted(recent))),
        changes=ffl.active_changes(hold, active),
    )
    return {"note": ffl.NOTE, "as_of": day.isoformat(), "stocks": flags}, None


def _pick_board_for_day(day: dt.date, data_dir: Path) -> str:
    """每日選股看板（D-164）：各條線從 replay_start 重播到 `day`，存進出／持股／明日計畫。

    持股不是帳戶資料，是回測引擎的重播——同一套成交語意，所以跟研究回測對得上。
    **不存任何報酬**（第二保留期，D-109）：只把帳本轉成名單與持有天數。
    """
    import twquant.factors.builtins  # noqa: F401  # 觸發註冊，才問得到 lookback
    from twquant.backtest.costs import CostConfig
    from twquant.backtest.engine import run_backtest
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.errors import DataError
    from twquant.factors.registry import list_factors
    from twquant.reporting.pick_board import (
        PickBoardConfig,
        board_payload,
        new_names,
        replay_days,
        save_board,
        strategy_payload,
    )
    from twquant.strategy.config import load_strategy

    board_cfg = load_config(_CONFIG_DIR / "pick_board.yaml", PickBoardConfig)
    if day < board_cfg.replay_start:
        raise typer.BadParameter(f"{day} 早於看板重播起點 {board_cfg.replay_start}")
    cost = load_config(_CONFIG_DIR / "costs.yaml", CostConfig)
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        # 下界取重播起點往前的最長回看：沒有下界 PgTableSource 會整張 ml_scores 全掃（T-278）
        source = _daily_pg_source(
            conn, scfg.pg_schema, board_cfg.replay_start, list(list_factors())
        )
        payloads = []
        for strat in board_cfg.strategies:
            cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{strat.config}.yaml"))
            res = run_backtest(
                cfg,
                cost,
                board_cfg.replay_start,
                day,
                source,
                initial_capital=board_cfg.initial_capital,
                max_participation=board_cfg.max_participation,
                lot_size=board_cfg.lot_size,
            )
            sessions = [p.date for p in res.equity]
            if sessions[-1] != day:
                raise DataError("pick_board", day, f"{strat.code} 重播只到 {sessions[-1]}")
            time_stops = [r.sessions for r in cfg.exit_rules if r.kind == "time_stop"]
            watch = None
            top_by_day = None
            if strat.watch:
                n = cfg.slots.n if cfg.slots is not None else cfg.selection.top_n
                if n is None:
                    raise typer.BadParameter(f"{strat.code}：推薦名單要固定檔數（selection.top_n）")
                need = board_cfg.display_sessions + board_cfg.watch_lookback_sessions
                if len(sessions) < need:
                    raise DataError(
                        "pick_board",
                        day,
                        f"{strat.code}：重播只有 {len(sessions)} 個交易日，觀察名單要 {need} 個——"
                        "把 configs/pick_board.yaml 的 replay_start 往前移",
                    )
                span = sessions[-need:]
                tops = [(d, _top_names(cfg, source, d, n)) for d in span]
                # 沒打分的日子在「新名字」回看裡當作沒有名單（頁面另標缺漏）
                watch = new_names(
                    [(d, t or []) for d, t in tops], board_cfg.watch_lookback_sessions
                )
                top_by_day = dict(tops)  # 推薦名單：位子滿不滿都照列
            payloads.append(
                strategy_payload(
                    strat,
                    "slots" if cfg.slots is not None else "rebalance",
                    time_stops[0] if time_stops else None,
                    # 補位型＝位子數；月換股型＝每次挑幾檔（持股少於它時頁面說明原因）
                    cfg.slots.n if cfg.slots is not None else cfg.selection.top_n,
                    replay_days(
                        sessions,
                        res.ledger,
                        res.splits,
                        _closes_for(conn, scfg.pg_schema, res.ledger, board_cfg.replay_start, day),
                    ),
                    res.next_plan,
                    board_cfg.display_sessions,
                    watch,
                    top_by_day,
                    [(p.date, p.equity) for p in res.equity],
                    cfg.slots is not None,
                )
            )
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT stock_id, name, market, industry FROM "{scfg.pg_schema}"."stock_meta"'
            )
            stocks = {
                str(sid): {
                    "name": str(nm or ""),
                    "market": str(mk or ""),
                    "industry": str(ind or ""),
                }
                for sid, nm, mk, ind in cur.fetchall()
            }
    finally:
        conn.close()
    payload = board_payload(
        as_of=day, generated_at=now_tw(), cfg=board_cfg, strategies=payloads, stocks=stocks
    )
    # T-341／SPEC-016 §7：資金流旗標（監控、未回測）。PG 沒有資金流表 → null 並註明原因，不是 0。
    flags, missing = _flow_flags_for(day, sorted(cast("dict[str, object]", payload["stocks"])))
    payload["flow_flags"] = flags
    if missing:
        payload["flow_flags_missing"] = missing
    path = save_board(data_dir, payload)
    # T-329／T-330：同一份看板順便畫成 HTML（含收盤、漲幅與整體表現），放 data/reports/，瀏覽器直接開。
    html_path = _save_board_html(
        payload, data_dir, board_cfg, with_prices=True, with_references=True
    )
    parts = []
    for p in payloads:
        last = cast("list[dict[str, list[object]]]", p["days"])[-1]
        nxt = cast("dict[str, list[object]]", p["next"])
        parts.append(
            f"{p['name']} 持股 {len(last['holdings'])}、當日 +{len(last['entered'])}／"
            f"−{len(last['exited'])}、下一交易日 +{len(nxt['buy'])}／−{len(nxt['sell'])}"
        )
    return f"{path.name}＋{html_path.name}：" + "；".join(parts)


def _board_prices(
    board: dict[str, object], window_sessions: int
) -> dict[str, dict[str, float | None]]:
    """HTML 版（T-329）要的收盤與漲幅：讀 PG `prices_daily` 裡看板每一檔在 as_of、前一交易日、視窗第一天的收盤。

    只查這三個日期、只查看板出現過的代號（不是整張表，T-278）。前一交易日＝PG 裡 as_of 之前最後一個有行情的日子。
    """
    from twquant.data.serving import store
    from twquant.reporting.pick_board_html import price_changes

    as_of = dt.date.fromisoformat(str(board["as_of"]))
    strategies = cast("list[dict[str, Any]]", board["strategies"])
    # 視窗第一天＝各條線最後 N 個交易日裡最早的那天（兩條線日曆相同；取 min 以防不同）
    firsts = [
        dt.date.fromisoformat(str(s["days"][-window_sessions:][0]["date"]))
        for s in strategies
        if s["days"]
    ]
    window_start = min(firsts) if firsts else None
    ids = sorted(cast("dict[str, object]", board["stocks"]))
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT MAX(date) FROM "{scfg.pg_schema}"."prices_daily" WHERE date < %s',
                (as_of,),
            )
            row = cur.fetchone()
            prev = cast("dt.date | None", row[0] if row else None)
            wanted = [d for d in (as_of, prev, window_start) if d is not None]
            cur.execute(
                f'SELECT stock_id, date, close FROM "{scfg.pg_schema}"."prices_daily" '
                "WHERE stock_id = ANY(%s) AND date = ANY(%s)",
                (ids, wanted),
            )
            closes: dict[str, dict[dt.date, float]] = {}
            for sid, d, c in cur.fetchall():
                if c is not None:
                    closes.setdefault(str(sid), {})[cast("dt.date", d)] = float(c)
    finally:
        conn.close()
    return price_changes(closes, as_of=as_of, prev_session=prev, window_start=window_start)


def _board_references(cfg: Any, data_dir: Path) -> dict[str, list[dict[str, object]]]:
    """HTML 版「研究回測參考」（T-330）：讀 `pick_board.yaml` 每條線登記的結果包，取它們的 metrics。

    包不在或沒有 metrics 就 raise——頁面不給半套參考列，登記了就要拿得到。
    """
    import json

    from twquant.errors import DataError
    from twquant.reporting.pick_board_html import reference_summary

    out: dict[str, list[dict[str, object]]] = {}
    for strat in cfg.strategies:
        rows: list[dict[str, object]] = []
        for ref in strat.references:
            path = data_dir / ref.path
            if not path.exists():
                raise DataError(
                    "pick_board",
                    None,
                    f"{strat.code} 登記的結果包不存在：{path}（pick_board.yaml references）",
                )
            try:
                summary = reference_summary(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError) as exc:
                raise DataError(
                    "pick_board", None, f"{strat.code} 的結果包 {path} 讀不出 metrics：{exc}"
                ) from exc
            rows.append({"label": ref.label, "source": ref.path, "summary": summary})
        out[str(strat.code)] = rows
    return out


def _save_board_html(
    board: dict[str, object], data_dir: Path, cfg: Any, *, with_prices: bool, with_references: bool
) -> Path:
    """把看板畫成 HTML 並寫到 `<data_dir>/reports/pick_board.html`。

    `cfg`＝`PickBoardConfig`；`with_prices` 決定要不要讀 PG 補收盤與漲幅，`with_references` 決定要不要讀研究回測結果包。
    """
    from twquant.reporting.pick_board_html import render_board_html, save_board_html

    prices = _board_prices(board, cfg.html_window_sessions) if with_prices else None
    references = _board_references(cfg, data_dir) if with_references else None
    page = render_board_html(
        board, window_sessions=cfg.html_window_sessions, prices=prices, references=references
    )
    return save_board_html(data_dir, page)


def _top_names(cfg: Any, source: Any, day: dt.date, n: int) -> list[str] | None:
    """`day` 收盤後模型的前 n 名：補位型＝策略實際候選序；月換股型＝若當天換股的目標名單。

    月換股型的串接閘門（排序模型分數）當天全市場沒有值 → 回 None（D-165：頁面明示「這天模型沒有打分」）。
    引擎只在換股日取分數，平常日缺分數不影響持股；但缺漏本身仍是資料問題，看板會逐日標出來。
    """
    picks = _line_picks(cfg, source, day, n)
    return None if picks is None else [sid for sid, _ in picks]


def _line_picks(
    cfg: Any, source: Any, day: dt.date, n: int
) -> list[tuple[str, float | None]] | None:
    """`_top_names` 的帶分數版：補位型附模型分數；月換股型的名單由規則排名產生，分數為 None。"""
    import polars as pl

    from twquant.factors.compute import compute
    from twquant.strategy.build import build_targets
    from twquant.strategy.slots import slot_candidates

    if cfg.slots is not None:
        return [(sid, float(score)) for sid, score in slot_candidates(cfg, source, day)[:n]]
    view = source.view(day)
    for pf in cfg.prefilter:
        gate = compute([pf.factor], view, dates=[day], params={pf.factor: dict(pf.params)})
        if gate.filter(pl.col("value").is_not_null() & pl.col("value").is_finite()).is_empty():
            return None
    tg = build_targets(cfg, source, day)
    rows = sorted(
        ((str(r["stock_id"]), float(r["target_weight"])) for r in tg.to_dicts()),
        key=lambda x: (-x[1], x[0]),
    )
    ranked: list[tuple[str, float | None]] = [(sid, None) for sid, w in rows if w > 0.0]
    return ranked[:n]


def _strategy_model_id(cfg: Any) -> str | None:
    """策略用的模型代號：先看串接閘門（prefilter），再看訊號；都沒有回 None。"""
    for item in (*cfg.prefilter, *cfg.signals):
        model_id = dict(item.params).get("model_id")
        if model_id:
            return str(model_id)
    return None


def _daily_picks_for_day(day: dt.date) -> str:
    """每日名單（T-332／D-170）：`pick_board.yaml` 的每條線算出當天前 n 名，寫進 PG `daily_picks`。

    與看板的「推薦名單」同一個函式（`_line_picks`），所以兩邊的名單逐檔相同；
    但這裡不重播持股，只要當天的資料，雲端的滾動窗口就夠。
    """
    import twquant.factors.builtins  # noqa: F401  # 觸發註冊，才問得到 lookback
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.data.serving.picks_store import write_line
    from twquant.errors import DataError
    from twquant.factors.registry import list_factors
    from twquant.reporting.pick_board import PickBoardConfig
    from twquant.strategy.config import load_strategy

    board_cfg = load_config(_CONFIG_DIR / "pick_board.yaml", PickBoardConfig)
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        source = _daily_pg_source(conn, scfg.pg_schema, day, list(list_factors()))
        done = []
        for strat in board_cfg.strategies:
            cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{strat.config}.yaml"))
            n = cfg.slots.n if cfg.slots is not None else cfg.selection.top_n
            if n is None:
                raise typer.BadParameter(f"{strat.code}：名單要固定檔數（selection.top_n）")
            picks = _line_picks(cfg, source, day, n)
            if picks is None:
                raise DataError("daily_picks", day, f"{strat.code}：排序模型當天沒有分數")
            written = write_line(
                conn,
                scfg.pg_schema,
                day,
                strat.code,
                picks,
                _strategy_model_id(cfg),
                now_tw(),
                strat.allow_empty,
            )
            done.append(f"{strat.name} {written} 檔")
    finally:
        conn.close()
    return "、".join(done)


def _scorecard_for_day(day: dt.date) -> str:
    """名單績效（D-176）：每條線照公布的名單（`daily_picks`）從開戶日重播到 `day`，連同逐筆寫進 PG `scorecard`。

    只讀名單、行情、除權息與股名——不重算模型分數，所以雲端的滾動窗口就夠（行情 500 日）。
    """
    from twquant.backtest.costs import CostConfig
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.data.serving.scorecard_store import read_published, write_scorecard
    from twquant.errors import DataError, TwquantError
    from twquant.reporting.pick_board import PickBoardConfig
    from twquant.reporting.scorecard import ScorecardConfig, build_scorecards
    from twquant.strategy.config import load_strategy

    cfg = load_config(_CONFIG_DIR / "scorecard.yaml", ScorecardConfig)
    if day < cfg.start:
        raise typer.BadParameter(f"{day} 早於名單績效開戶日 {cfg.start}")
    board = load_config(_CONFIG_DIR / "pick_board.yaml", PickBoardConfig)
    cost = load_config(_CONFIG_DIR / "costs.yaml", CostConfig)
    lines = [
        (s, load_strategy(str(_CONFIG_DIR / "strategies" / f"{s.config}.yaml")))
        for s in board.strategies
    ]
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        # 只讀開戶日前 warmup_days 起的行情與除權息（前收、20 日成交額中位數），不整張全掃（T-278）
        source = PgTableSource(
            conn,
            scfg.pg_schema,
            cache=True,
            since=cfg.start - dt.timedelta(days=cfg.warmup_days),
        )
        with conn.cursor() as cur:
            cur.execute(f'SELECT stock_id, name FROM "{scfg.pg_schema}"."stock_meta"')
            names = {str(sid): str(nm) for sid, nm in cur.fetchall() if nm}
        published = read_published(conn, scfg.pg_schema, cfg.start, day)
        # 一條線一條線算、算好就寫：某條線壞了（例如換股日缺名單）不能讓其他線也沒有成績；
        # 壞的那條最後一起 raise，步驟照樣亮紅燈（不靜默）
        payloads: list[dict[str, Any]] = []
        failures: list[str] = []
        for line in lines:
            try:
                (payload,) = build_scorecards(
                    source=source,
                    published=published,
                    names=names,
                    lines=[line],
                    cfg=cfg,
                    cost=cost,
                    as_of=day,
                    generated_at=now_tw(),
                )
            except (TwquantError, ValueError) as exc:
                failures.append(f"{line[0].name}：{exc}")
                continue
            write_scorecard(conn, scfg.pg_schema, str(payload["code"]), day, payload, now_tw())
            payloads.append(payload)
    finally:
        conn.close()
    parts = []
    for p in payloads:
        acct = p["account"]["all"]
        stats = p["trades"]["stats"]["all"]
        parts.append(f"{p['name']} 帳戶 {acct['ret']:+.1%}、逐筆已結算 {stats['n']} 筆")
    bench_err = payloads[0]["benchmark"]["error"] if payloads else None
    tail = f"（⚠ {cfg.benchmark} 對照算不出來：{bench_err}）" if bench_err else ""
    if failures:
        done = "；".join(parts) or "沒有任何一條線"
        raise DataError("scorecard", day, f"{'；'.join(failures)}（已寫入：{done}）")
    return "；".join(parts) + tail


def _closes_for(
    conn: Any, schema: str, ledger: Sequence[Any], start: dt.date, end: dt.date
) -> dict[dt.date, dict[str, float]]:
    """看板漲跌用的每日收盤價（未還原，與引擎成交價同一把尺；D-165），只取帳本出現過的股票。"""
    ids = sorted({str(t.stock_id) for t in ledger})
    out: dict[dt.date, dict[str, float]] = {}
    if not ids:
        return out
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, close FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND stock_id = ANY(%s) AND close IS NOT NULL",
            (start, end, ids),
        )
        for d, sid, close in cur.fetchall():
            out.setdefault(d, {})[str(sid)] = float(close)
    return out


def _stock_groups_refresh(force: bool) -> str:
    """族群表比 `refresh_days` 舊（或還沒有）就整份重抓重算；`force` 不管新舊都重抓。"""
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.http import HttpClient
    from twquant.data.serving import store
    from twquant.data.serving.groups_store import groups_updated_at, read_industries, replace_groups
    from twquant.data.sources.tpex_chain import RETRY_STATUS, ChainSource
    from twquant.data.stock_groups import StockGroupsConfig, primary_groups

    cfg = load_config(_CONFIG_DIR / "stock_groups.yaml", StockGroupsConfig)
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        now = now_tw()
        last = groups_updated_at(conn, scfg.pg_schema)
        if not force and last is not None and now - last < dt.timedelta(days=cfg.refresh_days):
            return f"族群表 {last.astimezone(now.tzinfo):%Y-%m-%d} 更新過（每 {cfg.refresh_days} 天重抓），略過"
        client = HttpClient(
            source="tpex_ic",
            rate=_load_system().source_rates["tpex_ic"],
            timeout=cfg.timeout_seconds,
            retry_status=RETRY_STATUS,
        )
        chains, members = ChainSource(client, cfg.base_url, cfg.first_chain).fetch()
        groups = primary_groups(
            members,
            read_industries(conn, scfg.pg_schema),
            cfg.refine,
            chains,
            cfg.split,
            cfg.split_min_members,
        )
        replace_groups(conn, scfg.pg_schema, groups, now)
    finally:
        conn.close()
    named = sum(1 for g in groups if g.chain is not None)
    return (
        f"{len(groups)} 檔、{len({g.group for g in groups})} 個族群"
        f"（{named} 檔對上產業鏈，{len(chains)} 條鏈）"
    )


def _group_flow_for_day(day: dt.date) -> str:
    """族群資金（D-177 修訂）：`day` 與之前 avg_sessions 個交易日的行情 ＋ 族群表 → `group_flow_daily`。"""
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.data.serving.group_flow_store import read_price_window, write_group_flow
    from twquant.data.serving.groups_store import read_group_map
    from twquant.data.stock_groups import StockGroupsConfig
    from twquant.reporting.group_flow import group_flow

    cfg = load_config(_CONFIG_DIR / "stock_groups.yaml", StockGroupsConfig).flow
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        groups = read_group_map(conn, scfg.pg_schema)
        with conn.cursor() as cur:
            cur.execute(f'SELECT stock_id, name FROM "{scfg.pg_schema}"."stock_meta"')
            names = {str(sid): str(nm) for sid, nm in cur.fetchall() if nm}
        prices = read_price_window(conn, scfg.pg_schema, day, cfg.avg_sessions + 1)
        flows = group_flow(prices, groups, names, day, cfg, scfg.cloud.stock_pattern)
        write_group_flow(conn, scfg.pg_schema, day, flows, now_tw())
    finally:
        conn.close()
    hot = [f.group for f in sorted(flows, key=lambda f: -(f.ratio or 0.0)) if f.hot]
    return f"{len(flows)} 個族群，資金流入 {len(hot)} 個" + (
        f"：{'、'.join(hot[:5])}" if hot else ""
    )


def _group_flow_check() -> str:
    """D-178：讀名單績效逐筆、族群、行情，重算每個進名單日子的族群資金，照登記的門檻判讀，回 markdown。"""
    import json

    import polars as pl

    from twquant.config import load_config
    from twquant.data.serving import store
    from twquant.data.serving.groups_store import read_group_map, read_industries
    from twquant.data.stock_groups import StockGroupsConfig
    from twquant.errors import DataError
    from twquant.reporting.group_flow import group_flow
    from twquant.reporting.group_flow_check import (
        GroupFlowCheckConfig,
        evaluate,
        label_trades,
        market_lift,
        render,
    )
    from twquant.reporting.holdout import config_fingerprint

    cfg = load_config(_CONFIG_DIR / "group_flow_check.yaml", GroupFlowCheckConfig)
    flow_cfg = load_config(_CONFIG_DIR / "stock_groups.yaml", StockGroupsConfig).flow
    if config_fingerprint(flow_cfg) != cfg.flow_fingerprint:
        raise DataError(
            "group_flow_check",
            None,
            "「資金流入」的定義（stock_groups.yaml flow）在 D-178 登記之後被改了——這份檢定不算數，要另開登記",
        )
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        schema = scfg.pg_schema
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT line, (payload->'trades'->'rows')::text FROM \"{schema}\".\"scorecard\" ORDER BY line"
            )
            trade_rows = {str(line): json.loads(body) for line, body in cur.fetchall()}
            cur.execute(
                f'SELECT date, stock_id, close, amount FROM "{schema}"."prices_daily" WHERE date >= %s',
                (cfg.start - dt.timedelta(days=120),),
            )
            prices = pl.DataFrame(
                cur.fetchall(),
                schema={
                    "date": pl.Date,
                    "stock_id": pl.String,
                    "close": pl.Float64,
                    "amount": pl.Float64,
                },
                orient="row",
            )
        groups = {sid: ind for sid, ind in read_industries(conn, schema).items() if ind}
        groups.update(read_group_map(conn, schema))  # 族群表優先，沒有的用官方產業別（同網站）
    finally:
        conn.close()
    if not trade_rows:
        raise DataError("scorecard", None, "雲端還沒有名單績效（serving.scorecard 是空的）")
    sessions = sorted(prices.get_column("date").unique().to_list())
    by_day = {d[0]: f for d, f in prices.partition_by("date", as_dict=True).items()}
    hot_cache: dict[dt.date, frozenset[str] | None] = {}

    def hot_groups(day: dt.date) -> frozenset[str] | None:
        """那天資金流入的族群；前面不足 avg_sessions 天（算不出倍數）→ None。"""
        if day not in hot_cache:
            i = sessions.index(day) if day in by_day else -1
            if i < flow_cfg.avg_sessions:
                hot_cache[day] = None
            else:
                window = pl.concat([by_day[d] for d in sessions[i - flow_cfg.avg_sessions : i + 1]])
                flows = group_flow(window, groups, {}, day, flow_cfg, scfg.cloud.stock_pattern)
                hot_cache[day] = frozenset(f.group for f in flows if f.hot)
        return hot_cache[day]

    signals = sorted(
        {
            dt.date.fromisoformat(str(r["signal"]))
            for rows in trade_rows.values()
            for r in rows
            if r.get("status") == "closed" and str(r["signal"]) >= cfg.start.isoformat()
        }
    )
    hot_by_date: dict[dt.date, frozenset[str]] = {}
    for d in signals:
        got = hot_groups(d)
        if got is None:
            raise DataError("group_flow_check", d, "這天前面的行情不到 20 個交易日，算不出資金倍數")
        hot_by_date[d] = got
    obs = [
        o
        for line, rows in trade_rows.items()
        for o in label_trades(rows, line, groups, hot_by_date, cfg.start)
    ]
    result = evaluate(obs, cfg)

    def hot_members(day: dt.date) -> frozenset[str] | None:
        hot = hot_groups(day)
        return None if hot is None else frozenset(sid for sid, g in groups.items() if g in hot)

    market = market_lift(
        prices, hot_members, sessions, scfg.cloud.stock_pattern, cfg.market_horizon, cfg.market_step
    )
    return render(result, market, cfg, max(signals) if signals else cfg.start)


def _benchmark_fill(day: dt.date) -> str:
    """名單績效對照（scorecard.yaml 的 benchmark）從開戶日前 warmup_days 起到 `day`：FinMind 行情＋除權息，缺的才補。"""
    import json
    import os

    from twquant.config import load_config
    from twquant.data.http import HttpClient
    from twquant.data.serving import store
    from twquant.data.serving.benchmark_fill import (
        market_latest,
        plan_fill,
        read_existing,
        write_fill,
    )
    from twquant.data.sources.finmind import API, DIVIDEND_RESULT_DATASET
    from twquant.errors import DataError
    from twquant.reporting.scorecard import ScorecardConfig

    cfg = load_config(_CONFIG_DIR / "scorecard.yaml", ScorecardConfig)
    code = cfg.benchmark
    lo = cfg.start - dt.timedelta(days=cfg.warmup_days)
    client = HttpClient(source="finmind", rate=_load_system().source_rates["finmind"], timeout=60.0)
    token = os.environ.get("FINMIND_TOKEN", "")

    def rows(dataset: str) -> list[dict[str, Any]]:
        params = {
            "dataset": dataset,
            "data_id": code,
            "start_date": lo.isoformat(),
            "end_date": day.isoformat(),
        }
        if token:  # 沒有 token 也能抓單一檔（免費匿名額度）；有就帶上
            params["token"] = token
        payload = json.loads(client.get(API, params=params).text)
        if payload.get("status") != 200:
            raise DataError(
                "finmind",
                day,
                f"{dataset} {code}：status {payload.get('status')} {str(payload.get('msg'))[:120]}",
            )
        return list(payload.get("data") or [])

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        latest = market_latest(conn, scfg.pg_schema, code)
        if latest is None:
            raise DataError("prices_daily", None, "PG 沒有任何行情，不補對照")
        upto = min(day, latest)  # 全市場還沒有的日子不補，見 benchmark_fill 模組說明
        fm_prices, fm_dividends = rows("TaiwanStockPrice"), rows(DIVIDEND_RESULT_DATASET)
        pg_prices, pg_events = read_existing(conn, scfg.pg_schema, code, lo, upto)
        plan = plan_fill(code, upto, fm_prices, fm_dividends, pg_prices, pg_events)
        write_fill(conn, scfg.pg_schema, plan)
    finally:
        conn.close()
    return (
        f"{code} {lo}～{upto}：補行情 {plan.prices.height} 天、除權息 {plan.events.height} 筆"
        f"（已有 {plan.prices_checked} 天、{plan.events_checked} 筆，比對一致）"
    )


def _pg_price_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """PG 在 [start, end] 有行情的交易日。"""
    from twquant.data.serving import store

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT DISTINCT date FROM "{scfg.pg_schema}"."prices_daily" '
                "WHERE date BETWEEN %s AND %s ORDER BY date",
                (start, end),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _pg_has_prices(day: dt.date) -> bool:
    """PG 在 `day` 有沒有行情。休市日（如中秋）新步驟要說「略過」，而不是報失敗。"""
    from twquant.data.serving import store

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT 1 FROM "{scfg.pg_schema}"."prices_daily" WHERE date = %s LIMIT 1', (day,)
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _pg_latest_price_day() -> dt.date:
    """PG 最新行情日；沒有行情即 raise（不猜日期）。"""
    from twquant.data.serving import store
    from twquant.errors import DataError

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT MAX(date) FROM "{scfg.pg_schema}"."prices_daily"')
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise DataError("prices_daily", None, "PG 無行情，無法決定日期")
    return cast("dt.date", row[0])


@app.command(name="pick-board")
def pick_board(
    date: str = typer.Option("", help="看板日期 YYYY-MM-DD；空＝PG 最新行情日"),
    out: str = typer.Option("", help="資料根目錄（絕對路徑）；空＝configs/system.yaml data_dir"),
) -> None:
    """重建每日選股看板（D-164）：兩條線的新增、剔除、持股與下一個交易日的計畫。"""
    day = dt.date.fromisoformat(date) if date else _pg_latest_price_day()
    data_dir = Path(out) if out else Path(_load_system().data_dir)
    typer.secho(f"[OK] {day} {_pick_board_for_day(day, data_dir)}", fg=typer.colors.GREEN)


@app.command(name="pick-board-html")
def pick_board_html(
    out: str = typer.Option("", help="資料根目錄（絕對路徑）；空＝configs/system.yaml data_dir"),
    no_prices: bool = typer.Option(
        False, "--no-prices", help="不讀 PG：收盤與漲幅顯示「—」（測試或 PG 不可達時用）"
    ),
    no_references: bool = typer.Option(
        False, "--no-references", help="不讀研究回測結果包：整體表現只留重播以來的數字（測試用）"
    ),
) -> None:
    """把已存好的看板（pick_board.json）畫成 HTML（T-329／T-330）：不重播、不改 JSON，寫到 data/reports/pick_board.html。"""
    from twquant.config import load_config
    from twquant.reporting.pick_board import PickBoardConfig, load_board

    data_dir = Path(out) if out else Path(_load_system().data_dir)
    board = load_board(data_dir)
    if board is None:
        typer.secho(
            f"找不到 {data_dir / 'pick_board' / 'pick_board.json'}：先跑 pick-board 產生看板。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    cfg = load_config(_CONFIG_DIR / "pick_board.yaml", PickBoardConfig)
    path = _save_board_html(
        board, data_dir, cfg, with_prices=not no_prices, with_references=not no_references
    )
    typer.secho(f"[OK] {board['as_of']} {path}", fg=typer.colors.GREEN)


def _file_fingerprint(path: Path) -> str:
    """配置檔內容的 SHA-256。規則變了指紋就變，帳本因此攔得住「偷偷改規則繼續記」。"""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paper_trading_for_day(day: dt.date, data_dir: Path) -> str:
    """把 `day` 這一天記進每一條紙上交易軌（D-142／T-280）。回傳一行摘要。

    **名單不在這裡算**：再平衡日的目標持股直接讀當日已保存的策略持股快照（D-112），
    也就是平台頁面上看到的同一份。快照不在就記成 `skipped`——如果這裡改成「自己算一次」，
    那就等於允許事後補算名單，帳本的全部意義都沒了（D-142 §四）。

    沒有當日行情就什麼都不寫（休市不是洞）。真正的洞由報告比對帳本日期與 `prices_daily`
    的交易日找出來，不靠這裡猜。
    """
    from twquant.backtest.costs import CostConfig
    from twquant.backtest.engine import price_panel
    from twquant.config import load_config
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.errors import TwquantError
    from twquant.reporting import paper_ledger as pl_
    from twquant.reporting.paper_trading import assert_trackable, is_signal_day, step_day
    from twquant.reporting.strategy_targets import select_snapshots
    from twquant.strategy.config import load_strategy

    paper_path = _CONFIG_DIR / "paper_trading.yaml"
    costs_path = _CONFIG_DIR / "costs.yaml"
    cfg = load_config(paper_path, pl_.PaperTradingConfig)
    strategy_path = _CONFIG_DIR / "strategies" / f"{cfg.strategy}.yaml"
    strat = load_strategy(str(strategy_path))
    assert_trackable(strat)
    cost_cfg = load_config(costs_path, CostConfig)

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        # 回看 90 天足夠算前收與 20 日 ADV 中位數（`price_panel` 的全部需求）。
        source = PgTableSource(
            conn, scfg.pg_schema, cache=True, since=day - dt.timedelta(days=_PAPER_LOOKBACK_DAYS)
        )
        rows = price_panel(source, day, day).get(day, {})
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT DISTINCT date FROM "{scfg.pg_schema}"."prices_daily" '
                "WHERE date >= %s AND date <= %s ORDER BY date",
                (day.replace(day=1), day),
            )
            sessions = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    if not rows:
        return f"{day} 沒有行情（休市或尚未擷取），未寫入任何一條軌"

    signal_day = is_signal_day(strat, day, sessions)
    targets: list[pl_.OrderRow] | None = None
    blocked = ""
    if signal_day:
        snapshots, _ = select_snapshots(data_dir, date=day, limit=1)
        runs = [r for s in snapshots for r in s.strategies if r.config == cfg.strategy]
        if not runs:
            blocked = f"再平衡日但當日策略持股快照沒有 {cfg.strategy}（pipeline 未跑完？）"
        elif runs[0].status == "failed":
            blocked = f"再平衡日但策略算不出來：{runs[0].detail}"
        else:  # status=flat 是合法的空手日，名單就是空的
            targets = [
                pl_.OrderRow(stock_id=t.stock_id, target_weight=t.target_weight)
                for t in runs[0].targets
            ]

    now = now_tw()
    summary: list[str] = []
    for track in cfg.tracks:
        state = pl_.load_track(data_dir, track.code)
        if not state.opened:
            pl_.append_event(
                data_dir,
                track.code,
                pl_.TrackOpened(
                    code=track.code,
                    label=track.label,
                    registration=cfg.registration,
                    strategy=cfg.strategy,
                    strategy_fingerprint=_file_fingerprint(strategy_path),
                    cost_fingerprint=_file_fingerprint(costs_path),
                    model_id=cfg.model_id,
                    capital_twd=track.capital_twd,
                    start_date=day,
                    credit_dividends=cfg.credit_dividends,
                ),
                day=day,
                recorded_at=now,
                cfg=cfg,
            )
            state = pl_.load_track(data_dir, track.code)
            summary.append(f"{track.code} 開軌")
        if state.last_date is not None and day <= state.last_date and state.seq > 1:
            summary.append(f"{track.code} 已記過")
            continue
        if blocked:
            pl_.append_event(
                data_dir,
                track.code,
                pl_.SkippedDay(reason=blocked),
                day=day,
                recorded_at=now,
                cfg=cfg,
            )
            summary.append(f"{track.code} 略過")
            continue
        try:
            record = step_day(
                state,
                day,
                rows,
                targets=targets,
                cost_cfg=cost_cfg,
                max_participation=cfg.max_participation,
            )
        except TwquantError as exc:
            # 算不出來也是一種結果：記下原因，不讓這一天憑空消失（黃金規則 5）。
            pl_.append_event(
                data_dir,
                track.code,
                pl_.SkippedDay(reason=f"{type(exc).__name__}: {exc}"[:500]),
                day=day,
                recorded_at=now,
                cfg=cfg,
            )
            summary.append(f"{track.code} 失敗")
            continue
        pl_.append_event(data_dir, track.code, record, day=day, recorded_at=now, cfg=cfg)
        summary.append(
            f"{track.code} 權益 {record.equity:,.0f}"
            f"（持股 {len(record.holdings)}、成交 {len(record.fills)}"
            f"{'、換股日' if signal_day else ''}）"
        )
    return ("再平衡日：" if signal_day else "") + "；".join(summary)


@app.command(name="paper-trade")
def paper_trade(
    date: str = typer.Option("", help="交易日 YYYY-MM-DD；空＝PG 最新行情日"),
    out: str = typer.Option("", help="帳本資料根目錄；空＝configs/system.yaml data_dir"),
) -> None:
    """把某一天記進紙上交易帳本（D-142）。append-only，不得回填，重跑同一天是安全的。"""
    from twquant.data.serving import store

    if date:
        day = dt.date.fromisoformat(date)
    else:
        scfg = _load_serving()
        conn = store.connect(scfg)
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT MAX(date) FROM "{scfg.pg_schema}"."prices_daily"')
                row = cur.fetchone()
        finally:
            conn.close()
        if not row or row[0] is None:
            typer.secho("PG 無行情，無法決定交易日。", fg=typer.colors.RED)
            raise typer.Exit(1)
        day = row[0]
    data_dir = Path(out) if out else Path(_load_system().data_dir)
    typer.secho(f"[OK] {day} {_paper_trading_for_day(day, data_dir)}", fg=typer.colors.GREEN)


@app.command(name="paper-verify")
def paper_verify(
    out: str = typer.Option("", help="帳本資料根目錄；空＝configs/system.yaml data_dir"),
) -> None:
    """驗每一條軌的雜湊鏈。被動過就以非零碼結束——這是帳本可信度的唯一憑據。"""
    from twquant.config import load_config
    from twquant.errors import TwquantError
    from twquant.reporting import paper_ledger as pl_

    cfg = load_config(_CONFIG_DIR / "paper_trading.yaml", pl_.PaperTradingConfig)
    data_dir = Path(out) if out else Path(_load_system().data_dir)
    bad = 0
    for track in cfg.tracks:
        events = pl_.read_events(pl_.ledger_path(data_dir, track.code))
        if not events:
            typer.secho(f"  [--] {track.code}：尚未開軌", fg=typer.colors.YELLOW)
            continue
        try:
            pl_.verify_chain(events)
        except TwquantError as exc:
            bad += 1
            typer.secho(f"  [BAD] {track.code}：{exc}", fg=typer.colors.RED)
            continue
        state = pl_.replay(events)
        typer.secho(
            f"  [OK] {track.code}：{len(events)} 筆、{state.start_date} ~ {state.last_date}、"
            f"權益 {state.equity:,.0f}、已平倉 {len(state.closed)} 筆、洞 {len(state.skipped)} 天",
            fg=typer.colors.GREEN,
        )
    if bad:
        raise typer.Exit(1)


@app.command()
def targets(
    strategy: str = typer.Option(..., help="策略 YAML 路徑"),
    date: str = typer.Option("", help="訊號日 YYYY-MM-DD，空=最新交易日"),
) -> None:
    """依策略配置產生某訊號日的目標權重（不需 LLM；純量化）。"""
    from twquant.data.warehouse import Warehouse
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import load_strategy

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    strat = load_strategy(strategy)

    day = dt.date.fromisoformat(date) if date else wh.latest_date("prices_daily")
    if day is None:
        typer.secho("資料湖無行情，先 backfill。", fg=typer.colors.RED)
        raise typer.Exit(1)

    out = build_targets(strat, wh, day)
    typer.secho(
        f"策略 {strat.name} · 訊號日 {day} · {out.height} 檔目標部位",
        fg=typer.colors.CYAN,
        bold=True,
    )
    if out.is_empty():
        typer.secho("空手日（無合格標的或曝險為 0）。", fg=typer.colors.YELLOW)
        return
    ranked = out.sort("target_weight", descending=True)
    for r in ranked.head(20).to_dicts():
        typer.echo(f"  {r['stock_id']}　權重 {r['target_weight'] * 100:.2f}%")
    typer.secho(
        f"合計曝險 {out['target_weight'].sum() * 100:.1f}%（上限單股 "
        f"{strat.max_weight_per_stock * 100:.0f}%）",
        fg=typer.colors.GREEN,
    )


def _fingerprint_inputs(wh: object, strategy: object) -> dict[str, tuple[int, str]]:
    """收集這次回測會讀到的每張表的 (列數, max(available_at))，供資料指紋用（T-131）。

    表的清單由策略自己決定：訊號因子宣告的 `tables`，加上一定會用到的 prices_daily，
    再依宇宙濾網補上 financial_statements（ROE／市值）與 monthly_revenue（營收篩）。
    寫死一份固定清單會在新增濾網時悄悄失效——指紋涵蓋不到的資料變動等於沒有指紋。
    """
    import polars as pl

    import twquant.factors.builtins  # noqa: F401  # 匯入觸發因子註冊
    from twquant.factors.registry import get

    names = {"prices_daily"}
    for s in strategy.signals:  # type: ignore[attr-defined]
        names |= set(get(s.factor).tables)
    u = strategy.universe  # type: ignore[attr-defined]
    if u.min_roe is not None or u.mktcap_rank_max is not None or u.mktcap_rank_min is not None:
        names.add("financial_statements")
    if (
        u.min_revenue_yoy is not None
        or u.max_revenue_yoy is not None
        or u.min_revenue_streak is not None
    ):
        names.add("monthly_revenue")

    out: dict[str, tuple[int, str]] = {}
    for name in sorted(names):
        row = (
            wh.scan(name)  # type: ignore[attr-defined]
            .select(pl.len().alias("n"), pl.col("available_at").max().alias("mx"))
            .collect()
            .row(0)
        )
        out[name] = (int(row[0]), str(row[1]))
    return out


def _resolve_costs(name: str) -> pathlib.Path:
    """把 --costs 的值解析成路徑：絕對路徑照用，相對名稱相對於 configs/（T-124）。

    先前寫死 `configs/costs.yaml`，使 `configs/costs_finlab.yaml`（finlab 口徑，
    報表已在用）從 CLI 根本無法選用。預設值維持 "costs.yaml"，故不指定時
    解析結果與改動前逐位元相同。
    """
    p = pathlib.Path(name)
    return p if p.is_absolute() else _CONFIG_DIR / name


@app.command()
def backtest(
    strategy: str = typer.Option(..., help="策略 YAML 路徑"),
    start: str = typer.Option(..., help="回測起 YYYY-MM-DD"),
    end: str = typer.Option(..., help="回測迄 YYYY-MM-DD"),
    capital: float = typer.Option(10_000_000.0, help="初始資金"),
    participation: float = typer.Option(0.05, min=0.0, max=1.0, help="單股 ADV 參與率上限"),
    cost_scale: float | None = typer.Option(
        None, "--cost-scale", min=0.0, help="成本倍率覆寫；0=零成本反事實"
    ),
    costs: str = typer.Option(
        "costs.yaml", "--costs", help="成本配置檔；相對路徑相對於 configs/（T-124）"
    ),
    out: str | None = typer.Option(
        None, "--out", help="把完整結果包（曲線／ledger／未成交／指紋）落地成 JSON（T-131）"
    ),
    pg: bool = typer.Option(
        True, "--pg/--no-pg", help="讀 PG serving 鏡射表（D-026 預設）；--no-pg 走本機資料湖"
    ),
    dividends: bool = typer.Option(
        True,
        "--dividends/--no-dividends",
        help="除息日是否把股利現金入帳（D-085 預設開）；--no-dividends 回到價格報酬口徑，供 D-086 的對照",
    ),
    lot_size: int | None = typer.Option(
        None,
        "--lot-size",
        min=1,
        help="每筆成交的最小股數；預設整張（1,000），1＝零股回測（D-153 §一 補充二）",
    ),
) -> None:
    """跑回測，印績效指標並記入 research_log（純量化，不需 LLM）。"""
    from twquant.backtest.costs import CostConfig
    from twquant.backtest.diagnostics import summarize_backtest
    from twquant.backtest.engine import DEFAULT_LOT_SIZE, run_backtest
    from twquant.config import load_config
    from twquant.data.warehouse import Warehouse
    from twquant.reporting.research_log import append_research_log, backtest_log_entries

    lot = lot_size if lot_size is not None else DEFAULT_LOT_SIZE
    from twquant.strategy.config import load_strategy

    cfg = _load_system()
    if pg:
        from twquant.data.serving import store
        from twquant.data.serving.pg_view import PgTableSource

        scfg = _load_serving()
        pg_conn = store.connect(scfg)
        # cache=True：回測逐訊號日重算因子，每表只讀 PG 一次（一次性 CLI 流程）
        wh: TableSource = PgTableSource(pg_conn, scfg.pg_schema, cache=True)
    else:
        pg_conn = None
        wh = Warehouse(cfg.data_dir)
    strat = load_strategy(strategy)
    cost = load_config(_resolve_costs(costs), CostConfig)
    if cost_scale is not None:
        cost = cost.model_copy(update={"cost_scale": cost_scale})
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)

    try:
        # 指紋在跑回測**之前**取：跑完再取的話，長回測期間若有 ingest 寫入，
        # 記下的會是跑完後的狀態，與這次實際讀到的資料不符。
        fp_tables = _fingerprint_inputs(wh, strat) if out else {}
        res = run_backtest(
            strat,
            cost,
            s,
            e,
            wh,
            initial_capital=capital,
            max_participation=participation,
            credit_dividends=dividends,
            lot_size=lot,
        )
    finally:
        if pg_conn is not None:
            pg_conn.close()
    m = res.metrics
    diag = summarize_backtest(res)
    total_ret = res.final_equity / res.initial_capital - 1.0
    typer.secho(
        f"回測 {strat.name} · {s} ~ {e} · {m.n_days} 交易日"
        f" · {'含息' if dividends else '**價格報酬（--no-dividends）**'}"
        + (f" · 零股（最小 {lot} 股）" if lot != DEFAULT_LOT_SIZE else ""),
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  總報酬 {total_ret * 100:+.2f}%　CAGR {m.cagr * 100:+.2f}%")
    typer.echo(f"  Sharpe {m.sharpe:+.2f}　Sortino {m.sortino:+.2f}　Calmar {m.calmar:+.2f}")
    typer.echo(f"  最大回撤 {m.mdd * 100:.2f}%　勝率 {m.win_rate * 100:.1f}%")
    typer.echo(
        f"  年化周轉 {m.turnover * 100:.0f}%　成交 {len(res.ledger)} 筆　未成交 {len(res.unfilled)} 筆"
    )
    typer.echo(
        f"  成本 {diag.total_cost:,.0f} TWD（手續費 {diag.commission:,.0f}／"
        f"稅 {diag.tax:,.0f}／滑價 {diag.slippage:,.0f}；初始資金 {diag.cost_to_initial:.2%}）"
    )
    gm = diag.same_path_gross_metrics
    typer.echo(
        f"  同成交路徑 gross CAGR {gm.cagr * 100:+.2f}%　Sharpe {gm.sharpe:+.2f}　"
        f"平均持有 {diag.average_holding_sessions:.1f} 交易日"
    )

    source = "pg" if pg else "lake"
    if out:
        from twquant.reporting.result_bundle import (
            bundle_backtest,
            data_fingerprint,
            write_bundle,
        )

        bundle = bundle_backtest(
            res,
            strategy=strat,
            cost=cost,
            start=s,
            end=e,
            initial_capital=capital,
            max_participation=participation,
            source=source,
            data_fingerprint=data_fingerprint(fp_tables),
            data_tables=fp_tables,
            lot_size=lot,
        )
        write_bundle(out, bundle)
        typer.secho(
            f"結果包已寫入 {out}（指紋 {bundle['data_fingerprint']}）", fg=typer.colors.GREEN
        )

    log_metrics = {
        "sharpe": m.sharpe,
        "cagr": m.cagr,
        "mdd": m.mdd,
        "turnover": m.turnover,
        "win_rate": m.win_rate,
        "total_cost": diag.total_cost,
        "same_path_gross_sharpe": gm.sharpe,
        "same_path_gross_cagr": gm.cagr,
    }
    append_research_log(
        cfg.data_dir,
        backtest_log_entries(
            strategy=strat,
            cost=cost,
            start=s,
            end=e,
            initial_capital=capital,
            max_participation=participation,
            source=source,
            metrics=log_metrics,
        ),
    )
    typer.secho("已記入 research_log。", fg=typer.colors.GREEN)


@app.command()
def capacity(
    strategy: str = typer.Option("configs/strategies/sample_revenue_trust.yaml", help="策略 YAML"),
    date: str = typer.Option("", help="訊號日 YYYY-MM-DD，空=最新"),
    participation: float = typer.Option(0.05, help="單股參與率上限"),
) -> None:
    """估策略資金容量上限（目標權重 × 20 日 ADV × 參與率上限）。"""
    from twquant.backtest.capacity import estimate_capacity
    from twquant.data.warehouse import Warehouse
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import load_strategy

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    strat = load_strategy(strategy)
    day = dt.date.fromisoformat(date) if date else wh.latest_date("prices_daily")
    if day is None:
        typer.secho("無行情。", fg=typer.colors.RED)
        raise typer.Exit(1)

    tdf = build_targets(strat, wh, day)
    weights = {r["stock_id"]: float(r["target_weight"]) for r in tdf.to_dicts()}
    if not weights:
        typer.secho("空手日，無容量可估。", fg=typer.colors.YELLOW)
        return
    adv_df = wh.sql(
        "SELECT stock_id, median(amount) AS adv FROM prices_daily WHERE date IN "
        f"(SELECT DISTINCT date FROM prices_daily WHERE date <= DATE '{day}' "
        "ORDER BY date DESC LIMIT 20) GROUP BY stock_id"
    )
    adv = {r["stock_id"]: float(r["adv"]) for r in adv_df.to_dicts()}
    res = estimate_capacity(weights, adv, max_participation=participation)
    typer.secho(f"=== 容量分析 · {strat.name} · {day} ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  資金容量上限：{res.capacity_twd / 1e8:.1f} 億元")
    typer.echo(f"  瓶頸標的：{res.binding_stock}（參與率上限 {participation:.0%}）")


def _insight_client() -> tuple[InsightClient, SystemConfig]:
    from twquant.insight.client import InsightClient
    from twquant.insight.config import load_llm_config

    cfg = _load_system()
    llm = load_llm_config(_CONFIG_DIR / "llm.yaml")
    return InsightClient(llm, cfg.data_dir), cfg


@app.command(name="insight-brief")
def insight_brief(date: str = typer.Option("", help="訊號日 YYYY-MM-DD，空=最新")) -> None:
    """產生每日洞悉摘要（無 API key 時自動降級、不報錯）。"""
    from twquant.conventions import now_tw
    from twquant.data.warehouse import Warehouse
    from twquant.insight.daily_brief import build_daily_brief

    client, cfg = _insight_client()
    wh = Warehouse(cfg.data_dir)
    day = dt.date.fromisoformat(date) if date else wh.latest_date("prices_daily")
    if day is None:
        typer.secho("無行情。", fg=typer.colors.RED)
        raise typer.Exit(1)
    report = wh.sql(
        "SELECT count(*) AS 檔數, round(avg(close),1) AS 平均收盤 "
        f"FROM prices_daily WHERE date = DATE '{day}'"
    ).to_dicts()[0]
    report["date"] = day.isoformat()
    brief = build_daily_brief(client, report, now_tw())
    if brief is None:
        typer.secho(
            "洞悉層離線（未設 ANTHROPIC_API_KEY）或降級——系統其餘功能不受影響。",
            fg=typer.colors.YELLOW,
        )
        return
    typer.secho(f"=== 每日洞悉 · {day} ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(brief.summary)


proposal_app = typer.Typer(help="策略調整提案生命週期（SPEC-008 §4）。")
app.add_typer(proposal_app, name="proposal")


def _proposals_dir() -> Path:
    return _CONFIG_DIR.parent / "proposals"


@proposal_app.command("list")
def proposal_list() -> None:
    """列出所有提案與狀態。"""
    from twquant.reporting.proposals import list_proposals, load_proposal

    ids = list_proposals(_proposals_dir())
    if not ids:
        typer.secho("無提案。", fg=typer.colors.YELLOW)
        return
    for pid in ids:
        d = load_proposal(_proposals_dir(), pid)
        typer.echo(
            f"  {pid}　[{d['status']}]　{d['proposal']['action']}　{d['proposal']['target_strategy']}"
        )


@proposal_app.command("validate")
def proposal_validate(
    pid: str = typer.Argument(..., help="提案 id"),
    start: str = typer.Option("2025-08-01"),
    end: str = typer.Option("2025-12-31"),
) -> None:
    """以提案參數跑回測（機器閘門）：Sharpe 不劣於基準→validated，否則 rejected_by_gate。"""
    import yaml

    from twquant.backtest.costs import CostConfig
    from twquant.backtest.engine import run_backtest
    from twquant.config import load_config as _lc
    from twquant.data.warehouse import Warehouse
    from twquant.errors import ConfigError
    from twquant.reporting.proposals import apply_changes, load_proposal, update_proposal
    from twquant.strategy.config import StrategyConfig

    pdir = _proposals_dir()
    data = load_proposal(pdir, pid)
    prop = data["proposal"]
    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    cost = _lc(_CONFIG_DIR / "costs.yaml", CostConfig)
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)

    base_yaml = yaml.safe_load(
        (_CONFIG_DIR / "strategies" / f"{prop['target_strategy']}.yaml").read_text(encoding="utf-8")
    )
    update_proposal(pdir, pid, status="backtesting")
    try:
        proposed = apply_changes(base_yaml, prop["changes"])
        base_cfg = StrategyConfig.model_validate(base_yaml)
        new_cfg = StrategyConfig.model_validate(proposed)  # 權重加總等驗證即閘門
    except (ConfigError, ValueError) as exc:
        update_proposal(pdir, pid, status="rejected_by_gate", backtest={"error": str(exc)})
        typer.secho(f"[REJECT] 配置驗證失敗：{exc}", fg=typer.colors.RED)
        return

    base_sr = run_backtest(base_cfg, cost, s, e, wh).metrics.sharpe
    new_sr = run_backtest(new_cfg, cost, s, e, wh).metrics.sharpe
    ok = new_sr >= base_sr - 1e-9
    status = "validated" if ok else "rejected_by_gate"
    update_proposal(
        pdir, pid, status=status, backtest={"sharpe_base": base_sr, "sharpe_new": new_sr}
    )
    color = typer.colors.GREEN if ok else typer.colors.RED
    typer.secho(
        f"[{status.upper()}] Sharpe 基準 {base_sr:+.2f} → 提案 {new_sr:+.2f}", fg=color, bold=True
    )


@proposal_app.command("approve")
def proposal_approve(pid: str = typer.Argument(..., help="提案 id")) -> None:
    """人工核准 validated 提案 → approved（進模擬倉觀察期）。"""
    from twquant.reporting.proposals import load_proposal, update_proposal

    d = load_proposal(_proposals_dir(), pid)
    if d["status"] != "validated":
        typer.secho(f"僅能核准 validated 提案（目前 {d['status']}）。", fg=typer.colors.RED)
        raise typer.Exit(1)
    update_proposal(_proposals_dir(), pid, status="approved")
    typer.secho(f"提案 {pid} 已核准 → approved。", fg=typer.colors.GREEN)


@app.command()
def sql(query: str = typer.Argument(..., help="對 clean 資料湖執行的 SQL")) -> None:
    """對資料湖跑 SQL 分析（各 clean 表為同名視圖）。"""
    from twquant.data.warehouse import Warehouse

    cfg = _load_system()
    result = Warehouse(cfg.data_dir).sql(query)
    with pl_config():
        typer.echo(str(result))


serving_app = typer.Typer(
    add_completion=False, help="供應層 PG（SPEC-009）：sync / rebuild / status。"
)
app.add_typer(serving_app, name="serving")


def _load_serving() -> ServingConfig:
    from twquant.config import ServingConfig, load_config

    return load_config(_CONFIG_DIR / "serving.yaml", ServingConfig)


def _echo_sync_results(results: list[object]) -> None:
    from twquant.data.serving.sync import SyncResult

    for r in results:
        assert isinstance(r, SyncResult)
        tail = f"→ {r.last_date}" if r.last_date else "（快照）"
        typer.secho(
            f"[OK] {r.table}：+{r.synced_dates} 日、PG 共 {r.row_count} 列 {tail}",
            fg=typer.colors.GREEN,
        )


@serving_app.command("sync")
def serving_sync() -> None:
    """增量同步 clean 資料湖 → PG 供應層（watermark，冪等）。"""
    from twquant.data.serving import sync as serving_sync_mod
    from twquant.data.warehouse import Warehouse

    wh = Warehouse(_load_system().data_dir)
    _echo_sync_results(list(serving_sync_mod.sync_all(wh, _load_serving())))


@serving_app.command("rebuild")
def serving_rebuild(
    tables: str = typer.Option("", help="逗號分隔的表名；空＝全部鏡射表"),
) -> None:
    """truncate + 重置 watermark 後全量重灌（供來源修訂/初始化）。"""
    from twquant.data.serving import sync as serving_sync_mod
    from twquant.data.warehouse import Warehouse

    tlist = [t.strip() for t in tables.split(",") if t.strip()] or None
    wh = Warehouse(_load_system().data_dir)
    try:
        results: list[object] = list(serving_sync_mod.rebuild(wh, _load_serving(), tlist))
    except ValueError as exc:  # 非鏡射表名（如因子表 composite_daily）→ 清楚報錯而非靜默無效
        typer.secho(f"[ERROR] {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    _echo_sync_results(results)


@serving_app.command("status")
def serving_status() -> None:
    """列出各表 sync_state（last_date / 最後同步時間 / 列數）。"""
    from twquant.data.serving import sync as serving_sync_mod

    rows = serving_sync_mod.status(_load_serving())
    if not rows:
        typer.echo("（sync_state 為空——尚未同步）")
        return
    for r in rows:
        typer.echo(
            f"{r['table']:<24} last_date={r['last_date']!s:<12} "
            f"rows={r['row_count']:<8} synced_at={r['last_synced_at']}"
        )


@serving_app.command("materialize")
def serving_materialize(
    date: str = typer.Option("today", help="單日 YYYY-MM-DD 或 today（= 最新行情日）"),
    from_: str = typer.Option("", "--from", help="範圍起（含）YYYY-MM-DD"),
    to: str = typer.Option("", "--to", help="範圍迄（含）YYYY-MM-DD"),
    factors: str = typer.Option(
        "", help="逗號分隔的因子名；空＝全部。與 composite 集合部分重疊會被拒"
    ),
    trim_lookback: bool = typer.Option(
        False, "--trim-lookback", help="依各因子宣告的 lookback_days 裁切歷史（T-083）"
    ),
) -> None:
    """物化因子（全宇宙 value/z/pct/composite）→ PG。單日或 --from/--to 逐日範圍。

    讀取源改為 PG serving 鏡射表（D-026，全面停用 parquet）：`PgTableSource` 結構滿足
    `PitView` 需要的介面，因子函數不變、只是不再讀本地資料湖。

    **補算少數新因子時務必同時給 `--factors` 與 `--trim-lookback`**：全因子、全歷史模式下
    單日實測超過 10 分鐘，瓶頸是 `chip.broker_*` 的 expanding 統計要掃 `broker_trades`
    全部 5,770 萬列（D-138 §五）。只算短視窗因子時兩者合計可差好幾個量級。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.data.serving import materialize as smat
    from twquant.data.serving import store
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.errors import TwquantError
    from twquant.factors.registry import list_factors

    cfg = _load_serving()
    conn = store.connect(cfg)
    try:
        source = PgTableSource(conn, cfg.pg_schema)
        names = [f.strip() for f in factors.split(",") if f.strip()] or None
        if names:
            unknown = sorted(set(names) - set(list_factors()))
            if unknown:
                raise TwquantError(f"未註冊的因子：{', '.join(unknown)}")
        if from_ and to:
            results = smat.materialize_range(
                source,
                cfg,
                dt.date.fromisoformat(from_),
                dt.date.fromisoformat(to),
                names,
                trim_lookback=trim_lookback,
            )
        else:
            day = None if date == "today" else dt.date.fromisoformat(date)
            results = [smat.materialize_day(source, cfg, day, names, trim_lookback=trim_lookback)]
    finally:
        conn.close()
    for r in results:
        typer.secho(
            f"[OK] {r.as_of}：{r.factor_rows} 因子列、{r.composite_rows} 綜合列、{r.factors} 因子",
            fg=typer.colors.GREEN,
        )


@serving_app.command("broker-flow")
def serving_broker_flow(
    from_: str = typer.Option(..., "--from", help="起日（含）YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="迄日（含）YYYY-MM-DD"),
    cloud: bool = typer.Option(False, "--cloud", help="對雲端（Supabase）而非本機 PG 執行"),
) -> None:
    """從 broker_trades 聚合 (日, 股) 買賣超 → broker_flow_daily（T-055，upsert 冪等）。"""
    from twquant.data.serving import store
    from twquant.data.serving.cloud import refresh_broker_flow
    from twquant.data.serving.schema import ensure_schema

    cfg = _load_serving()
    d0, d1 = dt.date.fromisoformat(from_), dt.date.fromisoformat(to)
    days = [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1)]
    conn = store.connect_cloud(cfg) if cloud else store.connect(cfg)
    try:
        ensure_schema(conn, cfg.pg_schema)  # 首跑時 broker_flow_daily 可能尚未建（冪等）
        n = refresh_broker_flow(conn, cfg.pg_schema, days)
    finally:
        conn.close()
    typer.secho(f"[OK] broker_flow_daily：{from_}~{to} 共 {n} 列", fg=typer.colors.GREEN)


@serving_app.command("prune")
def serving_prune(
    cloud: bool = typer.Option(True, "--cloud/--local", help="預設對雲端執行；--local 打本機 PG"),
) -> None:
    """依 configs/serving.yaml cloud.windows 修剪滾動窗口（D-030，冪等）。"""
    from twquant.data.serving import store
    from twquant.data.serving.cloud import prune
    from twquant.data.serving.schema import GROUP_FLOW_SPEC, ensure_tables

    cfg = _load_serving()
    conn = store.connect_cloud(cfg) if cloud else store.connect(cfg)
    try:
        # 族群資金表由每日「族群資金」步驟建；那一步還沒成功過（例如上線當天族群標籤抓不到）時，
        # 修剪不能因為表不存在就整步失敗——先建空表（冪等）
        ensure_tables(conn, (GROUP_FLOW_SPEC,), cfg.pg_schema)
        results = prune(
            conn,
            cfg.pg_schema,
            dict(cfg.cloud.windows),
            cfg.cloud.stock_pattern,
            tuple(cfg.cloud.pattern_tables),
            tuple(cfg.cloud.industry_member_tables),
            tuple(cfg.cloud.keep_stocks),
        )
    finally:
        conn.close()
    for r in results:
        tail = f"cutoff={r.cutoff}" if r.cutoff else "（未超窗）"
        typer.secho(f"[OK] {r.table:<20} -{r.deleted} 列 {tail}", fg=typer.colors.GREEN)


@serving_app.command("seed-cloud")
def serving_seed_cloud() -> None:
    """本機 PG → 雲端（Supabase）一次性搬運滾動窗口（T-055；重跑＝TRUNCATE 重灌）。"""
    from twquant.data.serving import store
    from twquant.data.serving.cloud import seed_tables
    from twquant.data.serving.schema import ALL_TABLES, SYNC_STATE_SPEC

    cfg = _load_serving()
    tables = [t for t in ALL_TABLES if t != SYNC_STATE_SPEC.name]
    src = store.connect(cfg)
    dst = store.connect_cloud(cfg)
    try:
        results = seed_tables(
            src,
            dst,
            src_schema=cfg.pg_schema,
            dst_schema=cfg.pg_schema,
            tables=tables,
            windows=dict(cfg.cloud.windows),
            stock_pattern=cfg.cloud.stock_pattern,
            pattern_tables=tuple(cfg.cloud.pattern_tables),
        )
    finally:
        src.close()
        dst.close()
    total = sum(r.rows for r in results)
    for r in results:
        typer.echo(f"  {r.table:<24} {r.rows} 列")
    typer.secho(f"[OK] seed-cloud 完成：共 {total} 列", fg=typer.colors.GREEN)


@app.command(name="archive-daily")
def archive_daily(
    date: str = typer.Option("today", help="歸檔日 YYYY-MM-DD 或 today（台北）"),
    out: str = typer.Option("", help="歸檔根目錄；空＝<data_dir>/archive/daily"),
) -> None:
    """每日增量歸檔（T-054）：serving 原始表單日切片 → csv.gz + manifest（冪等覆蓋）。

    保命線（D-030）：落地後由呼叫端（GHA / runbook）rclone 上傳 Drive，見 docs/OPS-archive.md。
    """
    from twquant.conventions import now_tw
    from twquant.data.serving import archive as sarch
    from twquant.data.serving import store

    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)
    root = Path(out) if out else Path(_load_system().data_dir) / "archive" / "daily"
    cfg = _load_serving()
    conn = store.connect(cfg)
    try:
        results = sarch.dump_day(conn, cfg.pg_schema, day, root)
    finally:
        conn.close()
    for r in results:
        typer.echo(f"  {r.table:<24} {r.rows} 列")
    total = sum(r.rows for r in results)
    typer.secho(
        f"[OK] {day} 增量歸檔 {total} 列 → {sarch.day_dir(root, day)}", fg=typer.colors.GREEN
    )


@app.command(name="archive-restore")
def archive_restore(
    date: str = typer.Option(..., help="還原日 YYYY-MM-DD"),
    root: str = typer.Option("", help="歸檔根目錄；空＝<data_dir>/archive/daily"),
) -> None:
    """把某日增量檔還原進 serving schema（ON CONFLICT DO NOTHING 首錄保留，冪等）。"""
    from twquant.data.serving import archive as sarch
    from twquant.data.serving import store

    day = dt.date.fromisoformat(date)
    rootp = Path(root) if root else Path(_load_system().data_dir) / "archive" / "daily"
    cfg = _load_serving()
    conn = store.connect(cfg)
    try:
        results = sarch.restore_day(conn, cfg.pg_schema, day, rootp)
    finally:
        conn.close()
    for r in results:
        typer.echo(f"  {r.table:<24} +{r.rows} 列")
    typer.secho(f"[OK] {day} 還原完成（既有主鍵不覆寫）", fg=typer.colors.GREEN)


@app.command(name="recommendations-import")
def recommendations_import(
    source: str = typer.Argument(..., help="已存在的推薦快照 JSON（依 SPEC-014 §4 驗證）"),
    out: str = typer.Option("", help="快照資料根目錄；空＝configs/system.yaml data_dir"),
) -> None:
    """受控匯入既有 ML 推薦快照；不會訓練、推論或改寫既有 run。"""
    from twquant.reporting.recommendation_snapshot import import_snapshot

    data_dir = Path(out) if out else Path(_load_system().data_dir)
    result = import_snapshot(source, data_dir)
    action = "已保存" if result.created else "內容相同，保留既有快照"
    typer.secho(f"[OK] {action}：{result.path}", fg=typer.colors.GREEN)


@app.command(name="insights-run")
def insights_run(
    run: str = typer.Option(..., help="推薦快照 run_id（先跑 ml export-recommendations）"),
    top: int = typer.Option(30, help="分析前 N 檔候選（D-081b 事前登記 N=30）"),
    out: str = typer.Option("", help="資料根目錄；空＝configs/system.yaml data_dir"),
    window: int = typer.Option(20, help="產業資金流的回看交易日數"),
) -> None:
    """對推薦快照的前 N 檔候選組證據包並產生洞悉分析（SPEC-014 §6、PLAN §3.3）。

    每檔獨立：任一檔失敗只記錄，其餘照常完成。LLM 離線或超預算時仍會保存證據包
    並寫明缺席原因——洞悉是增強不是依賴。相同輸入重跑為冪等，不會產生重複版本。
    """
    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.insight.client import InsightClient
    from twquant.insight.config import load_llm_config
    from twquant.reporting.insight_run import run_insights
    from twquant.reporting.insight_sources import (
        fetch_industry_of,
        fetch_industry_rows,
        fetch_sections,
    )
    from twquant.reporting.recommendation_snapshot import select_snapshots

    data_dir = Path(out) if out else Path(_load_system().data_dir)
    catalog = select_snapshots(data_dir, run_id=run)
    if not catalog.runs:
        raise typer.BadParameter(f"找不到推薦快照 run_id={run}")
    as_of = catalog.runs[0].as_of

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        industries = fetch_industry_rows(conn, scfg.pg_schema, as_of, window)

        def sections(stock_id: str) -> dict[str, object]:
            industry = fetch_industry_of(conn, scfg.pg_schema, stock_id)
            return fetch_sections(
                conn, scfg.pg_schema, stock_id, as_of, industries.get(industry or "")
            )

        client = InsightClient(load_llm_config(_CONFIG_DIR / "llm.yaml"), data_dir)
        summary = run_insights(
            data_dir,
            recommendation_run_id=run,
            top_n=top,
            client=client,
            now=now_tw(),
            fetch_sections=sections,
        )
    finally:
        conn.close()

    typer.secho(
        f"[OK] {run}：{summary.requested} 檔候選 → 分析 {summary.analysed}、"
        f"缺席 {summary.absent}、新存 {summary.saved}、沿用 {summary.reused}",
        fg=typer.colors.GREEN,
    )
    for failure in summary.failures:
        typer.secho(f"  [!] {failure['stock_id']}：{failure['detail']}", fg=typer.colors.YELLOW)


@app.command(name="insights-import")
def insights_import(
    source: str = typer.Argument(..., help="已存在的洞悉證據包 JSON（依 SPEC-014 §6 驗證）"),
    out: str = typer.Option("", help="證據包資料根目錄；空＝configs/system.yaml data_dir"),
) -> None:
    """受控匯入既有洞悉證據包；只驗證與保存，不呼叫任何語言模型。"""
    from twquant.reporting.insight_evidence import import_pack

    data_dir = Path(out) if out else Path(_load_system().data_dir)
    result = import_pack(source, data_dir)
    action = "已保存" if result.created else "內容相同，保留既有證據包"
    typer.secho(f"[OK] {action}：{result.path}", fg=typer.colors.GREEN)


# ── ML 排序層（SPEC-010，T-067）──────────────────────────────────────

fund_flow_app = typer.Typer(
    help="投信／ETF 資金流（SPEC-016）：ETF 基本資料、已發行單位數與每日持股。"
)
app.add_typer(fund_flow_app, name="fund-flow")


@fund_flow_app.command("meta")
def fund_flow_meta() -> None:
    """configs/etf_meta.yaml → PG `etf_meta`（整份重灌）。"""
    from twquant.data.serving import fund_flow_ingest as ffi
    from twquant.data.serving import store

    scfg = _load_serving()
    _, meta = _load_fund_flow()
    with store.connect(scfg) as conn:
        n = ffi.write_meta(conn, scfg.pg_schema, meta)
    typer.secho(f"[OK] etf_meta {n} 檔", fg=typer.colors.GREEN)


@fund_flow_app.command("holdings")
def fund_flow_holdings(
    date: str | None = typer.Option(
        None, "--date", help="以這天為「今天」（預設今天）；補它之前的缺日"
    ),
) -> None:
    """補 ETF 每日持股（元大、統一）：與每日 pipeline 的「ETF 持股」步驟相同。"""
    from twquant.conventions import now_tw

    day = dt.date.fromisoformat(date) if date else now_tw().date()
    typer.secho(f"[OK] {_run_etf_holdings_day(day)}", fg=typer.colors.GREEN)


@fund_flow_app.command("rebalance")
def fund_flow_rebalance() -> None:
    """由 PG 的 ETF 持股整份重算指數換股事件（`index_rebalance_events`，T-337）。"""
    from twquant.data.serving import fund_flow_ingest as ffi
    from twquant.data.serving import store

    scfg = _load_serving()
    _, meta = _load_fund_flow()
    with store.connect(scfg) as conn:
        n = ffi.rebuild_rebalance(conn, scfg.pg_schema, meta)
    typer.secho(f"[OK] index_rebalance_events {n} 筆", fg=typer.colors.GREEN)


@fund_flow_app.command("units")
def fund_flow_units(
    date: str | None = typer.Option(
        None, "--date", help="以這天為「今天」（預設今天）；補它之前的缺日"
    ),
) -> None:
    """補 ETF 已發行單位數（TWSE MI_QFIIS）：與每日 pipeline 的「ETF 單位數」步驟相同。"""
    from twquant.conventions import now_tw

    day = dt.date.fromisoformat(date) if date else now_tw().date()
    typer.secho(f"[OK] {_run_etf_units_day(day)}", fg=typer.colors.GREEN)


ml_app = typer.Typer(help="ML 排序層（SPEC-010）：研究軌訓練與每日推論")
app.add_typer(ml_app, name="ml")


def _load_ml(config_path: str = "") -> MlConfig:
    """載入指定 ML 配置；空字串維持 Phase 1 的 configs/ml.yaml 相容路徑。"""
    from twquant.config import MlConfig, load_config

    path = Path(config_path) if config_path else _CONFIG_DIR / "ml.yaml"
    return load_config(path, MlConfig)


def _ml_features_resolved(cfg: MlConfig) -> list[str]:
    """features 空＝全部註冊因子（SPEC-010 §1），但排除 ml.* 自身——模型分數
    餵回自己的特徵是自我參照洩漏。排序保證欄序決定性。"""
    if cfg.features:
        return list(cfg.features)
    import twquant.factors.builtins  # noqa: F401  # 副作用：註冊全因子
    from twquant.factors import registry

    return sorted(n for n in registry.list_factors() if not n.startswith("ml."))


def _ml_feature_frames(
    conn: object, schema: str, features: list[str], start: dt.date, end: dt.date
) -> dict[str, pl.DataFrame]:
    """factor_values.z 切片 → {因子: (date, stock_id, value)}（逐因子 server 端過濾，
    見 ml_store.read_factor_frames 的 OOM 教訓）。"""
    from twquant.data.serving.ml_store import read_factor_frames

    return read_factor_frames(conn, schema, features, start, end)


@ml_app.command("train")
def ml_train(
    from_: str = typer.Option(..., "--from", help="訓練資料起日（含）YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="訓練資料迄日（含，含金庫期）YYYY-MM-DD"),
    out: str = typer.Option("", help="模型輸出根目錄；空＝<data_dir>/models"),
    config: str = typer.Option("", "--config", help="ML YAML；空＝configs/ml.yaml"),
) -> None:
    """walk-forward 訓練＋部署模型落地＋research_log 登記（SPEC-010 §4）。

    資料源＝PG serving（factor_values.z 特徵、prices_daily.open 標籤）。訓練需完整
    歷史庫（本機/還原庫）；Supabase 滾動窗口禁作訓練源（SPEC-010 §0）。
    """
    import lightgbm
    import polars as pl

    from twquant.backtest.costs import CostConfig
    from twquant.config import config_hash, load_config
    from twquant.data.serving import store
    from twquant.data.serving.ml_store import (
        read_corporate_events,
        read_index_close,
        read_model_rank_pct,
        read_monthly_revenue,
        read_prices_barrier,
        read_prices_close,
        read_prices_open,
        read_prices_training,
    )
    from twquant.ml import train as mlt
    from twquant.ml.context import CONTEXT_FEATURES, market_state_features
    from twquant.ml.dataset import (
        assemble,
        assert_feature_coverage,
        attach_adjustment,
        cost_adjusted_return_labels,
        down_probability_labels,
        excess_rank_labels,
        forward_drawdown_labels,
        forward_open_returns,
        liquid_pool,
        month_end_signal_dates,
        revenue_surprise_labels,
        state_pool,
        tail_up_labels,
        triple_barrier_labels,
    )
    from twquant.ml.splits import walk_forward_splits

    cfg = _load_ml(config)
    if cfg.label.is_price_based and cfg.label.total_return is None:
        # D-054：不給預設值是刻意的。未還原的標籤在 6~8 月有近兩成「輸家」是除息
        # 假象，而除息日事先公告 → 模型會學到「預測誰要除息」且驗收看起來會過關。
        raise typer.BadParameter(
            f"{config or 'configs/ml.yaml'} 的 label 未指定 total_return（D-054）。"
            "含股利的總報酬標籤請寫 `total_return: true`；"
            "要重現舊模型的訓練配方請明寫 `total_return: false`。"
        )
    sys_cfg = _load_system()
    start, end = dt.date.fromisoformat(from_), dt.date.fromisoformat(to)
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        features = _ml_features_resolved(cfg)
        feats = _ml_feature_frames(conn, scfg.pg_schema, features, start, end)
        _readers = {
            "cost_adjusted_return": read_prices_training,
            "forward_drawdown": read_prices_close,  # D-075：回撤以收盤價為基準
            "revenue_surprise": read_prices_close,  # 只用來取交易日曆，欄位不進標籤
            "triple_barrier": read_prices_barrier,  # D-120：開盤進場價＋逐日收盤＋成交金額
            "down_probability": read_prices_barrier,  # D-131：同上
            "tail_up": read_prices_barrier,  # D-143：同上（開盤進場價＋逐日收盤）
        }
        px = _readers.get(cfg.label.transform, read_prices_open)(conn, scfg.pg_schema, start, end)
        events = (
            read_corporate_events(conn, scfg.pg_schema, end) if cfg.label.total_return else None
        )
        revenue = (
            read_monthly_revenue(conn, scfg.pg_schema, start, end)
            if cfg.label.transform == "revenue_surprise"
            else None
        )
        unknown_ctx = sorted(set(cfg.context_features) - set(CONTEXT_FEATURES))
        if unknown_ctx:
            raise typer.BadParameter(
                f"未知的 context_features：{unknown_ctx}；可用：{CONTEXT_FEATURES}"
            )
        index_px = (
            read_index_close(conn, scfg.pg_schema, _CONTEXT_INDEX_ID, start, end)
            if cfg.context_features
            else None
        )
        model_scores = {
            col: read_model_rank_pct(conn, scfg.pg_schema, mid, start, end)
            for col, mid in cfg.model_score_features.items()
        }
    finally:
        conn.close()
    # 標籤宇宙＝四碼普通股（cloud.stock_pattern，同 screener/雲端白名單）：prices 含
    # 數萬檔權證/ETF，混入會稀釋橫斷面排名的意義
    px = px.filter(pl.col("stock_id").str.contains(scfg.cloud.stock_pattern))
    px_lo, px_hi = (
        cast("dt.date | None", px["date"].min()),
        cast("dt.date | None", px["date"].max()),
    )
    typer.echo(f"prices {px.height} 列（{px_lo} ~ {px_hi}）")
    if events is not None:
        px = attach_adjustment(px, events)
        typer.echo(f"除權息事件 {events.height} 筆 → 標籤改用總報酬（D-054）")
    days: list[dt.date] = sorted(px["date"].unique().to_list())
    signal_days = month_end_signal_dates(days) if cfg.label.signal_dates == "month_end" else days
    if cfg.label.signal_dates == "month_end":
        typer.echo(f"月頻訊號日 {len(signal_days)} 個（{signal_days[0]} ~ {signal_days[-1]}）")
    cost_cfg: CostConfig | None = None
    if cfg.label.transform == "revenue_surprise":
        assert revenue is not None  # transform 與讀取分支同源
        typer.echo(f"月營收 {revenue.height} 列（D-074：標籤不含任何價格欄位）")
        labels = revenue_surprise_labels(
            revenue,
            signal_days,
            days,
            max_lag_sessions=cast("int", cfg.label.max_lag_sessions),
        )
    elif cfg.label.transform == "forward_drawdown":
        labels = forward_drawdown_labels(
            px,
            signal_days,
            horizon=cfg.horizon_days,
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
        )
    elif cfg.label.transform == "triple_barrier":
        labels = triple_barrier_labels(
            px,
            horizon=cfg.horizon_days,
            up=cast("float", cfg.label.barrier_up),
            down=cast("float", cfg.label.barrier_down),
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
            vol_scaled=cfg.label.vol_scaled,
            vol_window=cfg.label.vol_window,
        )
    elif cfg.label.transform == "tail_up":
        labels = tail_up_labels(
            px,
            horizon=cfg.horizon_days,
            up=cast("float", cfg.label.barrier_up),
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
        )
    elif cfg.label.transform == "down_probability":
        labels = down_probability_labels(
            px,
            horizon=cfg.horizon_days,
            drop=cast("float", cfg.label.drop_threshold),
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
        )
    elif cfg.label.transform == "cost_adjusted_return":
        cost_cfg = load_config(_CONFIG_DIR / "costs.yaml", CostConfig)
        labels = cost_adjusted_return_labels(
            px,
            horizon=cfg.horizon_days,
            adv_window_sessions=cfg.label.adv_window_sessions,
            position_notional_twd=cfg.label.position_notional_twd,
            downside_penalty=cfg.label.downside_penalty,
            cost_cfg=cost_cfg,
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
        )
    else:
        labels = excess_rank_labels(
            forward_open_returns(
                px,
                cfg.horizon_days,
                adjusted=cast("bool", cfg.label.total_return),
                max_span_sessions=cfg.label.max_span_sessions,
            )
        )
    # T-088 AC-3：任一 pin 特徵在 factor_values 全空即拒絕訓練（Phase 1 曾以 3 個空特徵訓練並回報成功）
    shares = assert_feature_coverage(feats)
    sparsest = sorted(shares.items(), key=lambda kv: kv[1])[:5]
    typer.echo("特徵覆蓋最低 5 個：" + "、".join(f"{n} {v:.0%}" for n, v in sparsest))
    frame = assemble(feats, labels)
    # T-248：個股因子以外的三種欄位。全部 left join——缺值交給 LightGBM 原生處理，但整欄空要擋。
    if index_px is not None:
        ctx = market_state_features(
            index_px, px.select("date", "stock_id", "close"), window=cfg.context_window
        )
        frame = frame.join(ctx.select("date", *cfg.context_features), on="date", how="left")
    for col, scores in model_scores.items():
        if scores.is_empty():
            raise typer.BadParameter(
                f"model_score_features[{col}]：ml_scores 查無 {cfg.model_score_features[col]} 的任何分數"
            )
        frame = frame.join(scores.rename({"rank_pct": col}), on=["date", "stock_id"], how="left")
    features = [*features, *cfg.context_features, *cfg.model_score_features]
    if cfg.sample_min_adv_pctl is not None or cfg.sample_min_price is not None:
        if "amount" not in px.columns or "close" not in px.columns:
            raise typer.BadParameter(
                "sample_min_* 需要 close 與 amount；此 transform 的價格讀取器沒有提供"
            )
        pool = liquid_pool(
            px,
            adv_window=cfg.label.adv_window_sessions,
            min_adv_pctl=cfg.sample_min_adv_pctl,
            min_price=cfg.sample_min_price,
        )
        before = frame.height
        frame = frame.join(pool, on=["date", "stock_id"], how="inner")
        typer.echo(f"流動性樣本池：{before} → {frame.height} 列")
    if cfg.sample_state is not None:
        # D-151：只在某種狀態的股票上訓練、也只在那裡預測——walk-forward 的測試列同樣只剩這些
        spool = state_pool(px, cfg.sample_state, adjusted=cast("bool", cfg.label.total_return))
        before = frame.height
        frame = frame.join(spool, on=["date", "stock_id"], how="inner")
        typer.echo(f"狀態樣本池（{cfg.sample_state.kind}）：{before} → {frame.height} 列")
    if cfg.label.signal_dates == "month_end":
        # 月頻標籤下，其餘交易日的列全是 label null 的空殼——留著只會讓
        # walk_forward_train 對它們逐日推論（十倍以上的無用計算），且讓
        # training_data_hash 綁到與訓練無關的列。切分仍用**完整日曆**，
        # 故 2 年訓練窗的長度不受影響。
        frame = frame.filter(pl.col("date").is_in(signal_days))
    labeled = frame.filter(pl.col("label").is_not_null())
    splits = walk_forward_splits(
        days,
        train_min_days=cfg.split.train_min_days,
        test_days=cfg.split.test_days,
        embargo_days=cfg.split.embargo_days,
        vault_days=cfg.split.vault_days,
    )
    typer.echo(
        f"資料 {len(days)} 交易日、{len(features)} 特徵、{frame.height} 列"
        f"（label 非 null {labeled.height} 列，{cast('dt.date | None', labeled['date'].min())}"
        f" ~ {cast('dt.date | None', labeled['date'].max())}）、"
        f"{len(splits)} 窗"
    )
    objective = mlt.objective_for(cfg.label.transform)
    result = mlt.walk_forward_train(
        frame, splits, features, cfg.lgbm, progress=typer.echo, objective=objective
    )
    ic = mlt.oos_rank_ic(result.oos_scores)
    train_end = days[-cfg.split.vault_days - 1] if cfg.split.vault_days else days[-1]
    deploy = mlt.fit_ranker(
        frame.filter(pl.col("date") <= train_end), features, cfg.lgbm, objective
    )
    model_id = mlt.make_model_id(train_end, cfg, cost_cfg=cost_cfg)
    mdir = (Path(out) if out else Path(sys_cfg.data_dir) / "models") / model_id
    mlt.save_model(
        mdir,
        deploy,
        {
            "model_id": model_id,
            "train_end": train_end.isoformat(),
            "features": features,
            "config_hash": mlt.training_config_hash(cfg, cost_cfg),
            # T-073：artifact 的身分＝配置 × 程式碼 × 資料。三者任一變了就是不同的模型，
            # save_model 會據此拒絕「同 id 不同內容」的覆寫。
            "artifact_fingerprint": mlt.artifact_fingerprint(
                config=mlt.training_config_hash(cfg, cost_cfg),
                code=mlt.training_code_hash(),
                data=mlt.training_data_hash(frame),
            ),
            "code_hash": mlt.training_code_hash(),
            "data_hash": mlt.training_data_hash(frame),
            "data_rows": frame.height,
            "ml_config_hash": config_hash(cfg),
            "cost_config_hash": config_hash(cost_cfg) if cost_cfg is not None else None,
            "label": cfg.label.model_dump(mode="json"),
            "objective": objective,
            "horizon_days": cfg.horizon_days,
            "lightgbm_version": lightgbm.__version__,
            "oos_rank_ic": ic,
            "window_ics": result.window_ics,
            "data_from": from_,
            "data_to": to,
        },
    )
    result.oos_scores.write_parquet(mdir / "oos_scores.parquet")
    mlt.log_training_run(sys_cfg.data_dir, cfg, model_id, ic, cost_cfg=cost_cfg)
    typer.secho(
        f"[OK] {model_id}：OOS RankIC {ic:+.4f}（{len(splits)} 窗）→ {mdir}",
        fg=typer.colors.GREEN,
    )


def _ml_scores_in_pg(model_id: str, day: dt.date) -> int:
    """`ml_scores` 裡某模型某日的列數；用來判斷當日分數是否已進 PG。"""
    from twquant.data.serving import store

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*) FROM "{scfg.pg_schema}"."ml_scores" '
                "WHERE model_id = %s AND date = %s",
                (model_id, day),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _bucket_policy(day: dt.date, *, risk_gate: bool = True) -> BucketPolicy | None:
    """依 configs/ml.yaml 的宣告組出名單分組政策（D-111）；未啟用回 None。

    風控閘門的分數來自 `ml_scores`（D-075 的回撤模型，月頻＋沿用），
    **整天查無分數即 raise**——閘門宣告了卻沒生效，等於名單少了一層而沒有人被告知。
    `risk_gate=False` 只給研究模型的名單用（D-150），不查風控分數。
    """
    from twquant.data.serving import store
    from twquant.errors import DataError
    from twquant.reporting.recommendation_export import BucketPolicy

    cfg = _load_ml().recommendation_buckets
    if not cfg.enabled:
        return None
    risk: dict[str, float] | None = None
    if risk_gate and cfg.risk_gate_model:
        scfg = _load_serving()
        conn = store.connect(scfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT stock_id, rank_pct FROM "{scfg.pg_schema}"."ml_scores" '
                    "WHERE model_id = %s AND date = %s",
                    (cfg.risk_gate_model, day),
                )
                risk = {str(r[0]): float(r[1]) for r in cur.fetchall()}
        finally:
            conn.close()
        if not risk:
            raise DataError(
                "recommendation_export",
                day,
                f"風控閘門模型 {cfg.risk_gate_model} 在 {day} 沒有任何分數。"
                "閘門已在 configs/ml.yaml 宣告卻無法生效——名單會少一層風控而沒有人被告知，"
                "故拒絕匯出。先補該模型當日分數（月頻模型記得 --carry-forward-to）。",
            )
    return BucketPolicy(
        top_n=cfg.top_n,
        watch_n=cfg.watch_n,
        risk_gate_keep_pct=cfg.risk_gate_keep_pct,
        risk_scores=risk,
        liquid_ids=_liquid_ids(day, cfg),
    )


def _research_bucket_policy(base: BucketPolicy | None) -> BucketPolicy | None:
    """D-150：研究模型名單的分組＝生產政策拿掉風控閘門，流動性閘門與檔數照舊。

    風控閘門的回撤模型九成是 20 日波動度，而右尾模型要找的正是高波動股——
    套上去等於替研究策略加一條沒有登記過的選股規則。流動性閘門與 TAIL-B 的宇宙逐字相同，
    所以研究模型的「前 20 名」前 15 檔就是策略持股頁上 TAIL-B 那一天的候選。
    """
    if base is None:
        return None
    return replace(base, risk_gate_keep_pct=1.0, risk_scores=None)


def _export_policy(model_name: str, day: dt.date) -> BucketPolicy | None:
    """`ml export-recommendations` 依模型選政策：研究模型不查風控分數（D-150）。"""
    ml_cfg = _load_ml()
    research = set(ml_cfg.research_live_models) - {ml_cfg.production_ranking_model}
    if model_name in research:
        return _research_bucket_policy(_bucket_policy(day, risk_gate=False))
    return _bucket_policy(day)


def _liquid_ids(day: dt.date, cfg: RecommendationBucketConfig) -> frozenset[str] | None:
    """D-113：當日通過流動性濾網的代號；兩個門檻皆未宣告回 None（閘門不啟用）。

    刻意重用回測的 `universe_ids`（同一個 ADV 定義、同一個百分位母體），
    名單的「流動性合格」才與回測驗過的池子是同一件事。**空集合即 raise**——
    那代表當日行情沒進 PG，而不是「今天沒有一檔夠流動」。
    """
    if cfg.liquidity_min_adv_pctl is None and cfg.liquidity_min_price is None:
        return None
    from twquant.data.serving import store
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.errors import DataError
    from twquant.strategy.config import UniverseConfig
    from twquant.strategy.universe import universe_ids

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        view = PgTableSource(conn, scfg.pg_schema).view(day)
        ucfg = UniverseConfig(
            min_adv_twd=None,
            min_adv_pctl=cfg.liquidity_min_adv_pctl,
            min_price=cfg.liquidity_min_price,
        )
        ids = frozenset(universe_ids(view, day, ucfg))
    finally:
        conn.close()
    if not ids:
        raise DataError(
            "recommendation_export",
            day,
            f"流動性閘門在 {day} 沒有留下任何一檔——多半是當日行情尚未進 PG，拒絕匯出。",
        )
    return ids


def _ml_score_day(
    day: dt.date, model_dir: Path, out_path: Path, *, pg: bool = False
) -> tuple[int, int]:
    """對單日全宇宙推論並寫出 parquet；回傳（列數, upsert 列數）。

    由 `ml score` 與每日 pipeline 共用，兩條路徑因此不可能算出不同的分數。
    """
    import polars as pl

    from twquant.conventions import now_tw
    from twquant.data.serving import store
    from twquant.ml import score as mls
    from twquant.ml import train as mlt
    from twquant.ml.dataset import (
        assemble,
        assert_inference_features_present,
        cross_rank_pct,
    )

    ran_at = now_tw()
    booster, meta = mls.load_model(model_dir, day)
    raw_feats = meta.get("features")
    if not isinstance(raw_feats, list) or not raw_feats:
        raise ValueError(f"{model_dir} meta.json 缺 features：模型不可推論，拒用")
    features = [str(f) for f in raw_feats]
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        feats = _ml_feature_frames(conn, scfg.pg_schema, features, day, day)
        # T-227：整欄空的必要特徵會被 LightGBM 當 NaN 吞掉，分數照出、沒有人被告知。
        assert_inference_features_present(feats, features, day)
        no_labels = pl.DataFrame(
            schema={"date": pl.Date, "stock_id": pl.String, "label": pl.Float64}
        )
        frame = assemble(feats, no_labels).drop("label")
        if frame.is_empty():
            raise ValueError(
                f"{day} 無特徵資料：factor_values 該日為空（先跑 serving materialize）"
            )
        scored = cross_rank_pct(
            mlt.predict_scores(booster, frame, features), "score", "rank_pct"
        ).with_columns(
            pl.lit(model_dir.name).alias("model_id"),
            # parquet 留**實際執行時刻**：推薦快照的匯出以它當 generated_at，
            # 並要求它不早於當日截點（資料到齊後才推論）。
            pl.lit(ran_at).alias("available_at"),
        )
        inserted = 0
        if pg:
            from twquant.conventions import ml_score_available_at
            from twquant.data.serving.ml_store import upsert_ml_scores
            from twquant.data.serving.schema import ensure_schema

            ensure_schema(conn, scfg.pg_schema)  # ml_scores 首寫時可能尚未建表（冪等）
            # PG 寫**資訊可得時刻**（T-228）——與 parquet 那一欄是兩個不同的問題。
            inserted = upsert_ml_scores(
                conn,
                scfg.pg_schema,
                scored.with_columns(
                    pl.lit(ml_score_available_at(day, ran_at)).alias("available_at")
                ),
            )
    finally:
        conn.close()
    scored.write_parquet(out_path)
    return scored.height, inserted


@ml_app.command("score")
def ml_score(
    date: str = typer.Option(..., help="推論日 YYYY-MM-DD（取 train_end < 該日的最新模型）"),
    models: str = typer.Option("", help="模型根目錄；空＝<data_dir>/models"),
    model: str = typer.Option("", help="直接指定模型目錄；給了就不自動選（T-224）"),
    purpose: str = typer.Option(
        "ranking", help="自動選模型時的用途：ranking／risk_gate／revenue_surprise（T-224）"
    ),
    out: str = typer.Option("", help="輸出 parquet；空＝<模型目錄>/scores_<date>.parquet"),
    pg: bool = typer.Option(False, "--pg", help="同時 upsert serving.ml_scores（T-068）"),
) -> None:
    """對單日全宇宙推論 → parquet（＋選擇性 upsert ml_scores 表，SPEC-010 §4/§5）。"""
    from twquant.ml import score as mls

    day = dt.date.fromisoformat(date)
    sys_cfg = _load_system()
    root = Path(models) if models else Path(sys_cfg.data_dir) / "models"
    # 指定目錄優先；否則按用途自動選，同 train_end 有多個候選時 latest_model_dir 會 raise
    # 而不是靜默挑第一個（T-224：曾因此挑到回撤風控模型當選股器）。
    bound = _load_ml().production_ranking_model
    if model:
        mdir = Path(model)
    elif bound:
        mdir = root / bound
    else:
        mdir = mls.latest_model_dir(root, day, purpose=purpose)
    out_path = Path(out) if out else mdir / f"scores_{day}.parquet"
    rows, inserted = _ml_score_day(day, mdir, out_path, pg=pg)
    pg_note = f"、PG +{inserted} 列" if pg else ""
    typer.secho(
        f"[OK] {day}：{rows} 檔 → {out_path}（模型 {mdir.name}）{pg_note}",
        fg=typer.colors.GREEN,
    )


@ml_app.command("export-recommendations")
def ml_export_recommendations(
    date: str = typer.Option(..., help="推論日 YYYY-MM-DD（讀該日既有的 scores parquet）"),
    model: str = typer.Option(..., help="模型目錄（讀其 meta.json 取 model_id 與指紋）"),
    scores: str = typer.Option("", help="推論結果 parquet；空＝<模型目錄>/scores_<date>.parquet"),
    out: str = typer.Option("", help="快照資料根目錄；空＝configs/system.yaml data_dir"),
) -> None:
    """把**既有**日頻推論結果輸出為推薦快照（SPEC-014 §7、T-213）。

    只讀已產生的 scores parquet 與模型 metadata：不重跑推論、不訓練、不啟動新研究試驗。
    輸出一律是 mode=research／published_at=None——訓練成功不等於取得發布資格。
    同一推論日重複執行為冪等；內容不同則拒絕覆寫，失敗時不留半成品。
    """
    from twquant.reporting.recommendation_export import export_scores

    day = dt.date.fromisoformat(date)
    mdir = Path(model)
    scores_path = Path(scores) if scores else mdir / f"scores_{day}.parquet"
    data_dir = Path(out) if out else Path(_load_system().data_dir)
    result = export_scores(scores_path, mdir, data_dir, day, _export_policy(mdir.name, day))
    action = "已保存" if result.created else "內容相同，保留既有快照"
    typer.secho(f"[OK] {day} {action}：{result.path}", fg=typer.colors.GREEN)


def _carry_forward_scores(
    scored: pl.DataFrame, calendar: list[dt.date], signal_days: list[dt.date]
) -> pl.DataFrame:
    """把每個訊號日的分數複製到「該訊號日（含）起、下一個訊號日之前」的每個交易日。

    `available_at` 不變（分數是在原訊號日算出來的），只換 `date`。
    這不是插值也不是外推——分數在下一個訊號日之前本來就沒有更新。
    """
    import polars as pl

    frames = []
    for i, sig in enumerate(signal_days):
        nxt = signal_days[i + 1] if i + 1 < len(signal_days) else None
        span = [d for d in calendar if d >= sig and (nxt is None or d < nxt)]
        if not span:
            continue
        block = scored.filter(pl.col("date") == sig).drop("date")
        frames.append(
            pl.DataFrame({"date": span}, schema={"date": pl.Date}).join(block, how="cross")
        )
    return pl.concat(frames).select(scored.columns).sort("date", "stock_id")


@ml_app.command("score-quiet")
def ml_score_quiet(
    from_: str = typer.Option(..., "--from", help="起日（含）YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="迄日（含）YYYY-MM-DD"),
    chunk_days: int = typer.Option(31, min=1, help="每批幾個日曆天（控制記憶體）"),
) -> None:
    """潛伏飆股（D-163）：安靜股模型對一段日期補打分並寫進 ml_scores（每日排程只打當天）。"""
    start, end = dt.date.fromisoformat(from_), dt.date.fromisoformat(to)
    if end < start:
        raise typer.BadParameter(f"--to {end} 早於 --from {start}")
    lo = start
    while lo <= end:
        hi = min(end, lo + dt.timedelta(days=chunk_days - 1))
        typer.echo(f"{lo} ~ {hi}：{_quiet_score_range(lo, hi)}")
        lo = hi + dt.timedelta(days=1)


@ml_app.command("backfill-scores")
def ml_backfill_scores(
    model: str = typer.Option(..., help="模型目錄（讀其 oos_scores.parquet）"),
    carry_forward_to: str = typer.Option(
        "",
        "--carry-forward-to",
        help="月頻模型專用：把每個訊號日的分數沿用到下一個訊號日之前的每個交易日，"
        "最後一個訊號日沿用到此日（YYYY-MM-DD）。空＝不沿用",
    ),
) -> None:
    """把 walk-forward 的**樣本外**分數寫進 ml_scores，供回測（SPEC-010 §7 第 3 級）。

    回測必須用「該日當時可用的模型」算的分數——`oos_scores.parquet` 正是各窗以
    ≤train_end 資料訓練、對其後測試段推論的結果，天然滿足此條件；用最終模型回頭
    對全期推論則是前視。available_at 設為該交易日 19:00（盤後推論可得），使
    `PitView`（20:00 截點）在訊號日當天即看得到。

    註：各列實際來自不同窗的模型，統一標記為部署模型的 model_id（薄因子以前綴取用）。
    研究軌專用；生產分數由 `ml score` 逐日寫入。

    **`--carry-forward-to`（月頻模型必要）**：D-074／D-075 的模型每月只在月底出一次
    分數，而策略在每月第一個週一再平衡——不沿用的話 `prefilter` 當天撈不到任何列，
    池子會整個空掉（而不是報錯）。沿用的列 `available_at` 保留**原訊號日**的 19:00，
    因為那才是這個數字實際被算出來的時間；`date` 則是它適用的交易日。
    沿用不是插值也不是外推：分數在下一個訊號日之前本來就沒有更新。
    """
    import json

    import polars as pl

    from twquant.conventions import ML_SCORE_AVAILABLE_HOUR, TZ
    from twquant.data.serving import store
    from twquant.data.serving.ml_store import upsert_ml_scores
    from twquant.data.serving.schema import ensure_schema
    from twquant.ml.dataset import cross_rank_pct

    mdir = Path(model)
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    oos = pl.read_parquet(mdir / "oos_scores.parquet")
    scored = cross_rank_pct(oos, "score", "rank_pct").with_columns(
        pl.lit(str(meta["model_id"])).alias("model_id"),
        pl.col("date")
        .cast(pl.Datetime("us"))
        .dt.offset_by(f"{ML_SCORE_AVAILABLE_HOUR}h")
        .dt.replace_time_zone(str(TZ))
        .alias("available_at"),
    )
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        ensure_schema(conn, scfg.pg_schema)
        if carry_forward_to:
            until = dt.date.fromisoformat(carry_forward_to)
            signal_days = sorted(scored["date"].unique().to_list())
            if until <= signal_days[-1]:
                raise typer.BadParameter(
                    f"--carry-forward-to {until} 不晚於最後一個訊號日 {signal_days[-1]}，"
                    "沿用不會產生任何新列"
                )
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT DISTINCT date FROM "{scfg.pg_schema}"."prices_daily" '
                    "WHERE date BETWEEN %s AND %s ORDER BY date",
                    (signal_days[0], until),
                )
                calendar = [r[0] for r in cur.fetchall()]
            scored = _carry_forward_scores(scored, calendar, signal_days)
            typer.echo(
                f"沿用：{len(signal_days)} 個訊號日 → {scored['date'].n_unique()} 個交易日"
                f"（至 {until}），{scored.height} 列"
            )
        n = upsert_ml_scores(conn, scfg.pg_schema, scored)
    finally:
        conn.close()
    d0 = cast("dt.date", scored["date"].min())
    d1 = cast("dt.date", scored["date"].max())
    typer.secho(
        f"[OK] ml_scores +{n} 列（{d0} ~ {d1}、"
        f"{scored['stock_id'].n_unique()} 檔、model {meta['model_id']}）",
        fg=typer.colors.GREEN,
    )


@ml_app.command("eval")
def ml_eval(
    model: str = typer.Option(..., help="模型目錄（含 meta.json 與 oos_scores.parquet）"),
    buckets: int = typer.Option(10, help="分位數（SPEC-010 §7 第 2 級）"),
) -> None:
    """評估階梯第 1-2 級：模型 OOS 分數 vs 等權基準組合（SPEC-010 §7、AC-9）。

    基準＝`zscore_weighted` 等權（direction 帶符號），刻意不調參——基準若也可調，
    「打贏基準」就失去意義。兩者用同一批 (date, stock_id) 與同一批標籤，對照才成立。
    """
    import json

    import polars as pl

    import twquant.factors.builtins  # noqa: F401  # 副作用：註冊全因子（取 direction）
    from twquant.data.serving import store
    from twquant.data.serving.ml_store import read_factor_long
    from twquant.factors import registry
    from twquant.ml import evaluate as ev
    from twquant.reporting.research_log import append_research_log
    from twquant.strategy.combine import zscore_weighted

    cfg = _load_ml()
    mdir = Path(model)
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    oos = pl.read_parquet(mdir / "oos_scores.parquet")
    features = [str(f) for f in meta["features"]]
    lo = cast("dt.date", oos["date"].min())
    hi = cast("dt.date", oos["date"].max())
    typer.echo(f"模型 {meta['model_id']}｜OOS {lo} ~ {hi}｜{oos.height} 筆、{len(features)} 特徵")

    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        long = read_factor_long(conn, scfg.pg_schema, features, lo, hi)
    finally:
        conn.close()
    # 兩組基準：全因子等權，以及 D-020 裁決的實證有效子集等權。前者是 D-020 已證實
    # 無預測力的 composite，只打贏它不足以宣稱增值；後者才是真正該跨過的門檻。
    subset = [f for f in scfg.composite_factors if f in features]
    specs = [("base_all", "基準A 全因子等權", features)]
    if subset:
        specs.append(("base_d020", "基準B D-020 有效子集", subset))
    both = oos
    for col, _, feats in specs:
        w = ev.equal_weight_signed({f: registry.get(f).direction for f in feats})
        b = zscore_weighted(long.filter(pl.col("factor").is_in(feats)), w).rename({"score": col})
        # inner join：三者必須在完全相同的 (date, stock) 上比較，否則宇宙差異冒充成績差異
        both = both.join(b, on=["date", "stock_id"], how="inner")
    typer.echo(
        f"共同樣本 {both.height} 筆｜基準A 全因子等權 {len(features)} 個"
        + (f"；基準B D-020 有效子集 {len(subset)} 個（{', '.join(subset)}）" if subset else "")
    )

    rows: list[tuple[str, str, ev.IcSummary, float, bool]] = []
    for col, name in [("score", "ML（LightGBM）")] + [(c, n) for c, n, _ in specs]:
        ic = ev.daily_rank_ic(both, col, "label")
        s = ev.ic_summary(ic, nw_lag=cfg.horizon_days)
        tbl = ev.decile_table(both, col, "label", k=buckets)
        rows.append((col, name, s, ev.spread(tbl), ev.is_monotonic(tbl)))
        typer.echo(
            f"\n{name}\n  RankIC {s.mean:+.4f}｜IR {s.ir:.3f}｜勝率 {s.hit_rate:.1%}"
            f"｜t(NW,{cfg.horizon_days}) {s.t_newey_west:.2f}（樸素 {s.t_naive:.2f}）"
            f"\n  分位頭尾 {ev.spread(tbl):+.4f}｜嚴格單調 {'是' if ev.is_monotonic(tbl) else '否'}"
        )
    ml = rows[0]
    # 判定對「最強基準」（IC 最高者）——打贏最弱的那個不算數，避免挑軟柿子宣布勝利
    tough = max(rows[1:], key=lambda r: r[2].mean)
    lift = ml[2].mean - tough[2].mean
    verdict = "ML 勝出" if lift > 0 else "ML 未勝出"
    typer.secho(
        f"\n=== 階梯第 1 級：{verdict}（對最強基準「{tough[1]}」）｜RankIC 差 {lift:+.4f}"
        f"（ML {ml[2].mean:+.4f} vs {tough[2].mean:+.4f}）、分位頭尾差 {ml[3] - tough[3]:+.4f} ===",
        fg=typer.colors.GREEN if lift > 0 else typer.colors.RED,
        bold=True,
    )
    append_research_log(
        _load_system().data_dir,
        [
            {
                "entry_type": "ml_eval",
                "strategy": meta["model_id"],
                "factor": "ml" if col == "score" else col,
                "params_hash": str(meta.get("config_hash", "")),
                "date": str(hi),
                "metric_name": "oos_rank_ic",
                "metric_value": s.mean,
                "trial_scope": f"ml:h{cfg.horizon_days}",
            }
            for col, _, s, _, _ in rows
        ],
    )


def pl_config() -> pl.Config:
    import polars as pl

    return pl.Config(tbl_rows=30, tbl_cols=20, fmt_str_lengths=40)


if __name__ == "__main__":
    app()
