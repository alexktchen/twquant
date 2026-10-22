"""T-423：LINE 群組每日圖卡。不連 LINE：轉接器以假的 runner 注入；PG 整合測試沒有 DSN 自動跳過。"""

from __future__ import annotations

import base64
import datetime as dt
import io
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from twquant.config import LineCardFont, LineGroupConfig, load_config
from twquant.conventions import TZ
from twquant.errors import ConfigError, DataError, ExecutionError
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


def test_ac13_job_request_image_fallback() -> None:
    """Flex 不通時直接發那張圖（圖在請求的 cards 裡，用 token 對），後面一行網址（一般圖片點了不會開網站）。"""
    cfg = _cfg()
    req = _job().request(cfg)
    assert req["card"] == "f" * 32
    assert req["imageCaption"] == f"{cfg.image_caption}\nhttps://twquant.pages.dev/?tab=picks"
    assert _job().request(_cfg(fallback_image=False))["card"] == ""
    assert cfg.fallback_image  # 預設要發圖：使用者 2026-10-21「沒有發圖」


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
            '{"type":"group","homeId":"c1","name":"股友","noteHome":"c1"}',
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
    assert out.groups == ({"homeId": "c1", "name": "股友", "noteHome": "c1"},)
    assert out.sending == (7, 8) and out.results[0]["via"] == "flex"
    assert out.fatal is None
    assert "linejs 自己印的雜訊" in capsys.readouterr().err  # 不吞掉


def test_ac4_storage_snapshot_rides_on_credential() -> None:
    # 登入：credential 之後來一份 storage（端對端加密金鑰等），疊在 credential 上
    out = lg.parse_events(
        [
            '{"type":"credential","accessToken":"a","refreshToken":"r","expire":9}',
            '{"type":"storage","data":"{\\"e2eeKeys:u1\\":\\"k\\",\\"reqseq\\":\\"1\\"}"}',
        ],
        0,
        None,
    )
    assert out.credential == lg.Credential("a", "r", 9, '{"e2eeKeys:u1":"k","reqseq":"1"}')
    # 後面又來一次 credential（token 輪替）不會把先前的 storage 弄丟
    out2 = lg.parse_events(['{"type":"credential","accessToken":"a2"}'], 0, out.credential)
    assert out2.credential == lg.Credential("a2", "r", 9, '{"e2eeKeys:u1":"k","reqseq":"1"}')
    # 給轉接器的請求：storage 還原成物件
    assert lg._cred_json(out.credential)["storage"] == {"e2eeKeys:u1": "k", "reqseq": "1"}
    assert "storage" not in lg._cred_json(lg.Credential("a", "r", 9))  # 沒有就不帶
    assert "storage" not in lg._cred_json(lg.Credential("a", "r", 9, "壞掉的"))  # 壞的當沒有


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


def test_ac14_square_chat_skips_flex_and_starts_with_image() -> None:
    """社群（m…）不發 Flex（LIFF 分享不支援社群）：直接發圖＋一行網址，圖也不行才發文字。"""
    sq = _job(home_id="m" + "1" * 32).request(_cfg())
    assert sq["message"] is None
    assert sq["card"] == "f" * 32 and sq["imageCaption"].endswith(
        "https://twquant.pages.dev/?tab=picks"
    )
    assert sq["fallbackText"]
    assert _job(home_id="c" + "1" * 32).request(_cfg())["message"]["type"] == "flex"
    assert (lg.chat_label("m" + "1" * 32), lg.chat_label("c" + "1" * 32)) == ("社群", "群組")
    # 社群沒試 Flex，發圖就是正常路線：沒有「圖卡發不出去」的註記
    assert _next({"sent": True, "via": "image"})["last_error"] is None


def test_ac15_job_request_note_first() -> None:
    """先貼記事本再分享（使用者 2026-10-22）：記事本在哪由同步決定（群組 c…、社群 s…），上次發好的那篇帶著走。"""
    cfg = _cfg()
    assert cfg.via_note
    sq = _job(home_id="m" + "1" * 32, note_home="s" + "1" * 32, note_post_id="P9").request(cfg)
    assert (sq["noteHome"], sq["noteId"]) == ("s" + "1" * 32, "P9")
    assert (
        sq["noteText"]
        == "10/21（三）今日名單：大盤精選　2 檔\n\nhttps://twquant.pages.dev/?tab=picks"
    )
    grp = _job(note_home="cabc").request(cfg)
    assert (grp["noteHome"], grp["noteId"]) == ("cabc", "")
    assert _job().request(cfg)["noteHome"] == ""  # 還沒同步過（不知道記事本在哪）：照舊走聊天
    assert _job(note_home="cabc").request(_cfg(via_note=False))["noteHome"] == ""
    # 聊天發圖的備案關掉，記事本照樣有圖
    off = _job(note_home="cabc").request(_cfg(fallback_image=False))
    assert (off["card"], off["noteCard"]) == ("", "f" * 32)


