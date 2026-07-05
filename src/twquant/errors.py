"""例外樹（SPEC-000 §錯誤處理）。

所有例外訊息必須含上下文：哪張表/模組、哪個日期、哪檔股票（若適用）、期望 vs 實際。
根類別 TwquantError 供上層一次攔截。
"""

from __future__ import annotations

import datetime as dt


class TwquantError(Exception):
    """所有 twquant 例外的根。"""


class DataError(TwquantError):
    """資料層錯誤：抓取、清洗、schema 不符等。"""

    def __init__(self, table: str, date: dt.date | None, detail: str) -> None:
        self.table = table
        self.date = date
        self.detail = detail
        when = date.isoformat() if date is not None else "-"
        super().__init__(f"[{table} @ {when}] {detail}")


class QualityGateError(TwquantError):
    """品質閘門未過（列數、交叉核對、schema 驗證失敗等）。"""

    def __init__(self, check: str, detail: str) -> None:
        self.check = check
        self.detail = detail
        super().__init__(f"品質閘門「{check}」未過：{detail}")


class ConfigError(TwquantError):
    """配置載入或驗證錯誤。"""


class ExecutionError(TwquantError):
    """執行層錯誤：下單、對帳、券商 API。"""
