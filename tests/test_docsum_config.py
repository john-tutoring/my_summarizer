"""docsum.config — validation and summary-length maths."""

from __future__ import annotations

from pathlib import Path

import pytest

from docsum import config as config_mod
from docsum.config import VALID_EFFORTS, Config


def test_defaults():
    cfg = config_mod.load()
    assert cfg.model == "gpt-5.4"
    assert cfg.effort == "high"
    assert cfg.carry_context is True
    assert cfg.chapter_threshold_words == 20000


def test_cwd_config_overrides_defaults(isolated_cwd):
    (isolated_cwd / "docsum.yaml").write_text("model: gpt-4.1\neffort: low\n")
    cfg = config_mod.load()
    assert cfg.model == "gpt-4.1"
    assert cfg.effort == "low"


def test_explicit_path_wins(isolated_cwd, tmp_path):
    (isolated_cwd / "docsum.yaml").write_text("model: from-cwd\n")
    explicit = tmp_path / "other.yaml"
    explicit.write_text("model: from-explicit\n")
    assert config_mod.load(explicit).model == "from-explicit"


def test_unknown_keys_are_surfaced(isolated_cwd):
    (isolated_cwd / "docsum.yaml").write_text("model: m\nnot_a_key: 1\n")
    assert config_mod.load().unknown_keys == ("not_a_key",)


def test_shipped_default_template_has_no_unknown_keys(isolated_cwd):
    config_mod.write_default()
    assert config_mod.load().unknown_keys == ()


def test_write_default_never_overwrites(isolated_cwd):
    (isolated_cwd / "docsum.yaml").write_text("model: mine\n")
    assert config_mod.write_default() is None
    assert config_mod.load().model == "mine"


# --- validation ------------------------------------------------------------


@pytest.mark.parametrize("effort", VALID_EFFORTS)
def test_every_documented_effort_is_accepted(effort):
    Config(effort=effort).validate()


def test_unknown_effort_is_rejected(isolated_cwd):
    (isolated_cwd / "docsum.yaml").write_text("effort: turbo\n")
    with pytest.raises(SystemExit, match="effort must be one of"):
        config_mod.load()


def test_inverted_ratios_are_rejected(isolated_cwd):
    (isolated_cwd / "docsum.yaml").write_text("min_ratio: 0.5\nmax_ratio: 0.1\n")
    with pytest.raises(SystemExit, match="min_ratio <= max_ratio"):
        config_mod.load()


def test_ratio_of_one_or_more_is_rejected():
    with pytest.raises(SystemExit):
        Config(min_ratio=0.1, max_ratio=1.0).validate()


def test_zero_min_ratio_is_rejected():
    with pytest.raises(SystemExit):
        Config(min_ratio=0.0).validate()


def test_non_positive_max_tokens_is_rejected():
    with pytest.raises(SystemExit, match="max_tokens must be positive"):
        Config(max_tokens=0).validate()


def test_malformed_yaml_exits(isolated_cwd):
    (isolated_cwd / "docsum.yaml").write_text("model: [unclosed\n")
    with pytest.raises(SystemExit, match="cannot parse"):
        config_mod.load()


# --- target_words ----------------------------------------------------------


def test_target_words_applies_the_ratio():
    cfg = Config(target_ratio=0.10, min_words=0)
    assert cfg.target_words(10000) == 1000


def test_target_words_respects_the_floor():
    cfg = Config(target_ratio=0.10, min_words=150)
    assert cfg.target_words(100) == 150


def test_target_ratio_is_clamped_upward_by_min_ratio():
    cfg = Config(target_ratio=0.01, min_ratio=0.05, max_ratio=0.15, min_words=0)
    assert cfg.target_words(10000) == 500  # 5%, not 1%


def test_target_ratio_is_clamped_downward_by_max_ratio():
    cfg = Config(target_ratio=0.90, min_ratio=0.05, max_ratio=0.15, min_words=0)
    assert cfg.target_words(10000) == 1500  # 15%, not 90%


def test_target_words_of_zero_source():
    cfg = Config(min_words=150)
    assert cfg.target_words(0) == 150


@pytest.mark.parametrize("words", [1000, 5000, 20000, 100000])
def test_target_stays_inside_the_configured_band(words):
    cfg = Config()
    target = cfg.target_words(words)
    assert target <= words * cfg.max_ratio or target == cfg.min_words
