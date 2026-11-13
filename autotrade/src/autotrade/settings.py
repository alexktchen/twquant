"""後台的設定編輯（A-012）：讀設定檔、改值、驗證、存檔。只改值，不動註解與排版。

能從後台改的欄位列在 FIELDS；`mode`（模擬／真單）**不能**從後台改——真單要在檔案裡改、並過真單的啟動檢查（D-226）。
存檔前一定先用跟交易程式一模一樣的規則（`config.parse_config`）驗過；不合格就不寫，原檔一字不動。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

from autotrade.config import Config, ConfigError, parse_config

KINDS = ("int", "float", "bool", "time", "text")


@dataclass(frozen=True)
class Field:
    """一個可以從後台改的設定值。optional＝可以留空（留空＝不設，檔案裡那一行會被註解掉）。"""

    section: str
    key: str
    label: str
    kind: str
    help: str
    optional: bool = False


FIELDS: tuple[Field, ...] = (
    Field("risk", "max_position", "最多幾口", "int", "同時最多持有幾口"),
    Field("risk", "max_order_qty", "單筆最多幾口", "int", "一筆委託最多幾口（平倉也照這個分批）"),
    Field("risk", "max_entries_per_day", "一天最多進場幾次", "int", "系統的保險，策略自己另有限制"),
    Field("risk", "entry_start", "幾點開始可以進場", "time", "格式 HH:MM:SS"),
    Field("risk", "entry_end", "幾點之後不再進場", "time", "格式 HH:MM:SS"),
    Field("risk", "force_flat_time", "強制平倉時間", "time", "這個時間還有部位就由系統平倉"),
    Field("risk", "order_timeout_s", "委託幾秒沒成交就刪單", "float", "秒"),
    Field("risk", "max_quote_age_s", "行情幾秒沒更新就不進場", "float", "秒"),
    Field(
        "risk",
        "daily_loss_limit_twd",
        "每日虧損上限（元）",
        "float",
        "賠到這個數字就停手到明天；真單一定要設",
        True,
    ),
    Field(
        "risk",
        "position_stop_loss_twd",
        "單一部位停損（元）",
        "float",
        "未實現虧損到這個數字就由系統平倉；真單一定要設",
        True,
    ),
    Field(
        "risk",
        "max_orders_per_minute",
        "一分鐘最多送幾筆委託",
        "int",
        "到了就暫停送單、要人工解除；真單一定要設",
        True,
    ),
    Field(
        "risk",
        "max_consecutive_broker_rejects",
        "連續被券商拒絕幾筆就暫停",
        "int",
        "到了就暫停送單、要人工解除；真單一定要設",
        True,
    ),
    Field("contract", "commission_twd", "手續費（每口單邊，元）", "float", "照券商實際費率填"),
    Field("sim", "latency_s", "模擬：下單後幾秒成交", "float", "只影響模擬"),
    Field("sim", "slippage_points", "模擬：每邊滑價幾點", "float", "只影響模擬"),
    Field("control", "poll_s", "遠端停止：幾秒讀一次指令", "float", "秒"),
    Field("control", "flatten_timeout_s", "遠端停止：幾秒沒平掉就回報要人處理", "float", "秒"),
    Field(
        "control", "stale_s", "遠端停止：指令檔幾秒沒更新算斷線", "float", "至少是讀指令間隔的兩倍"
    ),
    Field("momentum", "contracts", "動能順勢：每次進場幾口", "int", "不能超過單筆最多幾口"),
    Field(
        "notify",
        "quiet_routine",
        "一般通知靜音",
        "bool",
        "成交、解除這類一般通知不響；「注意」「要處理」一定會響",
    ),
    Field("notify", "throttle_s", "同一件事幾秒內不重複通知", "float", "超過的合併成一則"),
    Field("export", "max_events", "網站「今日大事」最多幾則", "int", "留最新的"),
)
FIELD_BY_KEY = {(f.section, f.key): f for f in FIELDS}

_SECTION = re.compile(r"^\s*\[([A-Za-z0-9_]+)\]\s*(#.*)?$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


class SettingsError(ValueError):
    """設定改不了：值不合格、檔案不對、或欄位不能從後台改。"""


def to_literal(field: Field, raw: str | None) -> str | None:
    """後台送來的文字 → TOML 的值（None＝不設）。格式不對就 raise。"""
    text = "" if raw is None else str(raw).strip()
    if text == "":
        if field.optional:
            return None
        raise SettingsError(f"「{field.label}」不能留空")
    if field.kind == "int":
        if not re.fullmatch(r"-?\d+", text):
            raise SettingsError(f"「{field.label}」要是整數：{text!r}")
        return str(int(text))
    if field.kind == "float":
        try:
            value = float(text)
        except ValueError as exc:
            raise SettingsError(f"「{field.label}」要是數字：{text!r}") from exc
        if value != value or value in (float("inf"), float("-inf")):
            raise SettingsError(f"「{field.label}」要是一般的數字：{text!r}")
        return repr(value)
    if field.kind == "bool":
        if text not in ("true", "false"):
            raise SettingsError(f"「{field.label}」要是 true 或 false：{text!r}")
        return text
    if field.kind == "time":
        if not _TIME.fullmatch(text):
            raise SettingsError(f"「{field.label}」要是 HH:MM:SS：{text!r}")
        try:
            time.fromisoformat(text)
        except ValueError as exc:
            raise SettingsError(f"「{field.label}」不是合法時間：{text!r}") from exc
        return f'"{text}"'
    raise SettingsError(f"不認得的欄位種類 {field.kind!r}")


def _split_comment(rest: str) -> tuple[str, str]:
    """`值   # 註解` → （值, `   # 註解`）。值裡面的字串引號內不會有 #（我們寫的值都不含）。"""
    in_str = False
    for i, ch in enumerate(rest):
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            j = i
            while j > 0 and rest[j - 1] in " \t":
                j -= 1
            return rest[:j], rest[j:]
    return rest.rstrip(), ""


