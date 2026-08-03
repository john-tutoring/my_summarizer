"""PDF extraction — the format where positional anchoring is hardest to get right."""

from __future__ import annotations

import pytest

from doc2md.config import Config
from doc2md.extract import extract
from doc2md.extract import pdf as pdf_mod


@pytest.fixture
def pdf_doc(docs, cfg):
    return extract(docs["pdf"], cfg)


def kinds_and_text(doc):
    """Flatten to (kind, identifier) pairs for order assertions."""
    out = []
    for block in doc.blocks:
        if block.kind == "image":
            out.append(("image", block.image.id))
        else:
            out.append((block.kind, block.text[:40]))
    return out


# --- structure -------------------------------------------------------------


def test_title_comes_from_pdf_metadata(pdf_doc):
    assert pdf_doc.title == "Annual Review 2026"
    assert pdf_doc.slug == "annual-review-2026"


def test_toc_entries_become_headings(pdf_doc):
    headings = [b.text for b in pdf_doc.blocks if b.kind == "heading"]
    for expected in ("Introduction", "Results", "Conclusion"):
        assert expected in headings


def test_toc_level_is_applied(pdf_doc):
    levels = {b.text: b.level for b in pdf_doc.blocks if b.kind == "heading"}
    assert levels["Introduction"] == 1
    assert levels["Results"] == 1


def test_body_text_is_not_promoted_to_a_heading(pdf_doc):
    headings = [b.text for b in pdf_doc.blocks if b.kind == "heading"]
    assert "Opening paragraph before the figure." not in headings


# --- positional anchoring --------------------------------------------------


def test_image_sits_between_the_right_paragraphs(pdf_doc):
    """The whole point of the extractor: not batched at the end of the page."""
    sequence = kinds_and_text(pdf_doc)
    kinds = [k for k, _ in sequence]
    texts = [t for _, t in sequence]

    image_index = kinds.index("image")
    before = " ".join(texts[:image_index])
    after = " ".join(texts[image_index + 1:])

    assert "Opening paragraph before the figure." in before
    assert "Closing paragraph after the figure." in after


def test_page_locator_matches_the_page_the_image_was_on(pdf_doc):
    ids = [b.image.id for b in pdf_doc.blocks if b.kind == "image"]
    assert ids[0].endswith("-p001-img01")
    assert ids[1].endswith("-p002-img01")


def test_both_figures_are_extracted_with_bytes(pdf_doc):
    extracted = [i for i in pdf_doc.images if i.extracted]
    assert len(extracted) == 2
    assert all(i.data.startswith(b"\x89PNG") for i in extracted)


# --- captions --------------------------------------------------------------


def test_figure_caption_is_absorbed_into_the_anchor(pdf_doc):
    captions = [i.caption for i in pdf_doc.images]
    assert "Figure 1. Revenue by segment." in captions
    assert "Figure 2. Cost breakdown." in captions


def test_absorbed_caption_is_not_also_a_body_paragraph(pdf_doc):
    """A figure and its caption convert as one unit, not duplicated text."""
    body = [b.text for b in pdf_doc.blocks if b.kind in ("text", "heading")]
    assert "Figure 1. Revenue by segment." not in body


# --- image filtering -------------------------------------------------------


def test_repeated_logo_is_dropped_as_page_furniture(pdf_doc):
    """The 80x30 logo sits on all three pages; it is letterhead, not content."""
    assert len(pdf_doc.images) == 2  # only the two figures


def test_tiny_icon_is_below_the_pixel_floor(docs):
    """The 8x8 icon on page 1 is 64 px, far below the 10,000 px floor."""
    doc = extract(docs["pdf"], Config(min_image_pixels=10000))
    assert len(doc.images) == 2
    assert all(i.extracted for i in doc.images)


def test_lowering_the_pixel_floor_admits_the_icon(docs):
    """Confirms the pixel filter is what excluded it, not the furniture rule.

    The icon appears on one page only, so it is not page furniture; dropping
    the floor must let it through.
    """
    permissive = extract(docs["pdf"], Config(min_image_pixels=1))
    assert len(permissive.images) == 3
    assert any(i.id.endswith("-p001-img01") for i in permissive.images)


def test_furniture_rule_is_independent_of_the_pixel_floor(docs):
    """Even with no pixel floor, the logo on all three pages stays excluded."""
    permissive = extract(docs["pdf"], Config(min_image_pixels=1))
    # 3 images = 2 figures + 1 icon. The logo would add 3 more if not filtered.
    assert len(permissive.images) == 3


def test_extract_images_false_still_records_positions(docs):
    doc = extract(docs["pdf"], Config(extract_images=False))
    assert len(doc.images) == 2
    assert all(not i.extracted for i in doc.images)


# --- failure modes ---------------------------------------------------------


def test_encrypted_pdf_fails_with_a_readable_message(docs, cfg):
    with pytest.raises(RuntimeError, match="password protected"):
        extract(docs["pdf_encrypted"], cfg)


def test_corrupt_pdf_raises_runtime_error(isolated_cwd, cfg):
    broken = isolated_cwd / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(RuntimeError, match="could not open PDF"):
        extract(broken, cfg)


def test_pdf_with_no_text_is_noted_as_possibly_scanned(isolated_cwd, cfg):
    import pymupdf

    target = isolated_cwd / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(target))
    doc.close()

    result = extract(target, cfg)
    assert any("scanned" in n for n in result.notes)


# --- internals worth pinning ----------------------------------------------


def test_body_font_size_picks_the_most_common_size(docs):
    import pymupdf

    with pymupdf.open(docs["pdf"]) as handle:
        assert pdf_mod._body_font_size(handle) == pytest.approx(11.0)


def test_dehyphenation_joins_split_words():
    block = {
        "lines": [
            {"spans": [{"text": "hyphen-", "size": 11}]},
            {"spans": [{"text": "ated", "size": 11}]},
        ]
    }
    text, _ = pdf_mod._block_text(block)
    assert text == "hyphenated"


def test_lines_without_hyphen_are_space_joined():
    block = {
        "lines": [
            {"spans": [{"text": "first line", "size": 11}]},
            {"spans": [{"text": "second line", "size": 11}]},
        ]
    }
    text, _ = pdf_mod._block_text(block)
    assert text == "first line second line"


@pytest.mark.parametrize(
    "caption, matches",
    [
        ("Figure 3. A thing", True),
        ("figure 3", True),
        ("Table 1: results", True),
        ("Exhibit A", True),
        ("Diagram of the flow", True),
        ("The figure shows", False),
        ("Ordinary paragraph", False),
    ],
)
def test_caption_pattern(caption, matches):
    assert bool(pdf_mod.CAPTION_RE.match(caption)) is matches
