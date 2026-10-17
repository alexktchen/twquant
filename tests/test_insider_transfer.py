"""內部人持股轉讓事前申報（T-384）：證交所 t187ap12_L＋櫃買 mopsfin_t187ap12_O。錄下的 2026-10-16 回應，不連網。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from twquant.config import ServingConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.data.sources import insider_transfer as it
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "http" / "insider"
AT = dt.datetime(2026, 10, 18, 0, 20, tzinfo=TZ)


def _twse() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (_FIX / "twse_t187ap12_L.json").read_text(encoding="utf-8")
    )
    return rows


def _tpex() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (_FIX / "tpex_t187ap12_O.json").read_text(encoding="utf-8")
    )
    return rows


def _as_tpex(row: dict[str, Any]) -> dict[str, Any]:
    """把證交所那份的一列改成櫃買的鍵名（櫃買 2026-10-16 沒有申報，拿不到真實的有值列）。"""
    out = dict(row)
    out["Date"] = out.pop("出表日期")
    out["SecuritiesCompanyCode"] = out.pop("公司代號")
    out["CompanyName"] = out.pop("公司名稱")
    out["申請人身分"] = out.pop("申報人身分")
    return out


# ── AC-1：兩份申報解析成同一種列；民國日期、期間、股數 ────────────────────────────
def test_ac1_twse_rows_parse_with_period_and_shares() -> None:
    rows = {r["stock_id"]: r for r in it.parse_twse(_twse(), AT)}
    assert set(rows) == {"6534", "6668", "9939"}
    r = rows["6534"]
    assert (r["date"], r["market"], r["role"], r["method"], r["planned_shares"]) == (
        dt.date(2026, 10, 16),
        "twse",
        "董事本人",
        "鉅額逐筆交易",
        2_000_000.0,
    )
    assert (r["period_start"], r["period_end"]) == (dt.date(2026, 10, 19), dt.date(2026, 11, 18))
    assert (r["held_own"], r["after_own"], r["daily_max_shares"], r["transferee"]) == (
        16_736_810.0,
        14_736_810.0,
        None,
        None,
    )
    assert r["available_at"] == AT
    # 來源欄位帶前導空白（「 一般交易(每日得轉讓股數限制)」、「 1200000」）→ 去掉
    g = rows["6668"]
    assert g["method"] == "一般交易(每日得轉讓股數限制)"
    assert (g["planned_shares"], g["daily_max_shares"]) == (1_200_000.0, 495_410.0)


def test_ac1_gift_without_planned_shares_uses_holding_difference() -> None:
    # 贈與那列「轉讓股數」空白：預定轉讓股數＝轉讓前持股 − 轉讓後持股
    (r,) = [r for r in it.parse_twse(_twse(), AT) if r["stock_id"] == "9939"]
    assert r["method"] == "贈與" and r["transferee"] == "鄭雅文"
    assert r["planned_shares"] == 3_572_019.0 - 1_223_134.0
    assert (r["period_start"], r["period_end"]) == (dt.date(2026, 10, 16), dt.date(2026, 10, 18))


def test_ac1_tpex_placeholder_row_means_no_filings_and_real_rows_parse() -> None:
    assert it.parse_tpex(_tpex(), AT) == []  # 2026-10-16 實見：一列全部空字串
    rows = it.parse_tpex([_as_tpex(r) for r in _twse()], AT)
    assert [r["market"] for r in rows] == ["tpex"] * 3
    assert rows[0]["company_name"] == "正瀚-創" and rows[0]["role"] == "董事本人"


def test_ac1_combined_frame_matches_the_pg_spec() -> None:
    df = it.to_frame(it.parse_twse(_twse(), AT) + it.parse_tpex(_tpex(), AT))
    assert list(df.columns) == list(it.SCHEMA) == list(sch.INSIDER_TRANSFER_SPEC.column_names)
    assert sch.INSIDER_TRANSFER_SPEC.pk == ("date", "stock_id", "row_key")
    assert df.height == 3
    assert str(df.schema["available_at"]) == "Datetime(time_unit='us', time_zone='Asia/Taipei')"


# ── AC-2：壞回應 raise，不丟列 ───────────────────────────────────────────────
def test_ac2_bad_responses_raise() -> None:
    with pytest.raises(DataError, match="不是陣列"):
        it.parse_twse({"stat": "OK"}, AT)  # type: ignore[arg-type]
    missing = [{k: v for k, v in _twse()[0].items() if k != "有效轉讓期間"}]
    with pytest.raises(DataError, match="缺鍵"):
        it.parse_twse(missing, AT)
    half_empty = [{**_as_tpex(_twse()[0]), "SecuritiesCompanyCode": ""}]
    with pytest.raises(DataError, match="代號空白但其他欄有值"):
        it.parse_tpex(half_empty, AT)
    bad_period = [{**_twse()[0], "有效轉讓期間": "2026/10/19-2026/11/18"}]
    with pytest.raises(DataError, match="期間格式不認得"):
        it.parse_twse(bad_period, AT)
    bad_num = [{**_twse()[0], "目前持有股數-自有持股": "N/A"}]
    with pytest.raises(DataError, match="不是數字"):
        it.parse_twse(bad_num, AT)
    bad_date = [{**_twse()[0], "出表日期": "20261016"}]
    with pytest.raises(DataError, match="日期格式不認得"):
        it.parse_twse(bad_date, AT)
    no_role = [{**_twse()[0], "申報人身分": ""}]
    with pytest.raises(DataError, match="身分或轉讓方式空白"):
        it.parse_twse(no_role, AT)
    rows = it.parse_twse(_twse(), AT)
    with pytest.raises(DataError, match="重複"):
        it.to_frame(rows + rows[:1])


def test_ac2_fetch_rejects_naive_time_and_non_200() -> None:
    class _Resp:
        def __init__(self, code: int, text: str) -> None:
            self.status_code, self.text = code, text
            self.content = text.encode()

    class _Client:
        def __init__(self, code: int) -> None:
            self.code = code

        def get(self, url: str, params: dict[str, str] | None = None) -> _Resp:
            return _Resp(self.code, "[]")

    with pytest.raises(ValueError, match="時區"):
        it.fetch(_Client(200), _Client(200), dt.datetime(2026, 10, 18))  # type: ignore[arg-type]
    with pytest.raises(DataError, match="HTTP 503"):
        it.fetch(_Client(503), _Client(200), AT)  # type: ignore[arg-type]
    with pytest.raises(DataError, match="櫃買內部人申讓 HTTP 429"):
        it.fetch(_Client(200), _Client(429), AT)  # type: ignore[arg-type]


# ── AC-3：同一份輸入解析兩次結果 bit-identical（row_key 決定性，重跑 upsert 不會長出新列） ──
def test_ac3_row_key_is_deterministic_and_distinguishes_filings() -> None:
    a = it.to_frame(it.parse_twse(_twse(), AT))
    b = it.to_frame(it.parse_twse(_twse(), AT))
    assert a.equals(b)
    assert a["row_key"].n_unique() == 3
    # 同一人同一天再申報不同股數 → 不同 row_key（不會被 upsert 蓋掉）
    more = [{**_twse()[0], "預定轉讓方式及股數-轉讓股數": "500000"}]
    (c,) = it.parse_twse(more, AT)
    assert c["row_key"] != a["row_key"][0]


# ── AC-4：每日 pipeline 地端與雲端都跑「內部人申讓」，排在除權息預告之後 ─────────────
def test_ac4_pipeline_runs_the_step_in_cloud_too() -> None:
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert 'PipelineStep("內部人申讓"' in source
    assert source.index('PipelineStep("除權息預告"') < source.index('PipelineStep("內部人申讓"')
    assert '@app.command(name="insider-transfers")' in source
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "內部人申讓" not in scfg.cloud.skip_pipeline_steps
    assert "insider_transfer_notices" not in scfg.cloud.windows  # 小表，雲端全史保留
