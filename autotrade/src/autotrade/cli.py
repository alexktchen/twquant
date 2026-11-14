"""指令列：檢查設定、用期交所逐筆重播一天、把狀態寫成檔案給網站、通知手機、跑跟雲端溝通的中繼程式。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path

from autotrade.broker.sim import SimBroker
from autotrade.config import Config, ConfigError, load_config
from autotrade.delivery import (
    ENV_CHAT,
    ENV_TOKEN,
    ENV_VAPID,
    Failure,
    Outbox,
    PrintSender,
    PushPost,
    Sender,
    SendError,
    WebPushSender,
    parse_push_subscriptions,
    push_senders,
    pywebpush_post,
    telegram_from_env,
    telegram_tell_chat_ids,
)
from autotrade.drill import Drill, drill_config, load_prices
from autotrade.engine import Engine, Event
from autotrade.export import dumps, env_value, snapshot
from autotrade.feed.recorded import read_recorded_day
from autotrade.feed.replay import read_taifex_day
from autotrade.journal import Journal, read_journal
from autotrade.model import TAIPEI
from autotrade.notify import Channel, Level, Notifier, Report, sample, where_of
from autotrade.relay import CONSUMERS, Relay, open_pg, run
from autotrade.strategy.base import NullStrategy, Strategy
from autotrade.strategy.momentum import MomentumStrategy, load_original

STRATEGIES = ("none", "momentum")
FEEDS = ("taifex", "recorded")
NOTIFY_MODES = ("print", "send")  # print＝印在畫面上（不連網）；send＝真的送到手機
PUSH_LABEL = "網站推播"


def build_strategy(name: str, cfg: Config, ms_repo: Path | None) -> Strategy:
    """依名稱建立策略。momentum 需要網友原版程式的資料夾。"""
    if name == "none":
        return NullStrategy()
    if name == "momentum":
        if ms_repo is None:
            raise ValueError(
                "--strategy momentum 要給 --ms-repo（網友原版 Momentum_Scalping 的資料夾）"
            )
        if cfg.momentum is None:
            raise ValueError("設定檔沒有 [momentum]")
        mods, params = load_original(ms_repo)
        return MomentumStrategy(mods, params, cfg.momentum, cfg.contract)
    raise ValueError(f"不認得的策略：{name!r}（可用：{STRATEGIES}）")


def read_env(names: Sequence[str], env_file: Path | None) -> dict[str, str]:
    """讀指定名稱的環境變數；環境裡沒有就從 env_file 找同名的變數。只讀這幾個，不印出來。"""
    text = env_file.read_text(encoding="utf-8") if env_file is not None else ""
    found: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name) or env_value(text, name)
        if value:
            found[name] = value
    return found


def failure_writer(path: Path | None, channel: str = "Telegram") -> Callable[[Failure], None]:
    """沒送出去的通知：印到 stderr；有給檔案就再記一列 JSON（只增不改）。channel＝哪個管道。"""

    def write(failure: Failure) -> None:
        state = "沒送出去" if failure.final else "還在重試"
        print(f"通知{state}（{channel}）：{failure.title}（{failure.reason}）", file=sys.stderr)
        if path is not None:
            row = {
                "channel": channel,
                "ts": failure.ts.isoformat(),
                "title": failure.title,
                "reason": failure.reason,
                "attempts": failure.attempts,
                "final": failure.final,
            }
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    return write


def load_push_senders(cfg: Config, push_subs: Path, env_file: Path | None) -> list[WebPushSender]:
    """讀本機的推播訂閱檔，每台裝置一個送出端（用網站的 VAPID 私密金鑰）。"""
    if cfg.notify_push is None:
        raise ConfigError("設定檔沒有 [notify_push]，不能送網站推播")
    subs = parse_push_subscriptions(
        push_subs.read_text(encoding="utf-8"), cfg.notify_push.allowed_hosts
    )
    return push_senders(read_env((ENV_VAPID,), env_file), cfg.notify_push, subs, push_factory)


def push_factory(key: str, subject: str) -> PushPost:
    """真的送網站推播的方式（測試會換掉 pywebpush_post）。"""
    return pywebpush_post(key, subject)


def open_notifier(
    cfg: Config,
    mode: str,
    env_file: Path | None,
    notify_log: Path | None,
    push_subs: Path | None = None,
) -> Notifier:
    """建立重播用的通知：print 印在畫面上，send 用 .env 的金鑰真的送；給 push_subs 再加網站推播。背景執行緒在這裡開。"""
    if cfg.notify is None:
        raise ConfigError("設定檔沒有 [notify]，不能開通知")
    if mode not in NOTIFY_MODES:
        raise ValueError(f"不認得的通知方式：{mode!r}（可用：{NOTIFY_MODES}）")
    if push_subs is not None and mode != "send":
        raise ValueError("--push-subs 只能配 --notify send（網站推播沒有印在畫面上的模式）")
    sender: Sender = (
        PrintSender()
        if mode == "print"
        else telegram_from_env(read_env((ENV_TOKEN, ENV_CHAT), env_file), cfg.notify)
    )
    extra: list[Channel] = []
    if push_subs is not None:
        devices = load_push_senders(cfg, push_subs, env_file)  # 沒有 [notify_push] 會在這裡 raise
        assert cfg.notify_push is not None
        level = Level[cfg.notify_push.min_level.upper()]
        for device in devices:
            name = f"{PUSH_LABEL} {device.host}"
            box = Outbox.from_config(
                device, cfg.notify, on_failure=failure_writer(notify_log, name)
            )
            extra.append(Channel(name, box, level))
    outbox = Outbox.from_config(sender, cfg.notify, on_failure=failure_writer(notify_log))
    outbox.start()
    for ch in extra:
        ch.outbox.start()
    return Notifier(cfg, outbox, replay=True, extra=extra)


def fan_out(sinks: Sequence[Callable[[Event], None]]) -> Callable[[Event], None] | None:
    """把引擎事件依序交給每個接收端；一個都沒有就回 None。"""
    if not sinks:
        return None

    def deliver(event: Event) -> None:
        for sink in sinks:
            sink(event)

    return deliver


def run_replay(
    config: Path,
    ticks: Path,
    day: date,
    strategy: str,
    journal: Path | None = None,
    ms_repo: Path | None = None,
    status_out: Path | None = None,
    notify: str | None = None,
    env_file: Path | None = None,
    notify_log: Path | None = None,
    push_subs: Path | None = None,
    feed: str = "taifex",
) -> dict[str, float | int | str]:
    """用模擬券商重播一天，回傳當日摘要。

    給 journal 就寫事件日誌，給 status_out 就寫給網站的狀態檔，給 notify 就把那天的通知印出來或送出去。
    """
    cfg = load_config(config)
    if feed == "recorded":
        # 富邦即時錄下來的逐筆（含買賣價，A-013）；檔案只有一個合約，不用挑月份
        contract, rows = (
            "recorded",
            read_recorded_day(ticks, day, cfg.session.start, cfg.session.end),
        )
    elif feed == "taifex":
        contract, rows = read_taifex_day(
            ticks, cfg.contract.symbol, day, cfg.session.start, cfg.session.end
        )
    else:
        raise ValueError(f"不認得的行情來源：{feed!r}（可用：{FEEDS}）")
    if push_subs is not None and notify is None:
        raise ValueError("--push-subs 要配 --notify send")
    notifier = (
        open_notifier(cfg, notify, env_file, notify_log, push_subs) if notify is not None else None
    )
    log: Journal | None = None
    report: Report | None = None
    try:
        log = Journal(journal) if journal is not None else None
        sinks: list[Callable[[Event], None]] = []
        if log is not None:
            sinks.append(log.write)  # 日誌先寫，通知在後
        if notifier is not None:
            sinks.append(notifier.on_event)
        engine = Engine(
            cfg, build_strategy(strategy, cfg, ms_repo), SimBroker(cfg.sim), sink=fan_out(sinks)
        )
        for tick in rows:
            engine.on_tick(tick)
            if notifier is not None:
                notifier.poll(tick.ts)
        summary = engine.finish()
    finally:
        if log is not None:
            log.close()
        if notifier is not None:
            report = notifier.close()
    if status_out is not None:
        last = engine.last_tick
        if last is None:
            raise ValueError(f"{day} 沒有行情，寫不出狀態檔")
        snap = snapshot(engine, last.ts, source="replay", status="closed", strategy=strategy)
        write_text_atomic(status_out, dumps(snap))
    result: dict[str, float | int | str] = {
        "day": day.isoformat(),
        "contract": contract,
        "ticks": len(rows),
        **summary,
    }
    if report is not None:
        result |= {"notify_sent": report.sent, "notify_failed": report.lost}
        if push_subs is not None:
            result |= {
                "push_sent": sum(ch.sent for ch in report.channels),
                "push_failed": sum(ch.lost for ch in report.channels),
            }
    return result


def write_text_atomic(path: Path, text: str) -> None:
    """先寫暫存檔再改名，讀的人不會看到寫到一半的檔。資料夾不存在就 raise。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_relay(
    config: Path,
    folder: Path,
    consumer: str | None,
    status: Path | None,
    dsn_env: str,
    env_file: Path | None,
    once: bool,
) -> None:
    """跑中繼程式：資料庫 ↔ 本機資料夾裡的指令檔與回報檔。跟交易程式是不同的程序。"""
    cfg = load_config(config)
    if cfg.control is None:
        raise ConfigError("設定檔沒有 [control]，沒有東西要中繼")
    if not folder.is_dir():
        raise FileNotFoundError(f"資料夾不存在：{folder}")
    dsn = os.environ.get(dsn_env)
    if not dsn and env_file is not None:
        dsn = env_value(env_file.read_text(encoding="utf-8"), dsn_env)
    if not dsn:
        raise ConfigError(f"環境變數 {dsn_env} 沒有設定")
    db = open_pg(dsn, timeout_s=cfg.control.poll_s)
    relay = Relay(db, consumer or cfg.mode, folder, cfg.control.poll_s, status, ensure=db.ensure)
    try:
        run(relay, once=once)
    except KeyboardInterrupt:
        print("中繼程式停止")


