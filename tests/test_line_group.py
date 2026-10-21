"""T-423：LINE 群組每日圖卡。不連 LINE：轉接器以假的 runner 注入；PG 整合測試沒有 DSN 自動跳過。"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from twquant.config import LineCardFont, LineGroupConfig, load_config
from twquant.conventions import TZ
from twquant.errors import ConfigError, ExecutionError
from twquant.monitoring import line_card as lc
from twquant.monitoring import line_group as lg
from twquant.monitoring.push_notify import HotGroup

ROOT = Path(__file__).resolve().parents[1]
LINES = [("CORE", "大盤精選"), ("QUIET", "潛伏飆股"), ("QUIETV", "高分爆量（驗證中）")]
DAY = dt.date(2026, 10, 21)
NOW = dt.datetime(2026, 10, 21, 17, 40, tzinfo=TZ)
# 畫圖測的是版面機制，不是字形：設定裡的中文字型找不到時（CI 沒裝 Noto CJK）退到 DejaVu
_TEST_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _cfg(**over: Any) -> LineGroupConfig:
    base = load_config(ROOT / "configs" / "line_group.yaml", LineGroupConfig)
    return base.model_copy(update=over)


def _fonts() -> lc.Fonts:
    try:
        return lc.pick_fonts(_cfg().card)
    except ConfigError:
        if not all(Path(p).is_file() for p in _TEST_FONTS):
            pytest.skip("沒有任何可用的字型檔")
        return lc.Fonts(
            LineCardFont(path=_TEST_FONTS[0], index=0), LineCardFont(path=_TEST_FONTS[1], index=0)
        )


def _job(**over: Any) -> lg.Job:
    base: dict[str, Any] = {
        "id": 7,
        "kind": "picks",
        "home_id": "cabc",
        "card_token": "f" * 32,
        "alt_text": "10/21（三）今日名單：大盤精選　2 檔",
        "link_url": "https://twquant.pages.dev/?tab=picks",
        "attempts": 0,
        "width": 1040,
        "height": 1200,
    }
    return lg.Job(**{**base, **over})


# ── AC1 名單圖卡 ───────────────────────────────────────────────────────────


def test_ac1_picks_sections_order_new_and_more() -> None:
    picks = [
        lg.PickRow("QUIET", 1, "3090", "日電貿", "被動元件"),
        lg.PickRow("CORE", 3, "2454", None, None),
        lg.PickRow("CORE", 2, "2317", "鴻海", None),
        lg.PickRow("CORE", 1, "2330", "台積電", "晶圓代工"),
    ]
    secs = lg.picks_sections(LINES, picks, {"CORE": {"2317"}}, rows_per_line=2)
    assert [s.heading for s in secs] == ["大盤精選", "潛伏飆股", "高分爆量（驗證中）"]
    core, quiet, quietv = secs
    assert core.note == "3 檔 · 新進 2"  # 2454 也是新進，只是沒列出來
    assert core.rows == (
        lc.CardRow("1", "2330", "台積電", "晶圓代工", "NEW"),
        lc.CardRow("2", "2317", "鴻海", "", ""),
    )
    assert core.more == "…另 1 檔"
    assert quiet.note == "1 檔" and quiet.rows[0].badge == ""  # 之前沒有名單：不標新進
    assert quietv == lc.CardSection("高分爆量（驗證中）", "今天沒有符合條件的股票")


# ── AC2 族群資金、晨報、標題與文字 ─────────────────────────────────────────


def test_ac2_groups_and_brief_sections() -> None:
    hot = [
        HotGroup("被動元件", 2.34, 12, 9, ("大毅", "國巨")),
        HotGroup("散熱", 1.8, 8, 6, ("奇鋐", "雙鴻")),
    ]
    assert lg.groups_sections(hot, max_listed=1) == (
        lc.CardSection(
            "被動元件",
            "資金 2.3 倍 · 12 檔 9 漲",
            paragraphs=("名單 2 檔：大毅、國巨",),
            more="…另 1 個族群",
        ),
    )
    with pytest.raises(ValueError, match="沒有資金流入"):
        lg.groups_sections([], max_listed=3)
    model = lg.BriefRow("夜盤小漲", ("【方向】偏多", "空單減"), "美債殖利率", "規則句")
    assert lg.brief_sections(DAY, "morning", model) == (
        lc.CardSection("夜盤小漲", paragraphs=("• 【方向】偏多", "• 空單減")),
        lc.CardSection("風險", paragraphs=("美債殖利率",)),
    )
    rule = lg.BriefRow(None, (), None, "加權收 23,000 點")
    assert lg.brief_sections(DAY, "evening", rule) == (
        lc.CardSection("", paragraphs=("加權收 23,000 點",)),
    )
    with pytest.raises(ValueError, match="沒有內容"):
        lg.brief_sections(DAY, "evening", lg.BriefRow(None, (), None, None))


def test_ac2_titles_alt_and_fallback_text() -> None:
    assert lg.card_title(DAY, "picks") == "10/21（三）今日名單"
    assert lg.card_title(DAY, "evening") == "10/21（三）盤後快訊"
    sections = (
        lc.CardSection("大盤精選", "15 檔 · 新進 3"),
        lc.CardSection("", paragraphs=("規則句",)),
    )
    card = lg.make_card("10/21（三）今日名單", sections, _cfg())
    assert card.host == "twquant.pages.dev"
    assert lg.alt_text(card) == "10/21（三）今日名單：大盤精選　15 檔 · 新進 3；規則句"
    long = lg.make_card("標", (lc.CardSection("字" * 500),), _cfg())
    assert len(lg.alt_text(long)) == lg.ALT_TEXT_MAX and lg.alt_text(long).endswith("…")
    assert lg.fallback_text("摘要", "https://x.dev/") == "摘要\n\nhttps://x.dev/"


def test_ac2_flex_message_is_one_tappable_image() -> None:
    link = "https://x.dev/?tab=picks"
    msg = lg.flex_message("alt", "https://x.dev/pub/line-card/t", 1040, 1214, link)
    hero = msg["contents"]["hero"]
    assert msg["type"] == "flex" and msg["altText"] == "alt"
    assert hero["url"] == "https://x.dev/pub/line-card/t"
    assert hero["aspectRatio"] == "1040:1214"
    assert hero["action"] == {"type": "uri", "label": "打開 twquant", "uri": link}
    with pytest.raises(ValueError, match="https"):
        lg.flex_message("alt", "http://x.dev/a", 10, 10, "https://x.dev/")
    with pytest.raises(ValueError, match="三倍"):
        lg.flex_message("alt", "https://x.dev/a", 100, 301, "https://x.dev/")


def test_ac2_job_request_for_adapter() -> None:
    req = _job().request(_cfg())
    assert req["id"] == 7 and req["homeId"] == "cabc"
    hero = req["message"]["contents"]["hero"]
    assert hero["url"] == f"https://twquant.pages.dev/pub/line-card/{'f' * 32}.png"
    assert req["fallbackText"].endswith("\n\nhttps://twquant.pages.dev/?tab=picks")
    assert _job().request(_cfg(fallback_text=False))["fallbackText"] == ""


# ── AC3 畫圖 ───────────────────────────────────────────────────────────────


def _card(rows: int) -> lc.Card:
    sec = lc.CardSection(
        "大盤精選",
        f"{rows} 檔",
        tuple(
            lc.CardRow(str(i), "2330", "台積電", "晶圓代工", "NEW" if i % 2 else "")
            for i in range(rows)
        ),
        paragraphs=("一段很長的文字，" * 20,),
        more="…另 3 檔",
    )
    return lc.Card(
        "10/21（三）今日名單",
        (sec, lc.CardSection("風險")),
        "點圖看完整內容 ›",
        "twquant.pages.dev",
        "僅供參考",
    )


def test_ac3_render_png_size_and_deterministic() -> None:
    style, fonts = _cfg().card, _fonts()
    png, w, h = lc.render_card(_card(3), style, fonts)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (w, h) and w == style.width
    assert lc.render_card(_card(3), style, fonts)[0] == png  # 同輸入兩次逐位元相同
    _, _, taller = lc.render_card(_card(6), style, fonts)
    assert taller > h  # 高度跟著內容長
    assert len(png) < 300_000  # 調色盤壓縮後很小（雲端資料庫只有 500 MB）


def test_ac3_wrap_and_ellipsize() -> None:
    def measure(s: str) -> float:
        return float(len(s))  # 一個字寬 1

    assert lc.wrap("一二三四五\n六", 2, measure) == ["一二", "三四", "五", "六"]
    assert lc.ellipsize("一二三四五", 3, measure) == "一二…"
    assert lc.ellipsize("一二", 3, measure) == "一二"


def test_ac3_pick_fonts_first_existing_or_raise() -> None:
    style = _cfg().card
    second = style.fonts_regular[1].path
    fonts = lc.pick_fonts(style, exists=lambda p: p in {second, style.fonts_bold[0].path})
    assert fonts.regular.path == second and fonts.bold == style.fonts_bold[0]
    with pytest.raises(ConfigError, match="fonts-noto-cjk"):
        lc.pick_fonts(style, exists=lambda p: False)


# ── AC4 轉接器事件 ─────────────────────────────────────────────────────────


def test_ac4_events_credential_sending_results(capsys: pytest.CaptureFixture[str]) -> None:
    base = lg.Credential("old", "r1", 100)
    out = lg.parse_events(
        [
            '{"type":"profile","mid":"u1","displayName":"小幫手"}',
            "linejs 自己印的雜訊",
            '{"type":"credential","refreshToken":"r2"}',
            '{"type":"credential","accessToken":"new","expire":200}',
            '{"type":"group","homeId":"c1","name":"股友"}',
            '{"type":"sending","id":7}',
            '{"type":"result","id":7,"sent":true,"via":"flex"}',
            '{"type":"sending","id":8}',
            "",
        ],
        0,
        base,
    )
    assert out.credential == lg.Credential("new", "r2", 200)
    assert out.profile == {"mid": "u1", "displayName": "小幫手"}
    assert out.groups == ({"homeId": "c1", "name": "股友"},)
    assert out.sending == (7, 8) and out.results[0]["via"] == "flex"
    assert out.fatal is None
    assert "linejs 自己印的雜訊" in capsys.readouterr().err  # 不吞掉


def test_ac4_e2ee_keys_carried_and_sent_back() -> None:
    keys = {"e2eeKeys:3": '{"keyId":3}', "e2eeKeys:u1": '{"keyId":3}'}
    login = lg.parse_events(
        [
            json.dumps(
                {"type": "credential", "accessToken": "a", "refreshToken": "r", "e2eeKeys": keys}
            )
        ],
        0,
        None,
    )
    assert login.credential is not None and login.credential.e2ee_keys is not None
    assert lg.credential_request(login.credential)["credential"]["e2eeKeys"] == keys
    # 換 token 的事件不帶金鑰：沿用原本那份；登入過但沒金鑰的舊憑證不送 e2eeKeys
    rotated = lg.parse_events(['{"type":"credential","accessToken":"b"}'], 0, login.credential)
    assert rotated.credential == lg.Credential("b", "r", None, login.credential.e2ee_keys)
    old = lg.credential_request(lg.Credential("a", "r", 1))["credential"]
    assert "e2eeKeys" not in old
    with pytest.raises(ExecutionError, match="加密金鑰"):
        lg.parse_events(['{"type":"credential","accessToken":"a","e2eeKeys":"x"}'], 0, None)


def test_ac4_fatal_unknown_event_and_relogin() -> None:
    fatal = lg.parse_events(['{"type":"fatal","error":"AUTHENTICATION_FAILED"}'], 2, None)
    assert fatal.fatal == "AUTHENTICATION_FAILED"
    assert lg.parse_events([], 1, None).fatal == "轉接器結束碼 1"
    with pytest.raises(ExecutionError, match="不認得的事件"):
        lg.parse_events(['{"type":"what"}'], 0, None)
    with pytest.raises(ExecutionError, match="沒有 access token"):
        lg.parse_events(['{"type":"credential","expire":1}'], 0, None)
    assert lg.needs_login('getProfile(/S4) -> {"code":"AUTHENTICATION_FAILED"}')
    assert lg.needs_login('{"code":"MUST_REFRESH_V3_TOKEN"}')
    assert not lg.needs_login("code=701 請稍後再重新發布貼文")


# ── AC5 每筆的下一個狀態 ───────────────────────────────────────────────────


def _next(res: dict[str, Any] | None, *, sending: bool = True, **job: Any) -> dict[str, Any]:
    return lg.next_state(_job(**job), res, sending=sending, now=NOW, max_attempts=3)


def test_ac5_sent_by_flex_or_text() -> None:
    assert _next({"sent": True, "via": "flex"}) == {
        "status": "sent",
        "via": "flex",
        "sent_at": NOW,
        "last_error": None,
    }
    upd = _next({"sent": True, "via": "text", "flexError": "Failed to get LiffToken"})
    assert upd["status"] == "sent" and upd["via"] == "text"
    assert upd["last_error"] == "圖卡發不出去，改發文字：Failed to get LiffToken"


def test_ac5_interrupted_send_is_unknown_not_retried() -> None:
    assert _next(None, sending=True) == {"status": "unknown", "last_error": lg.UNKNOWN_HINT}
    assert _next(None, sending=False) == {}  # 還沒輪到：原樣留著


def test_ac5_backoff_free_others_count_until_failed() -> None:
    backoff = {"sent": False, "error": "限制使用", "backoffMs": 600000}
    assert _next(backoff) == {"last_error": lg.BACKOFF_HINT}
    assert _next({"sent": False, "error": "x"})["status"] == "queued"
    assert _next({"sent": False, "error": "x"}, attempts=2) == {
        "attempts": 3,
        "status": "failed",
        "last_error": "x",
    }
    with pytest.raises(ExecutionError, match="卻沒有錯誤"):
        _next({"sent": False})


def test_ac5_backoff_until_takes_longest() -> None:
    assert lg.backoff_until([{"id": 1}], NOW) is None
    until = lg.backoff_until([{"backoffMs": 120000}, {"backoffMs": 600000}], NOW)
    assert until == NOW + dt.timedelta(minutes=10)


# ── AC7 配置 ───────────────────────────────────────────────────────────────


def test_ac7_config_kinds_links_and_lease() -> None:
    cfg = _cfg()
    assert set(cfg.kinds) == set(lg.KINDS)
    assert all(k.link.startswith(cfg.site_url) for k in cfg.kinds.values())
    assert cfg.lease_seconds > cfg.adapter_timeout_seconds  # 占用不能比轉接器先到期
    assert (ROOT / cfg.adapter).exists()
    # 圖卡網址要對到網站的公開端點 frontend/functions/pub/line-card/[token].ts
    assert cfg.card_url_base == f"{cfg.site_url}pub/line-card/"
    assert (ROOT / "frontend" / "functions" / "pub" / "line-card" / "[token].ts").exists()


def test_ac7_card_colors_match_site() -> None:
    css = (ROOT / "frontend" / "src" / "cloud" / "cloud.css").read_text(encoding="utf-8")
    site = dict(re.findall(r"--(bg|panel|text|mute|accent|line):\s*(#[0-9a-f]{6})", css))
    c = _cfg().card.colors
    assert (c.bg, c.panel, c.text, c.muted, c.accent, c.rule) == (
        site["bg"],
        site["panel"],
        site["text"],
        site["mute"],
        site["accent"],
        site["line"],
    )


# ── AC6 排隊與送出（PG 整合） ──────────────────────────────────────────────

_SCHEMA = "web_test_line_group"
_SERVING = "serving_test_line_group"


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
        for s in (_SCHEMA, _SERVING):
            cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
    conn.commit()
    lg.ensure_tables(conn, _SCHEMA)
    return conn


class FakeRunner:
    """假的轉接器：依序回預先寫好的結果，記下每次收到的請求。"""

    def __init__(self, *outs: lg.AdapterOutput) -> None:
        self.outs = list(outs)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, command: str, request: dict[str, Any], base: lg.Credential | None
    ) -> lg.AdapterOutput:
        self.calls.append((command, request))
        return self.outs.pop(0)


class Clock:
    """假時鐘：sleep 只把時間往前推。"""

    def __init__(self, start: dt.datetime) -> None:
        self.t = start
        self.slept: list[float] = []

    def now(self) -> dt.datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += dt.timedelta(seconds=s)


def _rows(conn: Any) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT id, kind, home_id, status, via, attempts FROM "{_SCHEMA}".line_posts ORDER BY id'
        )
        return list(cur.fetchall())


def _queue(conn: Any, kind: str, homes: list[str], *, day: dt.date = DAY, stale: float = 5) -> int:
    token = lg.save_card(conn, _SCHEMA, day, kind, b"png", (1040, 1200), NOW)
    return lg.enqueue(
        conn,
        _SCHEMA,
        day,
        kind,
        homes,
        card_token=token,
        alt=f"{kind} alt",
        link="https://twquant.pages.dev/",
        now=NOW,
        stale_hours=stale,
    )


def _login(conn: Any) -> None:
    lg.save_account(conn, _SCHEMA, lg.Credential("a1", "r1", 1), now=NOW, profile={"mid": "u1"})


def _enable(conn: Any, *homes: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE "{_SCHEMA}".line_groups SET enabled = true WHERE home_id = ANY(%s)',
            (list(homes),),
        )
    conn.commit()


def test_ac6_groups_sync_enabled_and_present() -> None:
    conn = _connect_or_skip()
    two = [{"homeId": "c1", "name": "A"}, {"homeId": "c2", "name": "B"}]
    try:
        lg.save_groups(conn, _SCHEMA, two, NOW)
        assert lg.enabled_groups(conn, _SCHEMA) == []  # 新加入的群組預設不發
        _enable(conn, "c1", "c2")
        lg.save_groups(conn, _SCHEMA, [{"homeId": "c1", "name": "A 改名"}], NOW)
        assert lg.enabled_groups(conn, _SCHEMA) == ["c1"]  # c2：帳號已不在群組
        lg.save_groups(conn, _SCHEMA, two, NOW)
        assert lg.enabled_groups(conn, _SCHEMA) == ["c1", "c2"]  # 回到群組：勾選還在
        cfg = _cfg(web_schema=_SCHEMA)
        assert lg.sync_groups(conn, cfg, FakeRunner(), now_fn=lambda: NOW) is None  # 還沒登入
        _login(conn)
        out = lg.AdapterOutput(0, groups=({"homeId": "c3", "name": "C"},))
        assert lg.sync_groups(conn, cfg, FakeRunner(out), now_fn=lambda: NOW) == out
        assert lg.enabled_groups(conn, _SCHEMA) == []
    finally:
        conn.close()


def test_ac6_enqueue_once_expiry_and_prune() -> None:
    conn = _connect_or_skip()
    try:
        assert _queue(conn, "picks", ["c1", "c2"]) == 2
        assert lg.already_queued(conn, _SCHEMA, DAY, "picks", ["c1", "c3"]) == {"c1"}
        assert _queue(conn, "picks", ["c1", "c3"]) == 1  # c1 已排過：不動
        assert _queue(conn, "morning", ["c1"], stale=1) == 1
        assert len(lg.due_jobs(conn, _SCHEMA, NOW)) == 4
        assert len(lg.due_jobs(conn, _SCHEMA, NOW + dt.timedelta(hours=2))) == 3
        assert [r[3] for r in _rows(conn)] == ["queued", "queued", "queued", "expired"]
        _queue(conn, "picks", ["c1"], day=DAY - dt.timedelta(days=90))
        assert lg.prune_cards(conn, _SCHEMA, DAY, 60) == 1
        assert len(_rows(conn)) == 4  # 舊圖卡的貼文紀錄跟著刪
    finally:
        conn.close()


def test_ac6_send_nothing_due_needs_no_login() -> None:
    conn = _connect_or_skip()
    try:
        cfg = _cfg(web_schema=_SCHEMA)
        res = lg.send_due(conn, cfg, FakeRunner(), now_fn=lambda: NOW, sleep=lambda s: None)
        assert res == lg.SendSummary()
        _queue(conn, "picks", ["c1"])
        with pytest.raises(ConfigError, match="還沒登入"):
            lg.send_due(conn, cfg, FakeRunner(), now_fn=lambda: NOW, sleep=lambda s: None)
    finally:
        conn.close()


def test_ac6_e2ee_keys_stored_and_old_table_upgraded() -> None:
    conn = _connect_or_skip()
    try:
        # 加密金鑰欄之前建的表：ensure_tables 補欄，舊的那組讀得出來（沒有金鑰）
        with conn.cursor() as cur:
            cur.execute(f'DROP TABLE "{_SCHEMA}".line_account')
            cur.execute(
                f'CREATE TABLE "{_SCHEMA}".line_account (id text PRIMARY KEY, mid text, '
                "display_name text, access_token text NOT NULL, refresh_token text, "
                "token_expire bigint, updated_at timestamptz NOT NULL, paused_until timestamptz, "
                "busy_until timestamptz, last_error text)"
            )
        conn.commit()
        lg.ensure_tables(conn, _SCHEMA)
        _login(conn)
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a1", "r1", 1)
        keys = lg.Credential("a2", "r2", 2, '{"e2eeKeys:3": "{}"}')
        lg.save_account(conn, _SCHEMA, keys, now=NOW)
        assert lg.load_credential(conn, _SCHEMA) == keys
    finally:
        conn.close()


def test_ac6_send_saves_rotated_credential_and_marks_rows() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA, liff_id="123-abc")
    try:
        _login(conn)
        _queue(conn, "picks", ["c1", "c2", "c3"])
        runner = FakeRunner(
            lg.AdapterOutput(
                0,
                lg.Credential("a2", "r2", 2),
                {"mid": "u1"},
                sending=(1, 2, 3),
                results=(
                    {"id": 1, "sent": True, "via": "flex"},
                    {"id": 2, "sent": True, "via": "text", "flexError": "liff"},
                ),
            )
        )
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert (res.sent, res.as_text, res.left) == (2, 1, 0)
        assert res.failed == (f"#3 {lg.UNKNOWN_HINT}",)
        req = runner.calls[0][1]
        assert req["credential"] == {"accessToken": "a1", "refreshToken": "r1", "expire": 1}
        assert req["liffId"] == "123-abc"
        assert [j["homeId"] for j in req["jobs"]] == ["c1", "c2", "c3"]
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a2", "r2", 2)
        assert [r[3:5] for r in _rows(conn)] == [
            ("sent", "flex"),
            ("sent", "text"),
            ("unknown", None),
        ]
        assert lg.account_state(conn, _SCHEMA)["busy_until"] is None  # 用完放開
    finally:
        conn.close()


def test_ac6_backoff_waits_then_retries_remaining() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA, max_backoff_wait_seconds=660)
    clock = Clock(NOW)
    try:
        _login(conn)
        _queue(conn, "picks", ["c1"])
        _queue(conn, "groups", ["c1"])
        backoff = {"id": 1, "sent": False, "error": "code=701", "backoffMs": 120000}
        ok = [{"id": i, "sent": True, "via": "flex"} for i in (1, 2)]
        runner = FakeRunner(
            lg.AdapterOutput(0, sending=(1,), results=(backoff,)),  # 第一筆被叫停，第二筆沒輪到
            lg.AdapterOutput(0, sending=(1, 2), results=tuple(ok)),
        )
        res = lg.send_due(conn, cfg, runner, now_fn=clock.now, sleep=clock.sleep)
        assert clock.slept == [120.0]
        assert (res.sent, res.left) == (2, 0)
        assert [r[3] for r in _rows(conn)] == ["sent", "sent"]
        assert [r[5] for r in _rows(conn)] == [0, 0]  # 叫停不算失敗次數
    finally:
        conn.close()


def test_ac6_long_backoff_or_busy_account_left_for_next_run() -> None:
    conn = _connect_or_skip()
    clock = Clock(NOW)
    try:
        _login(conn)
        _queue(conn, "picks", ["c1"])
        cfg = _cfg(web_schema=_SCHEMA, max_backoff_wait_seconds=60)
        stop = {"id": 1, "sent": False, "error": "限制使用", "backoffMs": 600000}
        runner = FakeRunner(lg.AdapterOutput(0, sending=(1,), results=(stop,)))
        res = lg.send_due(conn, cfg, runner, now_fn=clock.now, sleep=clock.sleep)
        assert res.left == 1 and "留給下一班" in res.note and clock.slept == []
        # 另一班占著帳號：等到 lock_wait_seconds 還沒空就留給下一班
        lg.set_account_status(conn, _SCHEMA, paused_until=None, last_error=None)
        assert lg.acquire_lease(conn, _SCHEMA, clock.now(), 3600)
        cfg = _cfg(web_schema=_SCHEMA, lock_wait_seconds=30, poll_seconds=15)
        res = lg.send_due(conn, cfg, FakeRunner(), now_fn=clock.now, sleep=clock.sleep)
        assert res.left == 1 and "另一班" in res.note and clock.slept == [15, 15]
    finally:
        conn.close()


def test_ac6_fatal_keeps_queue_and_records_error() -> None:
    conn = _connect_or_skip()
    try:
        _login(conn)
        _queue(conn, "picks", ["c1"])
        runner = FakeRunner(lg.AdapterOutput(2, fatal="AUTHENTICATION_FAILED"))
        cfg = _cfg(web_schema=_SCHEMA)
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert res.failed == ("AUTHENTICATION_FAILED",) and res.left == 1
        assert lg.account_state(conn, _SCHEMA)["last_error"] == "AUTHENTICATION_FAILED"
        assert _rows(conn)[0][3] == "queued"
    finally:
        conn.close()


def test_ac6_load_picks_and_brief_from_serving() -> None:
    conn = _connect_or_skip()
    s = _SERVING
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{s}"')
            cur.execute(
                f'CREATE TABLE "{s}".daily_picks (date date, line text, rank int, stock_id text)'
            )
            cur.execute(f'CREATE TABLE "{s}".stock_meta (stock_id text, name text, industry text)')
            cur.execute(f'CREATE TABLE "{s}".stock_group (stock_id text, grp text)')
            cur.execute(
                f'CREATE TABLE "{s}".morning_brief (date date, headline text, bullets jsonb, '
                "risk_note text, rule_text text)"
            )
            prev = DAY - dt.timedelta(days=1)
            cur.executemany(
                f'INSERT INTO "{s}".daily_picks VALUES (%s, %s, %s, %s)',
                [(DAY, "CORE", 1, "2330"), (DAY, "CORE", 2, "1802"), (prev, "CORE", 1, "2330")],
            )
            cur.executemany(
                f'INSERT INTO "{s}".stock_meta VALUES (%s, %s, %s)',
                [("2330", "台積電", "半導體業"), ("1802", "台玻", "玻璃陶瓷")],
            )
            cur.execute(f"INSERT INTO \"{s}\".stock_group VALUES ('2330', '晶圓代工')")
            cur.execute(
                f'INSERT INTO "{s}".morning_brief VALUES (%s, %s, %s, %s, %s)',
                (DAY, "標題", '["一", "二"]', None, "規則"),
            )
        conn.commit()
        loaded = lg.load_picks(conn, s, DAY, LINES)
        assert loaded is not None
        rows, previous = loaded
        assert rows == [
            lg.PickRow("CORE", 1, "2330", "台積電", "晶圓代工"),
            lg.PickRow("CORE", 2, "1802", "台玻", "玻璃陶瓷"),
        ]
        assert previous == {"CORE": {"2330"}}
        assert lg.load_picks(conn, s, DAY + dt.timedelta(days=1), LINES) is None
        brief = lg.load_brief(conn, s, DAY, "morning")
        assert brief == lg.BriefRow("標題", ("一", "二"), None, "規則")
        assert lg.load_brief(conn, s, DAY, "evening") is None  # 表還沒建
    finally:
        conn.close()


# ── AC11 從網站登入（QR 與 PIN 寫進 web.line_login） ───────────────────────


def test_ac11_login_progress_qr_png_and_pin() -> None:
    upd = lg.login_progress({"type": "qr", "url": "https://line.me/R/au/lgn/sq/abc"})
    assert upd is not None and upd["status"] == "scan" and upd["pin"] is None
    assert upd["qr_url"] == "https://line.me/R/au/lgn/sq/abc"
    img = Image.open(io.BytesIO(base64.b64decode(upd["qr_png"])))
    assert img.format == "PNG" and img.size[0] == img.size[1] >= 100
    assert lg.login_progress({"type": "pin", "pin": "123456"}) == {
        "status": "pin",
        "pin": "123456",
        "qr_url": None,
        "qr_png": None,
    }
    assert lg.login_progress({"type": "credential", "accessToken": "x"}) is None
    # qr／pin 事件在收尾解析時不是錯誤
    out = lg.parse_events(['{"type":"qr","url":"u"}', '{"type":"pin","pin":"1"}'], 0, None)
    assert out.fatal is None


def test_ac11_set_login_rejects_bad_fields() -> None:
    with pytest.raises(ValueError, match="要給 status"):
        lg.set_login(None, "web", NOW, {"pin": "1"})
    with pytest.raises(ValueError, match="沒有這些欄位"):
        lg.set_login(None, "web", NOW, {"status": "scan", "access_token": "x"})


def test_ac11_login_table_same_on_site() -> None:
    ts = (ROOT / "frontend" / "functions" / "_lib" / "line-admin.ts").read_text(encoding="utf-8")
    ddl = re.search(r"CREATE TABLE IF NOT EXISTS web\.line_login \((.*?)\)'", ts, re.S)
    assert ddl is not None
    cols = [p.strip().split()[0] for p in re.sub(r"'\s*\+\s*'", "", ddl.group(1)).split(",")]
    assert tuple(cols) == lg.LOGIN_COLUMNS
    assert "LOGIN_ACTIVE = ['requested', 'starting', 'scan', 'pin']" in ts
    assert lg.LOGIN_ACTIVE == ("requested", "starting", "scan", "pin")


_FAKE_ADAPTER = """
const say = (o) => process.stdout.write(JSON.stringify(o) + "\\n");
const cmd = process.argv[2];
say({ type: "qr", url: "https://line.me/R/au/lgn/sq/x" });
await new Promise((r) => setTimeout(r, 200));
say({ type: "pin", pin: "4321" });
if (cmd === "hang") await new Promise((r) => setTimeout(r, 60_000));
say({ type: "credential", accessToken: "a", refreshToken: "r", expire: 9 });
say({ type: "profile", mid: "u1", displayName: "小幫手" });
"""


def test_ac11_run_node_streams_events_and_times_out(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("沒有 node")
    (tmp_path / "fake.mjs").write_text(_FAKE_ADAPTER, encoding="utf-8")
    cfg = _cfg(adapter="fake.mjs", login_timeout_seconds=30, adapter_timeout_seconds=2)
    seen: list[str] = []
    out = lg.run_node(cfg, tmp_path, "login", {}, None, lambda ev: seen.append(str(ev["type"])))
    assert seen == ["qr", "pin", "credential", "profile"]  # 邊跑邊收到，不是跑完才一次給
    assert out.credential == lg.Credential("a", "r", 9) and out.fatal is None
    hung = lg.run_node(cfg, tmp_path, "hang", {}, None)
    assert hung.fatal is not None and "被中止" in hung.fatal


def test_ac11_web_login_writes_progress_and_credential() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA)

    def login_row() -> tuple[Any, ...]:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT status, qr_url, qr_png IS NOT NULL, pin, display_name, error, run_url FROM "{_SCHEMA}".line_login'
            )
            row = cur.fetchone()
        assert row is not None
        return tuple(row)

    seen: list[tuple[Any, ...]] = []

    def ok_run(on_event: lg.EventSink) -> lg.AdapterOutput:
        seen.append(login_row())
        on_event({"type": "qr", "url": "https://line.me/R/au/lgn/sq/x"})
        seen.append(login_row())
        on_event({"type": "pin", "pin": "4321"})
        seen.append(login_row())
        return lg.AdapterOutput(
            0, lg.Credential("a", "r", 9), {"mid": "u1", "displayName": "小幫手"}
        )

    try:
        out = lg.web_login(conn, cfg, ok_run, now_fn=lambda: NOW, run_url="https://gh/run/1")
        assert out.fatal is None
        assert [r[0] for r in seen] == ["starting", "scan", "pin"]
        assert seen[1][1:3] == ("https://line.me/R/au/lgn/sq/x", True)
        assert seen[2][1:4] == (None, False, "4321")  # 掃完就把 QR 拿掉
        assert login_row() == ("done", None, False, None, "小幫手", None, "https://gh/run/1")
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a", "r", 9)

        def bad_run(on_event: lg.EventSink) -> lg.AdapterOutput:
            on_event({"type": "qr", "url": "https://line.me/R/au/lgn/sq/y"})
            return lg.AdapterOutput(2, fatal="等太久沒掃 QR")

        lg.web_login(conn, cfg, bad_run, now_fn=lambda: NOW, run_url=None)
        assert login_row()[:3] == ("failed", None, False)
        assert login_row()[5] == "等太久沒掃 QR"
    finally:
        conn.close()
