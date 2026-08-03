"""OpenDocument extraction: .odt, .odp, .ods.

MarkItDown has no OpenDocument converter — it falls through to plain text and
emits raw XML — so these are handled directly. The format is a zip whose
`content.xml` holds the body in document order, which makes positional image
anchoring straightforward: images are `draw:image` elements pointing at
members of the same zip.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterator, Optional

from lxml import etree

from ..config import Config
from ..model import (
    Block,
    Document,
    ImageNamer,
    ImageRef,
    heading_locator,
    slide_locator,
    slugify,
    title_from_path,
)

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}


def _q(name: str) -> str:
    prefix, _, local = name.partition(":")
    return f"{{{NS[prefix]}}}{local}"


def extract(path: Path, cfg: Config) -> Document:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RuntimeError(f"could not open OpenDocument file: {exc}") from exc

    with archive:
        try:
            content = archive.read("content.xml")
        except KeyError as exc:
            raise RuntimeError("not an OpenDocument file (no content.xml)") from exc

        try:
            root = etree.fromstring(content)
        except etree.XMLSyntaxError as exc:
            raise RuntimeError(f"malformed content.xml: {exc}") from exc

        title = _meta_title(archive) or title_from_path(path)
        doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))
        namer = ImageNamer(doc.slug)

        body = root.find(_q("office:body"))
        if body is None:
            doc.note("document body is empty")
            return doc

        state = {"heading_index": 0, "page": 0}
        for child in body:
            _walk(child, doc, namer, archive, state, cfg)

        if not doc.blocks:
            doc.note("no readable content found")
        doc.has_h1 = any(b.kind == "heading" and b.level == 1 for b in doc.blocks)
        return doc


def _meta_title(archive: zipfile.ZipFile) -> str:
    try:
        meta = etree.fromstring(archive.read("meta.xml"))
    except (KeyError, etree.XMLSyntaxError):
        return ""
    node = meta.find(f".//{_q('dc:title')}")
    return (node.text or "").strip() if node is not None else ""


def _walk(element, doc: Document, namer: ImageNamer, archive, state: dict, cfg: Config) -> None:
    """Recursive document-order walk, emitting blocks as it goes."""
    tag = element.tag

    if tag == _q("draw:page"):
        # A presentation slide.
        state["page"] += 1
        name = element.get(_q("draw:name")) or f"Slide {state['page']}"
        doc.add(Block(kind="heading", text=name, level=2))
        for child in element:
            _walk(child, doc, namer, archive, state, cfg)
        return

    if tag == _q("text:h"):
        text = _text_of(element)
        if text:
            level = element.get(_q("text:outline-level")) or "1"
            state["heading_index"] += 1
            doc.add(Block(kind="heading", text=text, level=_level(level)))
        _emit_images(element, doc, namer, archive, state, cfg)
        return

    if tag == _q("text:p"):
        text = _text_of(element)
        if text:
            doc.add(Block(kind="text", text=text))
        _emit_images(element, doc, namer, archive, state, cfg)
        return

    if tag in (_q("text:list"), _q("text:list-item")):
        for child in element:
            _walk(child, doc, namer, archive, state, cfg)
        return

    if tag == _q("table:table"):
        rendered = _table_markdown(element)
        if rendered:
            doc.add(Block(kind="table", text=rendered))
        _emit_images(element, doc, namer, archive, state, cfg)
        return

    for child in element:
        _walk(child, doc, namer, archive, state, cfg)


def _level(value: str) -> int:
    try:
        return min(max(int(value), 1), 6)
    except (TypeError, ValueError):
        return 1


def _text_of(element) -> str:
    parts: list[str] = []
    for node in element.iter():
        if node.tag == _q("text:tab"):
            parts.append("\t")
        elif node.tag == _q("text:line-break"):
            parts.append(" ")
        if node.text and node.tag != _q("text:tab"):
            parts.append(node.text)
        if node is not element and node.tail:
            parts.append(node.tail)
    return " ".join("".join(parts).split()).strip()


def _emit_images(element, doc: Document, namer: ImageNamer, archive, state: dict, cfg: Config) -> None:
    for image_el in element.iter(_q("draw:image")):
        locator = (
            slide_locator(state["page"])
            if state["page"]
            else heading_locator(state["heading_index"])
        )
        doc.add(
            Block(
                kind="image",
                image=_build_image(image_el, namer.next(locator), archive, doc),
            )
        )


def _build_image(image_el, image_id: str, archive, doc: Document) -> ImageRef:
    caption = _frame_caption(image_el)
    href = (image_el.get(_q("xlink:href")) or "").lstrip("./")
    image = ImageRef(id=image_id, caption=caption)

    if not href:
        image.reason = "image has no source"
        return image
    if "://" in href:
        image.reason = "remote image not downloaded"
        return image

    image.ext = Path(href).suffix.lower() or ".png"
    try:
        image.data = archive.read(href)
    except KeyError:
        image.reason = "image not found in package"
        doc.note(f"{image.id}: {image.reason} ({href})")
    except OSError as exc:
        image.reason = f"could not read image ({exc})"
    return image


def _frame_caption(image_el) -> str:
    """Alt text from the enclosing draw:frame, if the author supplied any."""
    frame = image_el.getparent()
    if frame is None:
        return ""
    for tag in ("svg:desc", "svg:title"):
        prefix, _, local = tag.partition(":")
        node = frame.find(f"{{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}}{local}")
        if node is not None and (node.text or "").strip():
            return node.text.strip()
    name = (frame.get(_q("draw:name")) or "").strip()
    return "" if name.lower().startswith(("image", "picture", "graphics")) else name


def _rows(table) -> Iterator[list[str]]:
    for row in table.iter(_q("table:table-row")):
        cells: list[str] = []
        for cell in row:
            if cell.tag not in (_q("table:table-cell"), _q("table:covered-table-cell")):
                continue
            text = " ".join(_text_of(p) for p in cell).strip()
            repeat = cell.get(_q("table:number-columns-repeated")) or "1"
            try:
                count = min(int(repeat), 64)  # guard against the 1024-column padding idiom
            except ValueError:
                count = 1
            cells.extend([text.replace("|", "\\|")] * count)
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            yield cells


def _table_markdown(table) -> str:
    rows = list(_rows(table))
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = [c or f"col{i + 1}" for i, c in enumerate(rows[0])]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)