def run_notify_test(
    config: Path,
    env_file: Path | None,
    level: str,
    channel: str = "telegram",
    push_subs: Path | None = None,
) -> int:
    """送一則測試通知到手機；送出去回 0，沒送出去印出原因並回 1。"""
    cfg = load_config(config)
    if cfg.notify is None:
        raise ConfigError("設定檔沒有 [notify]，不能送測試通知")
    message = sample(datetime.now(TAIPEI), Level[level.upper()], where_of(cfg, replay=False))
    if channel == "push":
        if push_subs is None:
            raise ConfigError(
                "--channel push 要給 --push-subs（tools/pull_push_subs.py 拉下來的訂閱檔）"
            )
        failed = 0
        for device in load_push_senders(cfg, push_subs, env_file):
            try:
                device.send(message.text, silent=False)
            except SendError as exc:
                failed += 1
                print(f"{device.host}：沒送出去（{exc.reason}）")
                continue
            print(f"{device.host}：已送出")
        return 1 if failed else 0
    sender = telegram_from_env(read_env((ENV_TOKEN, ENV_CHAT), env_file), cfg.notify)
    silent = cfg.notify.quiet_routine and message.level is Level.INFO
    try:
        sender.send(message.text, silent=silent)
    except SendError as exc:
        print(f"測試通知沒送出去：{exc.reason}")
        return 1
    print("已送出 1 則測試通知，請看手機的 Telegram。")
    return 0


