#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
taifex_archive.py — 從期交所近 30 日逐筆的原始 zip 取出大台（TX）、微台（TMF）的列，各存一份（只存本機）。

forward_paper.sync_ticks 只留小台（MTX）的列、規則凍結不改；原始 zip 會留在 <work>/zips，這支從那裡另外取。
期交所只保留最近 30 個交易日，所以要每天跑（fubon_live.py 開盤前同步完會呼叫它）。已經取過的日子不重做。
格式與期交所原檔相同（表頭＋原始列，一般盤與夜盤都留），futures_replay.read_taifex(資料夾, "TX") 直接讀得到。

用法：python taifex_archive.py --work D:/project/twquant/data/research_ms/taifex_30d --out D:/project/twquant/data/research_ms/taifex_ticks
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

PRODUCTS = ("TX", "TMF")


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SystemExit("逐筆檔無法解碼")


def extract(zip_path: Path, out: Path, products=PRODUCTS, force: bool = False) -> dict:
    """一個 zip → 每個商品一份 Daily_YYYY_MM_DD.csv；回傳 {商品: 列數}（已經有的不重做，回 None）。"""
    name = zip_path.with_suffix(".csv").name
    todo = [p for p in products if force or not (out / p / name).exists()]
    if not todo:
        return {p: None for p in products}
    with zipfile.ZipFile(zip_path) as archive:
        members = [m for m in archive.namelist() if Path(m).name.lower().endswith(".csv") and Path(m).name.startswith("Daily_")]
        if not members:
            raise SystemExit(f"{zip_path.name} 裡沒有 Daily_*.csv")
        lines = decode(archive.read(members[0])).splitlines()
    head, rows = lines[0], lines[1:]
    counts = {}
    for product in todo:
        keep = [line for line in rows if line.split(",", 2)[1].strip() == product] if rows else []
        (out / product).mkdir(parents=True, exist_ok=True)
        tmp = out / product / (name + ".part")
        tmp.write_text("\n".join([head, *keep]) + "\n", encoding="utf-8")
        tmp.replace(out / product / name)
        counts[product] = len(keep)
    return counts


def archive_all(work: Path, out: Path, log=print, products=PRODUCTS) -> None:
    zips = sorted((work / "zips").glob("Daily_*.zip"))
    if not zips:
        raise SystemExit(f"{work / 'zips'} 底下沒有期交所 zip（先跑 forward_paper.sync_ticks）")
    # 最近兩個 zip 期交所可能還在補一般盤（同 forward_paper），每次重取
    recent = {z.name for z in zips[-2:]}
    for z in zips:
        counts = extract(z, out, products, force=z.name in recent)
        done = {p: n for p, n in counts.items() if n is not None}
        if done:
            log(f"{z.name}：" + "、".join(f"{p} {n:,} 筆" for p, n in done.items()))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--work", required=True, help="forward_paper.sync_ticks 的工作資料夾（底下有 zips/）")
    p.add_argument("--out", required=True, help="輸出資料夾（每個商品一個子資料夾）")
    args = p.parse_args()
    archive_all(Path(args.work), Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
