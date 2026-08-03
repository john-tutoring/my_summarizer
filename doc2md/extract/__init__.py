"""Extractor registry and dispatch.

Extractor modules are imported lazily so that a missing optional dependency
only breaks the formats that actually need it — a machine without `pymupdf`
can still convert `.txt` and `.md`.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from ..config import Config
from ..model import Document


class ExtractionError(Exception):
    """Raised when a file cannot be converted. Message is shown to the user."""


# extension -> (module, pip package that provides it)
_NATIVE: dict[str, tuple[str, str]] = {
    ".pdf": ("pdf", "pymupdf"),
    ".docx": ("docx", "python-docx"),
    ".pptx": ("pptx", "python-pptx"),
    ".epub": ("epub", "EbookLib"),
    ".html": ("html", "beautifulsoup4"),
    ".htm": ("html", "beautifulsoup4"),
    ".xhtml": ("html", "beautifulsoup4"),
    ".odt": ("opendocument", "lxml"),
    ".odp": ("opendocument", "lxml"),
    ".ods": ("opendocument", "lxml"),
    ".rtf": ("rtf", ""),
    ".md": ("text", ""),
    ".markdown": ("text", ""),
    ".txt": ("text", ""),
    ".text": ("text", ""),
    ".log": ("text", ""),
    ".csv": ("text", ""),
}

# Handed to MarkItDown. Text only — no image anchoring for these.
# Deliberately limited to formats MarkItDown actually converts: anything else
# falls through to its plain-text reader and emits raw markup as "Markdown".
_FALLBACK: frozenset[str] = frozenset(
    {".xlsx", ".xls", ".msg", ".ipynb", ".json", ".xml"}
)


def supported_extensions() -> set[str]:
    return set(_NATIVE) | set(_FALLBACK)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in supported_extensions()


def _load(module_name: str, package: str):
    try:
        return importlib.import_module(f".{module_name}", __package__)
    except ImportError as exc:
        hint = f" — install it with: pip install {package}" if package else ""
        raise ExtractionError(f"missing dependency for .{module_name} files{hint} ({exc})")


def extract(path: Path, cfg: Config) -> Document:
    """Convert `path` to a Document, dispatching on extension."""
    if not path.is_file():
        raise ExtractionError(f"not a file: {path}")

    suffix = path.suffix.lower()
    if suffix in _NATIVE:
        module_name, package = _NATIVE[suffix]
        module = _load(module_name, package)
    elif suffix in _FALLBACK:
        module = _load("fallback", "markitdown")
    else:
        supported = ", ".join(sorted(supported_extensions()))
        raise ExtractionError(f"unsupported file type '{suffix}'. Supported: {supported}")

    return module.extract(path, cfg)
