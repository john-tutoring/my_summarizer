"""DOCX extraction.

python-docx exposes paragraphs and tables but not their interleaved order, so
the document body's XML children are walked directly. Images are found by
their relationship id inside each paragraph, which is what keeps them attached
to the paragraph they actually sit in.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

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

HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)

# Legacy VML namespaces. python-docx does not register the `v` and `o`
# prefixes, so qn() raises KeyError on them — use Clark notation directly.
VML_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
OFFICE_TITLE = "{urn:schemas-microsoft-com:office:office}title"
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def extract(path: Path, cfg: Config) -> Document:
    try:
        source = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 - python-docx raises broadly
        raise RuntimeError(f"could not open DOCX: {exc}") from exc

    title = _core_title(source) or title_from_path(path)
    doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))
    namer = ImageNamer(doc.slug)

    heading_index = 0
    pending_caption_for: list[ImageRef] = []

    for element in source.element.body.iterchildren():
        if element.tag == qn("w:tbl"):
            pending_caption_for = []
            rendered = _table_markdown(Table(element, source))
            if rendered:
                doc.add(Block(kind="table", text=rendered))
            continue

        if element.tag != qn("w:p"):
            continue

        paragraph = Paragraph(element, source)
        text = paragraph.text.strip()
        level = _heading_level(paragraph)

        # A "Caption"-styled paragraph directly after an image describes it.
        if pending_caption_for and text and _is_caption_style(paragraph):
            for image in pending_caption_for:
                if not image.caption:
                    image.caption = text
            pending_caption_for = []
            continue
        pending_caption_for = []

        if level:
            heading_index += 1
            doc.add(Block(kind="heading", text=text, level=level))
        elif text:
            doc.add(Block(kind="text", text=text))

        locator = heading_locator(heading_index)
        for rid, caption, external in _image_refs(element):
            image = _build_image(source, namer.next(locator), rid, caption, external, doc)
            doc.add(Block(kind="image", image=image))
            pending_caption_for.append(image)

    if not doc.blocks:
        doc.note("document contains no readable content")
    doc.has_h1 = any(b.kind == "heading" and b.level == 1 for b in doc.blocks)
    return doc


def _core_title(source) -> str:
    try:
        return (source.core_properties.title or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _heading_level(paragraph: Paragraph) -> int:
    """Heading level from the paragraph style, or 0 for body text."""
    try:
        name = (paragraph.style.name or "").strip()
    except Exception:  # noqa: BLE001
        name = ""

    if name.lower() == "title":
        return 1
    match = HEADING_STYLE_RE.match(name)
    if match:
        return min(max(int(match.group(1)), 1), 6)

    # Styles are localized in non-English Word; outlineLvl is not.
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is not None:
        outline = p_pr.find(qn("w:outlineLvl"))
        if outline is not None:
            value = outline.get(qn("w:val"))
            if value is not None and value.isdigit() and int(value) < 9:
                return min(int(value) + 1, 6)
    return 0


def _is_caption_style(paragraph: Paragraph) -> bool:
    try:
        return (paragraph.style.name or "").strip().lower().startswith("caption")
    except Exception:  # noqa: BLE001
        return False


def _image_refs(element) -> Iterator[tuple[str, str, bool]]:
    """Yield (relationship id, caption, is_external) for images in order."""
    for blip in element.findall(".//" + qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        external = False
        if not rid:
            rid = blip.get(qn("r:link"))
            external = True
        if rid:
            yield rid, _drawing_caption(blip), external

    # Legacy VML images (older Word documents).
    for data in element.findall(".//" + VML_IMAGEDATA):
        rid = data.get(REL_ID)
        if rid:
            yield rid, (data.get(OFFICE_TITLE) or "").strip(), False


def _drawing_caption(blip) -> str:
    """Alt text from the enclosing drawing's docPr element."""
    node: Any = blip
    for _ in range(8):  # walk up out of the blip fill into the drawing wrapper
        node = node.getparent()
        if node is None:
            return ""
        for doc_pr in node.findall(".//" + qn("wp:docPr")):
            caption = (doc_pr.get("descr") or "").strip()
            if caption:
                return caption
            name = (doc_pr.get("name") or "").strip()
            # Word autogenerates names like "Picture 3"; those say nothing.
            if name and not re.fullmatch(r"(picture|image|graphic)\s*\d*", name, re.IGNORECASE):
                return name
            return ""
    return ""


def _build_image(
    source, image_id: str, rid: str, caption: str, external: bool, doc: Document
) -> ImageRef:
    image = ImageRef(id=image_id, caption=caption)
    if external:
        image.reason = "linked (not embedded) image not downloaded"
        return image

    try:
        part = source.part.related_parts[rid]
    except KeyError:
        image.reason = "embedded image not found in document package"
        doc.note(f"{image.id}: relationship {rid} could not be resolved")
        return image

    ext = Path(str(part.partname)).suffix.lower() or ".png"
    image.ext = ext
    try:
        image.data = part.blob
    except Exception as exc:  # noqa: BLE001
        image.reason = f"could not read embedded image ({exc})"
    return image


def _table_markdown(table: Table) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [c.text.replace("|", "\\|").replace("\n", " ").strip() for c in row.cells]
        if any(cells):
            rows.append(cells)
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
