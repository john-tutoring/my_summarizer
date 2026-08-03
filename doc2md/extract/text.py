"""Plain text, Markdown, and CSV.

Markdown is passed through verbatim rather than reparsed — it is already the
target format, so the only work is rewriting local image links to point at our
`images/` directory and giving each one an anchor ID.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..config import Config
from ..model import (
    Block,
    Document,
    ImageNamer,
    ImageRef,
    heading_locator,
    slugify,
    title_from_path,
)
from ..render import image_markdown

MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?P<title>\s+\"[^\"]*\")?\)")
ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

CSV_ROW_NOTE_THRESHOLD = 2000


def _read(path: Path) -> str:
    """Read as UTF-8, falling back to latin-1 so we never hard-fail on bytes."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def extract(path: Path, cfg: Config) -> Document:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return _extract_markdown(path, cfg)
    if suffix == ".csv":
        return _extract_csv(path, cfg)
    return _extract_plain(path, cfg)


# --- plain text ------------------------------------------------------------


def _extract_plain(path: Path, cfg: Config) -> Document:
    title = title_from_path(path)
    doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))
    text = _read(path)
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip("\n").rstrip()
        if chunk.strip():
            doc.add(Block(kind="text", text=chunk))
    if not doc.blocks:
        doc.note("file is empty")
    return doc


# --- markdown --------------------------------------------------------------


def _extract_markdown(path: Path, cfg: Config) -> Document:
    text = _read(path)
    first_h1, has_h1 = _scan_headings(text)
    title = first_h1 or title_from_path(path)
    doc = Document(
        source=path,
        title=title,
        slug=slugify(title, path.stem or "document"),
        has_h1=has_h1,
    )
    namer = ImageNamer(doc.slug)

    out_lines: list[str] = []
    heading_index = 0
    in_fence = False
    fence_marker = ""

    for line in text.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker[0]
            elif marker[0] == fence_marker:
                in_fence, fence_marker = False, ""
            out_lines.append(line)
            continue

        if in_fence:
            out_lines.append(line)
            continue

        if ATX_HEADING_RE.match(line):
            heading_index += 1
            out_lines.append(line)
            continue

        locator = heading_locator(heading_index)
        out_lines.append(
            MD_IMAGE_RE.sub(lambda m: _rewrite_image(m, path, doc, namer, locator, cfg), line)
        )

    doc.add(Block(kind="raw", text="\n".join(out_lines).strip()))
    return doc


def _scan_headings(text: str) -> tuple[str, bool]:
    """Return (text of the first level-1 heading, whether any H1 exists).

    Fence-aware, so a `# comment` inside a code block is not mistaken for a
    heading. Falls back to the first heading of any level for the title.
    """
    first_any = ""
    first_h1 = ""
    in_fence = False
    fence_char = ""

    for line in text.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char = True, marker[0]
            elif marker[0] == fence_char:
                in_fence, fence_char = False, ""
            continue
        if in_fence:
            continue

        match = ATX_HEADING_RE.match(line)
        if not match:
            continue
        level, body = len(match.group(1)), match.group(2).strip()
        if not body:
            continue
        if not first_any:
            first_any = body
        if level == 1 and not first_h1:
            first_h1 = body

    return (first_h1 or first_any), bool(first_h1)


def _rewrite_image(
    match: re.Match, source: Path, doc: Document, namer: ImageNamer, locator: str, cfg: Config
) -> str:
    alt = match.group("alt") or ""
    src = match.group("src")

    if src.startswith("<") and src.endswith(">"):
        src = src[1:-1]

    if URL_RE.match(src) or src.startswith("//"):
        # Remote by definition. We never fetch; record the position instead.
        image = ImageRef(id=namer.next(locator), caption=alt, reason="remote image not downloaded")
        doc.register_inline_image(image)
        return image_markdown(image)

    resolved = (source.parent / src).resolve()
    ext = resolved.suffix.lower() or ".png"
    image = ImageRef(id=namer.next(locator), ext=ext, caption=alt)

    if resolved.is_file():
        try:
            image.data = resolved.read_bytes()
        except OSError as exc:
            image.reason = f"could not read linked image ({exc.strerror or exc})"
    else:
        image.reason = "linked image file not found"
        doc.note(f"{image.id}: linked file missing ({src})")

    doc.register_inline_image(image)
    return image_markdown(image)


# --- csv -------------------------------------------------------------------


def _extract_csv(path: Path, cfg: Config) -> Document:
    title = title_from_path(path)
    doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))
    text = _read(path)

    try:
        dialect = csv.Sniffer().sniff(text[:8192])
    except csv.Error:
        dialect = csv.excel

    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        doc.note("CSV contains no data rows")
        return doc

    width = max(len(r) for r in rows)
    header = [c.strip() or f"col{i + 1}" for i, c in enumerate(rows[0])]
    header += [f"col{i + 1}" for i in range(len(header), width)]

    lines = [
        "| " + " | ".join(_cell(c) for c in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        padded = list(row) + [""] * (width - len(row))
        lines.append("| " + " | ".join(_cell(c) for c in padded) + " |")

    doc.add(Block(kind="table", text="\n".join(lines)))
    if len(rows) > CSV_ROW_NOTE_THRESHOLD:
        doc.note(f"{len(rows):,} rows rendered as a single Markdown table")
    return doc


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
