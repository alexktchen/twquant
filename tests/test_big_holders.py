"""集保千張大戶接進每日 pipeline（T-386／D-191 方向 4）。離線：沿用 tests/fixtures/http/tdcc 的真實 CSV。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twquant.config import ServingConfig, SourceRate, load_config
from twquant.data.http import HttpClient
from twquant.data.sources.tdcc import TdccDispersionSource

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _ROOT / "tests" / "fixtures" / "http" / "tdcc" / "dispersion.csv"
_RATE = SourceRate(max_requests=3, per_seconds=5.0)


@dataclass
class _Resp:
    status_code: int
    text: str
    content: bytes


def _source(body: bytes) -> TdccDispersionSource:
    def transport(method: str, url: str, *, params: object, timeout: float) -> _Resp:
        return _Resp(200, "", body)

    return TdccDispersionSource(client=HttpClient(source="tdcc", rate=_RATE, transport=transport))


# ── AC-1：四碼普通股（含前導零）照樣進表——早先註解說 id=1-5 只有六碼證券是錯的 ──────────
def test_ac1_four_digit_common_stocks_pass_through_the_cleaner() -> None:
    body = _FIXTURE.read_bytes().replace(b"006208", b"2330").replace(b"00400A", b"0050")
    df = _source(body).load("shareholding_dispersion", dt.date(2026, 7, 3))
    assert set(df["stock_id"].unique().to_list()) == {"2330", "0050"}
    assert df.height == 30  # 2 檔 × 15 級；16=差異數、17=合計 剔除
    # 第 15 級＝1,000 張以上，網站算千張大戶比例用它
    big = df.filter((pl.col("stock_id") == "2330") & (pl.col("level") == 15))
    assert big.height == 1 and big["shares"][0] > 0


# ── AC-2：每日 pipeline 地端與雲端都跑「集保大戶」，排在供應層之前（鏡射同步才帶得進 PG） ──────
def test_ac2_pipeline_step_runs_before_serving_sync_in_cloud_too() -> None:
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert 'PipelineStep("集保大戶"' in source
    assert source.index('PipelineStep("月營收"') < source.index('PipelineStep("集保大戶"')
    assert source.index('PipelineStep("集保大戶"') < source.index('PipelineStep("供應層"')
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "集保大戶" not in scfg.cloud.skip_pipeline_steps


# ── AC-3：雲端只留四碼普通股、窗口以「週」計且夠個股頁看 8 週走勢＋1 週算變化 ─────────────
def test_ac3_cloud_keeps_four_digit_stocks_for_at_least_nine_weeks() -> None:
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "shareholding_dispersion" in scfg.cloud.pattern_tables
    assert scfg.cloud.windows["shareholding_dispersion"] >= 9
