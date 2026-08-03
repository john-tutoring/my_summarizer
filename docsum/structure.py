"""Split a Markdown document into summarizable sections.

The whole point of this module is that each section can be summarized from its
own text alone. Detection is fence-aware, so `#` comments inside code blocks
are never mistaken for headings.

Three outcomes:

  single    short enough to summarize in one call
  chapters  long, and has real heading structure to split on
  chunks    long, but has no usable headings — split on word count instead,
            so we never send an oversized single request
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

# Sections shorter than this are folded into a neighbour rather than spending
# an API call on a heading with almost nothing under it.
MIN_SECTION_WORDS = 50
CHUNK_TARGET_WORDS = 8000

Mode = Literal["single", "chapters", "chunks"]


@dataclass
class Section:
    title: str
    level: int
    body: str

    @property
    def word_count(self) -> int:
        return word_count(self.body)

    @property
    def text(self) -> str:
        """Heading plus body — what actually gets sent to the model."""
        if not self.title:
            return self.body
        return f"{'#' * max(self.level, 1)} {self.title}\n\n{self.body}".strip()


@dataclass
class Plan:
    mode: Mode
    sections: list[Section]
    total_words: int

    @property
    def is_chaptered(self) -> bool:
        return self.mode in ("chapters", "chunks")


def word_count(text: str) -> int:
    return len(text.split())


@dataclass
class _Heading:
    line: int
    level: int
    title: str


def find_headings(markdown: str) -> list[_Heading]:
    """ATX headings outside fenced code blocks, in document order."""
    headings: list[_Heading] = []
    in_fence = False
    fence_char = ""

    for number, line in enumerate(markdown.splitlines()):
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
        if match:
            headings.append(_Heading(number, len(match.group(1)), match.group(2).strip()))
    return headings


def _split_level(headings: list[_Heading]) -> int:
    """Shallowest heading level that yields at least two sections."""
    for level in range(1, 7):
        if sum(1 for h in headings if h.level == level) >= 2:
            return level
    return 0


def _sections_at_level(markdown: str, headings: list[_Heading], level: int) -> list[Section]:
    lines = markdown.splitlines()
    splits = [h for h in headings if h.level == level]

    sections: list[Section] = []
    front = "\n".join(lines[: splits[0].line]).strip()
    if word_count(front) >= MIN_SECTION_WORDS:
        sections.append(Section(title="Front matter", level=level, body=front))
        front = ""

    for index, heading in enumerate(splits):
        end = splits[index + 1].line if index + 1 < len(splits) else len(lines)
        body = "\n".join(lines[heading.line + 1 : end]).strip()
        if front:
            body = f"{front}\n\n{body}".strip()
            front = ""
        sections.append(Section(title=heading.title, level=level, body=body))

    return _merge_thin(sections)


def _merge_thin(sections: list[Section]) -> list[Section]:
    """Fold near-empty sections into a neighbour.

    A document title immediately followed by the first real chapter heading
    would otherwise produce a section with no content and waste a call.
    """
    merged: list[Section] = []
    carry = ""

    for section in sections:
        body = f"{carry}\n\n{section.body}".strip() if carry else section.body
        carry = ""
        if word_count(body) < MIN_SECTION_WORDS:
            # Push this heading and its scrap of text onto the next section.
            carry = f"{'#' * max(section.level, 1)} {section.title}\n\n{body}".strip()
            continue
        merged.append(Section(title=section.title, level=section.level, body=body))

    if carry:
        if merged:
            merged[-1].body = f"{merged[-1].body}\n\n{carry}".strip()
        else:
            merged.append(Section(title="", level=1, body=carry))
    return merged


def _chunk(markdown: str, target_words: int = CHUNK_TARGET_WORDS) -> list[Section]:
    """Split on paragraph boundaries into roughly equal word-count parts."""
    paragraphs = [p for p in re.split(r"\n\s*\n", markdown) if p.strip()]
    sections: list[Section] = []
    current: list[str] = []
    current_words = 0

    for paragraph in paragraphs:
        words = word_count(paragraph)
        if current and current_words + words > target_words:
            sections.append(
                Section(title=f"Part {len(sections) + 1}", level=1, body="\n\n".join(current))
            )
            current, current_words = [], 0
        current.append(paragraph)
        current_words += words

    if current:
        sections.append(
            Section(title=f"Part {len(sections) + 1}", level=1, body="\n\n".join(current))
        )

    # Fold a small trailing remainder into the previous part rather than
    # spending a whole call on it.
    if len(sections) > 1 and sections[-1].word_count < target_words * 0.25:
        tail = sections.pop()
        sections[-1].body = f"{sections[-1].body}\n\n{tail.body}"
    return sections


def plan(markdown: str, *, chapter_threshold_words: int) -> Plan:
    """Decide how to summarize this document."""
    markdown = markdown.strip()
    total = word_count(markdown)

    if total < chapter_threshold_words:
        return Plan(mode="single", sections=[Section("", 1, markdown)], total_words=total)

    headings = find_headings(markdown)
    level = _split_level(headings)
    if level:
        sections = _sections_at_level(markdown, headings, level)
        if len(sections) >= 2:
            return Plan(mode="chapters", sections=sections, total_words=total)

    return Plan(mode="chunks", sections=_chunk(markdown), total_words=total)
