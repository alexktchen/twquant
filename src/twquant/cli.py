"""twquant CLI 進入點。

第一版只提供開發閘門 `check`（CLAUDE.md §2）。資料/回測等指令在後續任務加入。
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from collections.abc import Callable

    import polars as pl

    from twquant.config import ServingConfig, SystemConfig
    from twquant.data.http import HttpClient
    from twquant.data.sources.indices import TpexIndexSource, TwseIndexSource
    from twquant.insight.client import InsightClient

app = typer.Typer(add_completion=False, help="台股量化平台 CLI。")

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


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


def _load_system() -> SystemConfig:
    from twquant.config import SystemConfig, load_config

    return load_config(_CONFIG_DIR / "system.yaml", SystemConfig)


def _ingest_date(day: dt.date, tables: list[str] | None, *, tolerant: bool) -> int:
    """抓→清洗→存單日每日來源（TWSE+TPEX）。tolerant=True 時單表失敗只警告不中斷。

    來源由註冊表驅動（D-017）：新增每日來源 = data/ingest/builtins.py 一筆 register，
    不改本函數；Warehouse 於寫入站以 IngestSink 埠使用（儲存亦可換）。回傳成功表數。
    """
    import twquant.data.ingest.builtins  # noqa: F401  # 匯入觸發來源註冊
    from twquant.data.ingest.orchestrator import run_ingest
    from twquant.data.ingest.registry import list_specs
    from twquant.data.warehouse import Warehouse

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)

    def _announce(tag: str, table: str) -> None:
        typer.secho(f">> [{tag}] 抓取 {table} @ {day}", fg=typer.colors.CYAN)

    outcomes = run_ingest(
        list_specs(cadence="daily"),
        cfg,
        wh,
        day,
        data_dir=cfg.data_dir,
        tables=tables,
        tolerant=tolerant,
        on_start=_announce,
    )
    for o in outcomes:
        if o.status == "success":
            typer.secho(f"[OK] {o.source_tag}/{o.table}：{o.rows} 列已存入", fg=typer.colors.GREEN)
        else:
            typer.secho(f"[SKIP] {o.source_tag}/{o.table}：{o.detail}", fg=typer.colors.YELLOW)
    return sum(1 for o in outcomes if o.status == "success")


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


def _make_progress(total: int) -> Callable[[str, int], None]:
    """建 thread-safe 單行進度列（\\r 重繪）。回傳 update(label, advance)。

    顯示：進度條、整體百分比、完成/總數、目前日期+股號、起始時刻、已用時間、
    預估結束時刻（以平均速率外推）。total=0 時百分比視為 100%（防除零）。
    """
    import threading
    import time

    from twquant.conventions import now_tw

    lock = threading.Lock()
    state = {"done": 0}
    t0 = time.monotonic()
    started = now_tw()

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
            typer.echo(
                f"\r[{bar}] {frac * 100:5.1f}% {done}/{total}"
                f"｜{label}｜開始 {started:%H:%M:%S} 已用 {_fmt_hms(elapsed)}"
                f" 預估結束 {eta}".ljust(100),
                nl=False,
            )

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


def _seconds_until(now: dt.datetime, hh: int, mm: int) -> float:
    """距離下一個 hh:mm（同時區）的秒數；已過今日該時刻則算明日。"""
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


@app.command()
def scheduler(
    at: str = typer.Option("16:40", help="每日觸發時刻 HH:MM（台北）"),
) -> None:
    """常駐排程（v1）：每日於 at 時刻跑 daily 擷取。完整 pipeline 見 T-023。"""
    import time

    from twquant.conventions import now_tw

    hh, mm = (int(x) for x in at.split(":"))
    typer.secho(f"排程啟動：每日 {at} (Asia/Taipei) 觸發擷取", fg=typer.colors.CYAN, bold=True)
    while True:
        delay = _seconds_until(now_tw(), hh, mm)
        typer.secho(f"下次觸發：{delay / 3600:.1f} 小時後", fg=typer.colors.CYAN)
        time.sleep(delay)
        try:
            _ingest_date(now_tw().date(), None, tolerant=True)
        except Exception as exc:  # noqa: BLE001  # 排程不因單日失敗而死；記錄後續跑
            typer.secho(f"[排程錯誤] {exc}", fg=typer.colors.RED)


@app.command(name="ingest-revenue")
def ingest_revenue(
    month: str = typer.Option(..., help="營收月份 YYYY-MM（例如 2026-05）"),
) -> None:
    """抓某月 MOPS 月營收（上市+上櫃）、清洗、存入 clean 資料湖。"""
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
            typer.secho(f"[SKIP] {market}：{exc}", fg=typer.colors.YELLOW)
            continue
        record(
            cfg.data_dir, f"mops_{market}", "monthly_revenue", month, "success", rows=clean.height
        )
        typer.secho(f"[OK] {market} {month}：{clean.height} 家已存入", fg=typer.colors.GREEN)


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


@app.command()
def pipeline(date: str = typer.Option("today", help="交易日 YYYY-MM-DD 或 today")) -> None:
    """每日營運 pipeline：擷取→品質閘門→訊號生成，寫 heartbeat（LOOP §3）。"""
    from twquant.conventions import now_tw
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.pipeline import PipelineStep, run_pipeline
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import load_strategy

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    day = now_tw().date() if date == "today" else dt.date.fromisoformat(date)

    def _ingest_step() -> str:
        return f"{_ingest_date(day, None, tolerant=True)} 表"

    def _gate_step() -> str:
        latest = wh.latest_date("prices_daily")
        if latest != day:  # 訊號前硬性斷言：最新行情日 == 今日（D-008）
            raise DataError("prices_daily", day, f"最新行情日 {latest} ≠ 今日 {day}")
        return f"latest={latest}"

    def _signal_step() -> str:
        strat = load_strategy(_CONFIG_DIR / "strategies" / "sample_revenue_trust.yaml")
        return f"{build_targets(strat, wh, day).height} 檔目標"

    def _serving_step() -> str:
        # 尾波：鏡射同步 + 物化當日因子 → PG 供應層（display-only，best-effort）。
        from twquant.data.serving import materialize as smat
        from twquant.data.serving import store
        from twquant.data.serving import sync as ssync

        scfg = _load_serving()
        if not scfg.enabled:
            return "未啟用（serving.yaml enabled=false）"
        if not store.ping(scfg):
            return "PG 不可達，略過（display-only）"
        ssync.sync_all(wh, scfg)
        m = smat.materialize_day(wh, scfg, day)
        return f"鏡射同步 + 物化 {m.factor_rows} 因子列"

    steps = [
        PipelineStep("擷取", _ingest_step),
        PipelineStep("品質閘門", _gate_step, critical=True),
        PipelineStep("訊號生成", _signal_step, skip_if_degraded=True),
        PipelineStep("供應層", _serving_step),
    ]
    res = run_pipeline(day, steps, data_dir=cfg.data_dir, git_hash=_git_hash(), now=now_tw())
    typer.secho(f"=== 每日 pipeline @ {day} ===", fg=typer.colors.CYAN, bold=True)
    for s in res.steps:
        color = {"ok": typer.colors.GREEN, "failed": typer.colors.RED}.get(
            s.status, typer.colors.YELLOW
        )
        typer.secho(f"  [{s.status.upper()}] {s.name}：{s.detail}", fg=color)
    tag = "降級（沿用前日權重）" if res.degraded else "正常"
    typer.secho(f"結果：{tag}，訊號{'已' if res.signals_produced else '未'}產出", bold=True)


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
) -> None:
    """對因子跑 IC 檢驗（RankIC t-stat、多空價差），印出並記入 research_log。"""
    import hashlib

    import polars as pl

    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.data.warehouse import Warehouse
    from twquant.factors.compute import compute
    from twquant.factors.ic import compute_ic_report
    from twquant.reporting.research_log import append_research_log, trials_count

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    as_of = dt.date.fromisoformat(end) if end else wh.latest_date("prices_daily")
    if as_of is None:
        typer.secho("資料湖無行情，先 ingest/backfill。", fg=typer.colors.RED)
        raise typer.Exit(1)

    view = wh.view(as_of)
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
    append_research_log(cfg.data_dir, entries)
    n = trials_count(cfg.data_dir, f"ic:{factor}")
    typer.secho(
        f"已記入 research_log（trial_scope ic:{factor} 累計 {n} 組參數）", fg=typer.colors.GREEN
    )


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


@app.command()
def backtest(
    strategy: str = typer.Option(..., help="策略 YAML 路徑"),
    start: str = typer.Option(..., help="回測起 YYYY-MM-DD"),
    end: str = typer.Option(..., help="回測迄 YYYY-MM-DD"),
    capital: float = typer.Option(10_000_000.0, help="初始資金"),
) -> None:
    """跑回測，印績效指標並記入 research_log（純量化，不需 LLM）。"""
    import hashlib

    from twquant.backtest.costs import CostConfig
    from twquant.backtest.engine import run_backtest
    from twquant.config import load_config
    from twquant.data.warehouse import Warehouse
    from twquant.reporting.research_log import append_research_log
    from twquant.strategy.config import load_strategy

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)
    strat = load_strategy(strategy)
    cost = load_config(_CONFIG_DIR / "costs.yaml", CostConfig)
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)

    res = run_backtest(strat, cost, s, e, wh, initial_capital=capital)
    m = res.metrics
    total_ret = res.final_equity / res.initial_capital - 1.0
    typer.secho(
        f"回測 {strat.name} · {s} ~ {e} · {m.n_days} 交易日", fg=typer.colors.CYAN, bold=True
    )
    typer.echo(f"  總報酬 {total_ret * 100:+.2f}%　CAGR {m.cagr * 100:+.2f}%")
    typer.echo(f"  Sharpe {m.sharpe:+.2f}　Sortino {m.sortino:+.2f}　Calmar {m.calmar:+.2f}")
    typer.echo(f"  最大回撤 {m.mdd * 100:.2f}%　勝率 {m.win_rate * 100:.1f}%")
    typer.echo(
        f"  年化周轉 {m.turnover * 100:.0f}%　成交 {len(res.ledger)} 筆　未成交 {len(res.unfilled)} 筆"
    )

    phash = hashlib.sha256(f"{strat.name}|{start}|{end}".encode()).hexdigest()[:12]
    append_research_log(
        cfg.data_dir,
        [
            {
                "entry_type": "backtest",
                "strategy": strat.name,
                "factor": "",
                "params_hash": phash,
                "date": e.isoformat(),
                "metric_name": name,
                "metric_value": float(val),
                "trial_scope": f"bt:{strat.name}",
            }
            for name, val in [("sharpe", m.sharpe), ("cagr", m.cagr), ("mdd", m.mdd)]
        ],
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
) -> None:
    """物化因子（全宇宙 value/z/pct/composite）→ PG。單日或 --from/--to 逐日範圍。"""
    from twquant.data.serving import materialize as smat
    from twquant.data.warehouse import Warehouse

    wh = Warehouse(_load_system().data_dir)
    cfg = _load_serving()
    if from_ and to:
        results = smat.materialize_range(
            wh, cfg, dt.date.fromisoformat(from_), dt.date.fromisoformat(to)
        )
    else:
        day = None if date == "today" else dt.date.fromisoformat(date)
        results = [smat.materialize_day(wh, cfg, day)]
    for r in results:
        typer.secho(
            f"[OK] {r.as_of}：{r.factor_rows} 因子列、{r.composite_rows} 綜合列、{r.factors} 因子",
            fg=typer.colors.GREEN,
        )


def pl_config() -> pl.Config:
    import polars as pl

    return pl.Config(tbl_rows=30, tbl_cols=20, fmt_str_lengths=40)


if __name__ == "__main__":
    app()
