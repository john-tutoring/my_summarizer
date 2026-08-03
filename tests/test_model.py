"""doc2md.model — slugs, image IDs, locators."""

from __future__ import annotations

import pytest

from doc2md.model import (
    MAX_SLUG_LEN,
    NO_LOCATOR,
    Block,
    Document,
    ImageNamer,
    ImageRef,
    chapter_locator,
    heading_locator,
    page_locator,
    slide_locator,
    slugify,
    title_from_path,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Annual Review 2026", "annual-review-2026"),
        ("  Spaces   Everywhere  ", "spaces-everywhere"),
        ("Punctuation!?*&: Here", "punctuation-here"),
        ("Café Naïve Résumé", "cafe-naive-resume"),
        ("under_scores_and-dashes", "under-scores-and-dashes"),
        ("Multiple---Dashes", "multiple-dashes"),
        ("---leading and trailing---", "leading-and-trailing"),
    ],
)
def test_slugify_normalizes(raw, expected):
    assert slugify(raw) == expected


def test_slugify_falls_back_when_nothing_survives():
    assert slugify("!!!") == "document"
    assert slugify("", fallback="custom") == "custom"
    # Unicode that ASCII-folds to nothing must also fall back, not return "".
    assert slugify("日本語") == "document"


def test_slugify_caps_length_without_trailing_dash():
    slug = slugify("word " * 60)
    assert len(slug) <= MAX_SLUG_LEN
    assert not slug.endswith("-")


def test_locators_are_zero_padded():
    assert page_locator(12) == "p012"
    assert page_locator(7) == "p007"
    assert slide_locator(3) == "s03"
    assert chapter_locator(7) == "ch07"
    assert heading_locator(4) == "h04"


def test_image_namer_resets_counter_per_locator():
    namer = ImageNamer("report")
    assert namer.next("p001") == "report-p001-img01"
    assert namer.next("p001") == "report-p001-img02"
    assert namer.next("p002") == "report-p002-img01"
    # Returning to an earlier locator restarts too: IDs stay positional, and a
    # format that revisits a locator is not something we need to renumber for.
    assert namer.next("p001") == "report-p001-img01"


def test_image_namer_default_locator():
    assert ImageNamer("notes").next() == f"notes-{NO_LOCATOR}-img01"


def test_imageref_filename_and_extracted_flag():
    ref = ImageRef(id="x-p001-img01", ext=".jpg")
    assert ref.filename == "x-p001-img01.jpg"
    assert ref.extracted is False
    ref.data = b"bytes"
    assert ref.extracted is True


def test_document_images_covers_blocks_and_inline():
    doc = Document(source=None, title="t", slug="t")
    block_image = ImageRef(id="t-h00-img01")
    inline_image = ImageRef(id="t-h00-img02")
    doc.add(Block(kind="image", image=block_image))
    doc.add(Block(kind="text", text="no image here"))
    doc.register_inline_image(inline_image)

    assert doc.images == [block_image, inline_image]


def test_document_note_deduplicates():
    doc = Document(source=None, title="t", slug="t")
    doc.note("same")
    doc.note("same")
    doc.note("other")
    assert doc.notes == ["same", "other"]


@pytest.mark.parametrize(
    "name, expected",
    [("annual_report.pdf", "annual report"), ("my-notes.txt", "my notes"), ("a.b.docx", "a.b")],
)
def test_title_from_path(tmp_path, name, expected):
    assert title_from_path(tmp_path / name) == expected
