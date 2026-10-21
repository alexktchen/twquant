"""T-423：LINE 群組記事本貼文。不連 LINE：轉接器以假的 runner 注入；PG 整合測試沒有 DSN 自動跳過。"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any

import pytest

from twquant.config import LineGroupConfig, load_config
from twquant.conventions import TZ
from twquant.errors import ConfigError, DataError, ExecutionError
from twquant.monitoring import line_group as lg
from twquant.monitoring.push_notify import HotGroup

ROOT = Path(__file__).resolve().parents[1]
LINES = [("CORE", "大盤精選"), ("QUIET", "潛伏飆股"), ("QUIETV", "高分爆量（驗證中）")]
DAY = dt.date(2026, 10, 21)
NOW = dt.datetime(2026, 10, 21, 17, 40, tzinfo=TZ)


def _cfg(**over: Any) -> LineGroupConfig:
    base = load_config(ROOT / "configs" / "line_group.yaml", LineGroupConfig)
    return base.model_copy(update=over)


def _job(**over: Any) -> lg.Job:
    base: dict[str, Any] = {
        "id": 7,
        "home_id": "cabc",
        "text": "10/21（三）今日名單",
        "enqueued_at": NOW,
        "line_post_id": None,
        "status": "queued",
        "share_state": "pending",
        "attempts": 0,
    }
    return lg.Job(**{**base, **over})


# ── AC1 名單貼文 ───────────────────────────────────────────────────────────


def test_ac1_picks_lines_ranks_new_entries_and_blurbs() -> None:
    picks = [
        lg.PickRow("QUIET", 1, "3090", "日電貿", "被動元件", None),
        lg.PickRow("CORE", 2, "2317", "鴻海", None, None),
        lg.PickRow("CORE", 1, "2330", "台積電", "晶圓代工", "外資連三買。"),
    ]
    text = lg.compose_picks(DAY, LINES, picks, {"CORE": {"2317"}}, max_per_line=20)
    assert text == (
        "10/21（三）今日名單\n\n"
        "【大盤精選】2 檔，新進 1\n"
        "1. 2330 台積電（晶圓代工） 新進\n"
        "　外資連三買。\n"
        "2. 2317 鴻海\n\n"
        "【潛伏飆股】1 檔\n"  # 之前沒有名單：不標新進
        "1. 3090 日電貿（被動元件）\n\n"
        "【高分爆量（驗證中）】今天沒有符合條件的股票"
    )


def test_ac1_picks_beyond_limit_point_to_site() -> None:
    picks = [lg.PickRow("CORE", i, f"{1000 + i}", None, None, None) for i in range(1, 5)]
    text = lg.compose_picks(DAY, LINES[:1], picks, {}, max_per_line=2)
    assert "1. 1001\n2. 1002\n另 2 檔請看網站" in text
    assert "1003" not in text


# ── AC2 族群資金、晨報、收尾 ───────────────────────────────────────────────


def test_ac2_groups_text_and_empty_raises() -> None:
    hot = [
        HotGroup("被動元件", 2.34, 12, 9, ("大毅", "國巨")),
        HotGroup("散熱", 1.8, 8, 6, ("奇鋐", "雙鴻")),
    ]
    text = lg.compose_groups(DAY, hot, max_listed=1)
    assert text == (
        "10/21（三）名單上的族群資金流入\n\n"
        "被動元件：資金 2.3 倍、12 檔 9 漲｜名單 2 檔：大毅、國巨\n"
        "另 1 個族群請看網站「盤勢」"
    )
    with pytest.raises(ValueError, match="沒有資金流入"):
        lg.compose_groups(DAY, [], max_listed=3)


def test_ac2_brief_model_rule_and_missing() -> None:
    model = lg.BriefRow(
        "台指期夜盤小漲", ("【方向】偏多", "外資期貨空單減"), "美債殖利率", "規則句"
    )
    assert lg.compose_brief(DAY, "morning", model) == (
        "10/21（三）盤前晨報\n\n台指期夜盤小漲\n\n• 【方向】偏多\n• 外資期貨空單減\n\n風險：美債殖利率"
    )
    rule = lg.BriefRow(None, (), None, "加權收 23,000 點，漲 0.5%。")
    assert (
        lg.compose_brief(DAY, "evening", rule)
        == "10/21（三）盤後快訊\n\n加權收 23,000 點，漲 0.5%。"
    )
    with pytest.raises(ValueError, match="沒有內容"):
        lg.compose_brief(DAY, "evening", lg.BriefRow(None, (), None, None))


def test_ac2_finish_appends_link_footer_and_rejects_too_long() -> None:
    text = lg.finish("標題\n內文\n", site_url="https://x.dev/", footer="僅供參考", max_chars=100)
    assert text == "標題\n內文\n\nhttps://x.dev/\n僅供參考"
    with pytest.raises(DataError, match="超過上限"):
        lg.finish("字" * 200, site_url="https://x.dev/", footer="", max_chars=100)


# ── AC3 轉接器事件 ─────────────────────────────────────────────────────────


def test_ac3_credential_patches_stack_on_base(capsys: pytest.CaptureFixture[str]) -> None:
    base = lg.Credential("old", "r1", 100)
    out = lg.parse_events(
        [
            '{"type":"profile","mid":"u1","displayName":"小幫手"}',
            "linejs 自己印的雜訊",
            '{"type":"credential","refreshToken":"r2"}',
            '{"type":"credential","accessToken":"new","expire":200}',
            '{"type":"result","id":7,"posted":true,"linePostId":"p1","shared":true}',
            "",
        ],
        0,
        base,
    )
    assert out.credential == lg.Credential("new", "r2", 200)
    assert out.profile == {"mid": "u1", "displayName": "小幫手"}
    assert out.results[0]["linePostId"] == "p1"
    assert out.fatal is None
    assert "linejs 自己印的雜訊" in capsys.readouterr().err  # 不吞掉


def test_ac3_login_errors_point_to_relogin() -> None:
    assert lg.needs_login('getProfile(/S4) -> {"code":"AUTHENTICATION_FAILED"}')
    assert lg.needs_login('{"code":"MUST_REFRESH_V3_TOKEN"}')
    assert not lg.needs_login("code=701 請稍後再重新發布貼文")


def test_ac3_fatal_nonzero_exit_and_unknown_event() -> None:
    assert lg.parse_events(['{"type":"fatal","error":"AUTHENTICATION_FAILED"}'], 2, None).fatal == (
        "AUTHENTICATION_FAILED"
    )
    assert lg.parse_events([], 1, None).fatal == "轉接器結束碼 1"
    with pytest.raises(ExecutionError, match="不認得的事件"):
        lg.parse_events(['{"type":"what"}'], 0, None)
    with pytest.raises(ExecutionError, match="沒有 access token"):
        lg.parse_events(['{"type":"credential","expire":1}'], 0, None)


# ── AC4 每筆貼文的下一個狀態 ───────────────────────────────────────────────


def test_ac4_posted_and_shared() -> None:
    upd = lg.next_state(
        _job(), {"posted": True, "linePostId": "p1", "shared": True}, now=NOW, max_attempts=3
    )
    assert upd == {
        "status": "posted",
        "line_post_id": "p1",
        "last_error": None,
        "posted_at": NOW,
        "share_state": "shared",
        "shared_at": NOW,
    }


def test_ac4_share_backoff_stays_pending_other_share_errors_fail() -> None:
    res = {"posted": True, "linePostId": "p1", "shared": False, "error": "code=701", "backoffMs": 1}
    upd = lg.next_state(_job(), res, now=NOW, max_attempts=3)
    assert upd["status"] == "posted" and "share_state" not in upd  # 下一輪只補分享
    assert upd["last_error"].startswith("分享到聊天：LINE 要求暫緩")
    res = {"posted": True, "linePostId": "p1", "shared": False, "error": "code=500"}
    assert lg.next_state(_job(), res, now=NOW, max_attempts=3)["share_state"] == "failed"
    nid = lg.next_state(_job(), {"posted": True, "linePostId": None}, now=NOW, max_attempts=3)
    assert nid["share_state"] == "failed" and "沒拿到 LINE 貼文 id" in nid["last_error"]
    # 已發過、這輪只補分享：不改 posted_at
    again = lg.next_state(
        _job(status="posted", line_post_id="p1"),
        {"posted": True, "linePostId": "p1", "shared": True},
        now=NOW,
        max_attempts=3,
    )
    assert "posted_at" not in again and again["share_state"] == "shared"
    # 不分享的貼文不碰分享欄位
    skip = lg.next_state(
        _job(share_state="skipped"), {"posted": True, "linePostId": "p1"}, now=NOW, max_attempts=3
    )
    assert "share_state" not in skip


def test_ac4_post_failures_backoff_free_others_count_until_failed() -> None:
    res = {"posted": False, "error": "限制使用社群", "backoffMs": 600000}
    assert lg.next_state(_job(), res, now=NOW, max_attempts=3) == {"last_error": lg.BACKOFF_HINT}
    res = {"posted": False, "error": "code=118"}
    assert lg.next_state(_job(), res, now=NOW, max_attempts=3)["status"] == "queued"
    last = lg.next_state(_job(attempts=2), res, now=NOW, max_attempts=3)
    assert last == {"attempts": 3, "status": "failed", "last_error": "code=118"}
    with pytest.raises(ExecutionError, match="卻沒有錯誤"):
        lg.next_state(_job(), {"posted": False}, now=NOW, max_attempts=3)


def test_ac4_backoff_until_takes_longest() -> None:
    assert lg.backoff_until([{"id": 1}], NOW) is None
    until = lg.backoff_until([{"backoffMs": 120000}, {"backoffMs": 600000}], NOW)
    assert until == NOW + dt.timedelta(minutes=10)


# ── AC5 群組 id ────────────────────────────────────────────────────────────


def test_ac5_group_mids_only_regular_groups() -> None:
    cfg = _cfg()
    assert lg.group_mids(cfg, {cfg.group_mids_env: " c1 , c2,"}) == ["c1", "c2"]
    with pytest.raises(ConfigError, match="缺要發的 LINE 群組"):
        lg.group_mids(cfg, {})
    with pytest.raises(ConfigError, match="只收一般群組"):
        lg.group_mids(cfg, {cfg.group_mids_env: "c1,s9"})


def test_ac5_job_request_for_adapter() -> None:
    req = _job(share_state="skipped").request()
    assert req == {
        "id": 7,
        "homeId": "cabc",
        "text": "10/21（三）今日名單",
        "sinceMs": int(NOW.timestamp() * 1000),
        "linePostId": None,
        "share": False,
    }


# ── AC7 配置 ───────────────────────────────────────────────────────────────


def test_ac7_config_four_kinds_and_lease_longer_than_adapter() -> None:
    cfg = _cfg()
    assert set(cfg.kinds) == set(lg.KINDS)
    assert cfg.lease_seconds > cfg.adapter_timeout_seconds  # 占用不能比轉接器先到期
    assert (ROOT / cfg.adapter).exists()
    # 群組 id（c＋32 位十六進位）不進版控，只放環境變數
    text = (ROOT / "configs" / "line_group.yaml").read_text(encoding="utf-8")
    assert re.search(r"\bc[0-9a-f]{32}\b", text) is None


# ── AC6 送出流程（PG 整合） ────────────────────────────────────────────────

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
            f'SELECT id, kind, home_id, status, share_state, line_post_id, attempts FROM "{_SCHEMA}".line_posts ORDER BY id'
        )
        return list(cur.fetchall())


def test_ac6_enqueue_once_per_day_kind_group_and_expiry() -> None:
    conn = _connect_or_skip()
    try:
        lg.ensure_tables(conn, _SCHEMA)

        def put(kind: str, homes: list[str], text: str, share: bool = True) -> int:
            return lg.enqueue(
                conn, _SCHEMA, DAY, kind, homes, text, now=NOW, stale_hours=2.0, share=share
            )

        assert put("picks", ["c1", "c2"], "A") == 2
        assert put("picks", ["c1", "c3"], "B") == 1  # c1 已排過：不動、也不改內文
        assert put("morning", ["c1"], "C", share=False) == 1
        assert len(lg.due_jobs(conn, _SCHEMA, NOW)) == 4
        assert lg.due_jobs(conn, _SCHEMA, NOW + dt.timedelta(hours=3)) == []
        assert {r[3] for r in _rows(conn)} == {"expired"}
        assert {r[4] for r in _rows(conn)} == {"expired", "skipped"}
    finally:
        conn.close()


def test_ac6_send_posts_shares_and_saves_rotated_credential() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA)
    try:
        lg.ensure_tables(conn, _SCHEMA)
        with pytest.raises(ConfigError, match="還沒登入"):
            lg.send_due(conn, cfg, FakeRunner(), now_fn=lambda: NOW, sleep=lambda s: None)
        lg.save_account(conn, _SCHEMA, lg.Credential("a1", "r1", 1), now=NOW, profile={"mid": "u1"})
        lg.enqueue(conn, _SCHEMA, DAY, "picks", ["c1"], "名單", now=NOW, stale_hours=5, share=True)
        runner = FakeRunner(
            lg.AdapterOutput(
                0,
                lg.Credential("a2", "r2", 2),
                {"mid": "u1"},
                results=({"id": 1, "posted": True, "linePostId": "p1", "shared": True},),
            )
        )
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert (res.posted, res.shared, res.failed, res.left) == (1, 1, (), 0)
        assert runner.calls[0][1]["credential"] == {
            "accessToken": "a1",
            "refreshToken": "r1",
            "expire": 1,
        }
        assert runner.calls[0][1]["jobs"][0]["share"] is True
        assert lg.load_credential(conn, _SCHEMA) == lg.Credential("a2", "r2", 2)
        assert _rows(conn) == [(1, "picks", "c1", "posted", "shared", "p1", 0)]
        assert lg.account_state(conn, _SCHEMA)["busy_until"] is None  # 用完放開
        # 沒有到期的：不叫轉接器
        lg.send_due(conn, cfg, FakeRunner(), now_fn=lambda: NOW, sleep=lambda s: None)
    finally:
        conn.close()


def test_ac6_backoff_waits_then_retries_remaining() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA, max_backoff_wait_seconds=660)
    clock = Clock(NOW)
    try:
        lg.ensure_tables(conn, _SCHEMA)
        lg.save_account(conn, _SCHEMA, lg.Credential("a1", "r1", 1), now=NOW)
        for kind in ("picks", "groups"):
            lg.enqueue(conn, _SCHEMA, DAY, kind, ["c1"], kind, now=NOW, stale_hours=5, share=False)
        backoff = {"id": 1, "posted": False, "error": "code=701", "backoffMs": 120000}
        runner = FakeRunner(
            lg.AdapterOutput(0, results=(backoff,)),  # 第一筆被叫停，第二筆沒輪到
            lg.AdapterOutput(
                0,
                results=(
                    {"id": 1, "posted": True, "linePostId": "p1"},
                    {"id": 2, "posted": True, "linePostId": "p2"},
                ),
            ),
        )
        res = lg.send_due(conn, cfg, runner, now_fn=clock.now, sleep=clock.sleep)
        assert clock.slept == [120.0]
        assert (res.posted, res.left) == (2, 0)
        assert [r[3] for r in _rows(conn)] == ["posted", "posted"]
        assert [r[6] for r in _rows(conn)] == [0, 0]  # 叫停不算失敗次數
    finally:
        conn.close()


def test_ac6_long_backoff_or_busy_account_left_for_next_run() -> None:
    conn = _connect_or_skip()
    clock = Clock(NOW)
    try:
        lg.ensure_tables(conn, _SCHEMA)
        lg.save_account(conn, _SCHEMA, lg.Credential("a1", None, None), now=NOW)
        lg.enqueue(conn, _SCHEMA, DAY, "picks", ["c1"], "x", now=NOW, stale_hours=5, share=False)
        cfg = _cfg(web_schema=_SCHEMA, max_backoff_wait_seconds=60)
        runner = FakeRunner(
            lg.AdapterOutput(
                0,
                results=({"id": 1, "posted": False, "error": "限制使用社群", "backoffMs": 600000},),
            )
        )
        res = lg.send_due(conn, cfg, runner, now_fn=clock.now, sleep=clock.sleep)
        assert res.left == 1 and "留給下一班" in res.note
        assert clock.slept == []
        # 另一班占著帳號：等到 lock_wait_seconds 還沒空就留給下一班
        lg.set_account_status(conn, _SCHEMA, paused_until=None, last_error=None)
        assert lg.acquire_lease(conn, _SCHEMA, clock.now(), 3600)
        cfg = _cfg(web_schema=_SCHEMA, lock_wait_seconds=30, poll_seconds=15)
        res = lg.send_due(conn, cfg, FakeRunner(), now_fn=clock.now, sleep=clock.sleep)
        assert res.left == 1 and "另一班" in res.note
        assert clock.slept == [15, 15]
    finally:
        conn.close()


def test_ac6_fatal_keeps_queue_and_records_error() -> None:
    conn = _connect_or_skip()
    cfg = _cfg(web_schema=_SCHEMA)
    try:
        lg.ensure_tables(conn, _SCHEMA)
        lg.save_account(conn, _SCHEMA, lg.Credential("a1", None, None), now=NOW)
        lg.enqueue(conn, _SCHEMA, DAY, "picks", ["c1"], "x", now=NOW, stale_hours=5, share=False)
        runner = FakeRunner(lg.AdapterOutput(2, fatal="AUTHENTICATION_FAILED"))
        res = lg.send_due(conn, cfg, runner, now_fn=lambda: NOW, sleep=lambda s: None)
        assert res.failed == ("AUTHENTICATION_FAILED",) and res.left == 1
        assert lg.account_state(conn, _SCHEMA)["last_error"] == "AUTHENTICATION_FAILED"
        assert _rows(conn)[0][3:5] == ("queued", "skipped")
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
                f'CREATE TABLE "{s}".pick_blurbs (date date, line text, stock_id text, text text)'
            )
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
                f"INSERT INTO \"{s}\".pick_blurbs VALUES (%s, 'CORE', '1802', '量增')", (DAY,)
            )
            cur.execute(
                f'INSERT INTO "{s}".morning_brief VALUES (%s, %s, %s, %s, %s)',
                (DAY, "標題", '["一", "二"]', None, "規則"),
            )
        conn.commit()
        loaded = lg.load_picks(conn, s, DAY, LINES, with_blurbs=True)
        assert loaded is not None
        rows, previous = loaded
        assert rows == [
            lg.PickRow("CORE", 1, "2330", "台積電", "晶圓代工", None),
            lg.PickRow("CORE", 2, "1802", "台玻", "玻璃陶瓷", "量增"),
        ]
        assert previous == {"CORE": {"2330"}}
        assert lg.load_picks(conn, s, DAY + dt.timedelta(days=1), LINES, with_blurbs=True) is None
        assert lg.load_brief(conn, s, DAY, "morning") == lg.BriefRow(
            "標題", ("一", "二"), None, "規則"
        )
        assert lg.load_brief(conn, s, DAY, "evening") is None  # 表還沒建
    finally:
        conn.close()
