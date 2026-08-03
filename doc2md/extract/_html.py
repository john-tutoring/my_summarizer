"""Shared HTML -> Markdown conversion, used by the HTML and EPUB extractors.

Images are the reason this is hand-rolled rather than a single markdownify
call: each `<img>` is swapped for a unique token *before* conversion and the
token is swapped back for our anchor afterwards. That keeps every image at
exactly the position it occupied in the source while letting markdownify
handle the rest of the markup.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from bs4 import BeautifulSoup
from markdownify import markdownify

from ..model import Document, ImageNamer, ImageRef, heading_locator
from ..render import image_markdown

HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]

# Letters and digits only: markdownify escapes underscores in body text
# (`_` -> `\_`), which would corrupt the token before we substitute it back.
TOKEN_FMT = "@@DOC2MDIMG{}ZZ@@"
TOKEN_RE = re.compile(r"@@DOC2MDIMG(\d+)ZZ@@")

# (bytes, extension, reason-if-skipped)
Resolver = Callable[[str], tuple[Optional[bytes], str, str]]


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()


def convert(
    html: str,
    *,
    doc: Document,
    namer: ImageNamer,
    resolver: Resolver,
    locator: Optional[str] = None,
    parser: str = "html.parser",
) -> tuple[str, bool, str]:
    """Convert an HTML document to Markdown.

    `locator` fixes the image locator (EPUB uses the chapter number). When it
    is None, the locator tracks the nearest preceding heading, so IDs read as
    `slug-h03-img01`.

    Returns (markdown, has_h1, first_heading_text).
    """
    soup = BeautifulSoup(html, parser)
    _strip_noise(soup)

    body = soup.body or soup
    images: list[ImageRef] = []
    heading_index = 0
    first_heading = ""
    has_h1 = False

    # find_all returns document order, so headings and images interleave the
    # same way they do on the page.
    for tag in body.find_all(HEADING_TAGS + ["img"]):
        if tag.name != "img":
            heading_index += 1
            text = tag.get_text(" ", strip=True)
            if text and not first_heading:
                first_heading = text
            if tag.name == "h1":
                has_h1 = True
            continue

        image = _make_image(tag, doc, namer, resolver, locator, heading_index)
        images.append(image)
        tag.replace_with(TOKEN_FMT.format(len(images) - 1))

    markdown = markdownify(str(body), heading_style="ATX", bullets="-")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()

    def _swap(match: re.Match) -> str:
        return image_markdown(images[int(match.group(1))])

    markdown = TOKEN_RE.sub(_swap, markdown)

    for image in images:
        doc.register_inline_image(image)

    return markdown, has_h1, first_heading


def _make_image(
    tag,
    doc: Document,
    namer: ImageNamer,
    resolver: Resolver,
    locator: Optional[str],
    heading_index: int,
) -> ImageRef:
    caption = (tag.get("alt") or "").strip()
    if not caption:
        caption = _figcaption(tag)

    src = (tag.get("src") or tag.get("data-src") or "").strip()
    loc = locator if locator is not None else heading_locator(heading_index)
    image = ImageRef(id=namer.next(loc), caption=caption)

    if not src:
        image.reason = "image tag has no source"
        return image

    data, ext, reason = resolver(src)
    image.ext = ext or image.ext
    if data is None:
        image.reason = reason or "image not extracted"
        if "not found" in image.reason:
            doc.note(f"{image.id}: {image.reason} ({src})")
    else:
        image.data = data
    return image


def _figcaption(tag) -> str:
    """Caption from an enclosing <figure>, when the img has no alt text."""
    figure = tag.find_parent("figure")
    if figure is None:
        return ""
    caption = figure.find("figcaption")
    return caption.get_text(" ", strip=True) if caption else ""
