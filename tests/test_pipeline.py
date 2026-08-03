"""End to end: convert a document, then summarize the Markdown it produced."""

from __future__ import annotations

import pytest

import docgen
from doc2md.cli import OUTPUT_DIRNAME
from doc2md.cli import main as doc2md_main
from docsum.cli import main as docsum_main
from docsum.runner import footer, summarize
from docsum.structure import word_count


def test_convert_then_summarize_produces_the_expected_tree(workdir, fake_openai, monkeypatch):
    assert doc2md_main(["book.epub", "--check"]) == 0

    produced = workdir / OUTPUT_DIRNAME / "a-short-book"
    markdown = produced / "a-short-book.md"
    assert markdown.is_file()

    client, fake = fake_openai()
    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: client)

    assert docsum_main([str(markdown)]) == 0

    summary = produced / "a-short-book.summary.md"
    assert summary.is_file()
    assert summary.read_text().startswith("# Summary of")
    assert len(fake.calls) == 1  # short document: single pass


def test_summary_lands_beside_its_markdown_and_images(workdir, fake_openai, monkeypatch):
    doc2md_main(["review.pdf"])
    produced = workdir / OUTPUT_DIRNAME / "annual-review-2026"

    client, _ = fake_openai()
    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: client)
    docsum_main([str(produced / "annual-review-2026.md")])

    names = {p.name for p in produced.iterdir()}
    assert names == {
        "annual-review-2026.md",
        "annual-review-2026.summary.md",
        "images",
    }


def test_summarizing_a_stray_markdown_writes_into_outputs(isolated_cwd, fake_openai, monkeypatch):
    stray = isolated_cwd / "loose.md"
    stray.write_text("# Loose\n\n" + docgen.PARAGRAPH)

    client, _ = fake_openai()
    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: client)
    assert docsum_main(["loose.md"]) == 0

    assert (isolated_cwd / OUTPUT_DIRNAME / "loose.summary.md").is_file()


def test_existing_summary_requires_force(isolated_cwd, fake_openai, monkeypatch, capsys):
    stray = isolated_cwd / "loose.md"
    stray.write_text("# Loose\n\n" + docgen.PARAGRAPH)

    client, _ = fake_openai()
    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: client)

    assert docsum_main(["loose.md"]) == 0
    assert docsum_main(["loose.md"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert docsum_main(["loose.md", "--force"]) == 0


def test_a_long_book_is_chaptered_end_to_end(isolated_cwd, fake_openai, monkeypatch):
    """The headline case: a book-length document split per chapter."""
    book = isolated_cwd / "book.md"
    book.write_text(docgen.long_markdown(6, 40))

    from docsum.config import Config

    cfg = Config()
    client, fake = fake_openai(cfg, echo_target=True)
    result = summarize(book.read_text(), "Book", cfg, client, verbose=False)

    assert result.plan.mode == "chapters"
    assert len(fake.calls) == 7  # 6 chapters + overview

    # Every request is a small fraction of the whole book.
    total = word_count(book.read_text())
    assert all(word_count(m) < total * 0.25 for m in fake.user_messages)

    ratio = result.summary_words / result.plan.total_words
    assert cfg.min_ratio <= ratio <= cfg.max_ratio

    text = result.markdown + footer(result, cfg, "book.md")
    assert "## Overview" in text
    assert "## 6." in text
    assert "chapters, summarized independently" in text


def test_summary_is_excluded_from_a_later_picker_scan(isolated_cwd, fake_openai, monkeypatch):
    """Summaries must never be fed back in as inputs."""
    from docsum.cli import find_markdown

    stray = isolated_cwd / "loose.md"
    stray.write_text("# Loose\n\n" + docgen.PARAGRAPH)

    client, _ = fake_openai()
    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: client)
    docsum_main(["loose.md"])

    found = [p.name for p in find_markdown(isolated_cwd)]
    assert "loose.md" in found
    assert "loose.summary.md" not in found


@pytest.mark.parametrize("source", ["review.pdf", "memo.docx", "book.epub", "page.html", "memo.odt"])
def test_every_anchored_format_survives_the_round_trip(workdir, source):
    """Convert, then verify the anchor invariant, for each anchored format."""
    assert doc2md_main([source, "--check"]) == 0
