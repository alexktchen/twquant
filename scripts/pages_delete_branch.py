"""刪掉 Cloudflare Pages 某個預覽分支的全部部署，讓 `<分支>.<專案>.pages.dev` 與各次部署的網址都讀不到。

使用者 2026-10-15：「https://reports.twquant.pages.dev/reports/ 這個頁面拿掉不要讓人家讀了」。
那是以前用 `wrangler pages deploy --branch reports` 直接上傳的預覽部署（不是 git 分支），只能用 API 刪。
只刪預覽：分支是專案的正式分支（main）就拒絕；正式站的部署一律不碰。

只用標準函式庫（工作流程不必裝套件）。憑證讀環境變數 CLOUDFLARE_API_TOKEN、CLOUDFLARE_ACCOUNT_ID。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

API = "https://api.cloudflare.com/client/v4"
BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def check_branch(branch: str, production_branch: str) -> None:
    """分支名稱要合法、而且不能是正式分支。"""
    if not BRANCH_RE.match(branch):
        raise SystemExit(f"分支名稱不合法：{branch!r}")
    if branch == production_branch:
        raise SystemExit(f"{branch} 是正式分支，不刪（這支只刪預覽部署）")


def select_deployments(
    deployments: Sequence[Mapping[str, Any]], branch: str, production_branch: str
) -> list[str]:
    """這個分支的預覽部署 id。正式環境的、別的分支的、沒有分支資訊的都不選。"""
    check_branch(branch, production_branch)
    out: list[str] = []
    for d in deployments:
        meta = (d.get("deployment_trigger") or {}).get("metadata") or {}
        if d.get("environment") != "preview" or meta.get("branch") != branch:
            continue
        out.append(str(d["id"]))
    return out


def still_served(status: int, body: str, marker: str) -> bool:
    """網址還讀得到原本的內容嗎（200 而且內文有原本頁面的字）。"""
    return status == 200 and marker in body


def _call(method: str, path: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{API}{path}", method=method, headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload: dict[str, Any] = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} → HTTP {exc.code}：{exc.read().decode()[:500]}") from exc
    if not payload.get("success"):
        raise SystemExit(f"{method} {path} 失敗：{payload.get('errors')}")
    return payload


def _fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def main(argv: Sequence[str] | None = None) -> int:
    """列出分支的預覽部署、全部刪掉（force，含分支別名指著的那個），再確認網址讀不到。"""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--check-path", default="/", help="刪完要確認讀不到的路徑")
    ap.add_argument("--marker", default="<html", help="原本頁面裡會有的字（確認用）")
    ap.add_argument("--per-page", type=int, default=25)
    ap.add_argument("--wait-tries", type=int, default=6)
    ap.add_argument("--wait-seconds", type=float, default=10.0)
    args = ap.parse_args(argv)
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not token or not account:
        raise SystemExit("缺少 CLOUDFLARE_API_TOKEN 或 CLOUDFLARE_ACCOUNT_ID")
    base = f"/accounts/{account}/pages/projects/{args.project}"

    project = _call("GET", base, token)["result"]
    production = str(project["production_branch"])
    check_branch(args.branch, production)

    seen: list[dict[str, Any]] = []
    page = 1
    while True:
        got = _call(
            "GET", f"{base}/deployments?env=preview&page={page}&per_page={args.per_page}", token
        )
        rows = got.get("result") or []
        seen += rows
        total_pages = int((got.get("result_info") or {}).get("total_pages") or 1)
        if not rows or page >= total_pages:
            break
        page += 1
    ids = select_deployments(seen, args.branch, production)
    print(f"{args.project}：預覽部署 {len(seen)} 個，其中 {args.branch} 分支 {len(ids)} 個")
    for i in ids:
        _call("DELETE", f"{base}/deployments/{i}?force=true", token)
        print(f"  已刪除 {i}")

    url = f"https://{args.branch}.{project['subdomain']}{args.check_path}"
    for n in range(args.wait_tries):
        status, body = _fetch(url)
        if not still_served(status, body, args.marker):
            print(f"[OK] {url} 已讀不到（HTTP {status}）")
            return 0
        if n + 1 < args.wait_tries:
            time.sleep(args.wait_seconds)
    print(f"[FAIL] 刪完 {url} 仍讀得到（HTTP {status}）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
