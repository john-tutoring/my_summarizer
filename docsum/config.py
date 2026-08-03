"""Configuration for docsum.

Resolution order, first hit wins:
    --config PATH  ->  ./docsum.yaml  ->  ~/.config/docsum.yaml  ->  defaults
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

import yaml

CONFIG_NAME = "docsum.yaml"
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")

DEFAULT_YAML = """\
# docsum configuration.

# Any OpenAI model id. This is the setting most worth changing.
# Run `docsum --list-models` to see what your key can actually use.
#   gpt-5.4        recent, a generation back from the newest  (default)
#   gpt-5.4-mini   cheaper; a good default for long books
#   gpt-4.1        older non-reasoning model, most predictable cost
model: gpt-5.4

# How hard the model works: low | medium | high | xhigh | max
# Only applies to reasoning models (o-series, gpt-5+), where it maps to
# reasoning_effort (xhigh and max both map to high). Ignored otherwise.
effort: high

# Ceiling on tokens per response. Requests always stream, so this can be large.
# Reasoning models spend part of this budget on internal reasoning, so raise it
# if you switch to one and see truncated summaries.
max_tokens: 16000

# Documents at or above this length get chapter-by-chapter treatment, with
# each chapter summarized from its own text alone.
chapter_threshold_words: 20000

# Summary length as a fraction of the source, and the bounds it is clamped to.
target_ratio: 0.10
min_ratio: 0.05
max_ratio: 0.15

# Never ask for a summary shorter than this, however short the input.
min_words: 150

# Give each chapter the previous chapter's summary for narrative continuity.
# Costs a few hundred extra input tokens per chapter.
carry_context: true
"""


@dataclass
class Config:
    model: str = "gpt-5.4"
    effort: str = "high"
    max_tokens: int = 16000
    chapter_threshold_words: int = 20000
    target_ratio: float = 0.10
    min_ratio: float = 0.05
    max_ratio: float = 0.15
    min_words: int = 150
    carry_context: bool = True

    source_path: Optional[Path] = None
    unknown_keys: tuple[str, ...] = ()

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)} - {"source_path", "unknown_keys"}

    def validate(self) -> None:
        if self.effort not in VALID_EFFORTS:
            raise SystemExit(
                f"docsum: effort must be one of {', '.join(VALID_EFFORTS)} (got {self.effort!r})"
            )
        if not 0 < self.min_ratio <= self.max_ratio < 1:
            raise SystemExit("docsum: require 0 < min_ratio <= max_ratio < 1")
        if self.max_tokens < 1:
            raise SystemExit("docsum: max_tokens must be positive")

    def target_words(self, source_words: int) -> int:
        """Requested summary length for a piece of source text."""
        ratio = min(max(self.target_ratio, self.min_ratio), self.max_ratio)
        return max(self.min_words, int(source_words * ratio))


def _candidate_paths(explicit: Optional[Path]) -> list[Path]:
    if explicit is not None:
        return [explicit]
    return [Path.cwd() / CONFIG_NAME, Path.home() / ".config" / CONFIG_NAME]


def load(explicit: Optional[Path] = None) -> Config:
    for path in _candidate_paths(explicit):
        if not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SystemExit(f"docsum: cannot parse {path}: {exc}")
        if not isinstance(raw, dict):
            raise SystemExit(f"docsum: {path} must contain a YAML mapping")
        cfg = _from_mapping(raw, path)
        cfg.validate()
        return cfg

    if explicit is not None:
        raise SystemExit(f"docsum: config file not found: {explicit}")
    cfg = Config()
    cfg.validate()
    return cfg


def _from_mapping(raw: dict[str, Any], path: Path) -> Config:
    known = Config._field_names()
    unknown = tuple(sorted(k for k in raw if k not in known))
    cfg = Config(**{k: v for k, v in raw.items() if k in known})
    cfg.source_path = path
    cfg.unknown_keys = unknown
    return cfg


def write_default(directory: Optional[Path] = None) -> Optional[Path]:
    """Drop a commented default config. Never overwrites an existing one."""
    directory = directory or Path.cwd()
    if any(p.is_file() for p in _candidate_paths(None)):
        return None
    target = directory / CONFIG_NAME
    target.write_text(DEFAULT_YAML, encoding="utf-8")
    return target
