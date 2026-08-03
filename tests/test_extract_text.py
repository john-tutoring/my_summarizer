"""Plain text, Markdown passthrough, and CSV."""

from __future__ import annotations

import shutil

import pytest

import docgen
from doc2md.extract import extract
from doc2md.render import to_markdown


# --- plain text ------------------------------------------------------------


def test_txt_splits_on_blank_lines(docs, cfg):
    doc = extract(docs["txt"], cfg)
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["text", "text", "text"]
    assert doc.blocks[0].text == "The Quick Report"


def test_txt_title_comes_from_the_filename(docs, cfg):
    doc = extract(docs["txt"], cfg)
    assert doc.title == "notes"
    assert doc.slug == "notes"


def test_empty_txt_is_noted_not_crashed(isolated_cwd, cfg):
    empty = isolated_cwd / "empty.txt"
    empty.write_text("")
    doc = extract(empty, cfg)
    assert doc.blocks == []
    assert any("empty" in n for n in doc.notes)


def test_latin1_fallback_for_undecodable_bytes(isolated_cwd, cfg):
    weird = isolated_cwd / "weird.txt"
    weird.write_bytes(b"caf\xe9 not utf-8\n")
    doc = extract(weird, cfg)
    assert "caf" in doc.blocks[0].text  # decoded rather than raising


# --- markdown --------------------------------------------------------------


@pytest.fixture
def md_workdir(docs, isolated_cwd):
    shutil.copy(docs["markdown"], isolated_cwd / "guide.md")
    shutil.copy(docs["fig1_png"], isolated_cwd / "fig1.png")
    return isolated_cwd


def test_markdown_title_from_first_h1(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    assert doc.title == "User Guide"
    assert doc.has_h1 is True


def test_markdown_is_passed_through_as_one_raw_block(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    assert [b.kind for b in doc.blocks] == ["raw"]


def test_markdown_local_image_is_extracted_with_bytes(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    local = [i for i in doc.images if i.extracted]
    assert len(local) == 1
    assert local[0].id.endswith("-img01")
    assert local[0].caption == "the first figure"


def test_markdown_remote_and_missing_images_become_placeholders(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    reasons = sorted(i.reason for i in doc.images if not i.extracted)
    assert reasons == ["linked image file not found", "remote image not downloaded"]


def test_markdown_image_locator_tracks_the_preceding_heading(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    ids = [i.id for i in doc.images]
    assert ids[0].endswith("h01-img01")  # under "# User Guide"
    assert ids[1].endswith("h02-img01")  # under "## Setup"
    assert ids[2].endswith("h02-img02")


def test_markdown_fenced_hash_is_not_treated_as_a_heading(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    rendered = to_markdown(doc)
    # The comment inside the fence must survive untouched and must not have
    # bumped the heading counter (which would shift image locators).
    assert "# this is a comment, not a heading" in rendered
    assert all("h03" not in i.id for i in doc.images)


def test_markdown_without_h1_gets_a_title_prepended(isolated_cwd, cfg):
    target = isolated_cwd / "nohead.md"
    target.write_text("## Only A Subheading\n\nbody text\n")
    doc = extract(target, cfg)
    assert doc.has_h1 is False
    assert to_markdown(doc).startswith("# Only A Subheading")


def test_markdown_image_missing_file_is_noted(md_workdir, cfg):
    doc = extract(md_workdir / "guide.md", cfg)
    assert any("nope.png" in n for n in doc.notes)


# --- csv -------------------------------------------------------------------


def test_csv_renders_a_markdown_table(docs, cfg):
    doc = extract(docs["csv"], cfg)
    assert [b.kind for b in doc.blocks] == ["table"]
    lines = doc.blocks[0].text.splitlines()
    assert lines[0] == "| name | qty | note |"
    assert lines[1] == "| --- | --- | --- |"


def test_csv_quoted_comma_stays_in_one_cell(docs, cfg):
    doc = extract(docs["csv"], cfg)
    assert "| widget | 3 | has, comma |" in doc.blocks[0].text


def test_csv_pipes_are_escaped(isolated_cwd, cfg):
    target = isolated_cwd / "pipes.csv"
    target.write_text("a,b\nx|y,z\n")
    doc = extract(target, cfg)
    assert r"x\|y" in doc.blocks[0].text


def test_csv_ragged_rows_are_padded(isolated_cwd, cfg):
    target = isolated_cwd / "ragged.csv"
    target.write_text("a,b,c\n1,2\n")
    doc = extract(target, cfg)
    assert "| 1 | 2 |  |" in doc.blocks[0].text


def test_empty_csv_is_noted(isolated_cwd, cfg):
    target = isolated_cwd / "empty.csv"
    target.write_text("\n\n")
    doc = extract(target, cfg)
    assert doc.blocks == []
    assert any("no data" in n for n in doc.notes)


def test_large_csv_gets_a_note_but_still_renders(isolated_cwd, cfg):
    target = isolated_cwd / "big.csv"
    target.write_text("a,b\n" + "1,2\n" * 2500)
    doc = extract(target, cfg)
    assert doc.blocks[0].text.count("\n") > 2000
    assert any("rows rendered" in n for n in doc.notes)
