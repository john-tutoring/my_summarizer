"""EPUB extraction — spine order, TOC titles, in-package image resolution."""

from __future__ import annotations

import pytest

from doc2md.extract import extract
from doc2md.extract import epub as epub_mod


@pytest.fixture
def epub_doc(docs, cfg):
    return extract(docs["epub"], cfg)


def test_title_from_dublin_core_metadata(epub_doc):
    assert epub_doc.title == "A Short Book"
    assert epub_doc.slug == "a-short-book"


def test_chapters_appear_in_spine_order(epub_doc):
    headings = [b.text for b in epub_doc.blocks if b.kind == "heading"]
    raw = "\n".join(b.text for b in epub_doc.blocks if b.kind == "raw")
    assert "The Beginning" in raw or "The Beginning" in headings
    assert raw.index("Once upon a time") < raw.index("Things conclude")


def test_chapter_without_a_heading_tag_takes_its_toc_title(epub_doc):
    """The middle chapter has no <h1>; its title must come from the nav.

    Before this worked, every such chapter came out as a bare "Chapter N".
    """
    headings = [b.text for b in epub_doc.blocks if b.kind == "heading"]
    assert "A Titled Chapter" in headings
    assert not any(h.startswith("Chapter ") for h in headings)


def test_embedded_image_is_read_from_the_package(epub_doc):
    extracted = [i for i in epub_doc.images if i.extracted]
    assert len(extracted) == 1
    assert extracted[0].data.startswith(b"\x89PNG")


def test_image_locator_is_the_chapter_number(epub_doc):
    assert epub_doc.images[0].id.endswith("-ch01-img01")


def test_alt_text_wins_over_figcaption(epub_doc):
    assert epub_doc.images[0].caption == "A green plate"


def test_missing_image_becomes_a_placeholder_with_a_note(epub_doc):
    missing = [i for i in epub_doc.images if not i.extracted]
    assert len(missing) == 1
    assert "not found in EPUB package" in missing[0].reason
    assert any("missing.png" in n for n in epub_doc.notes)


def test_corrupt_epub_raises_runtime_error(isolated_cwd, cfg):
    broken = isolated_cwd / "broken.epub"
    broken.write_bytes(b"not a zip")
    with pytest.raises(RuntimeError, match="could not open EPUB"):
        extract(broken, cfg)


# --- TOC helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    "href, expected",
    [
        ("c1.xhtml", "c1.xhtml"),
        ("c1.xhtml#frag", "c1.xhtml"),
        ("/OEBPS/c1.xhtml", "OEBPS/c1.xhtml"),
        ("OEBPS/../c1.xhtml", "c1.xhtml"),
        ("a%20b.xhtml", "a b.xhtml"),
        ("", ""),
    ],
)
def test_href_key_normalization(href, expected):
    assert epub_mod._href_key(href) == expected


def test_toc_titles_flattens_nested_sections(docs):
    from ebooklib import epub as eb

    book = eb.read_epub(str(docs["epub"]), options={"ignore_ncx": True})
    titles = epub_mod._toc_titles(book)
    assert titles["c1.xhtml"] == "The Beginning"
    assert titles["c2.xhtml"] == "A Titled Chapter"
    assert titles["c3.xhtml"] == "The End"


def test_lookup_title_falls_back_to_basename():
    titles = {"c2.xhtml": "Found"}
    assert epub_mod._lookup_title(titles, "OEBPS/c2.xhtml") == "Found"


def test_lookup_title_returns_empty_when_absent():
    assert epub_mod._lookup_title({}, "nope.xhtml") == ""
