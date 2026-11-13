"""A-006 第二管道：網站 App（PWA）的推播（SPEC.md §4 A-006 AC14～AC20）。"""

from __future__ import annotations

import json
import threading
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli
from autotrade.broker.sim import SimBroker
from autotrade.cli import main
from autotrade.config import ConfigError, NotifyPushConfig, parse_config
from autotrade.delivery import (
    ENV_CHAT,
    ENV_TOKEN,
    ENV_VAPID,
    MemorySender,
    Outbox,
    Outgoing,
    PushSubscription,
    SendError,
    WebPushSender,
    parse_push_subscriptions,
    push_file_text,
    push_senders,
)
from autotrade.engine import Engine
from autotrade.model import Side, Tick
from autotrade.notify import Channel, Level, Notifier
from test_a002_engine import write_csv
from test_a006_notify import (
    ACT,
    CONFIG_PATH,
    TRADE_ROWS,
    FakeHttp,
    Stuck,
    at,
    cfg,
    ev,
    item,
    killed_then_released,
    tick,
)
from test_a010_broker_requirements import FlakyBroker, Scripted

APPLE = "https://web.push.apple.com/QGuQyavXutnMH7"
GOOGLE = "https://fcm.googleapis.com/fcm/send/dPa2xyz"
KEY = "fake-vapid-private-key-for-tests"
PUSH_URL = "https://twquant.pages.dev/?tab=futures"


def push_cfg() -> NotifyPushConfig:
    loaded = parse_config(raw_config()).notify_push
    assert loaded is not None
    return loaded


def raw_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as fh:
        return tomllib.load(fh)


def sub(endpoint: str = APPLE) -> PushSubscription:
    return PushSubscription(endpoint=endpoint, p256dh="BPubKeyForTests", auth="authsecret")


class FakePush:
    """假的推播服務：記下每個請求，照劇本回狀態碼；劇本用完就回 201。"""

    def __init__(self, *replies: int | BaseException) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, s: PushSubscription, data: str, ttl: int, timeout: float, headers: dict[str, str]
    ) -> int:
        self.calls.append(
            {"sub": s, "data": json.loads(data), "ttl": ttl, "timeout": timeout, "headers": headers}
        )
        answer = self.replies.pop(0) if self.replies else 201
        if isinstance(answer, BaseException):
            raise answer
        return answer


def sender(
    *replies: int | BaseException, s: PushSubscription | None = None
) -> tuple[WebPushSender, FakePush]:
    post = FakePush(*replies)
    return WebPushSender(s or sub(), push_cfg(), post), post


def notifier_with_push(min_level: Level = Level.ACT) -> tuple[Notifier, MemorySender, MemorySender]:
    config = cfg()
    tele, push = MemorySender(), MemorySender()

    def box(s: MemorySender) -> Outbox:
        return Outbox(
            s, warn_after_attempts=3, retry_wait_s=2.0, queue_size=200, wait=lambda x: False
        )

    channel = Channel("網站推播", box(push), min_level)
    return Notifier(config, box(tele), extra=(channel,)), tele, push


# ---- AC14 分級


def test_ac14_only_urgent_messages_reach_the_second_channel() -> None:
    r = killed_then_released()  # 成交、緊急停止（要處理）、成交、解除
    events = r.engine.events
    n, tele, push = notifier_with_push()
    for event in events:
        n.on_event(event)
    report = n.close()
    titles = [t.splitlines()[0] for t, _ in tele.sent]
    assert len(titles) == 4  # Telegram 照樣收全部
    assert [t.splitlines()[0] for t, _ in push.sent] == [ACT + "緊急停止"]
    (ch,) = report.channels
    assert (ch.name, ch.sent, ch.lost) == ("網站推播", 1, 0)
    assert (report.sent, report.lost) == (4, 0)


def test_ac14_min_level_can_include_warnings() -> None:
    n, tele, push = notifier_with_push(Level.WARN)
    stop = {"order_id": "S1", "symbol": "TMF", "side": "BUY", "qty": 1, "price": 27000.0}
    n.on_event(ev(1, "fill", "成交", **stop))
    n.on_event(ev(2, "stop", "單一部位未實現損益 -610 元，達停損 500.0 元，系統平倉"))
    n.close()
    assert len(tele.sent) == 2
    assert [t.splitlines()[0] for t, _ in push.sent] == ["⚠️ 注意【模擬】微台｜停損：程式正在平倉"]


# ---- AC15 每台裝置各自一條佇列


