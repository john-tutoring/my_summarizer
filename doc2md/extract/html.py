"""HTML / XHTML extraction.

Only images that already live on the local filesystem are extracted. Remote
`src` values become placeholder anchors: conversion is offline by design, so a
URL is recorded as a position rather than fetched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from ..config import Config
from ..model import Block, Document, ImageNamer, slugify, title_from_path
from . import _html

URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def extract(path: Path, cfg: Config) -> Document:
    html = _read(path)
    doc = Document(source=path, title=title_from_path(path), slug="tmp")

    title = _document_title(html) or title_from_path(path)
    doc.title = title
    doc.slug = slugify(title, path.stem or "document")

    namer = ImageNamer(doc.slug)
    resolver = _make_resolver(path, doc, cfg)

    parser = "xml" if path.suffix.lower() == ".xhtml" else "html.parser"
    try:
        markdown, has_h1, _ = _html.convert(
            html, doc=doc, namer=namer, resolver=resolver, parser=parser
        )
    except Exception:  # noqa: BLE001 - fall back to the forgiving parser
        markdown, has_h1, _ = _html.convert(
            html, doc=doc, namer=namer, resolver=resolver, parser="html.parser"
        )

    doc.has_h1 = has_h1
    if markdown.strip():
        doc.add(Block(kind="raw", text=markdown))
    else:
        doc.note("no readable content found")
    return doc


def _document_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _make_resolver(source: Path, doc: Document, cfg: Config):
    base = source.parent

    def resolve(src: str) -> tuple[Optional[bytes], str, str]:
        if src.startswith("data:"):
            return None, ".png", "inline data URI not extracted"
        if URL_RE.match(src) or src.startswith("//"):
            return None, ".png", "remote image not downloaded"

        rel = unquote(urlparse(src).path)
        target = (base / rel).resolve()
        ext = target.suffix.lower() or ".png"

        try:
            # Do not follow paths that escape the document's own directory.
            target.relative_to(base.resolve())
        except ValueError:
            return None, ext, "linked image outside document directory"

        if not target.is_file():
            return None, ext, "linked image file not found"
        try:
            return target.read_bytes(), ext, ""
        except OSError as exc:
            return None, ext, f"could not read linked image ({exc.strerror or exc})"

    return resolve