def test_ac15_next_state_keeps_note_post_id() -> None:
    upd = _next({"sent": True, "via": "note", "notePostId": "P1"})
    assert upd == {
        "status": "sent",
        "via": "note",
        "sent_at": NOW,
        "last_error": None,
        "note_post_id": "P1",
    }
    # 分享時被叫停：先不算送出，但記事本那篇記下來（下次只分享）
    stop = {"sent": False, "error": "code=701", "backoffMs": 120000, "notePostId": "P1"}
    assert _next(stop) == {"last_error": lg.BACKOFF_HINT, "note_post_id": "P1"}
    failed = _next({"sent": False, "error": "x", "notePostId": "P1"})
    assert failed["note_post_id"] == "P1" and failed["status"] == "queued"
    upd = _next({"sent": True, "via": "image", "noteError": "分享失敗", "notePostId": "P1"})
    assert upd["last_error"] == "記事本發不出去，改發圖片：分享失敗"
    upd = _next({"sent": True, "via": "flex", "noteError": "打不通"})
    assert upd["last_error"] == "記事本發不出去，改發圖卡：打不通" and "note_post_id" not in upd


def test_ac15_note_image_is_jpeg_and_switches() -> None:
    png = io.BytesIO()
    Image.new("RGB", (40, 30), (14, 15, 20)).quantize(colors=8).save(png, format="PNG")
    jpg = lc.to_jpeg(png.getvalue(), 90)
    assert jpg[:2] == b"\xff\xd8"  # JPEG
    with Image.open(io.BytesIO(jpg)) as im:
        assert im.size == (40, 30) and im.mode == "RGB"
    assert lc.to_jpeg(png.getvalue(), 90) == jpg  # 決定性
    cards, notes = lg.adapter_images({"t": png.getvalue()}, _cfg())
    assert set(cards) == set(notes) == {"t"}
    assert base64.b64decode(notes["t"]) == jpg
    assert lg.adapter_images({"t": png.getvalue()}, _cfg(via_note=False, fallback_image=False)) == (
        {},
        {},
    )


def test_ac13_sent_as_image_keeps_every_note() -> None:
    upd = _next({"sent": True, "via": "image", "flexError": "Failed to get LiffToken"})
    assert upd["status"] == "sent" and upd["via"] == "image"
    assert upd["last_error"] == "圖卡發不出去，改發圖片：Failed to get LiffToken"
    upd = _next({"sent": True, "via": "image", "flexError": "liff", "captionError": "code=701"})
    assert upd["last_error"] == "圖卡發不出去，改發圖片：liff；圖片後面那行網址沒發出去：code=701"
    upd = _next({"sent": True, "via": "text", "flexError": "liff", "imageError": "obs HTTP 400"})
    assert upd["last_error"] == "圖卡發不出去，改發文字：liff；圖片也發不出去：obs HTTP 400"


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
    # 一次送滿上限、每筆之間等 post_gap_seconds：要遠在轉接器逾時之前送完（每筆本身還要幾秒）
    assert cfg.max_posts_per_run * cfg.post_gap_seconds < 0.8 * cfg.adapter_timeout_seconds
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


def _png() -> bytes:
    """一張真的小 PNG（記事本要轉 JPEG，假的位元組轉不了）。"""
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (14, 15, 20)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()


def _queue(conn: Any, kind: str, homes: list[str], *, day: dt.date = DAY, stale: float = 5) -> int:
    token = lg.save_card(conn, _SCHEMA, day, kind, PNG, (1040, 1200), NOW)
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


