"""T-002 / SPEC-000 AC-2：配置載入與驗證。"""

from __future__ import annotations

from pathlib import Path

import pytest

from twquant.config import SystemConfig, config_hash, load_config
from twquant.errors import ConfigError

_REPO = Path(__file__).resolve().parent.parent


def test_ac2_real_system_yaml_loads() -> None:
    cfg = load_config(_REPO / "configs" / "system.yaml", SystemConfig)
    assert cfg.data_dir == "data"
    assert cfg.source_rates["twse_rwd"].max_requests == 3
    assert cfg.quality_gate.disk_min_gb == 50.0


def test_ac2_missing_required_field_fails_with_name(tmp_path: Path) -> None:
    p = tmp_path / "sys.yaml"
    p.write_text("source_rates: {}\n", encoding="utf-8")  # 缺 data_dir
    with pytest.raises(ConfigError, match="data_dir"):
        load_config(p, SystemConfig)


def test_ac2_extra_field_fails_with_name(tmp_path: Path) -> None:
    p = tmp_path / "sys.yaml"
    p.write_text("data_dir: data\nsource_rates: {}\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bogus"):
        load_config(p, SystemConfig)


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError, match="不存在"):
        load_config(_REPO / "configs" / "nope.yaml", SystemConfig)


def test_config_hash_is_deterministic() -> None:
    cfg = load_config(_REPO / "configs" / "system.yaml", SystemConfig)
    assert config_hash(cfg) == config_hash(cfg)
    assert len(config_hash(cfg)) == 16
