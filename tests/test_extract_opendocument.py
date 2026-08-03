"""OpenDocument extraction — .odt / .odp / .ods."""

from __future__ import annotations

import zipfile

import pytest

import docgen
from doc2md.extract import extract


@pytest.fixture
def odt_doc(docs, cfg):
    return extract(docs["odt"], cfg)


def test_title_from_meta_xml(odt_doc):
    assert odt_doc.title == "Quarterly Memo"


def test_outline_levels_become_heading_levels(odt_doc):
    levels = {b.text: b.level for b in odt_doc.blocks if b.kind == "heading"}
    assert levels["Overview"] == 1
    assert levels["Detail"] == 2


def test_image_lands_between_the_surrounding_paragraphs(odt_doc):
    sequence = [
        ("image", "") if b.kind == "image" else (b.kind, b.text) for b in odt_doc.blocks
    ]
    kinds = [k for k, _ in sequence]
    texts = [t for _, t in sequence]
    index = kinds.index("image")
    assert "Opening paragraph before the chart." in texts[:index]
    assert "Paragraph after the chart." in texts[index + 1:]


def test_image_bytes_come_from_the_package(odt_doc):
    extracted = [i for i in odt_doc.images if i.extracted]
    assert len(extracted) == 1
    assert extracted[0].data.startswith(b"\x89PNG")
    assert extracted[0].ext == ".png"


def test_missing_picture_becomes_a_placeholder(odt_doc):
    missing = [i for i in odt_doc.images if not i.extracted]
    assert len(missing) == 1
    assert "not found in package" in missing[0].reason


def test_frame_name_is_used_as_a_caption(odt_doc):
    assert odt_doc.images[0].caption == "Chart frame"


def test_table_renders_as_markdown(odt_doc):
    tables = [b.text for b in odt_doc.blocks if b.kind == "table"]
    assert len(tables) == 1
    assert "| Region | Total |" in tables[0]
    assert "| North | 120 |" in tables[0]


def test_list_items_are_kept_as_text(odt_doc):
    body = " ".join(b.text for b in odt_doc.blocks if b.kind == "text")
    assert "A list item" in body


def test_image_locator_uses_the_preceding_heading(odt_doc):
    assert odt_doc.images[0].id.endswith("-h01-img01")


def test_not_a_zip_raises_runtime_error(isolated_cwd, cfg):
    broken = isolated_cwd / "broken.odt"
    broken.write_bytes(b"not a zip")
    with pytest.raises(RuntimeError, match="could not open OpenDocument"):
        extract(broken, cfg)


def test_zip_without_content_xml_raises(isolated_cwd, cfg):
    target = isolated_cwd / "empty.odt"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
    with pytest.raises(RuntimeError, match="no content.xml"):
        extract(target, cfg)


def test_malformed_content_xml_raises(isolated_cwd, cfg):
    target = isolated_cwd / "bad.odt"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("content.xml", "<unclosed>")
    with pytest.raises(RuntimeError, match="malformed content.xml"):
        extract(target, cfg)


def test_presentation_pages_become_slide_headings(isolated_cwd, cfg):
    """ODP drives the draw:page branch and the slide locator."""
    content = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
  xmlns:xlink="http://www.w3.org/1999/xlink">
 <office:body><office:presentation>
  <draw:page draw:name="Opening">
   <text:p>Slide one body.</text:p>
   <text:p><draw:frame draw:name="Pic">
     <draw:image xlink:href="Pictures/chart.png"/></draw:frame></text:p>
  </draw:page>
  <draw:page draw:name="Closing"><text:p>Slide two body.</text:p></draw:page>
 </office:presentation></office:body>
</office:document-content>"""
    target = isolated_cwd / "deck.odp"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("content.xml", content)
        archive.writestr("Pictures/chart.png", docgen.png_bytes())

    doc = extract(target, cfg)
    headings = [b.text for b in doc.blocks if b.kind == "heading"]
    assert headings == ["Opening", "Closing"]
    assert doc.images[0].id.endswith("-s01-img01")


def test_repeated_columns_are_capped(isolated_cwd, cfg):
    """ODS pads rows to 1024 columns; that must not explode the table."""
    content = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
 <office:body><office:spreadsheet>
  <table:table><table:table-row>
   <table:table-cell><text:p>A</text:p></table:table-cell>
   <table:table-cell table:number-columns-repeated="1024"><text:p></text:p></table:table-cell>
  </table:table-row></table:table>
 </office:spreadsheet></office:body>
</office:document-content>"""
    target = isolated_cwd / "sheet.ods"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("content.xml", content)

    doc = extract(target, cfg)
    table = [b.text for b in doc.blocks if b.kind == "table"]
    if table:
        assert table[0].splitlines()[0].count("|") < 70
