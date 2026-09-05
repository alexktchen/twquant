"""T-132：分片檔名以時間戳命名 → 同刻度即靜默覆蓋（ingest_log 與 research_log）。

Windows 系統時鐘解析度約 1~16ms，實測連續兩次 `now_tw()` 有 ~100% 機率拿到
**完全相同**的時間戳。兩個模組都用 `part-<時間戳>.parquet` 當檔名，
第二筆會直接覆蓋第一筆而**毫無訊號**（黃金規則 5）。

`research_log` 這一邊尤其嚴重：它是 **DSR 的試驗登記簿**。
掉一筆登記 → 試驗數被低估 → DSR 被高估 → 晉升閘門被騙過。
整個反過度配適機制建立在這張表完整的前提上。

測試以**凍結時鐘**釘住不變量，而不是靠時序碰運氣——靠時序的測試只會偶爾紅，
正是這個缺陷長期沒被抓到的原因。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.conventions import now_tw


def test_t132_ingest_log_same_timestamp_records_all_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回補腳本每 25 檔記一次進度、失敗逐筆記；掉紀錄不會有任何訊號。"""
    import twquant.reporting.ingest_log as mod
    from twquant.reporting.ingest_log import read_ingest_log, record

    frozen = now_tw()
    monkeypatch.setattr(mod, "now_tw", lambda: frozen)
    for i in range(5):
        record(tmp_path, "twse", "prices_daily", dt.date(2026, 6, 30), "success", rows=i)
    log = read_ingest_log(tmp_path)
    assert log.height == 5, f"同一時間戳寫 5 筆只讀回 {log.height} 筆——檔名碰撞造成靜默覆蓋"
    assert sorted(log["rows"].to_list()) == [0, 1, 2, 3, 4]


def test_t132_research_log_same_timestamp_entries_all_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**這條最要緊**：research_log 是 DSR 的試驗計數來源。

    掉一筆登記，`trials_count` 就少算一個 distinct `params_hash`，
    DSR 隨之被高估——而 DSR 正是用來懲罰選擇歷史的那道閘門。
    """
    import twquant.reporting.research_log as mod
    from twquant.reporting.research_log import append_research_log, read_research_log

    frozen = now_tw()
    monkeypatch.setattr(mod, "now_tw", lambda: frozen)
    for i in range(5):
        append_research_log(
            tmp_path,
            [
                {
                    "entry_type": "backtest",
                    "strategy": f"s{i}",
                    "factor": "",
                    "params_hash": f"h{i}",
                    "metric": "sharpe",
                    "value": float(i),
                    "trial_scope": "bt:collision_probe",
                }
            ],
        )
    log = read_research_log(tmp_path)
    assert log.height == 5, f"同一時間戳寫 5 筆只讀回 {log.height} 筆——試驗登記靜默消失"


def test_t132_trials_count_is_not_understated_by_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把後果直接測出來：碰撞會讓 DSR 的試驗數少算，晉升閘門因此變鬆。"""
    import twquant.reporting.research_log as mod
    from twquant.reporting.research_log import append_research_log, trials_count

    frozen = now_tw()
    monkeypatch.setattr(mod, "now_tw", lambda: frozen)
    for i in range(4):
        append_research_log(
            tmp_path,
            [
                {
                    "entry_type": "backtest",
                    "strategy": "cand",
                    "factor": "",
                    "params_hash": f"hash{i}",
                    "metric": "sharpe",
                    "value": 1.0,
                    "trial_scope": "bt:probe",
                }
            ],
        )
    assert trials_count(tmp_path, "bt:probe") == 4, "試驗數被低估 → DSR 會被高估"