def test_ac15_a_stuck_device_does_not_hold_up_telegram_or_the_engine() -> None:
    config = cfg()
    assert config.notify is not None
    tele = MemorySender()
    stuck = Stuck()
    main_box = Outbox.from_config(tele, config.notify)
    push_box = Outbox.from_config(stuck, config.notify)
    main_box.start()
    push_box.start()
    n = Notifier(config, main_box, extra=(Channel("網站推播", push_box, Level.ACT),))
    engine = Engine(
        config, Scripted({0: Side.BUY}), FlakyBroker(SimBroker(config.sim)), sink=n.on_event
    )
    engine.on_tick(tick(0))
    engine.on_tick(tick(1))
    engine.kill(at(2))
    for s in range(3, 6):
        engine.on_tick(tick(s))
    assert stuck.entered.wait(timeout=60)  # 推播卡在送「緊急停止」
    deadline = threading.Event()
    for _ in range(600):  # Telegram 那邊照樣全部送到
        if len(tele.sent) == 3:
            break
        deadline.wait(0.05)
    assert len(tele.sent) == 3 and engine.ledger.position == 0
    stuck.release.set()
    report = n.close()
    assert report.sent == 3 and report.channels[0].sent == 1


# ---- AC16 推播內容


def test_ac16_payload_headers_and_unique_tags() -> None:
    s, post = sender()
    s.send(item(1).text, silent=True)
    s.send(item(2).text, silent=False)
    first, second = post.calls
    assert first["data"] == {
        "title": "第 1 則",
        "body": "內容",
        "url": PUSH_URL,
        "tag": first["data"]["tag"],
    }
    assert first["data"]["tag"].startswith("autotrade-")
    assert first["data"]["tag"] != second["data"]["tag"]
    assert first["headers"] == {"Urgency": "high"}
    assert first["ttl"] == push_cfg().ttl_s and first["timeout"] == push_cfg().timeout_s
    assert first["sub"] == sub()


def test_ac16_one_sender_per_device_signed_with_the_website_key() -> None:
    posts: list[tuple[str, str]] = []

    def factory(key: str, subject: str) -> FakePush:
        posts.append((key, subject))
        return FakePush()

    senders = push_senders({ENV_VAPID: KEY}, push_cfg(), [sub(APPLE), sub(GOOGLE)], factory)
    assert [s.host for s in senders] == ["web.push.apple.com", "fcm.googleapis.com"]
    assert posts == [(KEY, push_cfg().subject)]
    with pytest.raises(ConfigError, match=ENV_VAPID) as info:
        push_senders({}, push_cfg(), [sub()], factory)
    assert KEY not in str(info.value)


# ---- AC17 推播服務的回應


def test_ac17_responses_are_sorted_into_sent_retry_or_gone() -> None:
    for ok in (200, 201, 202):
        s, _ = sender(ok)
        s.send("x", silent=True)

    def fails(answer: int | BaseException) -> SendError:
        s, _ = sender(answer)
        with pytest.raises(SendError) as info:
            s.send("x\ny", silent=False)
        return info.value

    for gone in (404, 410):
        error = fails(gone)
        assert error.retryable is False
        assert (
            "失效" in error.reason
            and "重新開通知" in error.reason
            and "pull_push_subs" in error.reason
        )
    for again in (429, 500, 503, OSError("connection reset")):
        assert fails(again).retryable is True
    wrong_key = fails(403)
    assert wrong_key.retryable is False and ENV_VAPID in wrong_key.reason
    assert fails(400).retryable is False


def test_ac17_errors_name_only_the_push_service_host() -> None:
    s, _ = sender(OSError(f"cannot reach {APPLE} with key {KEY}"))
    with pytest.raises(SendError) as info:
        s.send("x", silent=True)
    shown = str(info.value) + repr(info.value) + repr(s)
    assert "web.push.apple.com" in shown
    assert APPLE not in shown and "QGuQyavXutnMH7" not in shown and KEY not in shown
    assert "authsecret" not in shown and "BPubKeyForTests" not in shown
    s, _ = sender(410)
    box = Outbox(s, warn_after_attempts=3, retry_wait_s=0.0, queue_size=5)
    box.put(Outgoing(at(1), "🚨 要處理【模擬】微台｜緊急停止", "🚨 要處理\n內容", silent=False))
    box.close(0.0)
    (failure,) = box.failures
    assert failure.final and "web.push.apple.com" in failure.reason and APPLE not in failure.reason


# ---- AC18 訂閱檔


