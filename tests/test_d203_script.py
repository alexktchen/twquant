"""D-203／T-419：主力大賣是不是籌碼鬆動的腳本——守登記的常數、純函數的正確性與決定性。離線、不連 PG。"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "d203_broker_sell_check.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("d203", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["d203"] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()
D = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17)]


# ── AC-1：登記的定義寫死在腳本裡（D-203 §二～§三） ─────────────────────────────────
def test_ac1_registered_constants() -> None:
    assert (dt.date(2022, 3, 1), dt.date(2026, 9, 8)) == (m.SIGNAL_START, m.DEV_END)
    assert (m.WINDOW, m.HORIZON, m.TAIL, m.N_BINS) == (5, 20, 0.10, 5)
    assert (m.DROP_10, m.DROP_15) == (-0.10, -0.15)
    assert (m.SEED, m.BOOT, m.SEGMENT_MONTHS) == (20261021, 10_000, 6)
    assert (m.RET_GATE, m.HIT_GATE) == (-0.005, 0.02)
    assert (m.MIN_ROWS, m.MIN_DAYS, m.MIN_NEG_YEARS, m.LIST_MIN) == (10_000, 300, 4, 300)
    assert m.YEARS == (2022, 2023, 2024, 2025, 2026)
    assert set(m.MATCH) == {"price.vol_20d", "price.mom_20d", "volume.adv_20d"}
    assert m.MODELS == {
        "CORE": "lgbm_lw_net_h20_20260225_83890036",
        "QUIET": "lgbm_quietA20_tu_h20_20260430_655df6d7",
    }
    params = dict(m.get(m.SIGNAL_FACTOR).params)
    assert (params["daytrade_min_ratio"], params["daytrade_min_events"]) == (0.5, 30)
    text = (_ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    assert "### D-203（" in text
    for phrase in (
        "seed 20261021",
        "**−0.5 個百分點**",
        "**+2 個百分點**",
        "**≥ 4 年**",
        "**t+1 開盤**",
    ):
        assert phrase in text


# ── AC-2：主力面板——隔日沖統計跨段累計（第二段看得到第一段累積的隔日沖券商） ───────────
class _View:
    def __init__(self, tables: dict[str, pl.DataFrame], a: dt.date, b: dt.date) -> None:
        self._t = {k: v.filter(pl.col("date").is_between(a, b)) for k, v in tables.items()}

    def table(self, name: str) -> pl.LazyFrame:
        return self._t[name].lazy()


def _tables() -> dict[str, pl.DataFrame]:
    days = D[:6]
    px = pl.DataFrame({"date": days, "stock_id": ["1101"] * 6, "volume": [10_000] * 6})
    rows = [
        # X：d0 買 → d1 賣、d2 買 → d3 賣，兩次都是隔日沖
        (days[0], "X", 1000, 0),
        (days[1], "X", 0, 1000),
        (days[2], "X", 1000, 0),
        (days[3], "X", 0, 1000),
        # d4：X 又買（應被排除），Y 是一般券商
        (days[4], "X", 5000, 0),
        (days[4], "Y", 3000, 0),
    ]
    bt = pl.DataFrame(
        rows, schema=["date", "broker_id", "buy_shares", "sell_shares"], orient="row"
    ).with_columns(stock_id=pl.lit("1101"))
    return {"prices_daily": px, "broker_trades": bt}


def test_ac2_panel_carries_daytrade_state_across_segments() -> None:
    t = _tables()
    params: dict[str, object] = {"daytrade_min_ratio": 0.5, "daytrade_min_events": 2}
    segs = [(D[0], D[3]), (D[4], D[5])]
    daily, vol = m.build_panel(lambda a, b: _View(t, a, b), segs, params)
    d4 = daily.filter(pl.col("date") == D[4]).row(0, named=True)
    assert (d4["_net"], d4["_nb"], d4["_ns"]) == (3000, 1, 0)  # X 被排除，只剩 Y
    # 對照：第二段單獨算（沒有注入全期 state）會把 X 當一般券商
    alone = m.clean_daily(_View(t, D[4], D[5]), params).collect()
    assert alone.filter(pl.col("date") == D[4])["_net"].item() == 8000
    assert vol.height == 6


# ── AC-3：訊號值＝近 5 日淨買超 ÷ 近 5 日成交量 ───────────────────────────────────
def test_ac3_signal_values_window5() -> None:
    nets = [100, -200, 300, -400, 500, -600, 700]
    daily = pl.DataFrame(
        {"date": D[:7], "stock_id": ["2330"] * 7, "_net": nets, "_nb": [1] * 7, "_ns": [1] * 7}
    ).vstack(
        pl.DataFrame(
            {"date": D[:7], "stock_id": ["00878"] * 7, "_net": nets, "_nb": [1] * 7, "_ns": [1] * 7}
        )
    )
    vol = daily.select("date", "stock_id").with_columns(volume=pl.lit(1000))
    sig = m.signal_values(daily, vol, D[0], D[6])
    assert sig["stock_id"].unique().to_list() == ["2330"]  # ETF（五碼）不算
    assert sig["date"].to_list() == D[4:7]
    assert sig["value"].to_list() == pytest.approx([300 / 5000, -400 / 5000, 500 / 5000])


# ── AC-4：結果以 t+1 開盤為基準、事件窗剔除、t+H 不得超過截止日 ───────────────────────
def test_ac4_outcomes() -> None:
    closes = [100.0, 100.0, 95.0, 85.0, 104.0, 110.0, 120.0]
    opens = [100.0, 100.0, 96.0, 90.0, 100.0, 108.0, 118.0]
    px = pl.DataFrame({"date": D[:7], "stock_id": ["1101"] * 7, "open": opens, "close": closes})
    no_ev = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.Utf8})
    out = m.outcomes(px, no_ev, D[6], horizon=3)
    r0 = out.row(0, named=True)  # t=D0：基準 t+1 開盤 100；t+1..t+3 收盤 100,95,85
    assert r0["date"] == D[0]
    assert r0["ret"] == pytest.approx(85 / 100 - 1)
    assert (r0["hit10"], r0["hit15"]) == (1.0, 1.0)
    r1 = out.row(1, named=True)  # t=D1：基準 96；收盤 95,85,104 → 最低 −11.5%
    assert r1["ret"] == pytest.approx(104 / 96 - 1)
    assert (r1["hit10"], r1["hit15"]) == (1.0, 0.0)
    assert out["date"].to_list() == D[:4]  # 第 t+3 個交易日到 D6 為止
    # 截止日提早 → t+3 超過截止的不算
    assert m.outcomes(px, no_ev, D[5], horizon=3)["date"].to_list() == D[:3]
    # D2 有事件：窗 (t, t+3] 含 D2 的 t=D0、D1 剔除；t=D2 本身不剔（事件在 t 當天）
    ev = pl.DataFrame({"date": [D[2]], "stock_id": ["1101"]})
    assert m.outcomes(px, ev, D[6], horizon=3)["date"].to_list() == [D[2], D[3]]


# ── AC-5：標記在當天可比較的股票上切；五分位格子 ─────────────────────────────────
def test_ac5_label_flags_and_cells() -> None:
    sids = [f"{1101 + i}" for i in range(20)]
    sig = pl.DataFrame(
        {"date": [D[0]] * 20, "stock_id": sids, "value": [i - 10.0 for i in range(20)]}
    )
    match = pl.DataFrame(
        {
            "date": [D[0]] * 20,
            "stock_id": sids,
            "vol": [float(i) for i in range(20)],
            "mom": [float(19 - i) for i in range(20)],
            "adv": [1.0] * 20,
        }
    )
    lab = m.label(sig, match)
    assert lab.filter(pl.col("sell"))["stock_id"].to_list() == sids[:2]
    assert lab.filter(pl.col("buy"))["stock_id"].to_list() == sids[18:]
    cells = dict(zip(lab["stock_id"].to_list(), lab["cell"].to_list(), strict=True))
    assert (
        cells[sids[0]] == 1 * 100 + 5 * 10 + 3
    )  # vol 最低、mom 最高、adv 全同分（平均名次 10.5 → 第 3 組）
    assert cells[sids[19]] == 5 * 100 + 1 * 10 + 3
    # 最低 10% 若不是負的就不標
    pos = m.label(sig.with_columns(pl.col("value") + 100), match)
    assert pos["sell"].sum() == 0
    # 缺配對變數的不進當天這一群（家數變少 → 名次重算）
    lab2 = m.label(
        sig,
        match.with_columns(adv=pl.when(pl.col("stock_id") == sids[0]).then(None).otherwise(1.0)),
    )
    assert lab2.height == 19 and lab2.filter(pl.col("sell"))["stock_id"].to_list() == [sids[1]]


# ── AC-6：配對超額＝被標的減同格子沒被標的平均；沒對照的剔除 ───────────────────────
def test_ac6_matched_excess() -> None:
    df = pl.DataFrame(
        {
            "date": [D[0]] * 5,
            "stock_id": ["1", "2", "3", "4", "5"],
            "cell": [111, 111, 111, 222, 222],
            "sell": [True, False, False, True, True],
            "ret": [0.01, 0.03, 0.05, -0.02, 0.0],
            "hit10": [1.0, 0.0, 1.0, 0.0, 1.0],
            "hit15": [0.0, 0.0, 0.0, 0.0, 0.0],
        }
    )
    ex, dropped = m.matched_excess(df, "sell")
    assert dropped == 2  # 格子 222 沒有對照
    r = ex.row(0, named=True)
    assert r["stock_id"] == "1"
    assert r["ex_ret"] == pytest.approx(0.01 - 0.04)
    assert r["ex_hit10"] == pytest.approx(1.0 - 0.5)
    # keys 換成只看日期＝不配對
    ex2, dropped2 = m.matched_excess(df, "sell", keys=("date",))
    assert dropped2 == 0 and ex2.height == 3
    assert ex2["ex_ret"].to_list() == pytest.approx([0.01 - 0.04, -0.02 - 0.04, 0.0 - 0.04])


# ── AC-7：整月一起抽的 bootstrap——決定性、點估計＝全部列平均 ───────────────────────
def test_ac7_month_bootstrap() -> None:
    dates = [dt.date(2023, mo, 2) for mo in range(1, 13) for _ in range(3)]
    vals = [((i * 7) % 11 - 5) / 100 for i in range(len(dates))]
    df = pl.DataFrame({"date": dates, "ex_ret": vals, "ex_hit10": [v / 2 for v in vals]})
    a = m.month_bootstrap(df, ["ex_ret", "ex_hit10"], n=2000)
    b = m.month_bootstrap(df, ["ex_ret", "ex_hit10"], n=2000)
    assert a == b
    mean, lo, hi = a["ex_ret"]
    assert mean == pytest.approx(sum(vals) / len(vals))
    assert lo <= mean <= hi and lo < hi
    one = m.month_bootstrap(df.filter(pl.col("date").dt.month() == 1), ["ex_ret"], n=100)
    assert one["ex_ret"][1] == pytest.approx(one["ex_ret"][2])
    with pytest.raises(ValueError, match="沒有任何列"):
        m.month_bootstrap(df.head(0), ["ex_ret"])


# ── AC-8：判讀照 D-203 §三 ──────────────────────────────────────────────────────
def _s(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "n_rows": 50_000,
        "n_days": 1000,
        "ret": (-0.008, -0.012, -0.004),
        "hit10": (0.03, 0.02, 0.04),
        "years": {2022: -0.01, 2023: -0.01, 2024: 0.002, 2025: -0.01, 2026: -0.01},
        "list_n": 500,
        "list_ret": -0.004,
        "list_hit10": 0.01,
    }
    base.update(kw)
    return m.Summary(**base)


def test_ac8_gates_and_verdict() -> None:
    assert m.verdict(m.gates(_s())) == "鬆動成立"
    assert m.verdict(m.gates(_s(list_ret=0.001))) == "全市場成立、名單內不成立"
    assert m.verdict(m.gates(_s(list_n=299))) == "全市場成立、名單內不成立"
    assert m.verdict(m.gates(_s(ret=(-0.004, -0.008, -0.001)))) == "不成立"  # 不到 −0.5 pp
    assert m.verdict(m.gates(_s(ret=(-0.006, -0.012, 0.001)))) == "不成立"  # 上界沒 < 0
    assert m.verdict(m.gates(_s(hit10=(0.019, 0.01, 0.03)))) == "不成立"
    assert m.verdict(m.gates(_s(hit10=(0.03, -0.001, 0.06)))) == "不成立"
    assert (
        m.verdict(m.gates(_s(years={2022: -0.01, 2023: -0.01, 2024: 0.0, 2025: 0.01}))) == "不成立"
    )
    assert m.verdict(m.gates(_s(n_rows=9_999))) == "樣本不足"
    assert m.verdict(m.gates(_s(n_days=299))) == "樣本不足"
    assert set(m.NEXT_STEP) == {"樣本不足", "鬆動成立", "全市場成立、名單內不成立", "不成立"}


# ── AC-9：融資增加＝比 5 個交易日前多 ───────────────────────────────────────────
def test_ac9_margin_up() -> None:
    bal = [100.0, 100, 100, 100, 100, 100, 90, 120]
    mg = pl.DataFrame({"date": D[:8], "stock_id": ["1101"] * 8, "margin_balance": bal})
    up = m.margin_up(mg)
    assert up["date"].to_list() == D[5:8]
    assert up["margin_up"].to_list() == [False, False, True]
