"""T-414：新聞薄因子（factors/news.py）——登記、反前視、缺值規則。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import yaml

import twquant.factors.news as news_mod
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.news_features import VALUE_COLS, topic_cols
from twquant.data.warehouse import Warehouse
from twquant.factors import registry
from twquant.factors.testing import assert_no_lookahead

_ROOT = Path(__file__).resolve().parents[1]
_DAYS = [dt.date(2026, 6, 1) + dt.timedelta(days=i) for i in range(8)]
_SIDS = ["1101", "2330"]


def _tw(days: list[dt.date], hour: int) -> pl.Series:
    return pl.Series(
        [dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=TZ) for d in days], dtype=schemas.TW_DT
    )


def _prices(days: list[dt.date], sids: list[str]) -> pl.DataFrame:
    rows = [(d, s) for d in days for s in sids]
    close = [100.0 + i for i in range(len(rows))]
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000] * len(rows),
            "amount": [1e6] * len(rows),
            "limit_up_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "limit_down_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "available_at": _tw([r[0] for r in rows], 18),
        }
    )


def _features() -> pl.DataFrame:
    rows = [(d, s) for d in _DAYS for s in _SIDS]
    n = len(rows)
    cols: dict[str, object] = {
        "date": [r[0] for r in rows],
        "stock_id": [r[1] for r in rows],
        "available_at": _tw([r[0] for r in rows], 19),
    }
    for c in [*VALUE_COLS, *topic_cols(news_mod.TOPIC_K)]:
        vals: list[float | None] = [
            float(i % 5) / 4 if (i + len(c)) % 3 else None for i in range(n)
        ]
        if c == "count_1d":
            vals = [float(i % 3) for i in range(n)]  # 0 是真值，不能被當缺值丟掉
        cols[c] = pl.Series(vals, dtype=pl.Float64)
    cols["topic_version"] = ["v"] * n
    return pl.DataFrame(cols)


def _lake(tmp_path: Path) -> Warehouse:
    wh = Warehouse(tmp_path / "lake")
    wh.write("clean", "prices_daily", _prices(_DAYS, _SIDS))
    wh.write("clean", "news_features", _features())
    return wh


def test_ac_registry_names_match_config_and_schema() -> None:
    cfg = yaml.safe_load((_ROOT / "configs" / "news_factors.yaml").read_text(encoding="utf-8"))
    assert int(cfg["topics"]["k"]) == news_mod.TOPIC_K
    for name in news_mod.NEWS_FACTORS:
        fdef = registry.get(name)
        assert (
            fdef.category == "news"
            and fdef.tables == ("news_features",)
            and fdef.lookback_days == 0
        )
    assert set(registry.list_factors("news")) == set(news_mod.NEWS_FACTORS)
    sch = schemas.get_schema("news_features")
    assert set(VALUE_COLS) | set(topic_cols(news_mod.TOPIC_K)) <= set(sch.columns)


def test_ac_no_lookahead_for_every_news_factor(tmp_path: Path) -> None:
    wh = _lake(tmp_path)
    for name in (
        "news.count_1d",
        "news.novelty",
        "news.sent_shift",
        "news.topic_01",
        "news.topic_16",
    ):
        assert_no_lookahead(name, wh, cut_dates=[_DAYS[2], _DAYS[5], _DAYS[-1]])


def test_ac_zero_count_is_a_value_and_null_is_dropped(tmp_path: Path) -> None:
    wh = _lake(tmp_path)
    view = wh.view(_DAYS[-1])
    count_fn = registry.get("news.count_1d").fn
    nov_fn = registry.get("news.novelty").fn
    assert count_fn is not None and nov_fn is not None
    count = count_fn(view, {})
    assert count.height == len(_DAYS) * len(_SIDS) and (count["value"] == 0.0).sum() > 0
    nov = nov_fn(view, {})
    assert nov["value"].null_count() == 0 and nov.height < len(_DAYS) * len(_SIDS)
    assert set(count.columns) == {"date", "stock_id", "value"}


def test_ac_rows_after_cutoff_are_invisible(tmp_path: Path) -> None:
    """t 日 19:00 的列，as_of＝t 看得到（訊號截點 20:00）；as_of＝t−1 看不到。"""
    wh = _lake(tmp_path)
    fn = registry.get("news.count_1d").fn
    assert fn is not None
    assert fn(wh.view(_DAYS[3]), {})["date"].max() == _DAYS[3]
    assert fn(wh.view(_DAYS[2]), {})["date"].max() == _DAYS[2]
