"""A-013：用富邦即時錄下來的逐筆（含買賣價）重播。使用者 2026-11-14：「不要證交所的」「我要11/13的」。"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from autotrade.cli import main, run_replay
from autotrade.feed.recorded import HEADER, read_recorded_day
from autotrade.feed.replay import FeedError

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
DAY = date(2026, 11, 13)
ROWS = [
    "2026-11-13T08:44:59.900000+08:00,43800,1,43799,43800",  # 開盤前，不算
    "2026-11-13T08:45:00.087000+08:00,43900,443,43848,43849",
    "2026-11-13T08:45:00.098000+08:00,43899,1,43899,43900",
    "2026-11-13T09:00:00.500000+08:00,43950,2,,",  # 那一筆沒有報價
]


def write(tmp_path: Path, rows: list[str], header: str = ",".join(HEADER)) -> Path:
    path = tmp_path / "TMF_2026-11-13.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def test_ac1_reads_ticks_with_quotes_inside_the_session(tmp_path: Path) -> None:
    ticks = read_recorded_day(write(tmp_path, ROWS), DAY, time(8, 45), time(13, 45))
    assert [(t.ts.strftime("%H:%M:%S.%f"), t.price, t.qty, t.bid, t.ask) for t in ticks] == [
        ("08:45:00.087000", 43900.0, 443, 43848.0, 43849.0),
        ("08:45:00.098000", 43899.0, 1, 43899.0, 43900.0),
        ("09:00:00.500000", 43950.0, 2, None, None),
    ]
    assert ticks[0].ts.utcoffset() is not None


@pytest.mark.parametrize(
    ("rows", "header", "why"),
    [
        (ROWS, "time,price,size,bid,ask", "欄位要是"),
        (["2026-11-13T09:00:00+08:00,43900,1,1"], None, "欄位數不對"),
        (["2026-11-13T09:00:00,43900,1,,"], None, "時區"),
        (
            ["2026-11-13T09:00:01+08:00,43900,1,,", "2026-11-13T09:00:00+08:00,43900,1,,"],
            None,
            "比前一列早",
        ),
        (["2026-11-13T09:00:00+08:00,abc,1,,"], None, "不是數字"),
        (["2026-11-13T09:00:00+08:00,43900,1.5,,"], None, "不是整數"),
        (["2026-11-13T09:00:00+08:00,43900,1,-1,"], None, "要大於 0"),
        (["2026-11-14T09:00:00+08:00,43900,1,,"], None, "裡沒有"),
    ],
)
def test_ac1_bad_files_raise_instead_of_skipping(
    tmp_path: Path, rows: list[str], header: str | None, why: str
) -> None:
    path = write(tmp_path, rows, header or ",".join(HEADER))
    with pytest.raises(FeedError, match=why):
        read_recorded_day(path, DAY, time(8, 45), time(13, 45))


def test_ac2_replay_command_takes_the_recorded_feed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, ROWS)
    summary = run_replay(CONFIG_PATH, path, DAY, "none", feed="recorded")
    assert summary["contract"] == "recorded" and summary["ticks"] == 3 and summary["fills"] == 0
    args = ["replay", "--config", str(CONFIG_PATH), "--ticks", str(path), "--day", "2026-11-13"]
    assert main([*args, "--strategy", "none", "--feed", "recorded"]) == 0
    assert "contract: recorded" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([*args, "--strategy", "none", "--feed", "guess"])
