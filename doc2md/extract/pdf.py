"""PDF extraction via PyMuPDF.

Reading order comes from sorting each page's blocks by position, which is what
puts images between the right paragraphs rather than batched at the end. Text
and image blocks come out of the same `get_text("dict")` call, already
interleaved, so no separate merge step is needed.

Headings are inferred from font size relative to the document's body size, with
the PDF outline (TOC) overriding levels where it has an entry. Many PDFs have
neither, in which case the document simply has no headings and docsum falls
back to word-count chunking.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import pymupdf

from ..config import Config
from ..model import (
    Block,
    Document,
    ImageNamer,
    ImageRef,
    page_locator,
    slugify,
    title_from_path,
)

CAPTION_RE = re.compile(
    r"^\s*(fig(?:ure)?|table|image|chart|exhibit|plate|diagram|scheme)\b", re.IGNORECASE
)
HEADING_SIZE_RATIO = 1.15
MAX_HEADING_CHARS = 200
# An image on this fraction of pages is page furniture (letterhead, watermark).
FURNITURE_PAGE_FRACTION = 0.5
FURNITURE_MIN_PAGES = 3
Y_TOLERANCE = 3.0


def extract(path: Path, cfg: Config) -> Document:
    try:
        pdf = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - pymupdf raises a variety of types
        raise RuntimeError(f"could not open PDF: {exc}") from exc

    with pdf:
        if pdf.is_encrypted and not pdf.authenticate(""):
            raise RuntimeError("PDF is password protected")

        title = (pdf.metadata or {}).get("title") or ""
        title = title.strip() or title_from_path(path)
        doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))
        namer = ImageNamer(doc.slug)

        body_size = _body_font_size(pdf)
        size_levels = _heading_levels(pdf, body_size)
        toc_titles = _toc_titles(pdf)
        furniture = _furniture_xrefs(pdf)

        for page_index in range(pdf.page_count):
            page = pdf[page_index]
            try:
                items = _page_items(pdf, page, page_index, furniture, cfg)
            except Exception as exc:  # noqa: BLE001 - skip a bad page, keep the rest
                doc.note(f"page {page_index + 1}: skipped ({exc})")
                continue
            _attach_captions(items)
            _emit(doc, items, namer, page_index, body_size, size_levels, toc_titles, cfg)

        if not doc.blocks:
            doc.note("no extractable text found — this may be a scanned PDF (no OCR is performed)")

        return doc


# --- font analysis ---------------------------------------------------------


def _iter_span_sizes(pdf: pymupdf.Document, limit_pages: int = 50):
    """Yield (rounded size, char count) over a sample of pages."""
    for page_index in range(min(pdf.page_count, limit_pages)):
        try:
            data = pdf[page_index].get_text("dict")
        except Exception:  # noqa: BLE001
            continue
        for block in data.get("blocks", ()):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    text = span.get("text", "").strip()
                    if text:
                        yield round(float(span.get("size", 0)), 1), len(text)


def _body_font_size(pdf: pymupdf.Document) -> float:
    """Most common font size weighted by characters — the body text size."""
    weights: Counter[float] = Counter()
    for size, count in _iter_span_sizes(pdf):
        weights[size] += count
    if not weights:
        return 0.0
    return weights.most_common(1)[0][0]


def _heading_levels(pdf: pymupdf.Document, body_size: float) -> dict[float, int]:
    """Map each distinct heading-sized font to a Markdown heading level."""
    if body_size <= 0:
        return {}
    sizes = {size for size, _ in _iter_span_sizes(pdf) if size >= body_size * HEADING_SIZE_RATIO}
    return {size: min(i + 1, 6) for i, size in enumerate(sorted(sizes, reverse=True))}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _toc_titles(pdf: pymupdf.Document) -> dict[str, int]:
    """Normalized outline title -> level, used to override inferred levels."""
    try:
        toc = pdf.get_toc() or []
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for entry in toc:
        if len(entry) < 2:
            continue
        level, title = entry[0], str(entry[1])
        key = _normalize(title)
        if key:
            out.setdefault(key, min(max(int(level), 1), 6))
    return out


def _furniture_xrefs(pdf: pymupdf.Document) -> set[int]:
    """Images repeated across most pages: letterheads, watermarks, rules."""
    if pdf.page_count < FURNITURE_MIN_PAGES:
        return set()
    seen: Counter[int] = Counter()
    for page_index in range(pdf.page_count):
        try:
            infos = pdf[page_index].get_image_info(xrefs=True)
        except Exception:  # noqa: BLE001
            continue
        for xref in {info.get("xref", 0) for info in infos}:
            if xref:
                seen[xref] += 1
    threshold = max(FURNITURE_MIN_PAGES, int(pdf.page_count * FURNITURE_PAGE_FRACTION))
    return {xref for xref, n in seen.items() if n >= threshold}


# --- per-page assembly -----------------------------------------------------


def _block_text(block: dict[str, Any]) -> tuple[str, float]:
    """Join a text block's lines, de-hyphenating. Returns (text, max size)."""
    lines: list[str] = []
    max_size = 0.0
    for line in block.get("lines", ()):
        parts = []
        for span in line.get("spans", ()):
            parts.append(span.get("text", ""))
            max_size = max(max_size, float(span.get("size", 0)))
        lines.append("".join(parts).rstrip())

    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-") and not out.endswith("--"):
            out = out[:-1] + line.lstrip()
        else:
            out = f"{out} {line.lstrip()}"
    return re.sub(r"[ \t]{2,}", " ", out).strip(), round(max_size, 1)


