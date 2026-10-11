"""產生 `configs/etf_meta.yaml`（SPEC-016 §2.2，T-335）：上市國內成分股票型 ETF（含主動式）。

    uv run --all-groups python scripts/build_etf_meta.py --out D:/project/twquant/.claude/worktrees/<wt>/configs/etf_meta.yaml

來源：TWSE OpenAPI `t187ap47_L`（上市 ETF 基本資料，只有現存的）。產出後人工核對再進版控。
限制（寫在 YAML 檔頭）：
- 只有上市；上櫃 ETF（如 006201）不在這份清單，stock_meta 也沒有它們。
- 只有現存；已下市的 ETF 不在清單 → 早年的機械流量會少算（存活者偏誤），報告要註明。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
UA = {"User-Agent": "Mozilla/5.0 twquant"}
# 只收這兩類：國內成分、持有現股（槓桿／反向持期貨、債券型不持股票，不產生個股機械流量）
FUND_TYPES = {
    "國內成分證券指數股票型基金": False,
    "國內成分證券主動式交易所交易基金(股票)": True,
}
# T-335 實測：這幾檔不在 TWSE MI_QFIIS（2025-06-16、2026-10-08 都沒有），FinMind 也只有零星幾天。
# 標成不追蹤並寫原因——否則每日完整性閘門天天失敗；規模都很小，對機械流量的影響可忽略。
UNTRACKED = {
    etf_id: "TWSE MI_QFIIS（外資持股統計）沒有此檔，FinMind 只有零星日子（T-335）"
    for etf_id in ("0054", "0058", "0059", "0060", "00742")
}
# 經理公司 → 簡稱；以「基金中文名稱」開頭比對，最長者優先。對不上即 raise（不猜）。
ISSUERS = {
    "元大": "元大",
    "國泰": "國泰",
    "群益": "群益",
    "統一": "統一",
    "凱基": "凱基",
    "復華": "復華",
    "中國信託": "中信",
    "大華銀": "大華",
    "野村": "野村",
    "台新": "台新",
    "富蘭克林華美": "富蘭克林",
    "安聯": "安聯",
    "兆豐": "兆豐",
    "永豐": "永豐",
    "第一金": "第一金",
    "玉山": "玉山",
    "聯博": "聯博",
    "摩根": "摩根",
    "聯邦": "聯邦",
    "華南永昌": "華南永昌",
    "富邦": "富邦",
    "新光": "新光",
    "街口": "街口",
    "中租": "中租",
    "柏瑞": "柏瑞",
    "保德信": "保德信",
    "宏利": "宏利",
    "日盛": "日盛",
    "瀚亞": "瀚亞",
    "貝萊德": "貝萊德",
    "主動": "",  # 不會命中：主動式的名稱以經理公司開頭；留著讓錯誤訊息清楚
}


def roc_date(s: str) -> dt.date:
    """民國 YYYMMDD（如 '0920630'）→ date。"""
    s = s.strip()
    if len(s) != 7 or not s.isdigit():
        raise ValueError(f"民國日期格式不對：{s!r}")
    return dt.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))


def issuer_of(name: str, etf_id: str) -> str:
    """由基金中文名稱開頭判斷經理公司。"""
    hits = [k for k in ISSUERS if name.startswith(k) and ISSUERS[k]]
    if not hits:
        raise ValueError(f"{etf_id} {name}：認不出經理公司，請補 ISSUERS")
    return ISSUERS[max(hits, key=len)]


def entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """t187ap47_L 列 → etf_meta 的 etfs 清單（依代號排序）。"""
    out = []
    for r in rows:
        typ = r["基金類型"]
        if typ not in FUND_TYPES:
            continue
        idx = r["標的指數/追蹤指數名稱"].strip()
        out.append(
            {
                "etf_id": r["基金代號"].strip(),
                "name": r["基金簡稱"].strip(),
                "issuer": issuer_of(r["基金中文名稱"].strip(), r["基金代號"]),
                "fund_type": typ,
                "is_active": FUND_TYPES[typ],
                "index_name": None if idx in ("", "不適用") else idx,
                "listed_date": roc_date(r["上市日期"]),
                "delisted_date": None,
                "units_tracked": r["基金代號"].strip() not in UNTRACKED,
                "note": UNTRACKED.get(r["基金代號"].strip()),
            }
        )
    return sorted(out, key=lambda e: e["etf_id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="configs/etf_meta.yaml 的絕對路徑")
    args = ap.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    r = requests.get(URL, headers=UA, timeout=60)
    r.raise_for_status()
    rows = r.json()
    etfs = entries(rows)
    as_of = roc_date(rows[0]["出表日期"])
    head = (
        "# SPEC-016 §2.2：資金流研究涵蓋的 ETF（T-335）。由 scripts/build_etf_meta.py 產生、人工核對後進版控。\n"
        f"# 來源 TWSE OpenAPI t187ap47_L，出表日 {as_of}；只收國內成分證券的指數股票型與主動式（股票）。\n"
        "# 限制：只有上市（上櫃 ETF 不在）、只有現存（已下市 ETF 不在 → 早年機械流量少算）。\n"
    )
    body = yaml.safe_dump({"etfs": etfs}, allow_unicode=True, sort_keys=False)
    Path(args.out).write_text(head + body, encoding="utf-8")
    print(f"寫入 {len(etfs)} 檔 → {args.out}（主動式 {sum(e['is_active'] for e in etfs)} 檔）")


if __name__ == "__main__":
    main()
