"""doc2md.render — Markdown emission, size-limit demotion, anchor checking."""

from __future__ import annotations

import pytest

from doc2md.config import Config
from doc2md.model import Block, Document, ImageRef
from doc2md.render import (
    IMAGES_DIRNAME,
    check_anchors,
    image_markdown,
    to_markdown,
    write_document,
)


def doc_with(*blocks, title="Doc", slug="doc", **kwargs) -> Document:
    document = Document(source=None, title=title, slug=slug, **kwargs)
    for block in blocks:
        document.add(block)
    return document


# --- image markdown --------------------------------------------------------


def test_extracted_image_is_a_link_carrying_the_id():
    ref = ImageRef(id="doc-p001-img01", ext=".png", data=b"x", caption="Revenue")
    assert image_markdown(ref) == "![doc-p001-img01: Revenue](images/doc-p001-img01.png)"


def test_extracted_image_without_caption_still_carries_the_id():
    ref = ImageRef(id="doc-p001-img01", ext=".png", data=b"x")
    assert image_markdown(ref) == "![doc-p001-img01](images/doc-p001-img01.png)"


def test_placeholder_carries_id_reason_and_caption():
    ref = ImageRef(id="doc-h02-img01", caption="diagram", reason="remote image not downloaded")
    rendered = image_markdown(ref)
    assert rendered.startswith("*[doc-h02-img01 —")
    assert "remote image not downloaded" in rendered
    assert "diagram" in rendered
    assert "images/" not in rendered  # nothing to link to


def test_caption_is_sanitized_for_the_alt_slot():
    ref = ImageRef(id="i", data=b"x", caption="has [brackets]\nand\n newlines")
    rendered = image_markdown(ref)
    assert "[" not in rendered[3:rendered.index("]")]  # no brackets inside alt
    assert "\n" not in rendered


def test_long_caption_is_truncated():
    ref = ImageRef(id="i", data=b"x", caption="w " * 400)
    assert len(image_markdown(ref)) < 300
    assert "…" in image_markdown(ref)


# --- document rendering ----------------------------------------------------


def test_title_prepended_only_when_no_h1_exists():
    without = to_markdown(doc_with(Block(kind="text", text="body"), title="My Title"))
    assert without.startswith("# My Title")

    with_h1 = to_markdown(
        doc_with(Block(kind="heading", level=1, text="Real"), title="My Title")
    )
    assert with_h1.startswith("# Real")
    assert "# My Title" not in with_h1


def test_has_h1_flag_suppresses_the_prepended_title():
    """The Markdown passthrough path reports its own H1 via this flag."""
    document = doc_with(Block(kind="raw", text="# Inside Raw\n\nbody"), title="Other", has_h1=True)
    assert to_markdown(document).startswith("# Inside Raw")


def test_heading_levels_are_clamped():
    assert to_markdown(doc_with(Block(kind="heading", level=9, text="Deep"))).count("#") >= 6
    rendered = to_markdown(doc_with(Block(kind="heading", level=0, text="Zero")))
    assert "# Zero" in rendered


def test_code_fence_widens_around_embedded_backticks():
    document = doc_with(Block(kind="code", lang="py", text="print('```')"))
    rendered = to_markdown(document)
    assert "````py" in rendered


def test_empty_blocks_are_dropped():
    document = doc_with(
        Block(kind="text", text="   "),
        Block(kind="text", text="kept"),
    )
    assert to_markdown(document).count("kept") == 1


# --- writing ---------------------------------------------------------------


def test_write_document_lays_out_markdown_and_images(tmp_path, cfg):
    ref = ImageRef(id="doc-p001-img01", ext=".png", data=b"pngbytes")
    document = doc_with(Block(kind="image", image=ref))
    result = write_document(document, tmp_path / "doc", cfg)

    # The ID appears twice by design: once as alt text, once in the path.
    assert "![doc-p001-img01](images/doc-p001-img01.png)" in result.markdown_path.read_text()
    assert (result.images_dir / "doc-p001-img01.png").read_bytes() == b"pngbytes"
    assert result.images_written == 1
    assert result.placeholders == 0


def test_images_dir_is_not_created_when_nothing_extracted(tmp_path, cfg):
    document = doc_with(Block(kind="image", image=ImageRef(id="i", reason="skipped")))
    result = write_document(document, tmp_path / "doc", cfg)
    assert result.images_dir is None
    assert not (tmp_path / "doc" / IMAGES_DIRNAME).exists()


