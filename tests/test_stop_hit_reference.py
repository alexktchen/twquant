"""D-191 §三 選項 C（T-389）：凍結的「同波動股票 20 日內碰 −15%／+20%」查表。離線、靜態。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import polars as pl

_ROOT = Path(__file__).resolve().parent.parent
_JSON = _ROOT / "configs" / "stop_hit_reference.json"
_MIRROR = _ROOT / "frontend" / "functions" / "_lib" / "stop-hit-table.ts"
_TS_TEST = _ROOT / "frontend" / "tests" / "cloud-stophit.test.ts"
# 2026-10-18 跑完登記的定義後凍結（D-191 §三-3）。改了就是改定義，只能另立登記重算。
# 指紋算在「換行統一成 LF」的內容上（＝git 裡存的那份）：Windows checkout 會變 CRLF、CI 是 LF，內容一樣指紋要一樣。
# 原本 30b004a2…995cc 是 Windows 上 CRLF 檔案的指紋，CI 因此紅過一次（2026-10-18），內容一字未改。
_SHA256 = "63a88697e4358b2768beeaa43ac0b06e16adc73503aabb53fd1f4c601e75c54e"


def _frozen_bytes() -> bytes:
    return _JSON.read_bytes().replace(b"\r\n", b"\n")


def _table() -> dict[str, object]:
    data: dict[str, object] = json.loads(_JSON.read_text(encoding="utf-8"))
    return data


# ── AC-1：查表凍結（sha256）、結構照登記：十組、九條界線、定義欄位 ──────────────────
def test_ac1_table_is_frozen_and_shaped_as_registered() -> None:
    assert hashlib.sha256(_frozen_bytes()).hexdigest() == _SHA256
    t = _table()
    assert (t["dev_start"], t["dev_end"], t["horizon_days"], t["stop"], t["surge"]) == (
        "2017-09-26",
        "2026-09-08",
        20,
        -0.15,
        0.2,
    )
    bounds = t["bounds"]
    groups = t["groups"]
    assert isinstance(bounds, list) and isinstance(groups, list)
    assert len(bounds) == 9 and bounds == sorted(bounds)
    assert [g["group"] for g in groups] == list(range(10))
    assert groups[0]["vol_lo"] is None and groups[9]["vol_hi"] is None
    for k in range(1, 10):
        assert groups[k]["vol_lo"] == bounds[k - 1] and groups[k - 1]["vol_hi"] == bounds[k - 1]


# ── AC-2：結果的形狀（登記的預測 1、2 的可檢查部分）：碰 −15% 隨組單調上升、兩方向相近 ───
def test_ac2_hit_rates_rise_with_volatility_in_both_directions() -> None:
    groups = _table()["groups"]
    assert isinstance(groups, list)
    down = [g["down15"] for g in groups]
    up = [g["up20"] for g in groups]
    assert down == sorted(down) and up == sorted(up)
    assert down[0] < 0.05 and down[9] > 0.2
    assert all(abs(d - u) < 0.1 for d, u in zip(down, up, strict=True))
    assert sum(g["n"] for g in groups) == _table()["n_obs"]


# ── AC-3：網站鏡像與 configs 相同（網站不能有自己的版本） ─────────────────────────────
def test_ac3_frontend_mirror_equals_configs() -> None:
    text = _MIRROR.read_text(encoding="utf-8")
    body = text[text.index("= ") + 2 :]
    body = body[: body.rindex(" as const")]
    assert json.loads(body) == _table()


# ── AC-4：網站重算波動度的公式與 factors/price.vol_20d 相同（同一串收盤、同一個數字） ─────
def test_ac4_vol20_formula_matches_production_factor() -> None:
    ts = _TS_TEST.read_text(encoding="utf-8")
    m_closes = re.search(r"const CLOSES = \[([^\]]+)\]", ts)
    m_ref = re.search(r"const VOL_REF = ([0-9.]+)", ts)
    assert m_closes and m_ref, "前端測試要放 CLOSES 與 VOL_REF"
    closes = [float(x) for x in m_closes.group(1).split(",")]
    df = pl.DataFrame(
        {"stock_id": ["X"] * len(closes), "date": list(range(len(closes))), "close": closes}
    )
    ret = pl.col("close") / pl.col("close").shift(1).over("stock_id", order_by="date") - 1.0
    vol = df.with_columns(_r=ret).with_columns(v=pl.col("_r").rolling_std(20).over("stock_id"))[
        "v"
    ][-1]
    assert abs(float(vol) - float(m_ref.group(1))) < 1e-12


# ── AC-5（靜態）：名單 API 與個股頁都接上；只讀到名單那天為止 ─────────────────────────
def test_ac5_wired_into_picks_and_stock_page() -> None:
    picks = (_ROOT / "frontend" / "functions" / "api" / "picks" / "index.ts").read_text(
        encoding="utf-8"
    )
    assert (
        "readStopHit(db, target, sids)" in picks
        and "stop_hit: stopHit.get(r.stock_id) ?? null" in picks
    )
    lib = (_ROOT / "frontend" / "functions" / "_lib" / "stop-hit.ts").read_text(encoding="utf-8")
    assert "date <= ${date}::date" in lib
    app = (_ROOT / "frontend" / "src" / "cloud" / "CloudApp.tsx").read_text(encoding="utf-8")
    assert (
        "stopHitLine(p.stop_hit)" in app and "<StockStopHit sid={sid} refresh={refresh} />" in app
    )
