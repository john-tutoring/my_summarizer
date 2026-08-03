"""doc2md.config — resolution order, unknown keys, malformed input."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2md import config as config_mod


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_file_exists():
    cfg = config_mod.load()
    assert cfg.extract_images is True
    assert cfg.min_image_pixels == 10000
    assert cfg.download_remote_images is False
    assert cfg.source_path is None


def test_cwd_config_is_picked_up(isolated_cwd):
    write(isolated_cwd / "doc2md.yaml", "min_image_pixels: 42\nextract_images: false\n")
    cfg = config_mod.load()
    assert cfg.min_image_pixels == 42
    assert cfg.extract_images is False
    assert cfg.source_path == isolated_cwd / "doc2md.yaml"


def test_cwd_beats_home(isolated_cwd):
    write(Path.home() / ".config" / "doc2md.yaml", "min_image_pixels: 1\n")
    write(isolated_cwd / "doc2md.yaml", "min_image_pixels: 2\n")
    assert config_mod.load().min_image_pixels == 2


def test_home_used_when_cwd_has_none(isolated_cwd):
    write(Path.home() / ".config" / "doc2md.yaml", "min_image_pixels: 7\n")
    assert config_mod.load().min_image_pixels == 7


def test_explicit_path_wins_over_both(isolated_cwd, tmp_path):
    write(isolated_cwd / "doc2md.yaml", "min_image_pixels: 2\n")
    explicit = write(tmp_path / "custom.yaml", "min_image_pixels: 99\n")
    assert config_mod.load(explicit).min_image_pixels == 99


def test_missing_explicit_path_is_an_error(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        config_mod.load(tmp_path / "nope.yaml")


def test_unknown_keys_are_reported_not_silently_ignored(isolated_cwd):
    write(isolated_cwd / "doc2md.yaml", "min_image_pixels: 5\ntypoed_key: 1\nanother: 2\n")
    cfg = config_mod.load()
    assert cfg.unknown_keys == ("another", "typoed_key")
    assert cfg.min_image_pixels == 5


def test_malformed_yaml_exits_with_a_message(isolated_cwd):
    write(isolated_cwd / "doc2md.yaml", "min_image_pixels: [unclosed\n")
    with pytest.raises(SystemExit, match="cannot parse"):
        config_mod.load()


def test_non_mapping_yaml_exits(isolated_cwd):
    write(isolated_cwd / "doc2md.yaml", "- just\n- a\n- list\n")
    with pytest.raises(SystemExit, match="must contain a YAML mapping"):
        config_mod.load()


def test_empty_file_falls_back_to_defaults(isolated_cwd):
    write(isolated_cwd / "doc2md.yaml", "")
    assert config_mod.load().min_image_pixels == 10000


def test_write_default_creates_a_loadable_file(isolated_cwd):
    written = config_mod.write_default()
    assert written == isolated_cwd / "doc2md.yaml"
    cfg = config_mod.load()
    assert cfg.extract_images is True
    assert cfg.unknown_keys == ()  # the shipped template must not drift from the dataclass


def test_write_default_never_overwrites(isolated_cwd):
    write(isolated_cwd / "doc2md.yaml", "min_image_pixels: 1\n")
    assert config_mod.write_default() is None
    assert config_mod.load().min_image_pixels == 1


def test_write_default_skips_when_home_config_exists(isolated_cwd):
    write(Path.home() / ".config" / "doc2md.yaml", "min_image_pixels: 3\n")
    assert config_mod.write_default() is None
