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
    subs = [Subscription(f"https://web.push.apple.com/{i}", "p", "a") for i in range(4)]
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
    sub = Subscription("https://web.push.apple.com/abc", _b64(ua_pub), _b64(auth))
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
