"""T-348：網頁推播——名單寫好後發「今日名單已更新」。不連網：發送函式注入、加密封包在本機解開驗證。"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

from twquant.config import PushConfig, load_config
from twquant.monitoring.push_notify import (
    LineCount,
    Subscription,
    compose,
    host_allowed,
    line_counts,
    send_all,
    webpush_sender,
)

ROOT = Path(__file__).resolve().parents[1]
LINES = [("CORE", "大盤精選"), ("QUIET", "潛伏飆股"), ("QUIETV", "高分爆量")]


def test_ac1_compose_counts_and_new_entries() -> None:
    lines = line_counts(
        {"CORE": ["2330", "2317", "2454"], "QUIET": ["5340"]},
        {"CORE": ["2330", "1101"], "QUIET": ["5340"]},
        LINES,
    )
    assert lines == [
        LineCount("CORE", "大盤精選", 3, 2),
        LineCount("QUIET", "潛伏飆股", 1, 0),
        LineCount("QUIETV", "高分爆量", 0, None),  # 之前沒有名單：不算新進
    ]
    msg = compose(dt.date(2026, 10, 13), lines, "https://twquant.pages.dev/")
    assert msg["title"] == "10/13 今日名單已更新"
    assert msg["body"] == "大盤精選 3 檔（新進 2）、潛伏飆股 1 檔、高分爆量今天沒有"
    assert msg["url"] == "https://twquant.pages.dev/"
    assert msg["tag"] == "picks-2026-10-13"  # 同一天只留一則


def test_ac2_only_known_push_services() -> None:
    hosts = ("web.push.apple.com", "fcm.googleapis.com", "notify.windows.com")
    assert host_allowed("https://web.push.apple.com/QGx…", hosts)
    assert host_allowed("https://wns2-bl2p.notify.windows.com/w/?token=x", hosts)
    assert not host_allowed("http://web.push.apple.com/x", hosts)  # 要 https
    assert not host_allowed("https://evil.example/web.push.apple.com", hosts)
    assert not host_allowed("https://web.push.apple.com.evil.example/x", hosts)
    assert not host_allowed("not a url", hosts)


def test_ac3_send_results_gone_and_failed_are_reported() -> None:
    subs = [Subscription(f"https://web.push.apple.com/{i}", "p", "a", "a@b.c") for i in range(4)]
    status = {
        subs[0].endpoint: 201,
        subs[1].endpoint: 410,
        subs[2].endpoint: 404,
        subs[3].endpoint: 500,
    }
    seen: list[str] = []

    def fake(s: Subscription, data: str) -> int:
        seen.append(data)
        return status[s.endpoint]

    r = send_all(subs, {"title": "t", "body": "中文"}, fake)
    assert r.sent == 1
    assert r.gone == (subs[1].endpoint, subs[2].endpoint)  # 失效：要刪
    assert r.failed == ("web.push.apple.com HTTP 500",)  # 其他錯誤：記下來，不吞
    assert json.loads(seen[0]) == {"title": "t", "body": "中文"}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def test_ac4_encrypted_payload_decrypts_for_the_subscriber(monkeypatch: pytest.MonkeyPatch) -> None:
    """真的走 pywebpush（VAPID 簽章＋aes128gcm 加密），只把送出換成本機攔截，再用訂閱者的私鑰解開。"""
    import http_ece
    import requests
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    # 訂閱者（瀏覽器）那邊的金鑰
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    auth = os.urandom(16)
    # 伺服器（我們）的 VAPID 金鑰
    vapid = ec.generate_private_key(ec.SECP256R1())
    vapid_priv = _b64(vapid.private_numbers().private_value.to_bytes(32, "big"))

    sent: dict[str, Any] = {}

    class Resp:
        status_code = 201
        text = ""
        headers: ClassVar[dict[str, str]] = {}

    def fake_post(
        url: str, data: bytes = b"", headers: dict[str, str] | None = None, **_: Any
    ) -> Resp:
        sent.update(url=url, data=data, headers=headers or {})
        return Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    sub = Subscription("https://web.push.apple.com/abc", _b64(ua_pub), _b64(auth), "a@b.c")
    payload = {"title": "10/13 今日名單已更新", "body": "大盤精選 15 檔"}
    r = send_all([sub], payload, webpush_sender(vapid_priv, "https://twquant.pages.dev", 43200, 15))

    assert r.sent == 1
    assert sent["url"] == sub.endpoint
    h = {k.lower(): v for k, v in sent["headers"].items()}
    assert h["content-encoding"] == "aes128gcm"
    assert h["ttl"] == "43200"
    assert h["authorization"].startswith("vapid t=")
    plain = http_ece.decrypt(sent["data"], private_key=ua, auth_secret=auth, version="aes128gcm")
    assert json.loads(plain) == payload


def test_ac5_public_key_and_hosts_match_the_website() -> None:
    cfg = load_config(ROOT / "configs" / "push.yaml", PushConfig)
    push_ts = (ROOT / "frontend" / "src" / "cloud" / "push.ts").read_text(encoding="utf-8")
    m = re.search(r"VAPID_PUBLIC_KEY = '([^']+)'", push_ts)
    assert m and m.group(1) == cfg.vapid_public_key, (
        "frontend/src/cloud/push.ts 的公開金鑰要與 configs/push.yaml 相同"
    )
    lib = (ROOT / "frontend" / "functions" / "_lib" / "push.ts").read_text(encoding="utf-8")
    hosts = re.search(r"PUSH_HOSTS = \[([^\]]+)\]", lib)
    assert hosts and tuple(re.findall(r"'([^']+)'", hosts.group(1))) == cfg.allowed_hosts
    # 私密金鑰只能在環境變數，不能出現在設定檔
    assert (
        "PRIVATE"
        not in (ROOT / "configs" / "push.yaml")
        .read_text(encoding="utf-8")
        .split("vapid_private_key_env")[0]
    )


# ── 族群資金推播（T-354）───────────────────────────────────────────────────

from twquant.monitoring.push_notify import HotGroup, compose_groups, hot_groups  # noqa: E402

_GROUPS = {
    "2478": "被動元件",
    "3090": "被動元件",
    "2327": "被動元件",
    "3017": "散熱",
    "3324": "散熱",
    "2330": "晶圓製造",
}
_FLOWS = {
    "被動元件": (2.1, 41, 33, True),
    "散熱": (1.6, 11, 8, True),
    "晶圓製造": (3.0, 18, 15, True),
    "IC設計": (1.2, 80, 50, False),
}


def test_ac6_t354_hot_groups_need_two_picks_and_inflow() -> None:
    """名單上同族群至少 2 檔、而且族群資金流入才算；同一檔在兩條線只算一次；名單檔數多的在前。"""
    picks = [
        ("2478", "大毅"),
        ("2330", "台積電"),  # 晶圓製造倍數最高但名單上只有 1 檔 → 不算
        ("3017", "奇鋐"),
        ("3090", "日電貿"),
        ("3324", None),  # 查不到股名 → 用代號
        ("2478", "大毅"),  # 另一條線又有大毅：不重複算
        ("2327", "國巨"),
        ("9999", "沒族群"),
    ]
    got = hot_groups(picks, _GROUPS, _FLOWS, 2)
    assert got == [
        HotGroup("被動元件", 2.1, 41, 33, ("大毅", "日電貿", "國巨")),
        HotGroup("散熱", 1.6, 11, 8, ("奇鋐", "3324")),
    ]
    # 沒有資金流入的族群，名單上再多檔也不算；門檻提高到 3 檔只剩被動元件
    assert hot_groups([("1", "a"), ("2", "b")], {"1": "IC設計", "2": "IC設計"}, _FLOWS, 2) == []
    assert [h.group for h in hot_groups(picks, _GROUPS, _FLOWS, 3)] == ["被動元件"]


def test_ac6_t354_compose_groups_title_body_and_link_to_the_group_tab() -> None:
    hot = [
        HotGroup("被動元件", 2.1, 41, 33, ("大毅", "日電貿", "國巨")),
        HotGroup("散熱", 1.64, 11, 8, ("奇鋐", "雙鴻")),
    ]
    p = compose_groups(dt.date(2026, 10, 13), hot, "https://twquant.pages.dev/", 3)
    assert p["title"] == "10/13 資金流入：被動元件 2.1 倍、散熱 1.6 倍"
    assert p["body"] == (
        "被動元件：資金 2.1 倍、41 檔 33 漲｜名單 3 檔：大毅、日電貿、國巨\n"
        "散熱：資金 1.6 倍、11 檔 8 漲｜名單 2 檔：奇鋐、雙鴻"
    )
    assert p["url"] == "https://twquant.pages.dev/?tab=group"
    assert p["tag"] == "groups-2026-10-13"  # 跟名單通知（picks-…）分開，不互相蓋掉
    one = compose_groups(dt.date(2026, 10, 13), hot, "https://twquant.pages.dev/", 1)
    assert one["title"] == "10/13 資金流入：被動元件 2.1 倍 等 2 個"
    assert "散熱" not in one["body"]
    with pytest.raises(ValueError):
        compose_groups(dt.date(2026, 10, 13), [], "https://x/", 3)


def test_ac6_t354_config_and_cloud_step() -> None:
    """門檻在 push.yaml；雲端排程在名單通知之後發、失敗不擋名單（靜態）。"""
    cfg = load_config(ROOT / "configs" / "push.yaml", PushConfig)
    assert (cfg.group_min_picks, cfg.group_max_listed) == (2, 3)
    wf = (ROOT / ".github" / "workflows" / "daily-pipeline.yml").read_text(encoding="utf-8")
    assert wf.index("notify-picks --date") < wf.index("notify-groups --date")
    step = wf[wf.index("族群資金推播") : wf.index("notify-groups --date")]
    assert "continue-on-error: true" in step and "TWQUANT_VAPID_PRIVATE_KEY" in step
    sw = (ROOT / "frontend" / "public" / "sw.js").read_text(encoding="utf-8")
    assert "postMessage({ type: 'open', url })" in sw  # 網站開著時點通知也會切到族群分頁


_SCHEMA = "serving_test_push_groups"


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
        cur.execute(
            f'CREATE TABLE "{_SCHEMA}".daily_picks (date date, line text, rank int, stock_id text)'
        )
        cur.execute(
            f'CREATE TABLE "{_SCHEMA}".stock_meta (stock_id text, name text, industry text)'
        )
        cur.execute(f'CREATE TABLE "{_SCHEMA}".stock_group (stock_id text, grp text)')
        cur.executemany(
            f'INSERT INTO "{_SCHEMA}".daily_picks VALUES (%s, %s, %s, %s)',
            [
                (dt.date(2026, 10, 13), "CORE", 1, "2478"),
                (dt.date(2026, 10, 13), "CORE", 2, "1802"),
                (dt.date(2026, 10, 13), "QUIET", 1, "3090"),
                (dt.date(2026, 10, 13), "QUIET", 2, "1809"),
            ],
        )
        cur.executemany(
            f'INSERT INTO "{_SCHEMA}".stock_meta VALUES (%s, %s, %s)',
            [
                ("2478", "大毅", "電子零組件業"),
                ("3090", "日電貿", "電子零組件業"),
                ("1802", "台玻", "玻璃陶瓷"),
                ("1809", "中釉", "玻璃陶瓷"),  # 族群表上沒有（重抓之間新上市）→ 用官方產業別
            ],
        )
        cur.executemany(
            f'INSERT INTO "{_SCHEMA}".stock_group VALUES (%s, %s)',
            [("2478", "被動元件"), ("3090", "被動元件"), ("1802", "玻璃陶瓷")],
        )
    conn.commit()
    return conn


def test_ac7_t354_pg_hot_groups_and_not_ready() -> None:
    from twquant.data.serving.group_flow_store import write_group_flow
    from twquant.monitoring.push_notify import load_hot_groups
    from twquant.reporting.group_flow import GroupFlow

    conn = _connect_or_skip()
    day = dt.date(2026, 10, 13)
    try:
        assert load_hot_groups(conn, _SCHEMA, day, 2) is None  # 族群資金表還沒建 → 還沒好
        at = dt.datetime(2026, 10, 13, 17, 40, tzinfo=dt.timezone(dt.timedelta(hours=8)))

        def flow(group: str, ratio: float, hot: bool) -> GroupFlow:
            return GroupFlow(day, group, 10, 7, 2, 0.02, 1e9, 0.02, 0.01, ratio, hot, ())

        write_group_flow(
            conn, _SCHEMA, day, [flow("被動元件", 2.1, True), flow("玻璃陶瓷", 1.8, True)], at
        )
        got = load_hot_groups(conn, _SCHEMA, day, 2)
        assert got is not None
        assert [(h.group, h.picks) for h in got] == [
            ("被動元件", ("大毅", "日電貿")),
            ("玻璃陶瓷", ("台玻", "中釉")),
        ]
        assert load_hot_groups(conn, _SCHEMA, dt.date(2026, 10, 14), 2) is None  # 那天還沒算
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()
        conn.close()


# ── 名單通知只給 admin（T-429／D-207）──────────────────────────────────────


def test_t429_ac5_list_notifications_only_for_recipients() -> None:
    """今日名單、族群資金兩種通知只發給 list_recipients 的帳號；同一帳號幾台裝置都收得到，email 不分大小寫。"""
    from twquant.monitoring.push_notify import for_recipients

    subs = [
        Subscription("https://web.push.apple.com/1", "p", "a", "boss@example.com"),
        Subscription("https://fcm.googleapis.com/2", "p", "a", "friend@example.com"),
        Subscription("https://fcm.googleapis.com/3", "p", "a", "Boss@Example.com"),
    ]
    got = for_recipients(subs, ["boss@example.com"])
    assert [s.endpoint for s in got] == [
        "https://web.push.apple.com/1",
        "https://fcm.googleapis.com/3",
    ]
    assert for_recipients(subs, ["nobody@example.com"]) == []


def test_t429_ac5_recipients_match_website_admin_and_cli_filters() -> None:
    """收件人＝網站 admin 角色（roles.ts）；兩個推播指令都先過濾收件人才發；讀訂閱時帶 email（靜態）。"""
    cfg = load_config(ROOT / "configs" / "push.yaml", PushConfig)
    roles = (ROOT / "frontend" / "functions" / "_lib" / "roles.ts").read_text(encoding="utf-8")
    m = re.search(r"^\s*admin: \[([^\]]*)\]", roles, re.MULTILINE)
    assert m, "roles.ts 找不到 admin 角色"
    assert tuple(re.findall(r"'([^']+)'", m.group(1))) == cfg.list_recipients
    assert all(e == e.strip().lower() and "@" in e for e in cfg.list_recipients)
    cli = (ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    for cmd in ("def notify_picks(", "def notify_groups("):
        body = cli[cli.index(cmd) :]
        body = body[: body.index("\n@app.command")]
        assert "pn.for_recipients(pn.load_subscriptions(conn), cfg.list_recipients)" in body, cmd
        assert body.count("load_subscriptions(") == 1, cmd  # 沒有別條路拿到全部訂閱
    src = (ROOT / "src" / "twquant" / "monitoring" / "push_notify.py").read_text(encoding="utf-8")
    assert "SELECT endpoint, p256dh, auth, email FROM" in src
