"""One test per bug actually hit during development.

Each of these represents a failure that shipped, or would have shipped, and was
found only by trying something specific. They are collected here rather than
scattered so the history stays legible.
"""

from __future__ import annotations

import shutil

import pytest

import docgen
from conftest import bad_request
from doc2md.config import Config as ExtractConfig
from doc2md.extract import extract
from doc2md.render import check_anchors, to_markdown, write_document
from docsum.client import SummarizeError
from docsum.config import Config as SummarizeConfig


def test_markdown_passthrough_does_not_duplicate_the_h1(docs, isolated_cwd):
    """render prepended a title because the passthrough H1 lives in a raw block.

    Output began "# User Guide\n\n# User Guide".
    """
    shutil.copy(docs["markdown"], isolated_cwd / "guide.md")
    shutil.copy(docs["fig1_png"], isolated_cwd / "fig1.png")

    doc = extract(isolated_cwd / "guide.md", ExtractConfig())
    rendered = to_markdown(doc)
    assert rendered.count("# User Guide") == 1


def test_html_image_token_survives_markdownify_escaping(docs, isolated_cwd):
    """markdownify escapes `_`, so the old @@DOC2MD_IMG_0@@ token became
    @@DOC2MD\\_IMG\\_0@@ and never matched — every HTML/EPUB image silently
    lost its anchor and the raw token leaked into the output.
    """
    shutil.copy(docs["html"], isolated_cwd / "page.html")
    shutil.copy(docs["chart_png"], isolated_cwd / "chart.png")

    doc = extract(isolated_cwd / "page.html", ExtractConfig())
    text = to_markdown(doc)

    assert "DOC2MDIMG" not in text
    assert "DOC2MD" not in text
    assert len(doc.images) == 4
    assert any("](images/" in text for _ in [1])


def test_bad_request_reaches_the_retry_instead_of_being_swallowed(fake_openai):
    """BadRequestError subclasses APIStatusError.

    The generic handler caught it first, so the parameter-fallback mechanism
    was dead code that would have failed on the first incompatible model.
    """
    client, fake = fake_openai(SummarizeConfig(model="o3"), reject=("reasoning_effort",))
    assert client.complete("s", "u") == "A summary."
    assert len(fake.calls) == 2, "the retry never happened"


def test_unfixable_bad_request_still_surfaces_cleanly(fake_openai):
    """The retry must not swallow genuine 400s either."""
    client, _ = fake_openai(raises=bad_request("Invalid value for 'messages'"))
    with pytest.raises(SummarizeError, match="request rejected"):
        client.complete("s", "u")


def test_disabled_images_rewrite_anchors_baked_into_raw_blocks(docs, isolated_cwd):
    """With extract_images: false the Markdown still linked to files that were
    never written, because the anchor text was rendered during extraction.
    --check caught it.
    """
    shutil.copy(docs["html"], isolated_cwd / "page.html")
    shutil.copy(docs["chart_png"], isolated_cwd / "chart.png")

    cfg = ExtractConfig(extract_images=False)
    doc = extract(isolated_cwd / "page.html", cfg)
    result = write_document(doc, isolated_cwd / "out", cfg)

    assert "](images/" not in result.markdown_path.read_text()
    assert check_anchors(result.markdown_path) == []


def test_docx_vml_namespace_does_not_raise_keyerror(docs):
    """qn("v:imagedata") raised KeyError: 'v' — python-docx does not register
    the legacy VML prefixes, so every DOCX failed to convert.
    """
    doc = extract(docs["docx"], ExtractConfig())
    assert doc.blocks  # got past the namespace lookup


def test_epub_chapter_uses_its_toc_title_not_an_ordinal(docs):
    """Chapters whose markup carries no heading came out as "Chapter 7".

    Real books put titles in the nav, so every heading was a meaningless
    ordinal and the summary inherited them.
    """
    doc = extract(docs["epub"], ExtractConfig())
    headings = [b.text for b in doc.blocks if b.kind == "heading"]
    assert "A Titled Chapter" in headings
    assert not any(h.startswith("Chapter ") for h in headings)


def test_short_section_is_not_summarized_into_something_longer(fake_openai):
    """min_words=150 asked for a 150-word "summary" of a 114-word section,
    producing output longer than the input and wasting a request.
    """
    short = docgen._SENTENCE * 4  # 100 words
    markdown = (
        "# Brief\n\n" + short + "\n\n"
        + "# One\n\n" + (docgen.PARAGRAPH + "\n\n") * 40
        + "# Two\n\n" + (docgen.PARAGRAPH + "\n\n") * 40
    )
    cfg = SummarizeConfig(min_words=150)
    client, fake = fake_openai(cfg)

    from docsum.runner import summarize

    result = summarize(markdown, "Book", cfg, client, verbose=False)
    assert len(fake.calls) == 3  # 2 chapters + overview, not 4
    assert "Lorem ipsum" in result.markdown  # kept verbatim


def test_slug_collision_between_two_documents_is_disambiguated(docs, isolated_cwd):
    """A .docx and its .odt export share a title, so the second overwrote or
    errored on the first's output directory.
    """
    from doc2md.cli import OUTPUT_DIRNAME, main

    shutil.copy(docs["docx"], isolated_cwd / "memo.docx")
    shutil.copy(docs["docx"], isolated_cwd / "duplicate.docx")

    assert main(["memo.docx", "duplicate.docx"]) == 0
    root = isolated_cwd / OUTPUT_DIRNAME
    assert (root / "quarterly-memo").is_dir()
    assert (root / "quarterly-memo-duplicate").is_dir()


def test_picker_finds_output_two_levels_down(isolated_cwd):
    """Moving output into outputs/ put it two levels below the cwd, but the
    picker still scanned one, so a bare `docsum` found nothing.
    """
    from docsum.cli import OUTPUT_DIRNAME, find_markdown

    produced = isolated_cwd / OUTPUT_DIRNAME / "report"
    produced.mkdir(parents=True)
    markdown = produced / "report.md"
    markdown.write_text("# R\n\nbody")

    assert markdown in find_markdown(isolated_cwd)


def test_rtf_body_survives_a_font_table(isolated_cwd):
    """The skip-group depth was off by one, so a \\fonttbl — present in every
    real RTF file — swallowed the entire document body.
    """
    target = isolated_cwd / "real.rtf"
    target.write_text(
        r"{\rtf1\ansi\deff0{\fonttbl{\f0\froman Times;}}"
        r"\pard This is the body text.\par It has two paragraphs.\par}",
        encoding="latin-1",
    )

    doc = extract(target, ExtractConfig())
    texts = [b.text for b in doc.blocks]
    assert "This is the body text." in texts
    assert "It has two paragraphs." in texts


def test_pdf_caption_is_not_emitted_twice(docs):
    """The caption appeared both as image alt text and as a body paragraph."""
    doc = extract(docs["pdf"], ExtractConfig())
    body = [b.text for b in doc.blocks if b.kind == "text"]
    assert "Figure 1. Revenue by segment." not in body
    assert doc.images[0].caption == "Figure 1. Revenue by segment."
