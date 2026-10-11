"""T-334 唯讀盤點：SPEC-016 §2.3 資金流來源能不能回補歷史（不落地、不寫 PG，只印摘要）。

    FINMIND_TOKEN=... uv run --all-groups python scripts/probe/probe_fund_flow_sources.py

每個來源印一行 `[OK |NO ] 標籤: 說明`。結論寫進 docs/reports/fund-flow-data-inventory-<日期>.md。
本腳本受 tests/test_probe_readonly.py 守著：不得 import psycopg／serving、不得 open()／write_*。
"""

from __future__ import annotations

import io
import os
import sys
import time
from typing import Any

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) twquant-probe"}
PAUSE_S = 1.2
MIS_ALL_ETF = "https://mis.twse.com.tw/stock/data/all_etf.txt"
TWSE_ETF_LIST = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
FINMIND = "https://api.finmindtrade.com/api/v4/data"
YUANTA_BRIDGE = "https://etfapi.yuantaetfs.com/ectranslation/api/bridge"
EZMONEY = "https://www.ezmoney.com.tw"
EZ_00981A = "49YTW"  # 統一投信的內部基金代碼（00981A）
TIP_NEWS = "https://taiwanindex.com.tw/news"


def show(tag: str, ok: bool, detail: str) -> None:
    print(f"[{'OK ' if ok else 'NO '}] {tag}: {detail}", flush=True)


def get_json(sess: requests.Session, url: str, **kw: Any) -> Any:
    r = sess.get(url, headers=UA, timeout=30, **kw)
    time.sleep(PAUSE_S)
    r.raise_for_status()
    return r.json()


def probe_mis(sess: requests.Session) -> None:
    d = get_json(sess, MIS_ALL_ETF)
    rows = [r for g in d["a1"] for r in g.get("msgArray", [])]
    dom = [r for r in rows if r["k"] == "1"]
    dates = sorted({r["i"] for r in rows})
    show(
        "TWSE MIS 申贖揭露",
        True,
        f"{len(rows)} 檔、國內成分 {len(dom)} 檔；資料日期 {dates}——**只有當日**，無歷史參數",
    )
    by = {r["a"]: r for r in rows}
    for sid in ("0050", "00981A"):
        r = by.get(sid)
        if r:
            show(f"  MIS {sid}", True, f"date={r['i']} units={r['c']} delta={r['d']} nav={r['f']}")


def probe_etf_list(sess: requests.Session) -> None:
    rows = get_json(sess, TWSE_ETF_LIST)
    kinds: dict[str, int] = {}
    for x in rows:
        kinds[x["基金類型"]] = kinds.get(x["基金類型"], 0) + 1
    show("TWSE OpenAPI t187ap47_L ETF 清單", bool(rows), f"{len(rows)} 檔；類型 {kinds}")


def probe_finmind_units(sess: requests.Session) -> None:
    token = os.environ.get("FINMIND_TOKEN", "")
    for sid, start, end in (
        ("0050", "2005-01-01", "2005-01-10"),
        ("0050", "2026-09-28", "2026-10-09"),
        ("00981A", "2026-09-28", "2026-10-09"),
    ):
        p = {
            "dataset": "TaiwanStockShareholding",
            "data_id": sid,
            "start_date": start,
            "end_date": end,
            "token": token,
        }
        rows = get_json(sess, FINMIND, params=p).get("data") or []
        tail = [(x["date"], x["NumberOfSharesIssued"]) for x in rows[-3:]]
        show(
            f"FinMind TaiwanStockShareholding {sid} {start}",
            bool(rows),
            f"{len(rows)} 列；發行單位數（股）{tail}",
        )


def probe_yuanta(sess: requests.Session) -> None:
    for d in ("", "20260901", "20200302", "20150105", "20100104"):
        p = {"APIType": "ETFAPI", "FuncId": "PCF/Daily", "ticker": "0050"}
        if d:
            p["date"] = d
        j = get_json(sess, YUANTA_BRIDGE, params=p)
        pcf = j.get("PCF") or {}
        sw = (j.get("FundWeights") or {}).get("StockWeights") or []
        wsum = round(sum(float(x["weights"]) for x in sw), 2)
        show(
            f"元大 PCF/Daily 0050 date={d or '今天'}",
            bool(sw),
            f"trandate={pcf.get('trandate')} osunit={pcf.get('osunit')} issuesdiff={pcf.get('issuesdiff')} "
            f"持股 {len(sw)} 檔、權重和 {wsum}%",
        )
    j = get_json(
        sess,
        YUANTA_BRIDGE,
        params={
            "APIType": "ETFBackstage",
            "CompanyID": "0",
            "FuncId": "ETFInformation/Rebalance/1066",
        },
    )
    data = j.get("Data") or []
    show(
        "元大 ETFInformation/Rebalance（0050）",
        bool(data),
        f"{len(data)} 次；只有日期與增刪檔數、沒有代號：{data[:3]}",
    )


def probe_ezmoney(sess: requests.Session) -> None:
    sess.get(f"{EZMONEY}/ETF/Transaction/PCF", headers=UA, timeout=30)  # 取 cookie
    time.sleep(PAUSE_S)
    for roc in ("115/10/08", "114/09/01", "114/06/02"):
        r = sess.post(
            f"{EZMONEY}/ETF/Transaction/GetPCF",
            headers=UA,
            timeout=30,
            json={"fundCode": EZ_00981A, "date": roc, "specificDate": True},
        )
        time.sleep(PAUSE_S)
        r.raise_for_status()
        j = r.json()
        pcf = {x["PCFCode"]: x["Amount"] for x in j.get("pcf") or []}
        st = [a for a in j.get("asset") or [] if a.get("AssetCode") == "ST"]
        det = (st[0].get("Details") or []) if st else []
        trd = det[0]["TranDate"] if det else None
        wsum = round(sum(float(x["NavRate"]) for x in det), 2)
        show(
            f"統一 GetPCF 00981A date={roc}",
            bool(det),
            f"持股基準日 {trd} OUT_UNIT={pcf.get('OUT_UNIT')} DIFF_UNIT={pcf.get('DIFF_UNIT')} "
            f"股票 {len(det)} 檔、權重和 {wsum}%（其餘為現金／期貨）",
        )


def probe_tip(sess: requests.Session) -> None:
    r = sess.get(TIP_NEWS, headers=UA, timeout=30)
    time.sleep(PAUSE_S)
    has = "定期審核" in r.text or "成分股調整" in r.text
    show(
        "臺灣指數公司 /news",
        has,
        f"HTTP {r.status_code}；伺服器端 HTML {'有' if has else '沒有'}公告文字（前端另以 API 載入，未解出）",
    )


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    with requests.Session() as sess:
        for fn in (
            probe_mis,
            probe_etf_list,
            probe_finmind_units,
            probe_yuanta,
            probe_ezmoney,
            probe_tip,
        ):
            try:
                fn(sess)
            except (requests.RequestException, KeyError, ValueError) as e:
                # 盤點腳本：單一來源失敗要印出來並繼續看下一個（不落地，沒有可汙染的資料）
                show(fn.__name__, False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
