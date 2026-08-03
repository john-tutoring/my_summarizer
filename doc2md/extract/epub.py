"""EPUB extraction.

Chapters come from the spine, which is the book's real reading order, so image
locators are chapter numbers: `moby-dick-ch07-img01`. Image bytes are read out
of the EPUB package itself, so there is nothing to fetch.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import ebooklib
from ebooklib import epub

from ..config import Config
from ..model import Block, Document, ImageNamer, chapter_locator, slugify, title_from_path
from . import _html


def extract(path: Path, cfg: Config) -> Document:
    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except Exception as exc:  # noqa: BLE001 - ebooklib raises broadly
        raise RuntimeError(f"could not open EPUB: {exc}") from exc

    title = _book_title(book) or title_from_path(path)
    doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))
    namer = ImageNamer(doc.slug)

    media = _media_index(book)
    toc_titles = _toc_titles(book)
    chapters = _spine_documents(book)
    if not chapters:
        doc.note("EPUB spine is empty; no chapters found")
        return doc

    saw_h1 = False
    for number, item in enumerate(chapters, 1):
        try:
            html = item.get_content().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - skip a bad chapter, keep the book
            doc.note(f"chapter {number}: skipped ({exc})")
            continue

        resolver = _make_resolver(media, item.file_name, doc)
        markdown, has_h1, first_heading = _html.convert(
            html,
            doc=doc,
            namer=namer,
            resolver=resolver,
            locator=chapter_locator(number),
        )
        if not markdown.strip():
            continue

        # Give a chapter a heading of its own when its markup had none, so the
        # book still splits into chapters for summarization. Many EPUBs set
        # chapter titles in the TOC only, with the body text carrying no
        # heading tag at all, so prefer the TOC title over a bare ordinal.
        if not first_heading:
            heading = _lookup_title(toc_titles, item.file_name) or f"Chapter {number}"
            doc.add(Block(kind="heading", text=heading, level=1))
            saw_h1 = True
        saw_h1 = saw_h1 or has_h1
        doc.add(Block(kind="raw", text=markdown))

    doc.has_h1 = saw_h1
    if not doc.blocks:
        doc.note("no readable content found in EPUB")
    return doc


def _book_title(book) -> str:
    try:
        entries = book.get_metadata("DC", "title")
    except Exception:  # noqa: BLE001
        return ""
    if entries and entries[0] and entries[0][0]:
        return str(entries[0][0]).strip()
    return ""


def _href_key(href: str) -> str:
    """Normalize a TOC or spine href so the two can be matched."""
    href = unquote((href or "").split("#", 1)[0]).lstrip("/")
    return posixpath.normpath(href) if href else ""


def _toc_titles(book) -> dict[str, str]:
    """Flatten the EPUB's TOC into {normalized href: title}.

    The TOC is a nested mix of Link objects and (Section, [children]) tuples.
    Nesting is discarded deliberately: chapters are the unit we want to split
    on, so promoting parts above them would produce far coarser sections.
    """
    titles: dict[str, str] = {}

    def visit(entry) -> None:
        if isinstance(entry, (tuple, list)):
            for part in entry:
                visit(part)
            return
        href = _href_key(getattr(entry, "href", "") or "")
        title = (getattr(entry, "title", "") or "").strip()
        if href and title:
            titles.setdefault(href, title)
            titles.setdefault(posixpath.basename(href), title)

    try:
        visit(book.toc or [])
    except Exception:  # noqa: BLE001 - a malformed TOC just means no titles
        return {}
    return titles


def _lookup_title(titles: dict[str, str], file_name: str) -> str:
    key = _href_key(file_name)
    return titles.get(key) or titles.get(posixpath.basename(key), "")


def _spine_documents(book) -> list:
    """Spine order, falling back to whatever documents exist."""
    by_id = {item.get_id(): item for item in book.get_items()}
    ordered = []
    for entry in book.spine or []:
        item_id = entry[0] if isinstance(entry, (tuple, list)) else entry
        item = by_id.get(item_id)
        if item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT:
            ordered.append(item)
    if ordered:
        return ordered
    return [i for i in book.get_items() if i.get_type() == ebooklib.ITEM_DOCUMENT]


def _media_index(book) -> dict[str, bytes]:
    """Every non-document item keyed by its normalized in-package path."""
    index: dict[str, bytes] = {}
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            continue
        name = (item.file_name or "").lstrip("/")
        if not name:
            continue
        try:
            index[posixpath.normpath(name)] = item.get_content()
        except Exception:  # noqa: BLE001 - a missing member is not fatal
            continue
    return index


def _make_resolver(media: dict[str, bytes], chapter_href: str, doc: Document):
    base = posixpath.dirname((chapter_href or "").lstrip("/"))

    def resolve(src: str) -> tuple[Optional[bytes], str, str]:
        if src.startswith("data:"):
            return None, ".png", "inline data URI not extracted"
        if urlparse(src).scheme:
            return None, ".png", "remote image not downloaded"

        rel = unquote(urlparse(src).path)
        candidates = [
            posixpath.normpath(posixpath.join(base, rel)),
            posixpath.normpath(rel.lstrip("/")),
        ]
        ext = Path(rel).suffix.lower() or ".png"
        for key in candidates:
            if key in media:
                return media[key], ext, ""
        # Last resort: match on basename, since some EPUBs have inconsistent paths.
        target = posixpath.basename(rel)
        for key, data in media.items():
            if posixpath.basename(key) == target:
                return data, ext, ""
        return None, ext, "image not found in EPUB package"

    return resolve
