"""Core data types shared by every doc2md extractor.

An extractor's only job is to turn a source file into a `Document`: a flat,
ordered list of `Block`s. Nothing here touches the filesystem — `render.py`
owns all output.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

# "raw" is a passthrough escape hatch for sources that are already Markdown.
BlockKind = Literal["heading", "text", "image", "table", "code", "raw"]

MAX_SLUG_LEN = 60


def slugify(value: str, fallback: str = "document") -> str:
    """Lowercase, filesystem-safe slug. Used for output dirs and image IDs."""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value.strip().lower())
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:MAX_SLUG_LEN].strip("-") or fallback


# Locator formatters. Each format uses whatever positional handle it gets for
# free; the width differences are intentional, since page numbers run much
# higher than slide or chapter numbers.
def page_locator(n: int) -> str:
    return f"p{n:03d}"


def slide_locator(n: int) -> str:
    return f"s{n:02d}"


def chapter_locator(n: int) -> str:
    return f"ch{n:02d}"


def heading_locator(n: int) -> str:
    return f"h{n:02d}"


NO_LOCATOR = "x00"


@dataclass
class ImageRef:
    """A single image occurrence at a specific point in the document.

    `data is None` means we chose not to extract the bytes — the image still
    gets an anchor in the Markdown so the position is recorded, it just has no
    file behind it. That is the deliberate cheap path, not an error.
    """

    id: str
    ext: str = ".png"
    data: Optional[bytes] = None
    caption: str = ""
    reason: str = ""  # why extraction was skipped, when data is None

    @property
    def filename(self) -> str:
        return f"{self.id}{self.ext}"

    @property
    def extracted(self) -> bool:
        return self.data is not None


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    level: int = 0  # heading depth, 1-6
    lang: str = ""  # code fence language
    image: Optional[ImageRef] = None


@dataclass
class Document:
    source: Path
    title: str
    slug: str
    blocks: list[Block] = field(default_factory=list)
    # Images already written into a "raw" block's text (the Markdown
    # passthrough path). They need files on disk but must not be rendered a
    # second time as their own blocks.
    inline_images: list[ImageRef] = field(default_factory=list)
    # Set by extractors whose output already carries its own top-level
    # heading (the Markdown passthrough path), so rendering does not prepend
    # a second one.
    has_h1: bool = False
    # Non-fatal things worth telling the user about, surfaced by the CLI.
    notes: list[str] = field(default_factory=list)

    def add(self, block: Block) -> None:
        self.blocks.append(block)

    def register_inline_image(self, image: ImageRef) -> None:
        self.inline_images.append(image)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    @property
    def images(self) -> list[ImageRef]:
        """Every image occurrence, whichever way it reaches the output."""
        return [b.image for b in self.blocks if b.image is not None] + list(self.inline_images)


class ImageNamer:
    """Generates image IDs of the form `{slug}-{locator}-img{NN}`.

    The counter resets whenever the locator changes, so IDs stay short and
    readable and remain stable across runs:

        annual-report-p012-img01
        annual-report-p012-img02
        annual-report-p013-img01
    """

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self._locator: Optional[str] = None
        self._n = 0

    def next(self, locator: str = NO_LOCATOR) -> str:
        if locator != self._locator:
            self._locator = locator
            self._n = 0
        self._n += 1
        return f"{self.slug}-{locator}-img{self._n:02d}"


def title_from_path(path: Path) -> str:
    """Human-readable title from a filename, for sources with no metadata."""
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s{2,}", " ", stem) or path.name
