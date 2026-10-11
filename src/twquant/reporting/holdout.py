"""第二保留期的登記與凍結（D-109、D-148）。純函數：不讀 PG、不寫檔。

事前登記唯一的價值是**看到答案之前就把東西釘死**。這裡釘三件事：策略配置、成本配置、
模型檔——各自以內容指紋記錄在 `configs/holdout2.yaml`。之後任何一項被改動，
`tests/test_holdout2_registration.py` 會讓閘門變紅；開啟日的判讀腳本也會拒絕執行。

指紋取**驗證後配置的正規化 JSON**，不取原始檔案位元組：註解、欄位順序、換行符號
（本 repo 在 Windows 上有 LF/CRLF 轉換）都不該讓指紋變動，只有會改變行為的欄位才該。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from twquant.config import BaseConfig

# 登記（D-148，2026-09-23）**之後**才加進配置 schema、而且預設值不改變行為的選配欄位。
# 雜湊前若它們等於預設就拿掉——否則每加一個選配欄位，所有凍結指紋都會失效，而那不是
# 行為改變（D-149 新增 slots.conditional_vol／inverse_vol 時正是如此）。**設了非預設值就保留**：
# 那才是行為改變，閘門照樣會紅。只能往這裡加「登記後新增的欄位」；拿舊欄位來加等於改寫登記。
_INERT_EXTENSIONS: tuple[tuple[tuple[str, ...], object], ...] = (
    (("slots", "conditional_vol"), None),  # D-149 F1／F2
    (("slots", "inverse_vol"), None),  # D-149 F3
    (("exit_rules", "*", "mult"), None),  # D-159：bb_lower_stop／atr_trailing 的倍數
    (("slots", "exclude"), []),  # D-172：進場排除規則（空清單＝沒有規則）
    (("slots", "require"), []),  # D-174：進場必要條件（空清單＝沒有條件）
)


def _strip_inert(payload: dict[str, Any]) -> dict[str, Any]:
    for path, default in _INERT_EXTENSIONS:
        _strip_path(payload, path, default)
    return payload


def _strip_path(node: Any, path: tuple[str, ...], default: object) -> None:
    """沿路徑刪掉等於預設的鍵；`*` 表示清單的每一個元素（例如每一條出場規則）。"""
    key, rest = path[0], path[1:]
    if key == "*":
        for item in node if isinstance(node, list) else ():
            _strip_path(item, rest, default)
    elif not rest:
        if isinstance(node, dict) and key in node and node[key] == default:
            del node[key]
    elif isinstance(node, dict):
        _strip_path(node.get(key), rest, default)


def config_fingerprint(model: BaseModel) -> str:
    """驗證後配置的內容指紋（sha256，排序鍵的 JSON）。只反映行為，不反映格式。"""
    payload = _strip_inert(model.model_dump(mode="json"))
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(data: bytes) -> str:
    """模型檔這類二進位產物的指紋——它們沒有「格式 vs 行為」之分，整檔比對。"""
    return hashlib.sha256(data).hexdigest()


class HoldoutEntry(BaseConfig):
    """一支登記進第二保留期的策略及其凍結指紋。"""

    code: str = Field(min_length=1)
    strategy: str = Field(min_length=1)  # configs/strategies/<strategy>.yaml
    model_id: str = Field(min_length=1)
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    costs_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    capital_twd: float = Field(gt=0.0)


class HoldoutRegistration(BaseConfig):
    """configs/holdout2.yaml 的模型。"""

    registration: str = Field(min_length=1)
    holdout_start: dt.date
    opens_on: dt.date
    min_sessions: int = Field(ge=1)
    entries: tuple[HoldoutEntry, ...]

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> HoldoutRegistration:
        if self.opens_on <= self.holdout_start:
            raise ValueError(f"開啟日 {self.opens_on} 必須晚於保留期起點 {self.holdout_start}")
        codes = [e.code for e in self.entries]
        if len(set(codes)) != len(codes):
            raise ValueError(f"holdout2.yaml 的 code 重複：{codes}")
        return self


def refuse_before_opening(reg: HoldoutRegistration, today: dt.date, sessions: int) -> None:
    """開啟條件（D-109 §二）不滿足就 raise——這是整份登記唯一不能繞過的一道門。"""
    if today < reg.opens_on:
        raise PermissionError(
            f"第二保留期最早 {reg.opens_on} 才能開（{reg.registration}），今天是 {today}。"
            "在那之前對保留期做任何績效統計都等於偷看答案（D-109 §一）。"
        )
    if sessions < reg.min_sessions:
        raise PermissionError(
            f"保留期自 {reg.holdout_start} 起只累積 {sessions} 個交易日，"
            f"未達 D-109 §二 的 {reg.min_sessions} 日。"
        )
