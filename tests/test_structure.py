"""docsum.structure — the planner that decides how a document is split."""

from __future__ import annotations

import pytest

import docgen
from docsum import structure
from docsum.structure import MIN_SECTION_WORDS, Section, find_headings, plan, word_count

THRESHOLD = 20000


# --- heading detection -----------------------------------------------------


def test_finds_atx_headings_with_levels():
    headings = find_headings("# One\n\nbody\n\n### Three\n")
    assert [(h.level, h.title) for h in headings] == [(1, "One"), (3, "Three")]


def test_closing_hashes_are_stripped():
    assert find_headings("## Title ##\n")[0].title == "Title"


def test_hash_inside_a_fenced_block_is_not_a_heading():
    markdown = "# Real\n\n```python\n# fake heading\n```\n\n# Also Real\n"
    assert [h.title for h in find_headings(markdown)] == ["Real", "Also Real"]


def test_tilde_fences_are_honoured():
    markdown = "# Real\n\n~~~\n# not a heading\n~~~\n"
    assert [h.title for h in find_headings(markdown)] == ["Real"]


def test_mismatched_fence_char_does_not_close_the_block():
    markdown = "```\n~~~\n# still inside\n```\n\n# Outside\n"
    assert [h.title for h in find_headings(markdown)] == ["Outside"]


def test_setext_and_non_headings_are_ignored():
    assert find_headings("Title\n=====\n\n#NoSpace\n") == []


# --- mode selection --------------------------------------------------------


def test_short_document_is_single_pass():
    result = plan("# T\n\n" + docgen.PARAGRAPH, chapter_threshold_words=THRESHOLD)
    assert result.mode == "single"
    assert len(result.sections) == 1
    assert result.is_chaptered is False


def test_long_document_with_headings_is_chaptered():
    result = plan(docgen.long_markdown(5, 20), chapter_threshold_words=THRESHOLD)
    assert result.mode == "chapters"
    assert len(result.sections) == 5
    assert result.is_chaptered is True


def test_long_document_without_headings_is_chunked():
    result = plan(docgen.flat_markdown(200), chapter_threshold_words=THRESHOLD)
    assert result.mode == "chunks"
    assert len(result.sections) >= 2
    assert all(s.title.startswith("Part ") for s in result.sections)


def test_threshold_boundary_is_respected():
    """Just under stays single; well over becomes chaptered."""
    small = plan(docgen.long_markdown(2, 20), chapter_threshold_words=THRESHOLD)
    assert small.mode == "single"
    big = plan(docgen.long_markdown(6, 20), chapter_threshold_words=THRESHOLD)
    assert big.mode == "chapters"


def test_a_lowered_threshold_forces_chaptering():
    result = plan(docgen.long_markdown(3, 2), chapter_threshold_words=100)
    assert result.mode == "chapters"


# --- split level -----------------------------------------------------------


def test_splits_at_h2_when_h1_is_a_lone_title():
    markdown = "# Book Title\n\n" + docgen.long_markdown(4, 20, level=2)
    result = plan(markdown, chapter_threshold_words=THRESHOLD)
    assert result.mode == "chapters"
    assert [s.title for s in result.sections] == [
        "Introduction", "Methods", "Results", "Discussion"
    ]


def test_splits_at_h1_when_there_are_several():
    result = plan(docgen.long_markdown(3, 40), chapter_threshold_words=THRESHOLD)
    assert result.mode == "chapters"
    assert all(s.level == 1 for s in result.sections)


def test_single_heading_of_every_level_falls_back_to_chunks():
    markdown = "# Only One\n\n" + docgen.flat_markdown(200)
    result = plan(markdown, chapter_threshold_words=THRESHOLD)
    assert result.mode == "chunks"


# --- merging ---------------------------------------------------------------


def test_title_only_section_is_folded_into_the_next():
    """A doc title immediately followed by chapter one must not waste a call."""
    markdown = "# Doc Title\n\n" + docgen.long_markdown(2, 40)
    result = plan(markdown, chapter_threshold_words=THRESHOLD)
    assert len(result.sections) == 2
    assert result.sections[0].title == "Introduction"
    assert "Doc Title" in result.sections[0].body


def test_front_matter_becomes_its_own_section_when_substantial():
    markdown = docgen.PARAGRAPH + "\n\n" + docgen.long_markdown(3, 40)
    result = plan(markdown, chapter_threshold_words=THRESHOLD)
    assert result.sections[0].title == "Front matter"


def test_small_trailing_chunk_is_folded_back():
    result = plan(docgen.flat_markdown(170), chapter_threshold_words=THRESHOLD)
    smallest = min(s.word_count for s in result.sections)
    assert smallest > 2000  # no orphan scrap part


def test_thin_sections_are_merged_forward():
    sections = [
        Section("Tiny", 1, "few words here"),
        Section("Real", 1, docgen.PARAGRAPH * 3),
    ]
    merged = structure._merge_thin(sections)
    assert len(merged) == 1
    assert merged[0].title == "Real"
    assert "Tiny" in merged[0].body


def test_trailing_thin_section_is_appended_to_the_previous():
    sections = [
        Section("Real", 1, docgen.PARAGRAPH * 3),
        Section("Tiny", 1, "few words"),
    ]
    merged = structure._merge_thin(sections)
    assert len(merged) == 1
    assert "Tiny" in merged[0].body


# --- Section behaviour -----------------------------------------------------


def test_section_text_includes_its_heading():
    section = Section("Methods", 2, "body text")
    assert section.text == "## Methods\n\nbody text"


def test_section_without_a_title_is_just_body():
    assert Section("", 1, "body text").text == "body text"


def test_word_count_counts_whitespace_separated_tokens():
    assert word_count("one two  three\nfour") == 4
    assert word_count("") == 0


def test_min_section_words_constant_is_sane():
    assert 0 < MIN_SECTION_WORDS < 500


# --- content preservation --------------------------------------------------


def test_chaptering_preserves_all_body_text():
    """Splitting must not silently drop content."""
    markdown = docgen.long_markdown(4, 20)
    result = plan(markdown, chapter_threshold_words=THRESHOLD)
    joined = " ".join(s.text for s in result.sections)
    # Allow for heading lines being re-emitted; body words must all survive.
    assert word_count(joined) >= result.total_words * 0.99


def test_chunking_preserves_all_body_text():
    markdown = docgen.flat_markdown(150)
    result = plan(markdown, chapter_threshold_words=THRESHOLD)
    joined = " ".join(s.body for s in result.sections)
    assert word_count(joined) == word_count(markdown)


@pytest.mark.parametrize("chapters", [2, 3, 7])
def test_section_count_matches_heading_count(chapters):
    result = plan(docgen.long_markdown(chapters, 40), chapter_threshold_words=THRESHOLD)
    assert len(result.sections) == chapters