def test_ac14_square_chats_sync_enqueue_and_send_without_flex() -> None:
    """社群聊天跟群組同一張表、同一個勾選：勾了就排隊，送出時那一筆沒有 Flex。"""
    conn = _connect_or_skip()
    grp, sq = "c" + "1" * 32, "m" + "2" * 32
    cfg = _cfg(web_schema=_SCHEMA)
    try:
        _login(conn)
        found = lg.AdapterOutput(
            0, groups=({"homeId": grp, "name": "家人"}, {"homeId": sq, "name": "台股研究社"})
        )
        lg.sync_groups(conn, cfg, FakeRunner(found), now_fn=lambda: NOW)
        _enable(conn, grp, sq)
        assert lg.enabled_groups(conn, _SCHEMA) == [grp, sq]
        _queue(conn, "picks", lg.enabled_groups(conn, _SCHEMA))
        done = lg.AdapterOutput(
            0,
            sending=(1, 2),
            results=(
                {"id": 1, "sent": True, "via": "image", "flexError": "liff"},
                {"id": 2, "sent": True, "via": "image"},
            ),
        )
        runner = FakeRunner(done)
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert (res.sent, res.as_image) == (2, 2)
        jobs = {j["homeId"]: j for j in runner.calls[0][1]["jobs"]}
        assert jobs[grp]["message"]["type"] == "flex" and jobs[sq]["message"] is None
        assert jobs[sq]["card"] in runner.calls[0][1]["cards"]
    finally:
        conn.close()


def test_ac15_note_home_synced_and_note_post_id_reused() -> None:
    """同步記下記事本在哪；分享被叫停時記事本那篇記下來，下一輪的請求帶著它（只分享、不再發一篇）。"""
    conn = _connect_or_skip()
    sq, home = "m" + "2" * 32, "s" + "2" * 32
    cfg = _cfg(web_schema=_SCHEMA, max_backoff_wait_seconds=0)
    try:
        _login(conn)
        found = lg.AdapterOutput(
            0, groups=({"homeId": sq, "name": "台股研究社", "noteHome": home},)
        )
        lg.sync_groups(conn, cfg, FakeRunner(found), now_fn=lambda: NOW)
        _enable(conn, sq)
        _queue(conn, "picks", [sq])
        assert lg.due_jobs(conn, _SCHEMA, NOW)[0].note_home == home
        stop = {
            "id": 1,
            "sent": False,
            "error": "code=701",
            "backoffMs": 120000,
            "notePostId": "P1",
        }
        runner = FakeRunner(lg.AdapterOutput(0, sending=(1,), results=(stop,)))
        lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        req = runner.calls[0][1]
        assert req["jobs"][0]["noteHome"] == home and req["jobs"][0]["noteId"] == ""
        assert set(req["noteImages"]) == set(req["cards"]) and req["gapMs"] == 20000
        job = lg.due_jobs(conn, _SCHEMA, NOW + dt.timedelta(minutes=3))[0]
        assert job.note_post_id == "P1"
        assert job.request(cfg)["noteId"] == "P1"
    finally:
        conn.close()


def test_ac15_more_than_one_run_worth_keeps_going() -> None:
    """一次最多送 max_posts_per_run 筆，多的同一班接著送（每筆之間要等，一次塞太多會撞到轉接器逾時）。"""
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA, max_posts_per_run=2)
    try:
        _login(conn)
        _queue(conn, "picks", ["c1", "c2", "c3"])

        def ok(*ids: int) -> lg.AdapterOutput:
            results = tuple({"id": i, "sent": True, "via": "note"} for i in ids)
            return lg.AdapterOutput(0, sending=ids, results=results)

        runner = FakeRunner(ok(1, 2), ok(3))
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert [len(c[1]["jobs"]) for c in runner.calls] == [2, 1]
        assert (res.sent, res.as_note, res.left) == (3, 3, 0)
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


