"""RTF extraction — the deliberately minimal control-word stripper."""

from __future__ import annotations

import pytest

from doc2md.extract import extract
from doc2md.extract.rtf import _paragraphs


@pytest.fixture
def rtf_doc(docs, cfg):
    return extract(docs["rtf"], cfg)


def test_paragraphs_are_recovered(rtf_doc):
    texts = [b.text for b in rtf_doc.blocks]
    assert "First paragraph of the document." in texts
    assert "Third paragraph after the picture." in texts


def test_control_words_do_not_leak_into_the_text(rtf_doc):
    joined = " ".join(b.text for b in rtf_doc.blocks)
    for marker in ("\\rtf1", "\\pard", "\\par", "\\fonttbl", "froman"):
        assert marker not in joined


def test_font_and_colour_tables_are_skipped(rtf_doc):
    joined = " ".join(b.text for b in rtf_doc.blocks)
    assert "Times New Roman" not in joined
    assert "Arial" not in joined


def test_picture_group_is_skipped(rtf_doc):
    joined = " ".join(b.text for b in rtf_doc.blocks)
    assert "89504e47" not in joined


def test_unicode_escape_is_decoded(rtf_doc):
    joined = " ".join(b.text for b in rtf_doc.blocks)
    assert "café" in joined


def test_limitation_is_recorded_as_a_note(rtf_doc):
    assert any("formatting and images are not recovered" in n for n in rtf_doc.notes)


def test_no_images_are_claimed(rtf_doc):
    assert rtf_doc.images == []


def test_non_rtf_file_is_rejected(isolated_cwd, cfg):
    target = isolated_cwd / "fake.rtf"
    target.write_text("just plain text, no rtf header")
    with pytest.raises(RuntimeError, match=r"not an RTF file"):
        extract(target, cfg)


def test_empty_rtf_is_noted(isolated_cwd, cfg):
    target = isolated_cwd / "empty.rtf"
    target.write_text(r"{\rtf1\ansi}")
    doc = extract(target, cfg)
    assert any("no readable text" in n for n in doc.notes)


@pytest.mark.parametrize(
    "source, expected",
    [
        (r"{\rtf1 plain}", ["plain"]),
        (r"{\rtf1 one\par two}", ["one", "two"]),
        (r"{\rtf1 a\tab b}", ["a\tb"]),
        (r"{\rtf1 brace \{ here}", ["brace { here"]),
        (r"{\rtf1 {\fonttbl junk} kept}", ["kept"]),
    ],
)
def test_paragraph_parsing(source, expected):
    assert _paragraphs(source) == expected