def run_notify_setup(config: Path, env_file: Path | None) -> int:
    """請機器人在 Telegram 裡回覆每個跟它說過話的人自己的收件代號（不印在電腦上）。"""
    cfg = load_config(config)
    if cfg.notify is None:
        raise ConfigError("設定檔沒有 [notify]")
    token = read_env((ENV_TOKEN,), env_file).get(ENV_TOKEN, "")
    try:
        told = telegram_tell_chat_ids(token, cfg.notify.timeout_s)
    except SendError as exc:
        print(f"沒辦法問 Telegram：{exc.reason}")
        return 1
    if told == 0:
        print(
            "還沒有人跟機器人說過話。請在手機的 Telegram 打開你的機器人，"
            "按「開始」（或傳任何一句話），再執行一次。"
        )
        return 1
    print(
        f"機器人在 Telegram 回覆了 {told} 個人。請把手機上看到的那串數字放進 .env 的 {ENV_CHAT}，"
        "再執行 notify-test。"
    )
    return 0


def run_drill(
    config: Path,
    ticks: Path,
    day: date,
    start: str,
    folder: Path,
    minutes: float,
    speed: float,
    status: Path | None,
    feed: str = "taifex",
) -> int:
    """演練遠端緊急停止：模擬券商、買 1 口抱著，等網站按停止。全程不碰券商。"""
    cfg = drill_config(load_config(config))
    prices = load_prices(cfg, ticks, day, datetime.strptime(start, "%H:%M:%S").time(), feed)
    print(
        f"演練開始：{cfg.contract.symbol} 用 {day} {start} 之後的價格（{len(prices)} 筆，{speed:g} 倍速），"
        f"最多 {minutes:g} 分鐘。等一下會先買 1 口；請在網站按「緊急停止」。Ctrl+C 結束。"
    )
    drill = Drill(cfg, prices, folder, speed=speed, status=status)
    try:
        report = drill.run(minutes * 60)
    except KeyboardInterrupt:
        print("演練中斷")
        return 1
    halted = "已停手" if report.halt else "沒有停手"
    print(
        f"演練結束：播了 {report.ticks} 筆行情；收到 {report.stops} 次遠端停止；{halted}；"
        f"帳上 {report.position} 口"
    )
    return 0


