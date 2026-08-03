"""HTML extraction — markdownify integration and local-only image policy."""

from __future__ import annotations

import shutil

import pytest

from doc2md.config import Config
from doc2md.extract import extract


@pytest.fixture
def html_workdir(docs, isolated_cwd):
    shutil.copy(docs["html"], isolated_cwd / "page.html")
    shutil.copy(docs["chart_png"], isolated_cwd / "chart.png")
    (isolated_cwd.parent / "outside.png").write_bytes(b"secret")
    return isolated_cwd


@pytest.fixture
def html_doc(html_workdir, cfg):
    return extract(html_workdir / "page.html", cfg)


def test_title_from_the_title_tag(html_doc):
    assert html_doc.title == "The Web Page"
    assert html_doc.slug == "the-web-page"


def test_body_is_converted_to_markdown(html_doc):
    text = html_doc.blocks[0].text
    assert "# Main Heading" in text
    assert "**bold**" in text
    assert "[link](#x)" in text
    assert "- one" in text


def test_script_tags_are_stripped(html_doc):
    assert "alert(" not in html_doc.blocks[0].text


def test_local_image_is_extracted(html_doc):
    extracted = [i for i in html_doc.images if i.extracted]
    assert len(extracted) == 1
    assert extracted[0].caption == "local chart"


def test_remote_image_is_never_fetched(html_doc):
    remote = [i for i in html_doc.images if i.reason == "remote image not downloaded"]
    assert len(remote) == 1
    assert remote[0].caption == "a remote image"


def test_missing_local_image_becomes_a_placeholder(html_doc):
    missing = [i for i in html_doc.images if "not found" in i.reason]
    assert len(missing) == 1


def test_path_traversal_is_refused(html_doc):
    """A ../ src must not read a file outside the document's directory."""
    escaped = [i for i in html_doc.images if "outside document directory" in i.reason]
    assert len(escaped) == 1
    assert all(i.data != b"secret" for i in html_doc.images)


def test_image_locators_track_headings(html_doc):
    ids = [i.id for i in html_doc.images]
    assert ids[0].endswith("-h01-img01")  # after <h1>
    assert ids[1].endswith("-h02-img01")  # after <h2>


def test_anchors_replace_the_tokens_in_the_output(html_doc):
    """The placeholder tokens are an implementation detail and must not leak."""
    text = html_doc.blocks[0].text
    assert "DOC2MDIMG" not in text
    assert text.count("![") + text.count("*[") == 4


def test_data_uri_is_not_extracted(isolated_cwd, cfg):
    target = isolated_cwd / "data.html"
    target.write_text("<html><body><h1>T</h1><img src='data:image/png;base64,AAAA' alt='inline'></body></html>")
    doc = extract(target, cfg)
    assert doc.images[0].reason == "inline data URI not extracted"


def test_image_with_no_src(isolated_cwd, cfg):
    target = isolated_cwd / "nosrc.html"
    target.write_text("<html><body><h1>T</h1><img alt='nothing'></body></html>")
    doc = extract(target, cfg)
    assert doc.images[0].reason == "image tag has no source"


def test_figcaption_used_when_alt_is_absent(isolated_cwd, docs, cfg):
    shutil.copy(docs["chart_png"], isolated_cwd / "c.png")
    target = isolated_cwd / "fig.html"
    target.write_text(
        "<html><body><h1>T</h1><figure><img src='c.png'>"
        "<figcaption>The caption</figcaption></figure></body></html>"
    )
    doc = extract(target, cfg)
    assert doc.images[0].caption == "The caption"


def test_extract_images_false_still_records_positions(html_workdir):
    doc = extract(html_workdir / "page.html", Config(extract_images=False))
    assert len(doc.images) == 4


def test_html_without_title_falls_back_to_filename(isolated_cwd, cfg):
    target = isolated_cwd / "untitled.html"
    target.write_text("<html><body><p>no title tag</p></body></html>")
    doc = extract(target, cfg)
    assert doc.title == "untitled"


def test_empty_html_is_noted(isolated_cwd, cfg):
    target = isolated_cwd / "blank.html"
    target.write_text("<html><body></body></html>")
    doc = extract(target, cfg)
    assert any("no readable content" in n for n in doc.notes)
