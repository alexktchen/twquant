"""訊息匯流排（A-019／D-231）：拿資料庫的一張表當 MQ。一則訊息一列，只新增、不改、不刪。

主題有五種：指令（網站發）、回報（交易 agent 發）、訊號、成交、狀態（之後幾步才用）。
這一步上匯流排的指令只有兩種：啟動（待命 → 運作）、停止（平倉後回待命）。
緊急停止不在這裡：它還是走 A-011 那張表、那條路（D-231 第 3 點）。

這個檔案只有資料表與查詢的定義；連資料庫的是中繼程式（relay.py）。交易的那一半仍然不連資料庫。
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from autotrade.model import ACTION_ARM, ACTION_STANDBY, Command, CommandResult

TOPIC_COMMAND = "command"
TOPIC_REPORT = "report"
TOPICS = (TOPIC_COMMAND, TOPIC_REPORT, "signal", "fill", "status")
BUS_ACTIONS = (ACTION_ARM, ACTION_STANDBY)  # 這一步可以從匯流排下的指令
COMMAND_TTL_S = 30.0  # 指令沒寫幾秒作廢時用這個：沒有 agent 接手就不再交給任何人

# 跟兩個網站那一份（functions/_lib/autotrade_bus.ts 的 MESSAGES_DDL）一字不差，兩邊各有測試守著。
# 編號跟停止、遙控指令共用同一個序號：交易程式用編號認指令，三張表不會撞號。
MESSAGES_DDL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS web.autotrade_messages (id bigint PRIMARY KEY "
    "DEFAULT nextval('web.autotrade_commands_id_seq'), day date NOT NULL, topic text NOT NULL "
    "CHECK (topic IN ('command', 'report', 'signal', 'fill', 'status')), target text NOT NULL "
    "CHECK (target IN ('sim', 'live')), action text NOT NULL, body jsonb NOT NULL DEFAULT '{}'::jsonb, "
    "created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), ttl_s real, "
    "reply_to bigint, CHECK (topic <> 'command' OR action IN ('arm', 'standby')))",
    "ALTER TABLE web.autotrade_messages ENABLE ROW LEVEL SECURITY",
    "CREATE INDEX IF NOT EXISTS autotrade_messages_day ON web.autotrade_messages (day, topic, id)",
    "CREATE INDEX IF NOT EXISTS autotrade_messages_reply ON web.autotrade_messages (reply_to) "
    "WHERE reply_to IS NOT NULL",
)
# 門鈴（A-015）：只有新的「指令」才按鈴——回報、訊號是 agent 自己寫的，不必把自己叫醒。
# 用的是 relay.BELL_DDL 建的那個函式，所以要排在它後面。
BUS_BELL_DDL: tuple[str, ...] = (
    "CREATE OR REPLACE TRIGGER autotrade_messages_ring AFTER INSERT ON web.autotrade_messages "
    "FOR EACH ROW WHEN (NEW.topic = 'command') EXECUTE FUNCTION web.autotrade_ring()",
)
# 今天、給這支 agent、還沒作廢、而且還沒有任何回報的指令
BUS_PENDING_SQL = (
    "SELECT m.id, m.action, m.day, m.created_at, m.target FROM web.autotrade_messages m "
    "WHERE m.day = %s AND m.topic = 'command' AND m.target = %s "
    "AND m.created_at > now() - make_interval(secs => COALESCE(m.ttl_s, %s)::double precision) "
    "AND NOT EXISTS (SELECT 1 FROM web.autotrade_messages r WHERE r.reply_to = m.id "
    "AND r.topic = 'report') ORDER BY m.id LIMIT 50"
)
# 回報也是一則訊息，reply_to 指回那則指令。只回給「編號與按下時間都對得上」的那一則；
# 「收到」只留第一次，同一個結果不重複寫（程式重開會補報）
BUS_REPORT_SQL = (
    "INSERT INTO web.autotrade_messages (day, topic, target, action, body, created_by, reply_to) "
    "SELECT m.day, 'report', m.target, %s, jsonb_build_object('code', %s::text, 'note', %s::text, "
    "'command', m.action), %s, m.id FROM web.autotrade_messages m "
    "WHERE m.id = %s AND m.created_at = %s AND m.topic = 'command' AND NOT EXISTS ("
    "SELECT 1 FROM web.autotrade_messages r WHERE r.reply_to = m.id AND r.topic = 'report' "
    "AND r.action = %s AND (%s::text = 'ack' OR r.body ->> 'code' = %s::text))"
)


def report_params(result: CommandResult, consumer: str) -> tuple[object, ...]:
    """BUS_REPORT_SQL 的參數。"""
    return (
        result.stage,
        result.code,
        result.note,
        consumer,
        result.command_id,
        result.created_at,
        result.stage,
        result.stage,
        result.code,
    )


class BusDb(Protocol):
    """中繼程式用到的匯流排動作。出錯一律丟 ControlError。"""

    def pending_bus(self, today: date, consumer: str) -> list[Command]:
        """今天、給這支 agent、還沒作廢也還沒有回報的指令。"""
        ...

    def report_bus(self, result: CommandResult, consumer: str) -> None:
        """把一筆處理結果寫成一則回報。"""
        ...
