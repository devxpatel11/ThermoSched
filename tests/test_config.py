from __future__ import annotations

import logging
from pathlib import Path

import pytest

from thermosched.config import ConfigError, SchedulerConfig, config_from_mapping, load_config


def test_documented_default_yaml_matches_built_in_defaults() -> None:
    assert load_config(Path("config/default.yaml")) == SchedulerConfig()


def test_partial_configuration_uses_sane_defaults(tmp_path: Path) -> None:
    path = tmp_path / "custom.yaml"
    path.write_text("sampling_interval_s: 1.0\nmicrobreak_ms: 100\n", encoding="utf-8")

    config = load_config(path)

    assert config.sampling_interval_s == 1.0
    assert config.microbreak_ms == 100
    assert config.high_temp_c == 75.0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"unexpected": 1}, "unknown configuration key"),
        ({"high_temp_c": 90, "critical_temp_c": 82}, "warm < high < critical"),
        ({"microbreak_ms": 300, "max_microbreak_ms": 250}, "cannot exceed"),
        ({"confirmation_samples": 0}, "at least 1"),
        ({"risk_weights": {"thermal": 1.0, "utilization": 1.0, "trend": 1.0}}, "sum to 1.0"),
    ],
)
def test_invalid_configuration_is_rejected(data: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        config_from_mapping(data)


def test_yaml_failure_is_logged_and_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("risk_weights: [not, a, mapping]", encoding="utf-8")

    with caplog.at_level(logging.ERROR), pytest.raises(ConfigError, match="risk_weights"):
        load_config(path)

    assert "failed to load configuration" in caplog.text
