"""T-450／D-220：小台模擬單的雲端表。寫入者是研究腳本 fubon_live.py（不 import twquant），兩邊的建表語句要一字不差。"""

from twquant.data.serving.schema import (
    FUTURES_PAPER_SECONDS_SPEC,
    FUTURES_PAPER_SPEC,
    FUTURES_QUOTES_SECONDS_SPEC,
    FUTURES_QUOTES_SPEC,
    spec_of,
)

# research/momentum_scalping/scripts/fubon_live.py 的 TABLE_DDL（研究分支）；改表要兩邊一起改
RESEARCH_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."futures_paper" '
    "(day date, source text, status text, payload jsonb, updated_at timestamptz, PRIMARY KEY (day))"
)
RESEARCH_SECONDS_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."futures_paper_seconds" '
    "(day date, payload jsonb, updated_at timestamptz, PRIMARY KEY (day))"
)


def test_ac1_spec_registered_and_ddl_matches_writer() -> None:
    assert spec_of("futures_paper") is FUTURES_PAPER_SPEC
    assert FUTURES_PAPER_SPEC.ddl("serving") == RESEARCH_DDL


def test_ac1_one_row_per_day_without_date_partition() -> None:
    assert FUTURES_PAPER_SPEC.pk == ("day",)
    assert FUTURES_PAPER_SPEC.snapshot


def test_ac6_seconds_table_registered_and_ddl_matches_writer() -> None:
    assert spec_of("futures_paper_seconds") is FUTURES_PAPER_SECONDS_SPEC
    assert FUTURES_PAPER_SECONDS_SPEC.ddl("serving") == RESEARCH_SECONDS_DDL


# T-451：大台、微台行情（fubon_live.py 的 QUOTES_DDL／QUOTES_SECONDS_DDL）
RESEARCH_QUOTES_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."futures_quotes" '
    "(day date, product text, source text, status text, payload jsonb, updated_at timestamptz, "
    "PRIMARY KEY (day, product))"
)
RESEARCH_QUOTES_SECONDS_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."futures_quotes_seconds" '
    "(day date, product text, payload jsonb, updated_at timestamptz, PRIMARY KEY (day, product))"
)


def test_t451_ac1_quotes_tables_registered_and_ddl_matches_writer() -> None:
    assert spec_of("futures_quotes") is FUTURES_QUOTES_SPEC
    assert spec_of("futures_quotes_seconds") is FUTURES_QUOTES_SECONDS_SPEC
    assert FUTURES_QUOTES_SPEC.ddl("serving") == RESEARCH_QUOTES_DDL
    assert FUTURES_QUOTES_SECONDS_SPEC.ddl("serving") == RESEARCH_QUOTES_SECONDS_DDL