def test_ac18_subscription_file_round_trip_and_validation() -> None:
    hosts = push_cfg().allowed_hosts
    rows = [(APPLE, "BPubKeyForTests", "authsecret"), (GOOGLE, "BPub2", "auth2")]
    text = push_file_text(rows)
    assert parse_push_subscriptions(text, hosts) == [
        PushSubscription(APPLE, "BPubKeyForTests", "authsecret"),
        PushSubscription(GOOGLE, "BPub2", "auth2"),
    ]
    assert push_file_text(rows) == text  # 決定性
    bad_cases = [
        ("[]", "格式"),
        (json.dumps({"schema": 1, "subscriptions": []}), "一台"),
        (push_file_text([("http://web.push.apple.com/x", "k", "a")]), "https"),
        (push_file_text([("https://evil.example.com/x", "k", "a")]), "evil.example.com"),
        (json.dumps({"schema": 1, "subscriptions": [{"endpoint": APPLE, "auth": "a"}]}), "p256dh"),
        (json.dumps({"schema": 2, "subscriptions": []}), "版本"),
    ]
    for bad, message in bad_cases:
        with pytest.raises(ConfigError, match=message) as info:
            parse_push_subscriptions(bad, hosts)
        assert "QGuQyavXutnMH7" not in str(info.value)
    # 子網域也算（例如 Windows 的推播服務會用不同的子網域）
    sub_host = "https://db5p.notify.windows.com/w/?token=abc"
    assert parse_push_subscriptions(push_file_text([(sub_host, "k", "a")]), hosts)[0].host == (
        "db5p.notify.windows.com"
    )


def test_ac18_pull_tool_keeps_only_the_named_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util

    path = Path(__file__).parents[1] / "tools" / "pull_push_subs.py"
    spec = importlib.util.spec_from_file_location("pull_push_subs", path)
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    seen: list[tuple[str, tuple[object, ...]]] = []

    class Conn:
        def __enter__(self) -> Conn:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def execute(self, sql: str, params: tuple[object, ...] = ()) -> Conn:
            seen.append((sql, params))
            return self

        def fetchall(self) -> list[tuple[str, str, str]]:
            return [(APPLE, "BPubKeyForTests", "authsecret")]

        def fetchone(self) -> tuple[bool]:
            return (True,)

    class FakePsycopg:
        @staticmethod
        def connect(dsn: str) -> Conn:
            assert dsn == "postgresql://u:p@h/db"
            return Conn()

    monkeypatch.setattr(tool.importlib, "import_module", lambda name: FakePsycopg)
    env = tmp_path / ".env"
    env.write_text(
        "TWQUANT_SUPABASE_DSN=postgresql://u:p@h/db\nAUTOTRADE_PUSH_EMAIL=Me@Example.com\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TWQUANT_SUPABASE_DSN", raising=False)
    monkeypatch.delenv("AUTOTRADE_PUSH_EMAIL", raising=False)
    out = tmp_path / "push_subscriptions.json"
    assert tool.main(["--out", str(out), "--env-file", str(env)]) == 0
    printed = capsys.readouterr().out
    assert "1 台裝置" in printed and "web.push.apple.com" in printed
    assert APPLE not in printed and "Me@Example.com" not in printed and "authsecret" not in printed
    query = [sql for sql, _ in seen if "push_subscriptions" in sql and "SELECT endpoint" in sql]
    assert query and all(
        not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in seen
    )
    assert ("me@example.com",) in [params for _, params in seen]
    assert parse_push_subscriptions(out.read_text(encoding="utf-8"), push_cfg().allowed_hosts) == [
        sub()
    ]
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


# ---- AC19 設定


def test_ac19_notify_push_config_is_validated() -> None:
    loaded = parse_config(raw_config()).notify_push
    assert loaded is not None and loaded.min_level == "act" and loaded.url == PUSH_URL
    bad: list[tuple[str, object, str]] = [
        ("min_level", "loud", "min_level"),
        ("url", "http://twquant.pages.dev/", "url"),
        ("ttl_s", 0, "ttl_s"),
        ("ttl_s", 2.5, "ttl_s"),
        ("timeout_s", float("inf"), "timeout_s"),
        ("allowed_hosts", [], "allowed_hosts"),
        ("allowed_hosts", "web.push.apple.com", "allowed_hosts"),
        ("subject", "twquant", "subject"),
    ]
    for key, value, message in bad:
        raw = raw_config()
        raw["notify_push"][key] = value
        with pytest.raises(ConfigError, match=message):
            parse_config(raw)
    raw = raw_config()
    del raw["notify"]
    with pytest.raises(ConfigError, match=r"\[notify_push\].*\[notify\]"):
        parse_config(raw)
    raw = raw_config()
    del raw["notify_push"]
    assert parse_config(raw).notify_push is None


# ---- AC20 指令列


def fake_push_factory(post: FakePush) -> Any:
    return lambda key, subject: post


def subs_file(tmp_path: Path, *endpoints: str) -> Path:
    path = tmp_path / "push_subscriptions.json"
    path.write_text(
        push_file_text([(e, "BPubKeyForTests", "authsecret") for e in endpoints]), encoding="utf-8"
    )
    return path


