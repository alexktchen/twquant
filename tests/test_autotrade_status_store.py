"""autotrade A-005 AC9：程式交易狀態的表。寫入者是 autotrade/tools/push_status.py（不 import twquant），兩邊的建表語句要一字不差。"""

from twquant.data.serving.schema import AUTOTRADE_STATUS_SPEC, spec_of

# autotrade/src/autotrade/export.py 的 STATUS_DDL（autotrade/tests/test_a005_export.py 守著同一句）；改表要兩邊一起改
AUTOTRADE_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."autotrade_status" '
    "(day date, mode text, source text, status text, payload jsonb, updated_at timestamptz, "
    "PRIMARY KEY (day, mode))"
)


def test_ac9_spec_registered_and_ddl_matches_writer() -> None:
    assert spec_of("autotrade_status") is AUTOTRADE_STATUS_SPEC
    assert AUTOTRADE_STATUS_SPEC.ddl("serving") == AUTOTRADE_DDL


def test_ac9_one_row_per_day_and_mode() -> None:
    assert AUTOTRADE_STATUS_SPEC.pk == ("day", "mode")
    assert AUTOTRADE_STATUS_SPEC.snapshot
