"""T-329：每日選股看板的 HTML 版（`reporting/pick_board_html.py`）。

每條 AC 至少一條測試；頁面的每個數字都必須來自看板 payload，不得另算。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from twquant.reporting.pick_board import save_board
from twquant.reporting.pick_board_html import HTML_FILE, render_board_html, save_board_html

WINDOW = 2  # 「近 N 日上榜」的 N；小於 fixture 的天數才驗得到「只看最後 N 天」


def _day(
    date: str, holdings: list[dict[str, Any]], picks: list[str] | None, **kw: Any
) -> dict[str, Any]:
    held = {h["stock_id"] for h in holdings}
    rec: dict[str, Any] = {
        "date": date,
        "entered": kw.get("entered", []),
        "exited": kw.get("exited", []),
        "holdings": holdings,
        "watch_new": kw.get("watch_new", []),
    }
    if picks is None:
        rec["picks"] = None
        rec["picks_missing"] = "no_scores"
    else:
        rec["picks"] = [
            {
                "stock_id": sid,
                "rank": i + 1,
                "held": sid in held,
                "new": sid in kw.get("watch_new", []),
            }
            for i, sid in enumerate(picks)
        ]
    return rec


def _board(*, no_quiet_picks: bool = False) -> dict[str, Any]:
    """兩條線、三個交易日的最小看板。數字刻意不整，讓格式化有東西可驗。"""
    quiet_last_picks = None if no_quiet_picks else ["5340", "4749", "8932"]
    core_days = [
        _day(
            "2026-10-06",
            [
                {
                    "stock_id": "1709",
                    "entry_date": "2026-09-02",
                    "held_sessions": 22,
                    "change": 0.6851,
                }
            ],
            ["1303", "1709", "6199"],
        ),
        _day(
            "2026-10-07",
            [
                {
                    "stock_id": "1709",
                    "entry_date": "2026-09-02",
                    "held_sessions": 23,
                    "change": 0.6851,
                },
                {
                    "stock_id": "6168",
                    "entry_date": "2026-10-02",
                    "held_sessions": 3,
                    "change": -0.1264,
                },
            ],
            ["1303", "6199", "6505"],
            entered=[{"stock_id": "6168", "reason": "rebalance"}],
            exited=[
                {
                    "stock_id": "2395",
                    "reason": "rebalance",
                    "entry_date": "2026-08-04",
                    "held_sessions": 40,
                    "change": 0.279,
                }
            ],
        ),
        _day(
            "2026-10-08",
            [
                {
                    "stock_id": "1709",
                    "entry_date": "2026-09-02",
                    "held_sessions": 24,
                    "change": 0.6851,
                },
                {
                    "stock_id": "6168",
                    "entry_date": "2026-10-02",
                    "held_sessions": 4,
                    "change": -0.1264,
                },
            ],
            ["1303", "3147", "6199"],
            watch_new=["3147"],
        ),
    ]
    quiet_days = [
        _day(
            "2026-10-06",
            [
                {
                    "stock_id": "4749",
                    "entry_date": "2026-09-08",
                    "held_sessions": 18,
                    "change": -0.012,
                }
            ],
            ["5340", "8932", "3211"],
        ),
        _day(
            "2026-10-07",
            [
                {
                    "stock_id": "4749",
                    "entry_date": "2026-09-08",
                    "held_sessions": 19,
                    "change": -0.012,
                }
            ],
            ["5340", "4749", "3211"],
        ),
        _day(
            "2026-10-08",
            [{"stock_id": "4749", "entry_date": "2026-09-08", "held_sessions": 20, "change": 0.0}],
            quiet_last_picks,
            watch_new=["8932"],
        ),
    ]
    return {
        "schema_version": 1,
        "as_of": "2026-10-08",
        "generated_at": "2026-10-08T19:47:18+08:00",
        "replay_start": "2026-05-01",
        "initial_capital": 10000000.0,
        "strategies": [
            {
                "code": "CORE",
                "name": "大盤精選",
                "summary": "全市場排序模型每月挑 15 檔。",
                "config": "lw_e0_ctrl",
                "kind": "rebalance",
                "hold_sessions": None,
                "slots": 15,
                "picks_ordered": False,
                "equity": [],
                "days": core_days,
                "next": {"rebalance": False, "buy": [], "sell": []},
            },
            {
                "code": "QUIET",
                "name": "潛伏飆股",
                "summary": "安靜股裡挑 20 天內最可能漲 20% 的。",
                "config": "quiet_s0",
                "kind": "slots",
                "hold_sessions": 20,
                "slots": 15,
                "picks_ordered": True,
                "equity": [],
                "days": quiet_days,
                "next": {
                    "rebalance": False,
                    "buy": [],
                    "sell": [{"stock_id": "4749", "reason": "time_stop"}],
                },
            },
        ],
        "stocks": {
            "1303": {"name": "南亞", "market": "TWSE", "industry": "塑膠工業"},
            "1709": {"name": "和益", "market": "TWSE", "industry": "化學工業"},
            "2395": {"name": "研華", "market": "TWSE", "industry": "電腦及週邊設備業"},
            "3147": {"name": "大綜", "market": "TPEX", "industry": "資訊服務業"},
            "3211": {"name": "順達", "market": "TWSE", "industry": "電腦及週邊設備業"},
            "4749": {"name": "新應材", "market": "TPEX", "industry": "半導體業"},
            "5340": {"name": "建榮", "market": "TPEX", "industry": "電子零組件業"},
            "6168": {"name": "宏齊", "market": "TPEX", "industry": "光電業"},
            "6199": {"name": "天品", "market": "TPEX", "industry": "其他業"},
            "6505": {"name": "台塑化", "market": "TWSE", "industry": "油電燃氣業"},
            "8932": {"name": "<b>智通</b>", "market": "TPEX", "industry": "數位雲端"},
        },
    }


# ── AC1：兩條線都畫出來，持股列與漲跌直接來自 payload ─────────────────────────


def test_ac1_both_lines_with_holdings_from_the_payload() -> None:
    html = render_board_html(_board(), window_sessions=WINDOW)
    assert "大盤精選" in html and "潛伏飆股" in html
    assert "全市場排序模型每月挑 15 檔。" in html
    # 持股：代號＋名稱、進場日、持有交易日、漲跌（紅漲綠跌：class up／down）
    assert "和益" in html and "1709" in html
    assert 'class="num up">+68.5%' in html
    assert 'class="num down">-12.6%' in html
    assert 'class="num flat">+0.0%' in html
    assert "2026-10-08" in html  # as_of


def test_ac1_holdings_count_matches_the_last_day() -> None:
    board = _board()
    html = render_board_html(board, window_sessions=WINDOW)
    core_rows = html.count('data-row="holding:CORE"')
    quiet_rows = html.count('data-row="holding:QUIET"')
    assert core_rows == len(board["strategies"][0]["days"][-1]["holdings"]) == 2
    assert quiet_rows == len(board["strategies"][1]["days"][-1]["holdings"]) == 1


# ── AC2：推薦名單的「近 N 日上榜」只看最後 N 天，數字由名單成員算出 ──────────────


def test_ac2_appearance_marks_count_only_the_last_window_days() -> None:
    board = _board()
    html = render_board_html(board, window_sessions=WINDOW)
    # 1303：三天都在前 n 名，但視窗只有最後 2 天 → 2/2；3147 只有最後一天 → 1/2
    assert 'data-marks="CORE:1303" data-count="2/2"' in html
    assert 'data-marks="CORE:3147" data-count="1/2"' in html
    # 4749：10/7、10/8 在 QUIET 名單 → 2/2；5340 三天都在 → 2/2
    assert 'data-marks="QUIET:4749" data-count="2/2"' in html
    assert 'data-marks="QUIET:5340" data-count="2/2"' in html


def test_ac2_window_larger_than_the_days_available_counts_what_exists() -> None:
    html = render_board_html(_board(), window_sessions=10)
    assert 'data-marks="CORE:1303" data-count="3/3"' in html


def test_ac2_held_and_new_chips_follow_the_payload_flags() -> None:
    html = render_board_html(_board(), window_sessions=WINDOW)
    assert 'data-chips="QUIET:4749" data-held="1"' in html  # 持有中
    assert 'data-chips="CORE:3147" data-held="0" data-new="1"' in html  # 新上榜
    assert 'data-chips="CORE:1303" data-held="0" data-new="0"' in html


# ── AC3：下一個交易日的計畫照 payload 的 next 寫 ─────────────────────────────


def test_ac3_slots_line_lists_sells_and_explains_the_empty_buy_list() -> None:
    html = render_board_html(_board(), window_sessions=WINDOW)
    assert "賣出 1 檔" in html and "新應材" in html
    assert "抱滿 20 個交易日" in html
    assert "買進 0 檔" in html
    assert "再下一個交易日" in html  # 位子要等賣掉才空出來


def test_ac3_rebalance_line_without_a_plan_says_no_trades() -> None:
    html = render_board_html(_board(), window_sessions=WINDOW)
    assert "不買不賣" in html


def test_ac3_rebalance_plan_lists_buys_and_sells() -> None:
    board = _board()
    board["strategies"][0]["next"] = {
        "rebalance": True,
        "buy": ["1303", "6505"],
        "sell": [{"stock_id": "6168", "reason": "rebalance"}],
    }
    html = render_board_html(board, window_sessions=WINDOW)
    assert "換股" in html
    assert "買進 2 檔" in html and "台塑化" in html
    assert "賣出 1 檔" in html and "宏齊" in html
    assert "不買不賣" not in html


# ── AC4：模型沒打分的那天要明說，不畫空表 ─────────────────────────────────────


def test_ac4_missing_picks_are_stated_not_drawn() -> None:
    html = render_board_html(_board(no_quiet_picks=True), window_sessions=WINDOW)
    assert "模型沒有打分" in html
    assert 'data-marks="QUIET:' not in html
    assert 'data-marks="CORE:1303"' in html  # 另一條線照常


# ── AC5：名稱一律跳脫，資料不能變成標籤 ───────────────────────────────────────


def test_ac5_names_are_html_escaped() -> None:
    html = render_board_html(_board(), window_sessions=WINDOW)
    assert "&lt;b&gt;智通&lt;/b&gt;" in html
    assert "<b>智通</b>" not in html


# ── AC6：決定性輸出；落地到 data/reports/pick_board.html ─────────────────────


def test_ac6_render_is_deterministic_and_save_overwrites(tmp_path: Path) -> None:
    board = _board()
    first = render_board_html(board, window_sessions=WINDOW)
    assert first == render_board_html(json.loads(json.dumps(board)), window_sessions=WINDOW)
    path = save_board_html(tmp_path, first)
    assert path == tmp_path / "reports" / HTML_FILE
    assert path.read_text(encoding="utf-8") == first
    assert (
        save_board_html(tmp_path, "<title>x</title>").read_text(encoding="utf-8")
        == "<title>x</title>"
    )
    assert not path.with_suffix(".tmp").exists()


# ── AC7：`pick-board-html` 從既有 JSON 重畫，不重播；沒有看板就明講 ───────────


def test_ac7_cli_renders_from_the_saved_board_or_refuses(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from twquant.cli import app

    runner = CliRunner()
    missing = runner.invoke(app, ["pick-board-html", "--out", str(tmp_path), "--no-prices"])
    assert missing.exit_code != 0
    assert "pick_board.json" in missing.output

    save_board(tmp_path, _board())
    ok = runner.invoke(app, ["pick-board-html", "--out", str(tmp_path), "--no-prices"])
    assert ok.exit_code == 0, ok.output
    out = tmp_path / "reports" / HTML_FILE
    assert out.exists() and "大盤精選" in out.read_text(encoding="utf-8")


def test_ac7_window_comes_from_the_config() -> None:
    from twquant.config import load_config
    from twquant.reporting.pick_board import PickBoardConfig

    cfg = load_config(Path("configs/pick_board.yaml"), PickBoardConfig)
    assert cfg.html_window_sessions >= 1


# ── AC8：收盤與漲幅來自傳入的價格，缺的顯示「—」而不是 0 ─────────────────────


def test_ac8_prices_render_close_and_changes_or_a_dash() -> None:
    prices: dict[str, dict[str, float | None]] = {
        "1709": {"close": 123.5, "change_1d": 0.0312, "change_window": -0.021},
        "5340": {"close": 48.0, "change_1d": None, "change_window": -0.021},
        "4749": {"close": 10.0, "change_1d": 0.0, "change_window": 0.0},
    }
    html = render_board_html(_board(), window_sessions=WINDOW, prices=prices)
    assert "123.50" in html and 'class="num up">+3.1%' in html and 'class="num down">-2.1%' in html
    assert "48.00" in html
    assert 'class="num flat">—' in html  # 5340 的當日漲幅缺
    assert 'class="num flat">+0.0%' in html  # 4749 的漲幅是 0
    without = render_board_html(_board(), window_sessions=WINDOW)
    assert "123.50" not in without and 'class="num">—' in without


def test_ac8_price_changes_math_and_missing_ends() -> None:
    import datetime as dt

    from twquant.reporting.pick_board_html import price_changes

    d6, d7, d8 = dt.date(2026, 10, 6), dt.date(2026, 10, 7), dt.date(2026, 10, 8)
    closes = {"A": {d6: 100.0, d7: 110.0, d8: 121.0}, "B": {d8: 50.0}}
    out = price_changes(closes, as_of=d8, prev_session=d7, window_start=d6)
    assert out["A"]["close"] == 121.0
    assert abs((out["A"]["change_1d"] or 0.0) - 0.1) < 1e-12
    assert abs((out["A"]["change_window"] or 0.0) - 0.21) < 1e-12
    assert out["B"] == {"close": 50.0, "change_1d": None, "change_window": None}
    none = price_changes(closes, as_of=d8, prev_session=None, window_start=None)
    assert none["A"]["change_1d"] is None and none["A"]["change_window"] is None


@pytest.mark.parametrize("value", [0.0, -0.0])
def test_ac1_zero_change_is_flat_not_down(value: float) -> None:
    board = _board()
    board["strategies"][1]["days"][-1]["holdings"][0]["change"] = value
    html = render_board_html(board, window_sessions=WINDOW)
    assert 'class="num flat">+0.0%' in html