def run_admin(
    config_dir: Path,
    data_dir: Path,
    ticks_dir: Path | None,
    env_file: Path | None,
    port: int,
    open_browser: bool,
    recorded_dir: Path | None = None,
) -> int:
    """開本機後台（只聽 127.0.0.1），按 Ctrl+C 結束；結束時把還在跑的演練一起停掉。"""
    from autotrade.admin import Admin, serve
    from autotrade.jobs import Jobs

    for folder in (config_dir, data_dir):
        if not folder.is_dir():
            raise FileNotFoundError(f"資料夾不存在：{folder}")
    # 開出來的程式在套件的資料夾裡執行，不是這裡；路徑先換成完整的，才不會指到別的地方
    config_dir, data_dir = config_dir.resolve(), data_dir.resolve()
    ticks_dir = None if ticks_dir is None else ticks_dir.resolve()
    recorded_dir = None if recorded_dir is None else recorded_dir.resolve()
    env_file = None if env_file is None else env_file.resolve()
    admin = Admin(
        config_dir, ticks_dir, Jobs(data_dir, env_file=env_file), recorded_dir=recorded_dir
    )
    server = serve(admin, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    admin.console.say(f"後台開好了：{url}（只有這台電腦連得到；Ctrl+C 結束）")
    info = admin.process
    admin.console.say(f"執行的程式：{info['script']}（程序編號 {info['pid']}）")
    admin.console.say(f"用的 Python：{info['python']}")
    admin.console.say(f"在哪個資料夾執行：{info['cwd']}")
    admin.console.say(f"設定檔資料夾：{config_dir.resolve()}；演練資料夾：{data_dir.resolve()}")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        admin.console.say("後台關閉")
    finally:
        server.server_close()
        admin.jobs.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(prog="autotrade", description="獨立的程式交易系統")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-config", help="讀設定檔並驗證")
    check.add_argument("config", type=Path)
    replay = sub.add_parser("replay", help="用期交所逐筆檔重播一天（模擬券商）")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument(
        "--ticks",
        type=Path,
        required=True,
        help="期交所 Daily_*.csv，或 --feed recorded 時的錄製檔",
    )
    replay.add_argument(
        "--feed",
        choices=FEEDS,
        default="taifex",
        help="taifex＝期交所盤後逐筆檔；recorded＝富邦即時錄下來的（tools/export_recorded_ticks.py 匯出）",
    )
    replay.add_argument("--day", type=date.fromisoformat, required=True)
    replay.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    replay.add_argument("--ms-repo", type=Path, help="網友原版 Momentum_Scalping 的資料夾")
    replay.add_argument("--journal", type=Path, help="事件日誌要寫到哪個檔案（請給絕對路徑）")
    replay.add_argument("--status-out", type=Path, help="給網站的狀態檔要寫到哪（請給絕對路徑）")
    replay.add_argument(
        "--notify",
        choices=sorted(NOTIFY_MODES),
        help="那一天的通知：print 印在畫面上（不連網）、send 真的送到手機",
    )
    replay.add_argument("--notify-log", type=Path, help="沒送出去的通知記到哪個檔案（絕對路徑）")
    replay.add_argument(
        "--push-subs", type=Path, help="另外送網站推播：tools/pull_push_subs.py 拉下來的訂閱檔"
    )
    relay = sub.add_parser("relay", help="中繼程式：把遠端停止指令拿下來、把結果與狀態送上去")
    relay.add_argument("--config", type=Path, required=True)
    relay.add_argument("--dir", type=Path, required=True, help="跟交易程式共用的資料夾（絕對路徑）")
    relay.add_argument("--consumer", choices=CONSUMERS, help="預設用設定檔的 mode")
    relay.add_argument("--status", type=Path, help="狀態檔；有更新就送上去")
    relay.add_argument("--dsn-env", default="TWQUANT_PG_DSN", help="放連線字串的環境變數名稱")
    relay.add_argument("--env-file", type=Path, help="環境變數沒設時，改從這個 .env 讀同名的變數")
    relay.add_argument("--once", action="store_true", help="只跑一輪就結束")
    drill = sub.add_parser("drill", help="演練遠端緊急停止：模擬券商、買 1 口抱著，等網站按停止")
    drill.add_argument("--config", type=Path, required=True)
    drill.add_argument(
        "--ticks", type=Path, required=True, help="拿它的價格：期交所 Daily_*.csv，或錄製檔"
    )
    drill.add_argument(
        "--feed", choices=FEEDS, default="taifex", help="recorded＝富邦即時錄下來的（A-013）"
    )
    drill.add_argument("--day", type=date.fromisoformat, required=True)
    drill.add_argument("--from", dest="start", default="09:00:00", help="從那一天的幾點開始播")
    drill.add_argument("--dir", type=Path, required=True, help="跟中繼程式共用的資料夾（絕對路徑）")
    drill.add_argument("--minutes", type=float, default=10.0, help="最多跑幾分鐘")
    drill.add_argument("--speed", type=float, default=1.0, help="幾倍速播價格")
    drill.add_argument(
        "--status", type=Path, help="把狀態寫到這個檔（給中繼程式送上網站；預設不寫）"
    )
    admin = sub.add_parser("admin", help="開本機後台：用瀏覽器改設定、開演練（只聽 127.0.0.1）")
    admin.add_argument("--config-dir", type=Path, required=True, help="設定檔的資料夾")
    admin.add_argument("--data-dir", type=Path, required=True, help="演練的紀錄放哪（絕對路徑）")
    admin.add_argument("--ticks-dir", type=Path, help="期交所逐筆檔的資料夾（演練拿價格）")
    admin.add_argument(
        "--recorded-dir", type=Path, help="富邦即時錄的逐筆匯出檔的資料夾（演練優先用這裡的）"
    )
    admin.add_argument("--env-file", type=Path, help="交給中繼程式的 .env（後台自己不讀）")
    admin.add_argument("--port", type=int, default=8765)
    admin.add_argument("--no-open", action="store_true", help="不要自動打開瀏覽器")
    verify = sub.add_parser("verify-journal", help="驗證事件日誌的雜湊鏈")
    verify.add_argument("journal", type=Path)
    test = sub.add_parser("notify-test", help="送一則測試通知到手機（要連網，請自己執行）")
    test.add_argument("--level", choices=["info", "warn", "act"], default="info", help="哪一級")
    test.add_argument(
        "--channel", choices=["telegram", "push"], default="telegram", help="送到哪個管道"
    )
    test.add_argument("--push-subs", type=Path, help="--channel push 用的訂閱檔")
    setup = sub.add_parser("notify-setup", help="請機器人在 Telegram 裡告訴你收件代號")
    for command in (replay, test, setup):
        command.add_argument(
            "--env-file", type=Path, help="環境變數沒設時，從這個 .env 讀通知的金鑰與收件代號"
        )
    for command in (test, setup):
        command.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "check-config":
        cfg = load_config(args.config)
        print(
            f"設定沒問題：{cfg.mode}、{cfg.contract.symbol}、部位 {cfg.risk.min_position}～{cfg.risk.max_position} 口"
        )
        return 0
    if args.command == "admin":
        return run_admin(
            args.config_dir,
            args.data_dir,
            args.ticks_dir,
            args.env_file,
            args.port,
            not args.no_open,
            args.recorded_dir,
        )
    if args.command == "drill":
        return run_drill(
            args.config,
            args.ticks,
            args.day,
            args.start,
            args.dir,
            args.minutes,
            args.speed,
            args.status,
            args.feed,
        )
    if args.command == "relay":
        run_relay(
            args.config,
            args.dir,
            args.consumer,
            args.status,
            args.dsn_env,
            args.env_file,
            args.once,
        )
        return 0
    if args.command == "verify-journal":
        records = read_journal(args.journal)
        print(f"日誌沒問題：{len(records)} 筆")
        return 0
    if args.command in ("notify-test", "notify-setup"):
        # 這兩個是使用者自己動手跑的：設定沒弄好時講人話，不丟一長串錯誤
        try:
            if args.command == "notify-test":
                return run_notify_test(
                    args.config, args.env_file, args.level, args.channel, args.push_subs
                )
            return run_notify_setup(args.config, args.env_file)
        except ConfigError as exc:
            print(f"設定還沒好：{exc}")
            return 2
    summary = run_replay(
        args.config,
        args.ticks,
        args.day,
        args.strategy,
        args.journal,
        args.ms_repo,
        args.status_out,
        args.notify,
        args.env_file,
        args.notify_log,
        args.push_subs,
        feed=args.feed,
    )
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0