def test_ac6_send_saves_rotated_credential_and_marks_rows() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA, liff_id="123-abc")
    try:
        _login(conn)
        _queue(conn, "picks", ["c1", "c2", "c3", "c4"])
        runner = FakeRunner(
            lg.AdapterOutput(
                0,
                lg.Credential("a2", "r2", 2),
                {"mid": "u1"},
                sending=(1, 2, 3, 4),
                results=(
                    {"id": 1, "sent": True, "via": "flex"},
                    {"id": 2, "sent": True, "via": "text", "flexError": "liff"},
                    {"id": 3, "sent": True, "via": "image", "flexError": "liff"},
                ),
            )
        )
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert (res.sent, res.as_image, res.as_text, res.left) == (3, 1, 1, 0)
        assert res.failed == (f"#4 {lg.UNKNOWN_HINT}",)
        req = runner.calls[0][1]
        assert req["credential"] == {"accessToken": "a1", "refreshToken": "r1", "expire": 1}
        assert req["liffId"] == "123-abc"
        assert [j["homeId"] for j in req["jobs"]] == ["c1", "c2", "c3", "c4"]
        # AC13 改發圖片：同一張圖發四個群組，請求裡只帶一份 PNG（base64）
        assert list(req["cards"].values()) == [base64.b64encode(PNG).decode()]
        assert {j["card"] for j in req["jobs"]} == set(req["cards"])
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a2", "r2", 2)
        assert [r[3:5] for r in _rows(conn)] == [
            ("sent", "flex"),
            ("sent", "text"),
            ("sent", "image"),
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


def test_ac13_card_images_dedup_and_missing_card_raises() -> None:
    conn = _connect_or_skip()
    try:
        _queue(conn, "picks", ["c1"])
        token = lg.due_jobs(conn, _SCHEMA, NOW)[0].card_token
        assert lg.card_pngs(conn, _SCHEMA, [token, token]) == {token: PNG}
        assert lg.card_pngs(conn, _SCHEMA, []) == {}
        with pytest.raises(DataError, match="找不到圖卡"):
            lg.card_pngs(conn, _SCHEMA, [token, "0" * 32])
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


def test_ac4_storage_json_persists_and_column_added_to_old_table() -> None:
    conn = _connect_or_skip()
    try:
        # 舊表（storage_json 之前就登入過）：ensure_tables 要把欄位補上
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
            # storage_json 之前的原始 schema（其他欄位都在，只差這一欄）
            cur.execute(
                f'CREATE TABLE "{_SCHEMA}".line_account ('
                "id text PRIMARY KEY, mid text, display_name text, "
                "access_token text NOT NULL, refresh_token text, token_expire bigint, "
                "updated_at timestamptz NOT NULL, paused_until timestamptz, busy_until timestamptz, "
                "last_error text)"
            )
            # 記事本之前的 line_groups／line_cards／line_posts（少 note_home、note_post_id）
            cur.execute(
                f'CREATE TABLE "{_SCHEMA}".line_groups ('
                "home_id text PRIMARY KEY, name text NOT NULL, enabled boolean NOT NULL DEFAULT false, "
                "present boolean NOT NULL DEFAULT true, synced_at timestamptz NOT NULL, "
                "updated_by text, updated_at timestamptz)"
            )
            cur.execute(
                f'CREATE TABLE "{_SCHEMA}".line_cards ('
                "token text PRIMARY KEY, date date NOT NULL, kind text NOT NULL, png bytea NOT NULL, "
                "width integer NOT NULL, height integer NOT NULL, created_at timestamptz NOT NULL)"
            )
            cur.execute(
                f'CREATE TABLE "{_SCHEMA}".line_posts ('
                "id bigserial PRIMARY KEY, date date NOT NULL, kind text NOT NULL, home_id text NOT NULL, "
                f'card_token text NOT NULL REFERENCES "{_SCHEMA}".line_cards(token) ON DELETE CASCADE, '
                "alt_text text NOT NULL, link_url text NOT NULL, status text NOT NULL, via text, "
                "enqueued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, sent_at timestamptz, "
                "attempts integer NOT NULL DEFAULT 0, last_error text, UNIQUE (date, kind, home_id))"
            )
        conn.commit()
        lg.ensure_tables(conn, _SCHEMA)
        with conn.cursor() as cur:
            for table, col in (("line_groups", "note_home"), ("line_posts", "note_post_id")):
                cur.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                    (_SCHEMA, table, col),
                )
                assert cur.fetchone(), f"{table}.{col} 沒補上"
        blob = '{"e2eeKeys:u1":"secret","reqseq":"3"}'
        lg.save_account(
            conn, _SCHEMA, lg.Credential("a1", "r1", 1, blob), now=NOW, profile={"mid": "u1"}
        )
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a1", "r1", 1, blob)
        # 之後 token 輪替、但沒有新的 storage（None）：不要把存好的那份蓋掉
        lg.save_account(conn, _SCHEMA, lg.Credential("a2", "r2", 2, None), now=NOW)
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a2", "r2", 2, blob)
    finally:
        conn.close()
