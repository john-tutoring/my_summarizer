"""MarkItDown fallback — text only, and honest about it."""

from __future__ import annotations

import pytest

from doc2md.extract import extract


@pytest.fixture
def xlsx_doc(docs, cfg):
    return extract(docs["xlsx"], cfg)


def test_spreadsheet_content_is_recovered(xlsx_doc):
    text = "\n".join(b.text for b in xlsx_doc.blocks)
    assert "Region" in text
    assert "North" in text
    assert "120" in text


def test_output_is_a_single_raw_block(xlsx_doc):
    assert [b.kind for b in xlsx_doc.blocks] == ["raw"]


def test_fallback_records_its_limitation(xlsx_doc):
    assert any("MarkItDown fallback" in n for n in xlsx_doc.notes)
    assert any("images are not anchored" in n for n in xlsx_doc.notes)


def test_no_images_are_claimed(xlsx_doc):
    assert xlsx_doc.images == []


def test_garbage_with_a_known_extension_does_not_crash(isolated_cwd, cfg):
    """MarkItDown sniffs content, so a corrupt .xlsx converts as plain text.

    Surprising but deliberate on its side; what matters here is that doc2md
    surfaces it rather than raising.
    """
    target = isolated_cwd / "broken.xlsx"
    target.write_bytes(b"definitely not a spreadsheet")
    doc = extract(target, cfg)
    assert doc.slug


def test_converter_failure_is_wrapped_in_a_readable_error(isolated_cwd, cfg, monkeypatch):
    """When MarkItDown does raise, the message must name the cause."""
    from doc2md.extract import fallback as fallback_mod

    class Exploding:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, *args, **kwargs):
            raise ValueError("unsupported internal format")

    monkeypatch.setattr(fallback_mod, "MarkItDown", Exploding)

    target = isolated_cwd / "boom.xlsx"
    target.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="MarkItDown could not convert"):
        extract(target, cfg)


def test_empty_conversion_is_noted(isolated_cwd, cfg, monkeypatch):
    from doc2md.extract import fallback as fallback_mod

    class Blank:
        def __init__(self, *args, **kwargs):
            pass

        def convert(self, *args, **kwargs):
            return type("R", (), {"title": "", "text_content": "   "})()

    monkeypatch.setattr(fallback_mod, "MarkItDown", Blank)

    target = isolated_cwd / "blank.xlsx"
    target.write_bytes(b"x")
    doc = extract(target, cfg)
    assert doc.blocks == []
    assert any("no readable content" in n for n in doc.notes)


def test_json_goes_through_the_fallback(isolated_cwd, cfg):
    target = isolated_cwd / "data.json"
    target.write_text('{"key": "value", "n": 1}')
    doc = extract(target, cfg)
    assert "value" in "\n".join(b.text for b in doc.blocks)


def test_formats_markitdown_cannot_convert_are_not_routed_to_it():
    """RTF and ODT have real extractors; routing them here would emit raw markup."""
    from doc2md.extract import _FALLBACK

    for extension in (".rtf", ".odt", ".odp", ".ods", ".doc", ".ppt"):
        assert extension not in _FALLBACK