def set_value(text: str, section: str, key: str, literal: str | None) -> str:
    """改一個值，保留註解與排版。literal 是 None＝不設（把那一行註解掉）。區塊裡沒有這個鍵就加在區塊最後。"""
    lines = text.split("\n")
    current: str | None = None
    start = end = None
    for i, line in enumerate(lines):
        match = _SECTION.match(line)
        if match:
            if current == section:
                end = i
                break
            current = match[1]
            if current == section:
                start = i
    if start is None:
        raise SettingsError(f"設定檔裡沒有 [{section}] 這一段")
    if end is None:
        end = len(lines)
    key_re = re.compile(rf"^(\s*){re.escape(key)}(\s*=\s*)(.*)$")
    for i in range(start + 1, end):
        match = key_re.match(lines[i])
        if match:
            value, comment = _split_comment(match[3])
            if literal is None:
                lines[i] = f"# {key} = {value.strip()}{comment}（從後台改成不設）"
            else:
                lines[i] = f"{match[1]}{key}{match[2]}{literal}{comment}"
            return "\n".join(lines)
    if literal is None:
        return text  # 本來就沒設
    insert = end
    while insert - 1 > start and lines[insert - 1].strip() == "":
        insert -= 1
    lines.insert(insert, f"{key} = {literal}")
    return "\n".join(lines)


def read_values(text: str) -> dict[str, dict[str, Any]]:
    """設定檔的內容 → {區塊: {鍵: 值}}（給後台顯示）。"""
    raw = tomllib.loads(text)
    out: dict[str, dict[str, Any]] = {"": {k: v for k, v in raw.items() if not isinstance(v, dict)}}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = dict(v)
    return out


def shown_value(values: dict[str, dict[str, Any]], field: Field) -> str:
    """欄位在後台顯示的文字（沒設＝空字串）。"""
    value = values.get(field.section, {}).get(field.key)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, time):
        return value.isoformat()
    return str(value)


def validate(text: str) -> Config:
    """用跟交易程式一模一樣的規則驗設定。不合格就 raise SettingsError（附上原因）。"""
    try:
        return parse_config(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"設定檔格式壞了：{exc}") from exc
    except (ConfigError, KeyError, TypeError, ValueError) as exc:
        raise SettingsError(f"設定不合格：{exc}") from exc


def apply_changes(path: Path, changes: dict[str, str | None]) -> list[str]:
    """套用後台送來的修改並存檔。changes 的鍵是 "區塊.鍵"。回傳改了哪些（給人看）。

    驗證不過就 raise，原檔一字不動；過了就先留一份 `.bak`、再整份換掉（不會留下寫到一半的檔）。
    """
    if not path.is_file():
        raise SettingsError(f"找不到設定檔：{path.name}")
    original = path.read_text(encoding="utf-8")
    before = read_values(original)
    text = original
    done: list[str] = []
    for name, raw in changes.items():
        section, _, key = name.partition(".")
        field = FIELD_BY_KEY.get((section, key))
        if field is None:
            raise SettingsError(f"「{name}」不能從後台改")
        literal = to_literal(field, raw)
        old = shown_value(before, field)
        new = (
            ""
            if literal is None
            else (literal.strip('"') if field.kind == "time" else str(raw).strip())
        )
        if (
            field.kind == "float"
            and literal is not None
            and old != ""
            and float(old) == float(literal)
        ):
            continue
        if old == new:
            continue
        text = set_value(text, section, key, literal)
        done.append(f"{field.label}：{old or '不設'} → {new or '不設'}")
    if not done:
        return []
    validate(text)
    backup = path.with_name(path.name + ".bak")
    backup.write_text(original, encoding="utf-8", newline="\n")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return done
