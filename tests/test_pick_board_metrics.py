"""T-330：HTML 版看板的「整體表現」——重播以來的年化／回撤／逐筆，與研究回測參考數字。

數字只能來自看板 payload 與結果包的 `metrics`；年化不足一年要標明是換算。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.test_pick_board_html import _board
from twquant.backtest.metrics import compute_metrics
from twquant.reporting.pick_board import save_board
from twquant.reporting.pick_board_html import (
    HTML_FILE,
    reference_summary,
    render_board_html,
    replay_summary,
)

EQUITY: list[dict[str, Any]] = [
    {"date": "2026-10-05", "value": 10_000_000.0},
    {"date": "2026-10-06", "value": 10_500_000.0},
    {"date": "2026-10-07", "value": 10_200_000.0},
    {"date": "2026-10-08", "value": 11_000_000.0},
]


def _pack(*, n_days: int = 1660, cagr: float = 0.2778, mdd: float = -0.4139) -> dict[str, Any]:
    """結果包裡頁面會用到的欄位（真包另有 ledger／equity 等，這裡不需要）。"""
    return {
        "start": "2019-05-02",
        "end": "2026-02-28",
        "initial_capital": 80_000_000.0,
        "final_equity": 401_789_608.99,
        "metrics": {
            "cagr": cagr,
            "calmar": 0.6712,
            "mdd": mdd,
            "n_days": n_days,
            "sharpe": 1.0119,
            "sortino": 1.3582,
            "turnover": 8.8812,
            "win_rate": 0.5588,
        },
    }


# ── AC1：重播以來的整體數字由淨值序列算，與引擎 compute_metrics 一致 ─────────────


def test_ac1_replay_summary_matches_the_engine_metrics() -> None:
    strat = _board()["strategies"][0]
    strat["equity"] = EQUITY
    s = replay_summary(strat, initial_capital=10_000_000.0)
    assert s is not None
    values = [p["value"] for p in EQUITY]
    m = compute_metrics(values, 0.0)
    assert s["start"] == "2026-10-05" and s["end"] == "2026-10-08" and s["n_days"] == 4
    assert abs(s["period_return"] - 0.10) < 1e-12
    assert (
        s["cagr"] == m.cagr
        and s["sharpe"] == m.sharpe
        and s["mdd"] == m.mdd
        and s["calmar"] == m.calmar
    )
    assert abs(s["mdd"] - (10_200_000 / 10_500_000 - 1.0)) < 1e-12
    assert s["short"] is True  # 4 個交易日 < 一年


def test_ac1_period_return_uses_the_configured_initial_capital_not_the_first_point() -> None:
    strat = _board()["strategies"][0]
    strat["equity"] = [
        {"date": "2026-10-07", "value": 9_000_000.0},
        {"date": "2026-10-08", "value": 9_900_000.0},
    ]
    s = replay_summary(strat, initial_capital=10_000_000.0)
    assert s is not None
    assert abs(s["period_return"] - (-0.01)) < 1e-12  # 990 萬 ÷ 1,000 萬 − 1，不是 ÷ 900 萬


# ── AC2：逐筆數字照前端 D-165 的定義（已賣出平均每筆、勝率；持股平均未實現） ─────


def test_ac2_trade_stats_from_exits_and_open_holdings() -> None:
    board = _board()
    strat = board["strategies"][0]
    strat["equity"] = EQUITY
    strat["days"][-1]["exited"] = [
        {
            "stock_id": "2395",
            "reason": "rebalance",
            "entry_date": "2026-08-04",
            "held_sessions": 40,
            "change": 0.279,
        },
        {
            "stock_id": "2357",
            "reason": "rebalance",
            "entry_date": "2026-09-02",
            "held_sessions": 20,
            "change": -0.05,
        },
    ]
    s = replay_summary(strat, initial_capital=10_000_000.0)
    assert s is not None
    assert s["closed"] == 3  # 10/7 原本就有一筆 +0.279，加上面兩筆
    assert abs(s["avg_closed"] - (0.279 + 0.279 - 0.05) / 3) < 1e-12
    assert abs(s["win_rate"] - 2 / 3) < 1e-12
    assert (
        s["open"] == 2 and abs(s["avg_open"] - (0.6851 - 0.1264) / 2) < 1e-12 and s["open_up"] == 1
    )


def test_ac2_no_exits_yet_gives_none_not_zero() -> None:
    strat = _board()["strategies"][1]  # QUIET 三天都沒有進出
    strat["equity"] = EQUITY
    s = replay_summary(strat, initial_capital=10_000_000.0)
    assert s is not None
    assert s["closed"] == 0 and s["avg_closed"] is None and s["win_rate"] is None


# ── AC3：研究回測參考只讀結果包的 metrics，期間報酬用期初／期末資金 ───────────────


def test_ac3_reference_summary_reads_the_pack_metrics() -> None:
    r = reference_summary(_pack())
    assert r["start"] == "2019-05-02" and r["end"] == "2026-02-28"
    assert abs(r["period_return"] - (401_789_608.99 / 80_000_000.0 - 1.0)) < 1e-9
    assert (
        r["cagr"] == 0.2778
        and r["mdd"] == -0.4139
        and r["calmar"] == 0.6712
        and r["sharpe"] == 1.0119
    )
    assert r["initial_capital"] == 80_000_000.0 and r["n_days"] == 1660
    assert r["short"] is False


def test_ac3_reference_shorter_than_a_year_is_flagged() -> None:
    assert reference_summary(_pack(n_days=126))["short"] is True


def test_ac3_pack_without_metrics_raises() -> None:
    import pytest

    pack = _pack()
    del pack["metrics"]
    with pytest.raises(ValueError, match="metrics"):
        reference_summary(pack)


# ── AC4：頁面上的整體表現區塊：年化、回撤、逐筆、參考列與來源 ─────────────────────


def test_ac4_page_shows_replay_metrics_and_reference_rows_with_sources() -> None:
    board = _board()
    board["strategies"][0]["equity"] = EQUITY
    refs = {
        "CORE": [
            {
                "label": "開發期回測（8,000 萬、整張）",
                "source": "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json",
                "summary": reference_summary(_pack()),
            },
            {
                "label": "金庫期",
                "source": "tracks_e_vault_nodiv/E0_lw_e0_ctrl_1x.json",
                "summary": reference_summary(_pack(n_days=126, cagr=0.8419, mdd=-0.345)),
            },
        ]
    }
    html = render_board_html(board, window_sessions=2, references=refs)
    assert "整體表現" in html
    # 重播以來：期間報酬 +10.0%、最大回撤 −2.9%、年化標「換算」
    assert 'data-metric="CORE:replay:period_return">+10.0%' in html
    assert 'data-metric="CORE:replay:mdd">-2.9%' in html
    assert 'data-metric="CORE:replay:cagr"' in html and "不足一年" in html
    # 參考列：標籤、期間、年化 +27.8%、回撤 −41.4%、來源檔名
    assert "開發期回測（8,000 萬、整張）" in html and "2019-05-02" in html
    assert 'data-metric="CORE:ref0:cagr">+27.8%' in html
    assert 'data-metric="CORE:ref0:mdd">-41.4%' in html
    assert "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json" in html
    assert 'data-metric="CORE:ref1:cagr">+84.2%' in html  # 金庫期半年，年化是換算
    # 另一條線沒有登記參考 → 明說
    assert "沒有登記研究回測" in html


def test_ac4_strategy_without_equity_says_so_instead_of_zeros() -> None:
    board = _board()  # 兩條線 equity 都是空的
    html = render_board_html(board, window_sessions=2)
    assert "尚無淨值" in html
    assert 'data-metric="CORE:replay:cagr">+0.0%' not in html


# ── AC5：配置登記的是結果包路徑與標籤，不是數字 ───────────────────────────────────


def test_ac5_config_registers_reference_packs_by_path() -> None:
    from twquant.config import load_config
    from twquant.reporting.pick_board import PickBoardConfig

    cfg = load_config(Path("configs/pick_board.yaml"), PickBoardConfig)
    for s in cfg.strategies:
        assert s.references, f"{s.code} 沒有登記研究回測參考"
        for r in s.references:
            assert r.label and r.path.endswith(".json") and not Path(r.path).is_absolute()


# ── AC6：CLI 讀結果包；包不在就大聲失敗、不給半套頁面 ───────────────────────────


def test_ac6_cli_loads_packs_or_names_the_missing_file(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from twquant.cli import app

    runner = CliRunner()
    save_board(tmp_path, _board())
    missing = runner.invoke(app, ["pick-board-html", "--out", str(tmp_path), "--no-prices"])
    assert missing.exit_code != 0
    assert "E0_lw_e0_ctrl_1x.json" in str(missing.output) + str(missing.exception)
    assert not (tmp_path / "reports" / HTML_FILE).exists()

    # 把登記的四個包放進去（只需要頁面會讀的欄位）→ 成功，頁面含參考列
    from twquant.config import load_config
    from twquant.reporting.pick_board import PickBoardConfig

    cfg = load_config(Path("configs/pick_board.yaml"), PickBoardConfig)
    for s in cfg.strategies:
        for r in s.references:
            p = tmp_path / r.path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_pack()), encoding="utf-8")
    ok = runner.invoke(app, ["pick-board-html", "--out", str(tmp_path), "--no-prices"])
    assert ok.exit_code == 0, ok.output
    page = (tmp_path / "reports" / HTML_FILE).read_text(encoding="utf-8")
    assert "整體表現" in page and 'data-metric="CORE:ref0:cagr">+27.8%' in page

    offline = runner.invoke(
        app, ["pick-board-html", "--out", str(tmp_path), "--no-prices", "--no-references"]
    )
    assert offline.exit_code == 0, offline.output
    assert "沒有登記研究回測" in (tmp_path / "reports" / HTML_FILE).read_text(encoding="utf-8")
