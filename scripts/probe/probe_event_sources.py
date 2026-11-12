"""一次性盤點：事件窗「知情者痕跡」候選資料源能否回補到 2020（不落地、只印摘要）。"""

from __future__ import annotations

import os
import sys
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (twquant data probe)"}
FM = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")


def show(tag: str, ok: bool, detail: str) -> None:
    print(f"[{'OK ' if ok else 'NO '}] {tag}: {detail}", flush=True)


def get(url: str, **kw: object) -> requests.Response | None:
    try:
        r = requests.get(url, headers=UA, timeout=30, **kw)  # type: ignore[arg-type]
        time.sleep(1.5)
        return r
    except requests.RequestException as e:
        print(f"      ! {url} -> {type(e).__name__}: {e}")
        return None


def post(url: str, data: dict[str, str]) -> requests.Response | None:
    try:
        r = requests.post(url, headers=UA, data=data, timeout=30)
        time.sleep(1.5)
        return r
    except requests.RequestException as e:
        print(f"      ! {url} -> {type(e).__name__}: {e}")
        return None


def finmind(dataset: str, start: str, end: str, stock: str | None = "2330") -> None:
    p = {"dataset": dataset, "start_date": start, "end_date": end, "token": TOKEN}
    if stock:
        p["data_id"] = stock
    r = get(FM, params=p)
    if r is None:
        return
    try:
        j = r.json()
    except ValueError:
        show(f"FinMind {dataset} {start}", False, f"HTTP {r.status_code} non-json")
        return
    data = j.get("data") or []
    cols = list(data[0].keys()) if data else []
    show(
        f"FinMind {dataset} {start}",
        bool(data),
        f"status={j.get('status')} msg={j.get('msg')!s:.60} rows={len(data)} cols={cols[:10]}",
    )


def html_probe(tag: str, r: requests.Response | None, needle: str, enc: str = "utf-8") -> None:
    if r is None:
        show(tag, False, "no response")
        return
    text = r.content.decode(enc, errors="replace")
    show(
        tag,
        needle in text and r.status_code == 200,
        f"HTTP {r.status_code} len={len(text)} has[{needle}]={needle in text}",
    )


def main() -> None:
    which = set(sys.argv[1:]) or {"fm", "twse", "mops", "tdcc"}
    if "fm" in which:
        print("== FinMind ==")
        for ds in [
            "TaiwanStockHoldingSharesPer",
            "TaiwanStockSecuritiesLending",
            "TaiwanStockNews",
            "TaiwanStockMarketValue",
            "TaiwanStockWarrantTradingDaily",
        ]:
            finmind(ds, "2020-03-02", "2020-03-13")
        finmind("TaiwanStockHoldingSharesPer", "2026-09-01", "2026-09-19")
    if "twse" in which:
        print("== TWSE 權證日行情（MI_INDEX type=0999）==")
        for d in ["20200305", "20260924"]:
            r = get(
                "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                params={"date": d, "type": "0999", "response": "json"},
            )
            if r is not None:
                j = r.json()
                tabs = j.get("tables") or []
                n = sum(len(t.get("data") or []) for t in tabs)
                f = next((t.get("fields") for t in tabs if t.get("data")), None)
                show(f"TWSE warrant quotes {d}", n > 0, f"stat={j.get('stat')} rows={n} fields={f}")
        print("== TPEx 權證日行情 ==")
        r = get(
            "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php",
            params={"l": "zh-tw", "d": "109/03/05", "se": "WW", "o": "json"},
        )
        if r is not None:
            try:
                j = r.json()
                rows = j.get("aaData") or (j.get("tables") or [{}])[0].get("data") or []
                show(
                    "TPEx warrant quotes 2020-03-05",
                    bool(rows),
                    f"rows={len(rows)} sample={rows[:1]}",
                )
            except ValueError:
                show("TPEx warrant quotes 2020-03-05", False, f"HTTP {r.status_code} non-json")
    if "mops" in which:
        base = "https://mopsov.twse.com.tw/mops/web"
        print("== MOPS（舊站 mopsov）==")
        html_probe(
            "MOPS 重大訊息 t05st02 2020-03-05",
            post(
                f"{base}/ajax_t05st02",
                {
                    "encodeURIComponent": "1",
                    "step": "0",
                    "firstin": "true",
                    "off": "1",
                    "TYPEK": "all",
                    "year": "109",
                    "month": "03",
                    "day": "05",
                },
            ),
            "發言",
        )
        html_probe(
            "MOPS 內部人轉讓事前申報 t56sb12 2020-03",
            post(
                f"{base}/ajax_t56sb12",
                {
                    "encodeURIComponent": "1",
                    "step": "1",
                    "firstin": "1",
                    "off": "1",
                    "TYPEK": "sii",
                    "year": "109",
                    "month": "03",
                },
            ),
            "申報",
        )
        html_probe(
            "MOPS 法說會 t100sb02_1 2020-03",
            post(
                f"{base}/ajax_t100sb02_1",
                {
                    "encodeURIComponent": "1",
                    "step": "1",
                    "firstin": "1",
                    "off": "1",
                    "TYPEK": "sii",
                    "year": "109",
                    "month": "03",
                },
            ),
            "召開",
        )
        html_probe(
            "MOPS 月營收公告時間 t05st10_ifrs 2020-03 2330",
            post(
                f"{base}/ajax_t05st10_ifrs",
                {
                    "encodeURIComponent": "1",
                    "step": "0",
                    "firstin": "1",
                    "off": "1",
                    "queryName": "co_id",
                    "inpuType": "co_id",
                    "TYPEK": "all",
                    "co_id": "2330",
                    "year": "109",
                    "month": "03",
                },
            ),
            "營業收入",
        )
    if "tdcc" in which:
        print("== TDCC 互動查詢（四碼股歷史週）==")
        r = get("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock")
        if r is not None:
            import re

            dates = re.findall(r'<option value="(\d{8})"', r.text)
            show(
                "TDCC qryStock 可查週數",
                bool(dates),
                f"weeks={len(dates)} range={dates[-1] if dates else None}..{dates[0] if dates else None}",
            )


if __name__ == "__main__":
    main()