def test_ac20_notify_test_push_reaches_every_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    post = FakePush(201, 410)
    monkeypatch.setattr(cli, "pywebpush_post", fake_push_factory(post))
    monkeypatch.setenv(ENV_VAPID, KEY)
    path = subs_file(tmp_path, APPLE, GOOGLE)
    args = [
        "notify-test",
        "--config",
        str(CONFIG_PATH),
        "--channel",
        "push",
        "--push-subs",
        str(path),
    ]
    assert main([*args, "--level", "act"]) == 1  # 第二台已失效
    out = capsys.readouterr().out
    assert "web.push.apple.com：已送出" in out and "fcm.googleapis.com：沒送出去" in out
    assert APPLE not in out and KEY not in out
    assert [c["data"]["title"] for c in post.calls] == [ACT + "測試通知"] * 2
    monkeypatch.delenv(ENV_VAPID)
    assert main(args) == 2
    assert ENV_VAPID in capsys.readouterr().out


def test_ac20_replay_sends_urgent_ones_to_both_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ticks = write_csv(tmp_path / "Daily_2026_11_13.csv", TRADE_ROWS, encoding="utf-8-sig")
    post = FakePush()
    monkeypatch.setattr(cli, "pywebpush_post", fake_push_factory(post))
    http = FakeHttp()
    monkeypatch.setattr(urllib.request, "urlopen", http)
    monkeypatch.setenv(ENV_VAPID, KEY)
    monkeypatch.setenv(ENV_TOKEN, "12345:fake-token-for-tests-only")
    monkeypatch.setenv(ENV_CHAT, "987654321")
    path = subs_file(tmp_path, APPLE)
    day = ["--ticks", str(ticks), "--day", "2026-11-13", "--strategy", "none"]
    args = [
        "replay",
        "--config",
        str(CONFIG_PATH),
        *day,
        "--notify",
        "send",
        "--push-subs",
        str(path),
    ]
    # 買一口、賣一口：這一天只有成交（一般通知），網站推播一則都不該收到
    monkeypatch.setattr(
        cli, "build_strategy", lambda name, c, ms_repo: Scripted({0: Side.BUY, 2: Side.SELL})
    )
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "notify_sent: 2" in out and "push_sent: 0" in out and "push_failed: 0" in out
    assert post.calls == []  # 這一天沒有要處理的事
    assert len(http.calls) == 2
    with pytest.raises(ValueError, match="--push-subs"):
        main(
            [
                "replay",
                "--config",
                str(CONFIG_PATH),
                *day,
                "--notify",
                "print",
                "--push-subs",
                str(path),
            ]
        )


def test_ac20_replay_failure_log_names_the_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ticks = write_csv(tmp_path / "Daily_2026_11_13.csv", TRADE_ROWS, encoding="utf-8-sig")

    class KillAfterFirstFill(Engine):
        """成交一筆之後按緊急停止（像人在電腦上按），讓這一天有一則「要處理」。"""

        def on_tick(self, tick: Tick) -> None:
            super().on_tick(tick)
            if len(self.ledger.fills) == 1 and self.risk.halt is None:
                self.kill(tick.ts)

    monkeypatch.setattr(cli, "Engine", KillAfterFirstFill)
    monkeypatch.setattr(cli, "build_strategy", lambda name, c, ms_repo: Scripted({0: Side.BUY}))
    post = FakePush(410)
    monkeypatch.setattr(cli, "pywebpush_post", fake_push_factory(post))
    monkeypatch.setattr(urllib.request, "urlopen", FakeHttp())
    monkeypatch.setenv(ENV_VAPID, KEY)
    monkeypatch.setenv(ENV_TOKEN, "12345:fake-token-for-tests-only")
    monkeypatch.setenv(ENV_CHAT, "987654321")
    log = tmp_path / "failures.jsonl"
    day = ["--ticks", str(ticks), "--day", "2026-11-13", "--strategy", "none"]
    args = [
        "replay",
        "--config",
        str(CONFIG_PATH),
        *day,
        "--notify",
        "send",
        "--push-subs",
        str(subs_file(tmp_path, APPLE)),
        "--notify-log",
        str(log),
    ]
    assert main(args) == 0
    out = capsys.readouterr()
    assert "push_sent: 0" in out.out and "push_failed: 1" in out.out
    assert "網站推播 web.push.apple.com" in out.err
    (line,) = log.read_text(encoding="utf-8").splitlines()
    row = json.loads(line)
    assert row["channel"] == "網站推播 web.push.apple.com" and row["final"] is True
    assert "緊急停止" in row["title"] and APPLE not in line
    assert [c["data"]["title"] for c in post.calls] == [
        ACT.replace("【模擬】", "【模擬重播】") + "緊急停止"
    ]
