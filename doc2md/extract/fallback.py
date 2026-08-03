"""MarkItDown fallback for formats without a hand-written extractor.

Text only: MarkItDown does not report image positions, so documents converted
this way have no image anchors. That is a known limitation of the fallback
path, not a failure, and it is recorded as a note on the document.
"""

from __future__ import annotations

from pathlib import Path

from markitdown import MarkItDown

from ..config import Config
from ..model import Block, Document, slugify, title_from_path


def extract(path: Path, cfg: Config) -> Document:
    converter = MarkItDown(enable_plugins=False)
    try:
        result = converter.convert(str(path))
    except Exception as exc:  # noqa: BLE001 - MarkItDown raises per-format errors
        raise RuntimeError(f"MarkItDown could not convert this file: {exc}") from exc

    title = (getattr(result, "title", "") or "").strip() or title_from_path(path)
    doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))

    text = (getattr(result, "text_content", "") or "").strip()
    if not text:
        doc.note("no readable content found")
        return doc

    doc.has_h1 = any(line.startswith("# ") for line in text.splitlines())
    doc.add(Block(kind="raw", text=text))
    doc.note(
        f"converted via MarkItDown fallback ({path.suffix}); images are not anchored for this format"
    )
    return doc
