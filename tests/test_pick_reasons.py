"""D-180／T-360：「為什麼選它」——句型齊全、白話寫法、模型線取加分最多的、規則線依權重排、貢獻與預測一致。"""

from __future__ import annotations

import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest
import yaml

from twquant.config import load_config
from twquant.ml.explain import contributions
from twquant.reporting.pick_reasons import (
    Phrase,
    PickReasonsConfig,
    Reason,
    model_reasons,
    phrase,
    reasons_json,
    rule_reasons,
)
from twquant.strategy.config import load_strategy

_ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> PickReasonsConfig:
    return load_config(_ROOT / "configs" / "pick_reasons.yaml", PickReasonsConfig)


def test_t360_ac1_every_live_feature_has_a_phrase_and_core_signals_match_the_strategy() -> None:
    """A20 的每個個股特徵都有句型（ctx. 不當理由）；大盤精選的規則因子與權重與策略設定一致。"""
    cfg = _cfg()
    feats = yaml.safe_load((_ROOT / "configs" / "ml_quiet_a20.yaml").read_text(encoding="utf-8"))[
        "features"
    ]
    missing = [
        f for f in feats if not f.startswith(cfg.model_line_exclude_prefix) and f not in cfg.factors
    ]
    assert missing == []
    strat = load_strategy(str(_ROOT / "configs" / "strategies" / "lw_e0_ctrl.yaml"))
    assert cfg.core_signals == {s.factor: s.weight for s in strat.signals}
    assert all(f in cfg.factors for f in cfg.core_signals)
    assert cfg.top_n == 3


@pytest.mark.parametrize(
    ("factor", "value", "pct", "want"),
    [
        ("chip.trust_streak", 4.0, None, "投信連買 4 天"),
        ("chip.trust_streak", 0.0, None, "投信今天沒有買超"),
        ("volume.vol_ratio_5d", 2.14, None, "成交量是前 5 日平均的 2.1 倍"),
        ("fundamental.revenue_yoy", 0.352, None, "月營收年增 35%"),
        ("fundamental.revenue_yoy", -0.12, None, "月營收年減 12%"),
        ("price.rs_market_20d", 0.083, None, "近 20 日比大盤多漲 8 個百分點"),
        ("price.intraday_ret_20d", math.log(1.10), None, "近 20 日盤中（開盤到收盤）累計漲 10%"),
        ("price.high_52w", 0.998, None, "在 52 週高點附近"),
        ("price.high_52w", 0.95, None, "離 52 週高點還差 5%"),
        ("price.vol_20d", 0.02, 0.85, "近 20 日波動在全市場前 15%"),
        ("price.vol_20d", 0.01, 0.10, "近 20 日波動在全市場後 10%"),
        ("price.vol_asym_20", math.log(1.8), None, "上漲日振幅是下跌日的 1.8 倍"),
        ("price.vol_asym_20", -math.log(1.5), None, "下跌日振幅是上漲日的 1.5 倍"),
        ("price.jump_count_60", 0.05, None, "近 60 日有 3 天大幅跳動"),
        ("fundamental.earnings_yield", 0.05, None, "本益比 20.0 倍"),
        ("fundamental.log_market_cap", math.log(123.4e8), None, "市值約 123 億"),
        ("price.bb_squeeze_120", 0.08, None, "布林通道收窄（近 120 日最窄的 8%）"),
        ("price.ret_skew_60", 0.9, None, "近 60 日報酬右偏（偶有大漲）"),
        ("chip.foreign_net_10d", -0.034, None, "外資近 10 日賣超（佔成交量 3.4%）"),
    ],
)
def test_t360_ac2_phrases_use_the_raw_value(
    factor: str, value: float, pct: float | None, want: str
) -> None:
    assert phrase(_cfg().factors[factor], value, pct) == want


def test_t360_ac2b_unwritable_values_give_none_not_made_up_text() -> None:
    f = _cfg().factors
    assert phrase(f["chip.trust_streak"], None, None) is None
    assert phrase(f["chip.trust_streak"], float("nan"), None) is None
    assert phrase(f["price.vol_20d"], 0.02, None) is None  # 排名型缺百分位
    assert phrase(f["fundamental.earnings_yield"], -0.01, None) is None  # 虧損：本益比沒有意義
    assert phrase(Phrase(kind="int", pos="連 {v} 天"), 2.6, None) == "連 3 天"


def _vals(**kw: float) -> dict[str, tuple[float | None, float | None]]:
    return {k.replace("__", "."): (v, 0.5) for k, v in kw.items()}


