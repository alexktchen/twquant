"""全域慣例（SPEC-000）：時區、時間戳斷言、單位常數、股票代號正規化。

黃金規則：所有 timestamp 一律 tz-aware Asia/Taipei；股票代號一律字串保留前導零；
成交量單位為股、金額 TWD、報酬率為小數。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from twquant.errors import TwquantError

# 台北時區（無日光節約，固定 +08:00）
TZ = ZoneInfo("Asia/Taipei")

# 台北的固定 UTC 偏移
_TW_OFFSET = dt.timedelta(hours=8)

# 訊號生成 PIT 截點（D-008）：每交易日 20:00。回測 date 型 as_of 一律 coerce 到此時刻。
SIGNAL_CUTOFF_HOUR = 20

# ML 分數的名目可得時刻：每交易日 19:00（盤後推論），早於 20:00 截點，
# 故訊號日當天的 PitView 看得到當日分數。回測（`ml backfill-scores`）與金庫驗證用的就是這個語意。
ML_SCORE_AVAILABLE_HOUR = 19

# 單位常數（SPEC-000 §單位）
SHARES_PER_LOT = 1000  # 一張 = 1000 股

# 可重現性預設種子（SPEC-000 §可重現性）
DEFAULT_SEED = 42


def now_tw() -> dt.datetime:
    """現在時刻（tz-aware Asia/Taipei）。"""
    return dt.datetime.now(TZ)


def assert_tw_timestamp(ts: dt.datetime) -> dt.datetime:
    """驗證時間戳為 tz-aware 且偏移等於台北（+08:00）；naive 或錯誤時區即 raise。

    回傳原時間戳以利鏈式使用。
    """
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise TwquantError(f"時間戳必須 tz-aware（Asia/Taipei），收到 naive：{ts!r}")
    if ts.utcoffset() != _TW_OFFSET:
        raise TwquantError(f"時間戳偏移必須為 +08:00（Asia/Taipei），收到 {ts.utcoffset()}：{ts!r}")
    return ts


def as_of_cutoff(date: dt.date | dt.datetime) -> dt.datetime:
    """把 as_of 轉成 PIT 截點 datetime（D-008）。

    date 型 → 當日 20:00（訊號生成截點）；datetime 型 → 驗證後原樣回傳。
    """
    if isinstance(date, dt.datetime):
        return assert_tw_timestamp(date)
    return dt.datetime(date.year, date.month, date.day, SIGNAL_CUTOFF_HOUR, 0, tzinfo=TZ)


def ml_score_available_at(day: dt.date, now: dt.datetime) -> dt.datetime:
    """`ml_scores.available_at` 該寫什麼：`min(now, 當日名目可得時刻)`（T-228）。

    **這一欄是資訊可得時刻，不是程式執行時刻。** 分數是「凍結的模型 × 當日因子」的確定函數，
    而因子物化走 PitView、已經遵守當日 20:00 截點——所以分數在資訊上屬於截點之前，
    推論程式實際幾點跑完是營運細節。

    直接寫 `now` 的後果（2026-09-19 實際發生）：排程在 22:00 跑、或隔天補跑，分數的時間戳
    就晚於當日 20:00 截點，PitView 判定「那個時點這筆分數還不存在」，於是所有以 ML 分數
    當閘門的策略**每天**都拿到整欄 null。回測看得到、生產看不到——驗證過的語意與上線的語意不一致。

    取 `min` 而不是一律寫名目時刻：提早跑（19:00 前）時不得把時間戳往後推，那才是真的前視。
    """
    assert_tw_timestamp(now)
    nominal = dt.datetime(day.year, day.month, day.day, ML_SCORE_AVAILABLE_HOUR, 0, tzinfo=TZ)
    return min(now, nominal)


def normalize_stock_id(value: str | int) -> str:
    """股票代號正規化為字串。字串保留原樣（含前導零）；int 會 raise 以防丟失前導零。"""
    if isinstance(value, int):
        raise TwquantError(f"股票代號必須是字串以保留前導零，收到 int：{value}")
    return value.strip()
