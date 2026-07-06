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
    import polars as pl

    from twquant.config import SystemConfig
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
    """抓→清洗→存單日 TWSE 資料。tolerant=True 時，非交易日/單表失敗只警告不中斷。

    回傳成功存入的表數。
    """
    from collections.abc import Mapping
    from typing import Any

    from twquant.data.clean import CLEANERS, TPEX_CLEANERS
    from twquant.data.http import HttpClient
    from twquant.data.sources import Source
    from twquant.data.sources.tpex import TpexSource, browser_client
    from twquant.data.sources.twse_rwd import TwseRwdSource
    from twquant.data.warehouse import Warehouse
    from twquant.errors import DataError
    from twquant.reporting.ingest_log import record

    cfg = _load_system()
    wh = Warehouse(cfg.data_dir)

    twse = TwseRwdSource(client=HttpClient(source="twse_rwd", rate=cfg.source_rates["twse_rwd"]))
    tpex = TpexSource(client=browser_client(cfg.source_rates["tpex"]))
    # (來源標籤, 來源物件, clean 函數表)
    plans: list[tuple[str, Source, Mapping[str, Any]]] = [
        ("twse", twse, CLEANERS),
        ("tpex", tpex, TPEX_CLEANERS),
    ]

    ok = 0
    for source_tag, src, cleaners in plans:
        for table in tables or src.tables:
            if table not in cleaners:
                continue
            typer.secho(f">> [{source_tag}] 抓取 {table} @ {day}", fg=typer.colors.CYAN)
            try:
                raw = src.fetch(table, day)
                clean = cleaners[table](raw, day)
                wh.write("clean", table, clean, source=source_tag)
            except DataError as exc:
                record(cfg.data_dir, source_tag, table, day, "failed", detail=str(exc))
                if not tolerant:
                    raise
                typer.secho(f"[SKIP] {source_tag}/{table}：{exc}", fg=typer.colors.YELLOW)
                continue
            record(cfg.data_dir, source_tag, table, day, "success", rows=clean.height)
            typer.secho(
                f"[OK] {source_tag}/{table}：{clean.height} 列已存入", fg=typer.colors.GREEN
            )
            ok += 1
    return ok


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


def _daterange(start: dt.date, end: dt.date) -> list[dt.date]:
    """start..end（含端點）的所有日曆日。"""
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


@app.command()
def backfill(
    from_: str = typer.Option(..., "--from", help="起始日 YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="結束日 YYYY-MM-DD"),
    skip_existing: bool = typer.Option(True, help="跳過已抓過的日期（續傳）"),
) -> None:
    """回補一段期間的免費來源日資料（TWSE+TPEX）。跳週末、跳假日、可續傳。"""
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
        n = _ingest_date(day, None, tolerant=True)
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

    steps = [
        PipelineStep("擷取", _ingest_step),
        PipelineStep("品質閘門", _gate_step, critical=True),
        PipelineStep("訊號生成", _signal_step, skip_if_degraded=True),
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


def pl_config() -> pl.Config:
    import polars as pl

    return pl.Config(tbl_rows=30, tbl_cols=20, fmt_str_lengths=40)


if __name__ == "__main__":
    app()