def _page_items(
    pdf: pymupdf.Document,
    page: pymupdf.Page,
    page_index: int,
    furniture: set[int],
    cfg: Config,
) -> list[dict[str, Any]]:
    """Ordered list of {kind: text|image, ...} for one page."""
    data = page.get_text("dict")
    xref_by_bbox = _xref_map(page)

    items: list[dict[str, Any]] = []
    for block in data.get("blocks", ()):
        bbox = block.get("bbox") or (0, 0, 0, 0)
        if block.get("type") == 0:
            text, size = _block_text(block)
            if text:
                items.append({"kind": "text", "text": text, "size": size, "bbox": bbox})
        elif block.get("type") == 1:
            item = _image_item(block, bbox, xref_by_bbox, furniture, cfg)
            if item is not None:
                items.append(item)

    items.sort(key=lambda it: (round(it["bbox"][1] / Y_TOLERANCE), it["bbox"][0]))
    return items


def _xref_map(page: pymupdf.Page) -> dict[tuple[int, ...], int]:
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[tuple[int, ...], int] = {}
    for info in infos:
        bbox = info.get("bbox")
        xref = info.get("xref", 0)
        if bbox and xref:
            out[tuple(round(v) for v in bbox)] = xref
    return out


def _image_item(
    block: dict[str, Any],
    bbox: tuple[float, ...],
    xref_by_bbox: dict[tuple[int, ...], int],
    furniture: set[int],
    cfg: Config,
) -> Optional[dict[str, Any]]:
    xref = xref_by_bbox.get(tuple(round(v) for v in bbox), 0)
    if xref and xref in furniture:
        return None  # repeated page furniture, not document content

    width = int(block.get("width") or 0)
    height = int(block.get("height") or 0)
    if width * height < cfg.min_image_pixels:
        return None

    data = block.get("image")
    ext = block.get("ext") or "png"
    reason = ""
    if not cfg.extract_images:
        data, reason = None, "image extraction disabled"
    elif not data:
        reason = "image data unavailable in source"

    return {
        "kind": "image",
        "bbox": bbox,
        "data": bytes(data) if data else None,
        "ext": f".{ext.lstrip('.')}",
        "reason": reason,
        "caption": "",
    }


def _attach_captions(items: list[dict[str, Any]]) -> None:
    """Give each image the adjacent 'Figure N...' line, if there is one.

    The caption line is marked consumed so it is not also emitted as a body
    paragraph — the image anchor takes its place, which is how a figure and
    its caption should convert.
    """
    for i, item in enumerate(items):
        if item["kind"] != "image":
            continue
        for neighbour in (i + 1, i - 1):
            if not 0 <= neighbour < len(items):
                continue
            other = items[neighbour]
            if other.get("consumed") or other["kind"] != "text":
                continue
            if CAPTION_RE.match(other["text"]):
                item["caption"] = other["text"]
                other["consumed"] = True
                break


def _emit(
    doc: Document,
    items: list[dict[str, Any]],
    namer: ImageNamer,
    page_index: int,
    body_size: float,
    size_levels: dict[float, int],
    toc_titles: dict[str, int],
    cfg: Config,
) -> None:
    locator = page_locator(page_index + 1)
    for item in items:
        if item.get("consumed"):
            continue
        if item["kind"] == "image":
            image = ImageRef(
                id=namer.next(locator),
                ext=item["ext"],
                data=item["data"],
                caption=item["caption"],
                reason=item["reason"],
            )
            doc.add(Block(kind="image", image=image))
            continue

        text = item["text"]
        level = _heading_level(text, item["size"], body_size, size_levels, toc_titles)
        if level:
            doc.add(Block(kind="heading", text=text, level=level))
        else:
            doc.add(Block(kind="text", text=text))


def _heading_level(
    text: str,
    size: float,
    body_size: float,
    size_levels: dict[float, int],
    toc_titles: dict[str, int],
) -> int:
    """Heading level for a text block, or 0 if it is body text."""
    toc_level = toc_titles.get(_normalize(text))
    if toc_level:
        return toc_level
    if len(text) > MAX_HEADING_CHARS or body_size <= 0:
        return 0
    if size < body_size * HEADING_SIZE_RATIO:
        return 0
    return size_levels.get(size, 1)
