#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fubon_check.py — 富邦 Neo API 連線測試：登入、開行情、抓一天小台 1 分 K，印出筆數與頭尾兩根。

登入資訊只從 .env 讀（不印出任何值）：
  FUBON_ID             身分證字號
  FUBON_API_KEY        API Key（有它就用 API Key 登入；SDK >= 2.2.7）
  FUBON_PASSWORD       沒有 API Key 時改用登入密碼
  FUBON_CERT_PATH      憑證 .pfx 的完整路徑
  FUBON_CERT_PASSWORD  憑證密碼（沒設就用 SDK 預設：身分證字號）

需要富邦官網下載的 fubon_neo（不在 PyPI）。
用法：python fubon_check.py --env D:/project/twquant/.env --day 2026-09-10 --month 202609
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def read_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def connect(env_path: Path):
    """登入並開行情，回傳 sdk。登入資訊只從 .env 讀。"""
    env = read_env(env_path)
    need = ["FUBON_ID", "FUBON_CERT_PATH"]
    missing = [k for k in need if not env.get(k)]
    if not env.get("FUBON_API_KEY") and not env.get("FUBON_PASSWORD"):
        missing.append("FUBON_API_KEY 或 FUBON_PASSWORD")
    if missing:
        raise SystemExit(f".env 缺少：{', '.join(missing)}")
    if not Path(env["FUBON_CERT_PATH"]).is_file():
        raise SystemExit("FUBON_CERT_PATH 指到的憑證檔不存在")

    from fubon_neo.sdk import FubonSDK

    sdk = FubonSDK()
    cert_args = [env["FUBON_CERT_PATH"]] + ([env["FUBON_CERT_PASSWORD"]] if env.get("FUBON_CERT_PASSWORD") else [])
    if env.get("FUBON_API_KEY"):
        result = sdk.apikey_login(env["FUBON_ID"], env["FUBON_API_KEY"], *cert_args)
        mode = "API Key"
    else:
        result = sdk.login(env["FUBON_ID"], env["FUBON_PASSWORD"], *cert_args)
        mode = "密碼"
    if not result.is_success:
        raise SystemExit(f"登入失敗（{mode}）：{result.message}")
    print(f"登入成功（{mode}），帳號數：{len(result.data)}", flush=True)
    sdk.init_realtime()
    return sdk


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True, help=".env 的絕對路徑")
    p.add_argument("--day", required=True, help="要抓的交易日 yyyy-MM-dd")
    p.add_argument("--month", required=True, help="合約月份 yyyyMM，例如 202609")
    p.add_argument("--product", default="MXF")
    args = p.parse_args()

    sdk = connect(Path(args.env))
    futopt = sdk.marketdata.rest_client.futopt
    payload = futopt.historical.candles(**{
        "product": args.product, "contractMonth": args.month, "timeframe": "1", "session": "regular",
        "from": args.day, "to": args.day, "sort": "asc",
        "fields": "open,high,low,close,volume,average,transaction"})
    bars = payload.get("data") or [] if isinstance(payload, dict) else []
    if not bars:
        raise SystemExit(f"沒有拿到資料，原始回應：{str(payload)[:300]}")
    print(f"{args.product} {args.month} {args.day} 一般盤 1 分 K：{len(bars)} 根（成交量單位：張）")
    print("第一根：", bars[0])
    print("最後一根：", bars[-1])
    sdk.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