def test_existing_destination_requires_force(tmp_path, cfg):
    dest = tmp_path / "doc"
    dest.mkdir()
    document = doc_with(Block(kind="text", text="hi"))

    with pytest.raises(FileExistsError):
        write_document(document, dest, cfg)

    result = write_document(document, dest, cfg, force=True)
    assert result.markdown_path.exists()


def test_force_replaces_a_file_where_a_directory_is_needed(tmp_path, cfg):
    dest = tmp_path / "doc"
    dest.write_text("i am a file")
    result = write_document(doc_with(Block(kind="text", text="hi")), dest, cfg, force=True)
    assert result.markdown_path.exists()


# --- demotion --------------------------------------------------------------


def test_extract_images_false_demotes_to_placeholder(tmp_path):
    cfg = Config(extract_images=False)
    ref = ImageRef(id="doc-p001-img01", ext=".png", data=b"x")
    result = write_document(doc_with(Block(kind="image", image=ref)), tmp_path / "d", cfg)

    text = result.markdown_path.read_text()
    assert "image extraction disabled" in text
    assert "images/" not in text
    assert result.images_written == 0


def test_oversized_image_demoted_with_a_readable_size(tmp_path):
    cfg = Config(max_image_bytes=10)
    ref = ImageRef(id="doc-p001-img01", ext=".png", data=b"x" * 5000)
    document = doc_with(Block(kind="image", image=ref))
    result = write_document(document, tmp_path / "d", cfg)

    text = result.markdown_path.read_text()
    assert "image too large" in text
    assert "4.9 KB" in text  # not "0.0 MB"
    assert any("exceeds max_image_bytes" in note for note in result.notes)


def test_demotion_rewrites_anchors_already_baked_into_raw_text(tmp_path):
    """Markdown/HTML/EPUB render anchors during extraction, before limits apply.

    Without the rewrite the raw text would still link to a file that never
    gets written — which is exactly what --check catches.
    """
    cfg = Config(extract_images=False)
    ref = ImageRef(id="doc-h01-img01", ext=".png", data=b"x", caption="chart")
    document = doc_with(
        Block(kind="raw", text="before\n\n![doc-h01-img01: chart](images/doc-h01-img01.png)\n\nafter"),
        has_h1=False,
    )
    document.register_inline_image(ref)

    result = write_document(document, tmp_path / "d", cfg)
    text = result.markdown_path.read_text()

    assert "](images/doc-h01-img01.png)" not in text
    assert "*[doc-h01-img01 — image extraction disabled: chart]*" in text
    assert check_anchors(result.markdown_path) == []


# --- check_anchors ---------------------------------------------------------


def test_check_passes_on_a_consistent_document(tmp_path, cfg):
    document = doc_with(
        Block(kind="image", image=ImageRef(id="d-p001-img01", ext=".png", data=b"x")),
        Block(kind="image", image=ImageRef(id="d-p001-img02", reason="skipped")),
    )
    result = write_document(document, tmp_path / "d", cfg)
    assert check_anchors(result.markdown_path) == []


def test_check_reports_anchor_pointing_at_a_missing_file(tmp_path, cfg):
    document = doc_with(Block(kind="image", image=ImageRef(id="d-p001-img01", ext=".png", data=b"x")))
    result = write_document(document, tmp_path / "d", cfg)
    (result.images_dir / "d-p001-img01.png").unlink()

    problems = check_anchors(result.markdown_path)
    assert any("missing file" in p for p in problems)


def test_check_reports_orphan_image_file(tmp_path, cfg):
    document = doc_with(Block(kind="image", image=ImageRef(id="d-p001-img01", ext=".png", data=b"x")))
    result = write_document(document, tmp_path / "d", cfg)
    (result.images_dir / "stray.png").write_bytes(b"x")

    problems = check_anchors(result.markdown_path)
    assert any("orphan" in p for p in problems)


def test_check_reports_id_that_is_both_link_and_placeholder(tmp_path, cfg):
    document = doc_with(Block(kind="image", image=ImageRef(id="d-p001-img01", ext=".png", data=b"x")))
    result = write_document(document, tmp_path / "d", cfg)
    with result.markdown_path.open("a") as handle:
        handle.write("\n\n*[d-p001-img01 — image not extracted]*\n")

    problems = check_anchors(result.markdown_path)
    assert any("both a link and a placeholder" in p for p in problems)