def test_t360_ac3_model_reasons_take_the_largest_pluses_and_the_biggest_minus() -> None:
    cfg = _cfg()
    contribs = {
        "chip.trust_streak": 0.40,
        "volume.vol_ratio_5d": 0.30,
        "ctx.taiex_vol20": 0.90,  # 大盤狀態：不當理由
        "fundamental.revenue_yoy": 0.25,  # 沒有值 → 跳過、往下遞補
        "price.mom_20d": 0.10,
        "price.reversal_5d": -0.05,
        "price.atr_14": -0.20,
        "chip.foreign_streak": 0.0,  # 沒有作用的不算
    }
    values = _vals(
        chip__trust_streak=4,
        volume__vol_ratio_5d=2.1,
        price__mom_20d=0.06,
        price__reversal_5d=0.03,
        price__atr_14=0.032,
    )
    got = model_reasons(contribs, values, cfg)
    assert [(r.factor, r.sign) for r in got] == [
        ("chip.trust_streak", 1),
        ("volume.vol_ratio_5d", 1),
        ("price.mom_20d", 1),
        ("price.atr_14", -1),
    ]
    assert got[0].text == "投信連買 4 天" and got[-1].text == "每日真實波幅約 3.2%"
    assert model_reasons({"chip.trust_streak": -0.1}, _vals(chip__trust_streak=0), cfg) == [
        Reason("chip.trust_streak", "投信今天沒有買超", -1, -0.1)
    ]
    with pytest.raises(ValueError, match="沒有句型"):
        model_reasons({"price.new_thing": 0.3}, {}, cfg)


def test_t360_ac4_rule_reasons_order_by_weight_times_percentile() -> None:
    cfg = _cfg()
    values = {
        "price.high_52w": (0.97, 0.90),  # 0.35 × 0.90 = 0.315
        "price.mom_60d": (0.35, 0.98),  # 0.25 × 0.98 = 0.245
        "volume.turnover_z_20d": (1.2, 0.80),  # 0.20
        "price.rs_market_20d": (0.12, 0.99),  # 0.1485
    }
    got = rule_reasons(values, cfg)
    assert [r.factor for r in got] == ["price.high_52w", "price.mom_60d", "volume.turnover_z_20d"]
    assert got[0].text == "離 52 週高點還差 3%，全市場前 10%"
    assert got[1].text == "近 60 日漲 35%，全市場前 2%"
    assert all(r.sign == 1 for r in got)
    # 缺值的跳過
    assert [r.factor for r in rule_reasons({**values, "price.mom_60d": (None, None)}, cfg)][
        1
    ] == "volume.turnover_z_20d"
    assert reasons_json(got[:1]) == [
        {"f": "price.high_52w", "t": "離 52 週高點還差 3%，全市場前 10%", "s": 1}
    ]


def _booster() -> tuple[lgb.Booster, list[str], pl.DataFrame]:
    rng = np.random.default_rng(0)
    names = ["chip.trust_streak", "volume.vol_ratio_5d", "price.atr_14"]
    x = rng.normal(size=(1500, 3))
    y = (x[:, 0] * 2 + x[:, 1] > 0).astype(int)
    b = lgb.train(
        {"objective": "binary", "verbose": -1, "num_leaves": 7, "seed": 1, "deterministic": True},
        lgb.Dataset(x, y, feature_name=names),
        30,
    )
    frame = pl.DataFrame(
        {"stock_id": ["1101", "2330"], **{n: x[:2, i] for i, n in enumerate(names)}}
    )
    return b, names, frame


def test_t360_ac5_contributions_add_up_to_the_model_output() -> None:
    b, names, frame = _booster()
    got = contributions(b, frame, names)
    assert [c.stock_id for c in got] == ["1101", "2330"]
    preds = b.predict(frame.select(names).to_numpy())
    for c, p in zip(got, preds, strict=True):
        raw = sum(c.contribs.values()) + c.bias
        assert 1.0 / (1.0 + math.exp(-raw)) == pytest.approx(p, abs=1e-9)  # 對數勝算 → 機率
        assert c.prediction == pytest.approx(p, abs=1e-12)
        assert set(c.contribs) == set(names)
    assert contributions(b, frame.head(0), names) == []
    with pytest.raises(ValueError, match="特徵順序"):
        contributions(b, frame, list(reversed(names)))


def test_t360_ac5b_rows_check_the_published_score_and_add_the_model_gate() -> None:
    import datetime as dt

    from twquant.data.serving.pick_reasons_store import DayPick
    from twquant.errors import DataError
    from twquant.ml.explain import Contributions
    from twquant.reporting.pick_reasons import model_rows, rule_rows

    cfg = _cfg()
    day = dt.date(2026, 10, 14)
    c = Contributions(
        "1101", {"chip.trust_streak": 0.4, "price.atr_14": -0.1, "ctx.taiex_vol20": 0.5}, -2.0, 0.42
    )
    raw = {("1101", "chip.trust_streak"): (4.0, 0.97), ("1101", "price.atr_14"): (0.032, 0.6)}
    pick = DayPick("QUIET", 1, "1101", 0.42, "m")
    (row,) = model_rows(day, [pick], {"1101": c}, raw, "m", cfg, tol=1e-6)
    assert row[:3] == ("QUIET", "1101", "m")
    assert row[3] == [
        {"f": "chip.trust_streak", "t": "投信連買 4 天", "s": 1},
        {"f": "price.atr_14", "t": "每日真實波幅約 3.2%", "s": -1},
    ]
    with pytest.raises(DataError, match="與名單上的"):
        model_rows(
            day, [DayPick("QUIET", 1, "1101", 0.43, "m")], {"1101": c}, raw, "m", cfg, tol=1e-6
        )
    with pytest.raises(DataError, match="不在當天的推論池"):
        model_rows(
            day, [DayPick("QUIET", 1, "2330", 0.42, "m")], {"1101": c}, raw, "m", cfg, tol=1e-6
        )
    core = DayPick("CORE", 1, "2454", None, "e0")
    craw = {("2454", "price.high_52w"): (0.99, 0.95), ("2454", "price.mom_60d"): (0.4, 0.97)}
    (crow,) = rule_rows([core], craw, {"e0": {"2454": 0.93}}, cfg)
    assert [r["f"] for r in crow[3]] == ["price.high_52w", "price.mom_60d", "ml.rank"]
    assert crow[3][-1] == {"f": "ml.rank", "t": "排序模型分數在全市場前 7%", "s": 0}
    assert [r["f"] for r in rule_rows([core], craw, {}, cfg)[0][3]] == [
        "price.high_52w",
        "price.mom_60d",
    ]


