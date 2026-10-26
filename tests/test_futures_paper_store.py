"""T-450／D-220：小台模擬單的雲端表。寫入者是研究腳本 fubon_live.py（不 import twquant），兩邊的建表語句要一字不差。"""

from twquant.data.serving.schema import FUTURES_PAPER_SECONDS_SPEC, FUTURES_PAPER_SPEC, spec_of

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
