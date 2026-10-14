"""刪 Cloudflare Pages 預覽分支（使用者 2026-10-15：reports.twquant.pages.dev「拿掉不要讓人家讀了」）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from scripts.pages_delete_branch import check_branch, select_deployments, still_served


def _d(i: str, env: str, branch: str | None) -> dict[str, Any]:
    meta = {} if branch is None else {"branch": branch}
    return {"id": i, "environment": env, "deployment_trigger": {"metadata": meta}}


def test_selects_only_preview_deployments_of_that_branch() -> None:
    rows: list[dict[str, Any]] = [
        _d("a", "preview", "reports"),
        _d("b", "production", "reports"),  # 正式環境的不碰（就算分支名一樣）
        _d("c", "preview", "main"),
        _d("d", "preview", "reports-old"),  # 前綴相同的別的分支不算
        _d("e", "preview", None),
        {"id": "f", "environment": "preview"},  # 沒有 deployment_trigger
        _d("g", "preview", "reports"),
    ]
    assert select_deployments(rows, "reports", "main") == ["a", "g"]


def test_refuses_production_branch_and_bad_names() -> None:
    with pytest.raises(SystemExit, match="正式分支"):
        check_branch("main", "main")
    with pytest.raises(SystemExit, match="正式分支"):
        select_deployments([_d("a", "preview", "main")], "main", "main")
    for bad in ["", "re ports", "../x", "a/b"]:
        with pytest.raises(SystemExit, match="不合法"):
            check_branch(bad, "main")
    check_branch("reports", "main")


def test_still_served_needs_200_and_original_content() -> None:
    assert still_served(200, "<title>研究報告目錄</title>", "研究報告目錄")
    assert not still_served(404, "<title>研究報告目錄</title>", "研究報告目錄")
    assert not still_served(200, "<h1>404</h1>", "研究報告目錄")


def test_workflow_targets_reports_and_never_main() -> None:
    text = Path(".github/workflows/pages-delete-branch.yml").read_text(encoding="utf-8")
    assert "scripts/pages_delete_branch.py" in text
    assert "--project twquant" in text
    assert "--branch main" not in text