def test_t360_ac6_step_runs_after_the_daily_list_and_in_the_cloud() -> None:
    """要讀當天剛寫的名單：排在每日名單之後；雲端因子只留 2 天，雲端不能略過（靜態）。"""
    from twquant.config import ServingConfig

    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("每日名單"') < source.index('PipelineStep("名單理由"')
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "名單理由" not in scfg.cloud.skip_pipeline_steps
    assert "pick_reasons" not in scfg.cloud.windows  # 全史保留：過了兩天就算不回來


# ── PG（無 DSN 則 skip）────────────────────────────────────────────────────

_SCHEMA = "serving_test_pick_reasons"


def _connect_or_skip() -> object:
    import os

    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    from twquant.data.serving import schema as sch

    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
    conn.commit()
    sch.ensure_tables(
        conn,
        (sch.spec_of("daily_picks"), sch.spec_of("factor_values"), sch.spec_of("ml_scores")),
        _SCHEMA,
    )
    return conn


def test_t360_ac7_pg_reads_and_whole_day_overwrite() -> None:
    import datetime as dt

    from twquant.conventions import TZ
    from twquant.data.serving.pick_reasons_store import (
        DayPick,
        read_day_picks,
        read_factor_raw,
        read_rank_pct,
        write_reasons,
    )
    from twquant.errors import DataError

    conn = _connect_or_skip()
    day = dt.date(2026, 10, 14)
    at = dt.datetime(2026, 10, 14, 17, 50, tzinfo=TZ)
    try:
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.executemany(
                f'INSERT INTO "{_SCHEMA}".daily_picks VALUES (%s, %s, %s, %s, %s, %s, %s)',
                [
                    (day, "QUIET", 2, "2330", 0.31, "m", at),
                    (day, "QUIET", 1, "1101", 0.42, "m", at),
                    (day, "CORE", 1, "2454", None, "e0", at),
                ],
            )
            cur.executemany(
                f'INSERT INTO "{_SCHEMA}".factor_values VALUES (%s, %s, %s, %s, %s, %s)',
                [
                    ("chip.trust_streak", "1101", day, 4.0, 1.2, 0.97),
                    ("chip.trust_streak", "2330", day, None, None, None),
                ],
            )
            cur.execute(
                f'INSERT INTO "{_SCHEMA}".ml_scores VALUES (%s, %s, %s, %s, %s, %s)',
                (day, "2454", "e0", 0.8, 0.95, at),
            )
        conn.commit()  # type: ignore[attr-defined]
        assert read_day_picks(conn, _SCHEMA, day) == [
            DayPick("CORE", 1, "2454", None, "e0"),
            DayPick("QUIET", 1, "1101", 0.42, "m"),
            DayPick("QUIET", 2, "2330", 0.31, "m"),
        ]
        raw = read_factor_raw(conn, _SCHEMA, day, ["chip.trust_streak"], ["1101", "2330", "9999"])
        assert raw == {
            ("1101", "chip.trust_streak"): (4.0, 0.97),
            ("2330", "chip.trust_streak"): (None, None),
        }
        assert read_rank_pct(conn, _SCHEMA, day, "e0", ["2454"]) == {"2454": 0.95}
        one = [{"f": "chip.trust_streak", "t": "投信連買 4 天", "s": 1}]
        write_reasons(
            conn, _SCHEMA, day, [("QUIET", "1101", "m", one), ("CORE", "2454", "e0", one)], at
        )
        write_reasons(
            conn, _SCHEMA, day, [("QUIET", "1101", "m", one)], at
        )  # 整天覆蓋：CORE 不會留著
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(f'SELECT line, stock_id, reasons::text FROM "{_SCHEMA}".pick_reasons')
            rows = cur.fetchall()
        assert [(a, b) for a, b, _ in rows] == [("QUIET", "1101")]
        assert '"投信連買 4 天"' in rows[0][2]
        with pytest.raises(DataError, match="寫不出任何理由"):
            write_reasons(conn, _SCHEMA, day, [("QUIET", "2330", "m", [])], at)
    finally:
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()  # type: ignore[attr-defined]
        conn.close()  # type: ignore[attr-defined]
