"""Configuration for doc2md.

Resolution order, first hit wins:
    --config PATH  ->  ./doc2md.yaml  ->  ~/.config/doc2md.yaml  ->  defaults

Unknown keys are reported rather than ignored, so a typo in the config file
doesn't silently do nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

import yaml

CONFIG_NAME = "doc2md.yaml"

DEFAULT_YAML = """\
# doc2md configuration.

# Extract image bytes to sidecar files. When false, every image still gets an
# anchor in the Markdown recording its position -- there is just no file.
extract_images: true

# Ignore images smaller than this many pixels (width * height). Filters out
# horizontal rules, bullet glyphs, and logos without touching real figures.
min_image_pixels: 10000

# Skip images larger than this many bytes; they become placeholder anchors.
max_image_bytes: 10485760

# Never fetch images referenced by an absolute URL. Extraction stays offline.
download_remote_images: false
"""


@dataclass
class Config:
    extract_images: bool = True
    min_image_pixels: int = 10000
    max_image_bytes: int = 10 * 1024 * 1024
    download_remote_images: bool = False

    # Populated during load; not a config key itself.
    source_path: Optional[Path] = None
    unknown_keys: tuple[str, ...] = ()

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)} - {"source_path", "unknown_keys"}


def _candidate_paths(explicit: Optional[Path]) -> list[Path]:
    if explicit is not None:
        return [explicit]
    return [Path.cwd() / CONFIG_NAME, Path.home() / ".config" / CONFIG_NAME]


def load(explicit: Optional[Path] = None) -> Config:
    """Load config from the first file that exists, else return defaults."""
    for path in _candidate_paths(explicit):
        if not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SystemExit(f"doc2md: cannot parse {path}: {exc}")
        if not isinstance(raw, dict):
            raise SystemExit(f"doc2md: {path} must contain a YAML mapping")
        return _from_mapping(raw, path)

    if explicit is not None:
        raise SystemExit(f"doc2md: config file not found: {explicit}")
    return Config()


def _from_mapping(raw: dict[str, Any], path: Path) -> Config:
    known = Config._field_names()
    unknown = tuple(sorted(k for k in raw if k not in known))
    values = {k: v for k, v in raw.items() if k in known}
    cfg = Config(**values)
    cfg.source_path = path
    cfg.unknown_keys = unknown
    return cfg


def write_default(directory: Optional[Path] = None) -> Optional[Path]:
    """Drop a commented default config so the settings are discoverable.

    Returns the path written, or None if a config already exists anywhere in
    the search order (we never overwrite).
    """
    directory = directory or Path.cwd()
    if any(p.is_file() for p in _candidate_paths(None)):
        return None
    target = directory / CONFIG_NAME
    target.write_text(DEFAULT_YAML, encoding="utf-8")
    return target
