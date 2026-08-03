"""Turn a `Document` into Markdown on disk. The only module that writes files.

The anchor invariant this module maintains, and that `--check` verifies:
every image occurrence appears in the Markdown carrying its ID, whether or not
the bytes were extracted. An extracted image is a normal Markdown image link
pointing into `images/`; a skipped one is an italic placeholder. Either way the
ID is greppable and marks the exact position the image held in the source.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .model import Block, Document, ImageRef

IMAGES_DIRNAME = "images"
MAX_CAPTION_LEN = 200


@dataclass
class RenderResult:
    markdown_path: Path
    images_dir: Path | None
    images_written: int = 0
    placeholders: int = 0
    notes: list[str] = field(default_factory=list)


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _clean_caption(caption: str) -> str:
    """Make caption text safe for a Markdown alt slot and readable inline."""
    caption = re.sub(r"\s+", " ", caption or "").strip()
    caption = caption.replace("[", "").replace("]", "")
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[: MAX_CAPTION_LEN - 1].rstrip() + "…"
    return caption


def image_markdown(image: ImageRef) -> str:
    """Markdown for one image occurrence. The ID always appears."""
    caption = _clean_caption(image.caption)
    if image.extracted:
        alt = f"{image.id}: {caption}" if caption else image.id
        return f"![{alt}]({IMAGES_DIRNAME}/{image.filename})"

    detail = image.reason or "image not extracted"
    tail = f": {caption}" if caption else ""
    return f"*[{image.id} — {detail}{tail}]*"


def _block_markdown(block: Block) -> str:
    if block.kind == "raw":
        return block.text
    if block.kind == "heading":
        level = min(max(block.level or 1, 1), 6)
        return f"{'#' * level} {block.text.strip()}"
    if block.kind == "image":
        return image_markdown(block.image) if block.image else ""
    if block.kind == "code":
        fence = "```"
        # Widen the fence if the code itself contains a triple backtick.
        while fence in block.text:
            fence += "`"
        return f"{fence}{block.lang}\n{block.text.rstrip()}\n{fence}"
    # "text" and "table" are already Markdown-shaped.
    return block.text.strip()


def to_markdown(doc: Document) -> str:
    """Render the document body to a Markdown string."""
    parts: list[str] = []

    # Give the document a title only when it has no H1 of its own, so we never
    # introduce a competing top level that would confuse chapter detection.
    has_h1 = doc.has_h1 or any(b.kind == "heading" and b.level == 1 for b in doc.blocks)
    if not has_h1:
        parts.append(f"# {doc.title.strip()}")

    for block in doc.blocks:
        rendered = _block_markdown(block)
        if rendered.strip():
            parts.append(rendered)

    return "\n\n".join(parts).rstrip() + "\n"


def _apply_size_limits(doc: Document, cfg: Config) -> None:
    """Demote images we won't write to placeholders, before rendering.

    Done here rather than in each extractor so the policy lives in one place
    and every format gets it identically.
    """
    demoted: list[ImageRef] = []
    for image in doc.images:
        if image.data is None:
            continue
        if not cfg.extract_images:
            image.data = None
            image.reason = "image extraction disabled"
            demoted.append(image)
        elif len(image.data) > cfg.max_image_bytes:
            size = _format_bytes(len(image.data))
            image.data = None
            image.reason = f"image too large ({size})"
            doc.note(f"{image.id}: skipped, {size} exceeds max_image_bytes")
            demoted.append(image)

    if demoted:
        _rewrite_demoted_anchors(doc, demoted)


def _rewrite_demoted_anchors(doc: Document, demoted: list[ImageRef]) -> None:
    """Fix anchors already baked into "raw" block text.

    Markdown, HTML, and EPUB render their image anchors during extraction, so
    a demotion here would otherwise leave a link pointing at a file that never
    gets written. Block-based images are unaffected: they are rendered from the
    ImageRef later and pick up the change on their own.
    """
    for block in doc.blocks:
        if block.kind != "raw":
            continue
        text = block.text
        for image in demoted:
            target = re.escape(f"{IMAGES_DIRNAME}/{image.filename}")
            pattern = re.compile(r"!\[[^\]]*\]\(" + target + r"\)")
            # A lambda replacement avoids backslash-escape processing.
            text = pattern.sub(lambda _m, img=image: image_markdown(img), text)
        block.text = text


def write_document(doc: Document, dest: Path, cfg: Config, *, force: bool = False) -> RenderResult:
    """Write `<dest>/<slug>.md` plus `<dest>/images/`.

    `dest` is the per-document output directory, created here.
    """
    if dest.exists():
        if not force:
            raise FileExistsError(
                f"{dest} already exists (use --force to overwrite)"
            )
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    _apply_size_limits(doc, cfg)

    dest.mkdir(parents=True, exist_ok=True)
    markdown_path = dest / f"{doc.slug}.md"
    markdown_path.write_text(to_markdown(doc), encoding="utf-8")

    images = doc.images
    to_write = [img for img in images if img.extracted]
    images_dir: Path | None = None
    if to_write:
        images_dir = dest / IMAGES_DIRNAME
        images_dir.mkdir(exist_ok=True)
        for image in to_write:
            (images_dir / image.filename).write_bytes(image.data)

    return RenderResult(
        markdown_path=markdown_path,
        images_dir=images_dir,
        images_written=len(to_write),
        placeholders=len(images) - len(to_write),
        notes=list(doc.notes),
    )


# --- --check support -------------------------------------------------------

ANCHOR_LINK_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(" + IMAGES_DIRNAME + r"/(?P<file>[^)]+)\)"
)
ANCHOR_PLACEHOLDER_RE = re.compile(r"\*\[(?P<id>[\w.-]+) — [^\]]*\]\*")


def check_anchors(markdown_path: Path) -> list[str]:
    """Verify the anchor invariant in both directions.

    Returns a list of problems; empty means the document is consistent.
    """
    problems: list[str] = []
    text = markdown_path.read_text(encoding="utf-8")
    images_dir = markdown_path.parent / IMAGES_DIRNAME

    linked = {m.group("file") for m in ANCHOR_LINK_RE.finditer(text)}
    placeholders = {m.group("id") for m in ANCHOR_PLACEHOLDER_RE.finditer(text)}

    for filename in sorted(linked):
        if not (images_dir / filename).is_file():
            problems.append(f"anchor references missing file: {IMAGES_DIRNAME}/{filename}")

    on_disk = {p.name for p in images_dir.iterdir() if p.is_file()} if images_dir.is_dir() else set()
    for filename in sorted(on_disk - linked):
        problems.append(f"orphan image file with no anchor: {IMAGES_DIRNAME}/{filename}")

    linked_ids = {Path(f).stem for f in linked}
    for image_id in sorted(placeholders & linked_ids):
        problems.append(f"{image_id} appears as both a link and a placeholder")

    return problems
