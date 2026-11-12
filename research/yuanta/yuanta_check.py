#!/usr/bin/env python3
"""
yuanta_check.py — 元大期貨交易 API 連線測試：登入、列帳號、查權益數與未平倉。**不送任何委託。**

預設連測試環境 apitest.yuantafutures.com.tw:80；要連正式環境得自己加 --host/--port。

登入資訊只從 .env 讀（不印出任何值）：
  YUANTA_ID        歸戶身分證字號
  YUANTA_PASSWORD  電子交易密碼

前置：
  1. 元大已核准 API 使用（線上簽「API風險預告暨申請使用聲明書」）。
  2. 64 位元元件已用系統管理員執行 C:\\Yuanta\\API_x64\\install_YTFutOrdAP64.bat 註冊。
  3. 64 位元 Python + comtypes（本資料夾的 .venv）。

用法：
  只檢查元件裝好沒（不登入、不需要 .env）：
    python yuanta_check.py --component-only
  登入測試環境：
    python yuanta_check.py --env D:/project/twquant/.env
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import struct
import sys
import time
from ctypes import POINTER, byref, wintypes
from pathlib import Path

TEST_HOST = "apitest.yuantafutures.com.tw"
TEST_PORT = "80"
PROGID = {64: "Yuanta.YuantaOrdCtrl.64", 32: "Yuanta.YuantaOrdCtrl.1"}

# 說明文件「連線狀況 TLinkStatus」
LINK_STATUS = {
    -102: "無 API 權限（還沒核准開通）",
    -101: "登入結果訊息有誤",
    -100: "無法執行登入作業",
    -10: "帳號錯誤：此帳號沒有設定 MKTT",
    -9: "作業代號錯誤",
    -8: "參數錯誤：Cflg",
    -7: "參數錯誤：Kind",
    -6: "參數錯誤：Stus",
    -5: "參數錯誤：Func",
    -4: "帳號錯誤",
    -3: "參數錯誤",
    -2: "網路連線失敗",
    -1: "網路連線中斷",
    0: "未進行連線",
    1: "線路已連上",
    2: "登入成功",
    3: "線路連線中",
    4: "無效憑證",
    5: "密碼錯誤",
    100: "連線訊息傳送中",
    201: "線路已連線，正在載入設定",
    202: "線路已連線，登入訊息已送出",
}
LOGON_OK = 2
LOGON_FAILED = {4, 5}  # 加上所有負值


def read_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def mask(text: str, keep: int = 3) -> str:
    """帳號只露最後幾碼。"""
    return "*" * max(len(text) - keep, 0) + text[-keep:]


class Events:
    """元件回呼。只收登入結果與通用查詢結果。"""

    def __init__(self):
        self.logon = []  # [(status, acclist)]
        self.queries = {}  # workid -> (rowcount, results)

    def OnLogonS(self, this, TLinkStatus, AccList, Casq, Cast):
        self.logon.append((int(TLinkStatus), AccList or ""))
        cert = "有" if Casq else "無"
        print(
            f"  登入回報：{TLinkStatus} {LINK_STATUS.get(int(TLinkStatus), '（文件沒列）')}"
            f"；憑證序號：{cert}；憑證狀態：{Cast}",
            flush=True,
        )

    def OnUserDefinsFuncResult(self, this, RowCount, Results, WorkID):
        self.queries[str(WorkID)] = (RowCount, Results or "")


def create_control():
    """在一個看不見的視窗裡建立元大下單元件，回傳 (元件, 事件連線, 事件物件)。"""
    bits = struct.calcsize("P") * 8
    progid = PROGID[bits]
    try:
        import comtypes
        from comtypes import GUID, IUnknown
        from comtypes.client import GetBestInterface, GetEvents
    except ImportError:
        raise SystemExit("缺少 comtypes：請用本資料夾 .venv 的 python 執行") from None

    comtypes.CoInitialize()
    try:
        clsid = GUID.from_progid(progid)
    except OSError:
        raise SystemExit(
            f"找不到元件 {progid}（這個 Python 是 {bits} 位元）。\n"
            f"請用系統管理員身分執行 C:\\Yuanta\\API_x64\\install_YTFutOrdAP64.bat 註冊元件。"
        ) from None
    print(f"元件已註冊：{progid} {clsid}", flush=True)

    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "yuanta_check", 0, 0, 0, 0, 0, None, None, None, None
    )
    if not hwnd:
        raise SystemExit("建立隱藏視窗失敗")

    atl = ctypes.windll.atl
    atl.AtlAxWinInit()
    atl.AtlAxCreateControlEx.restype = ctypes.HRESULT
    atl.AtlAxCreateControlEx.argtypes = [
        wintypes.LPCWSTR,
        wintypes.HWND,
        ctypes.c_void_p,
        POINTER(POINTER(IUnknown)),
        POINTER(POINTER(IUnknown)),
        POINTER(GUID),
        POINTER(IUnknown),
    ]
    container = POINTER(IUnknown)()
    control = POINTER(IUnknown)()
    try:
        atl.AtlAxCreateControlEx(
            progid, hwnd, None, byref(container), byref(control), byref(GUID()), POINTER(IUnknown)()
        )
    except OSError as exc:
        raise SystemExit(f"元件建立失敗：{exc}") from exc
    if not control:
        raise SystemExit("元件建立失敗：沒有拿到元件")

    ord_ctrl = GetBestInterface(control)
    events = Events()
    connection = GetEvents(ord_ctrl, events)
    return ord_ctrl, connection, events, container


def pump_until(done, timeout: float) -> bool:
    """處理視窗訊息直到 done() 為真或逾時。"""
    from comtypes.client import PumpEvents

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        # PumpEvents 逾時會丟 RPC_S_CALLPENDING（OSError），屬正常
        with contextlib.suppress(OSError):
            PumpEvents(0.2)
    return done()


def logon_finished(events: Events) -> bool:
    return any(s == LOGON_OK or s < 0 or s in LOGON_FAILED for s, _ in events.logon)


def query(ord_ctrl, events: Events, params: str, workid: str, label: str, timeout: float):
    ret = ord_ctrl.UserDefinsFunc(params, workid)
    if not pump_until(lambda: workid in events.queries, timeout):
        print(f"{label}：{timeout:.0f} 秒內沒有回應（呼叫回傳 {ret}）")
        return
    rows, results = events.queries[workid]
    print(f"{label}：{rows} 筆")
    print("  " + (results if results else "（空）"))


def main() -> int:
    p = argparse.ArgumentParser(description="元大期貨交易 API 連線測試（不送單）")
    p.add_argument("--env", help=".env 的絕對路徑（要有 YUANTA_ID、YUANTA_PASSWORD）")
    p.add_argument("--host", default=TEST_HOST, help=f"預設測試環境 {TEST_HOST}")
    p.add_argument("--port", default=TEST_PORT)
    p.add_argument("--timeout", type=float, default=30.0, help="每一步最多等幾秒")
    p.add_argument("--component-only", action="store_true", help="只檢查元件能不能建立，不登入")
    args = p.parse_args()

    # _connection／_container 要留著參考，放掉事件就收不到
    ord_ctrl, _connection, events, _container = create_control()
    print("元件建立成功", flush=True)
    if args.component_only:
        return 0

    if not args.env:
        raise SystemExit("要登入請給 --env")
    env = read_env(Path(args.env))
    missing = [k for k in ("YUANTA_ID", "YUANTA_PASSWORD") if not env.get(k)]
    if missing:
        raise SystemExit(f".env 缺少：{', '.join(missing)}")

    # 元件預設會把往來封包寫成檔案（可能含身分證字號），先關掉。
    ord_ctrl.SetLog(False)

    print(f"連線 {args.host}:{args.port} …", flush=True)
    ret = ord_ctrl.SetFutOrdConnection(
        env["YUANTA_ID"], env["YUANTA_PASSWORD"], args.host, args.port
    )
    print(f"  呼叫回傳：{ret} {LINK_STATUS.get(ret, '')}", flush=True)
    if not pump_until(lambda: logon_finished(events), args.timeout):
        last = events.logon[-1][0] if events.logon else ret
        print(
            f"{args.timeout:.0f} 秒內沒有等到登入結果（最後狀態：{last} {LINK_STATUS.get(last, '')}）"
        )
        print(f"多半是連不到 {args.host}:{args.port}——先確認這個連接埠通不通。")
        return 1
    status, acclist = events.logon[-1]
    if status != LOGON_OK:
        print(f"登入失敗：{status} {LINK_STATUS.get(status, '')}")
        return 1

    # AccList = "市場別-分公司-帳號-子帳-姓名;…"，市場別 2 = 期貨
    accounts = [a.split("-") for a in acclist.split(";") if a.strip()]
    futures = [a for a in accounts if len(a) >= 4 and a[0].strip() == "2"]
    print(f"登入成功，帳號數：{len(accounts)}（期貨 {len(futures)}）")
    for a in futures:
        print(
            f"  期貨帳號：分公司 {a[1].strip()}、帳號 {mask(a[2].strip())}、子帳「{a[3].strip()}」"
        )

    try:
        if futures:
            bhno, acno, suba = (futures[0][i].strip() for i in (1, 2, 3))
            base = f"bhno={bhno}|acno={acno}|suba={suba}"
            query(
                ord_ctrl,
                events,
                f"Func=FA003|{base}|type=1|currency=TWD",
                "FA003",
                "權益數（台幣基幣）",
                args.timeout,
            )
            query(
                ord_ctrl,
                events,
                f"Func=FA001|{base}|kind=A|FC=N",
                "FA001",
                "未平倉（合計）",
                args.timeout,
            )
        else:
            print("帳號清單裡沒有期貨帳號，不查帳務")
    finally:
        ord_ctrl.DoLogout()
        print("已登出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
