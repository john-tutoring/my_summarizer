"""RTF extraction — text only.

RTF has no converter in MarkItDown (it falls through to plain text and emits
raw control words). Full RTF parsing is a large job for a format that is
mostly legacy, so this is a deliberately small control-word stripper: it
recovers paragraph text and drops formatting and embedded images. The document
carries a note saying so.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import Config
from ..model import Block, Document, slugify, title_from_path

# Groups whose entire contents are metadata or binary, not body text.
SKIP_GROUPS = (
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "themedata",
    "colorschememapping",
    "latentstyles",
    "datastore",
    "generator",
    "listtable",
    "listoverridetable",
    "rsidtbl",
    "xmlnstbl",
)

CONTROL_RE = re.compile(r"\\([a-zA-Z]+)(-?\d+)?[ ]?|\\([^a-zA-Z])|([{}])|([^\\{}]+)")
ESCAPES = {"\\": "\\", "{": "{", "}": "}", "~": "\u00a0", "-": "", "_": "-"}
PARAGRAPH_BREAKS = {"par", "pard", "line", "sect", "page"}


def extract(path: Path, cfg: Config) -> Document:
    raw = path.read_bytes().decode("latin-1", errors="replace")
    if not raw.lstrip().startswith("{\\rtf"):
        raise RuntimeError("not an RTF file (missing \\rtf header)")

    title = title_from_path(path)
    doc = Document(source=path, title=title, slug=slugify(title, path.stem or "document"))

    for paragraph in _paragraphs(raw):
        doc.add(Block(kind="text", text=paragraph))

    if not doc.blocks:
        doc.note("no readable text found in RTF")
    else:
        doc.note("RTF converted as plain text; formatting and images are not recovered")
    return doc


def _paragraphs(raw: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    depth = 0
    skip_until_depth: int | None = None
    # Unicode runs are followed by a fallback character to discard.
    skip_chars = 0

    for match in CONTROL_RE.finditer(raw):
        word, param, symbol, brace, text = match.groups()

        if brace == "{":
            depth += 1
            continue
        if brace == "}":
            if skip_until_depth is not None and depth <= skip_until_depth:
                skip_until_depth = None
            depth -= 1
            continue

        if skip_until_depth is not None:
            continue

        if word is not None:
            if word in SKIP_GROUPS:
                skip_until_depth = depth - 1
                continue
            if word in PARAGRAPH_BREAKS:
                joined = "".join(current).strip()
                if joined:
                    out.append(joined)
                current = []
                continue
            if word == "u" and param is not None:
                code = int(param)
                current.append(chr(code if code >= 0 else code + 65536))
                skip_chars = 1
                continue
            if word == "tab":
                current.append("\t")
            continue

        if symbol is not None:
            if symbol == "'":
                continue  # hex escape marker; the two hex digits arrive as text
            current.append(ESCAPES.get(symbol, ""))
            continue

        if text is not None:
            if skip_chars:
                text = text[skip_chars:]
                skip_chars = 0
            current.append(text)

    joined = "".join(current).strip()
    if joined:
        out.append(joined)
    return [re.sub(r"[ \t]{2,}", " ", p) for p in out if p.strip()]
