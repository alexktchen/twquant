"""T-031 / SPEC-009 §1：ServingConfig 載入與驗證。"""

from __future__ import annotations

from pathlib import Path

import pytest

from twquant.config import ServingConfig, load_config
from twquant.errors import ConfigError

_REPO = Path(__file__).resolve().parent.parent


def test_real_serving_yaml_loads() -> None:
    cfg = load_config(_REPO / "configs" / "serving.yaml", ServingConfig)
    assert cfg.enabled is True  # T-033 起預設啟用；PG 不可達自動降級 DuckDB
    assert cfg.dsn_env == "TWQUANT_PG_DSN"
    assert cfg.pg_schema == "serving"
    assert cfg.factor_history_days == 1100
    assert cfg.fallback_duckdb is True


def test_defaults_apply(tmp_path: Path) -> None:
    p = tmp_path / "serving.yaml"
    p.write_text("enabled: true\n", encoding="utf-8")
    cfg = load_config(p, ServingConfig)
    assert cfg.enabled is True
    assert cfg.pg_schema == "serving"
    assert cfg.pool_max == 8


def test_extra_field_fails_with_name(tmp_path: Path) -> None:
    p = tmp_path / "serving.yaml"
    p.write_text("enabled: false\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bogus"):
        load_config(p, ServingConfig)
