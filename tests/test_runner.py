"""docsum.runner — strategy selection and the per-chapter isolation property."""

from __future__ import annotations

import pytest

import docgen
from docsum import runner
from docsum.config import Config
from docsum.runner import MIN_COMPRESSION, footer, summarize
from docsum.structure import word_count

THRESHOLD = 20000


def long_doc(chapters: int = 4, paragraphs: int = 40) -> str:
    return docgen.long_markdown(chapters, paragraphs)


# --- strategy --------------------------------------------------------------


def test_short_document_takes_the_single_path(fake_openai):
    client, fake = fake_openai()
    result = summarize("# T\n\n" + docgen.PARAGRAPH, "T", Config(), client, verbose=False)
    assert result.plan.mode == "single"
    assert len(fake.calls) == 1
    assert result.markdown.startswith("# Summary of T")


def test_long_document_makes_one_call_per_chapter_plus_overview(fake_openai):
    client, fake = fake_openai()
    result = summarize(long_doc(4), "Book", Config(), client, verbose=False)
    assert result.plan.mode == "chapters"
    assert len(fake.calls) == 5  # 4 chapters + 1 overview


def test_overview_is_placed_first_even_though_written_last(fake_openai):
    client, fake = fake_openai()
    result = summarize(long_doc(3), "Book", Config(), client, verbose=False)
    lines = result.markdown.splitlines()
    assert lines[0] == "# Summary of Book"
    assert lines[2] == "## Overview"
    # The overview request is the final one made.
    assert "section_summaries" in fake.user_messages[-1]


def test_chapter_headings_are_numbered_in_order(fake_openai):
    client, _ = fake_openai()
    result = summarize(long_doc(3), "Book", Config(), client, verbose=False)
    assert "## 1. Introduction" in result.markdown
    assert "## 2. Methods" in result.markdown
    assert "## 3. Results" in result.markdown


# --- the isolation property ------------------------------------------------


def test_each_chapter_request_carries_only_its_own_text(fake_openai):
    """The central claim: a chapter is summarized from its own text alone.

    Each chapter here has a unique marker; a request must contain its own and
    no other's.
    """
    markdown = ""
    for index in range(4):
        markdown += f"# Chapter {index}\n\nMARKER{index} " + (docgen.PARAGRAPH + "\n\n") * 30

    client, fake = fake_openai(Config(carry_context=False))
    summarize(markdown, "Book", Config(carry_context=False), client, verbose=False)

    chapter_requests = fake.user_messages[:-1]  # last one is the overview
    assert len(chapter_requests) == 4
    for index, request in enumerate(chapter_requests):
        assert f"MARKER{index}" in request
        for other in range(4):
            if other != index:
                assert f"MARKER{other}" not in request


def test_request_size_is_a_fraction_of_the_document(fake_openai):
    markdown = long_doc(5, 40)
    client, fake = fake_openai()
    summarize(markdown, "Book", Config(), client, verbose=False)

    total = word_count(markdown)
    largest = max(word_count(m) for m in fake.user_messages)
    assert largest < total * 0.35


# --- carry_context ---------------------------------------------------------


def test_carry_context_passes_the_previous_summary_forward(fake_openai):
    client, fake = fake_openai(Config(carry_context=True), reply="PRIOR SUMMARY TEXT")
    summarize(long_doc(3), "Book", Config(carry_context=True), client, verbose=False)

    chapter_requests = fake.user_messages[:-1]
    assert "previous_summary" not in chapter_requests[0]
    assert "PRIOR SUMMARY TEXT" in chapter_requests[1]


def test_carry_context_off_sends_nothing_extra(fake_openai):
    cfg = Config(carry_context=False)
    client, fake = fake_openai(cfg, reply="PRIOR SUMMARY TEXT")
    summarize(long_doc(3), "Book", cfg, client, verbose=False)

    for request in fake.user_messages[:-1]:
        assert "previous_summary" not in request


def test_carried_context_is_capped(fake_openai):
    cfg = Config(carry_context=True)
    long_reply = " ".join(f"w{i}" for i in range(2000))
    client, fake = fake_openai(cfg, reply=long_reply)
    summarize(long_doc(3), "Book", cfg, client, verbose=False)

    second = fake.user_messages[1]
    carried = second.split("<previous_summary>")[1].split("</previous_summary>")[0]
    assert word_count(carried) <= runner.CARRY_CONTEXT_WORDS + 5


# --- short-section skip ----------------------------------------------------


def test_section_shorter_than_its_target_is_kept_verbatim(fake_openai):
    """min_words would otherwise ask for a summary longer than the source.

    The short section must be big enough to survive structure._merge_thin
    (>= 50 words) but small enough to trip the runner's compression check
    (<= min_words * 2), so this isolates the runner's behaviour rather than
    the planner's.
    """
    short_body = docgen._SENTENCE * 4  # 100 words
    markdown = (
        "# Brief\n\n" + short_body + "\n\n"
        + "# Real\n\n" + (docgen.PARAGRAPH + "\n\n") * 40
        + "# Also Real\n\n" + (docgen.PARAGRAPH + "\n\n") * 40
    )
    cfg = Config(min_words=150)
    client, fake = fake_openai(cfg)
    result = summarize(markdown, "Book", cfg, client, verbose=False)

    # 3 sections, but the short one is never sent: 2 chapters + overview.
    assert len(result.plan.sections) == 3
    assert len(fake.calls) == 3
    assert "## 1. Brief" in result.markdown
    assert "Lorem ipsum" in result.markdown.split("## 2.")[0]


def test_skip_threshold_matches_min_compression():
    """A section is skipped exactly when the summary would not halve it."""
    cfg = Config(min_words=150)
    assert cfg.target_words(300) >= 300 * MIN_COMPRESSION  # skipped
    assert cfg.target_words(1000) < 1000 * MIN_COMPRESSION  # summarized


def test_no_section_is_skipped_when_all_are_long(fake_openai):
    client, fake = fake_openai()
    summarize(long_doc(3, 40), "Book", Config(), client, verbose=False)
    assert len(fake.calls) == 4  # nothing skipped


# --- target lengths --------------------------------------------------------


def test_requested_length_tracks_the_section_size(fake_openai):
    cfg = Config(target_ratio=0.10, min_words=10)
    client, fake = fake_openai(cfg)
    summarize(long_doc(3, 40), "Book", cfg, client, verbose=False)

    for request in fake.user_messages[:-1]:
        asked = int(request.split("approximately ")[1].split(" words")[0])
        assert 1000 <= asked <= 1400  # ~10% of a 12,000-word chapter


def test_summary_words_are_counted_from_the_replies(fake_openai):
    cfg = Config()
    client, _ = fake_openai(cfg, echo_target=True)
    result = summarize(long_doc(3, 40), "Book", cfg, client, verbose=False)
    ratio = result.summary_words / result.plan.total_words
    assert cfg.min_ratio <= ratio <= cfg.max_ratio


# --- footer ----------------------------------------------------------------


def test_footer_reports_model_shape_and_ratio(fake_openai):
    cfg = Config(model="gpt-test")
    client, _ = fake_openai(cfg)
    result = summarize(long_doc(3), "Book", cfg, client, verbose=False)
    text = footer(result, cfg, "book.md")

    assert "book.md" in text
    assert "gpt-test" in text
    assert "chapters, summarized independently" in text


def test_footer_names_the_single_pass_shape(fake_openai):
    cfg = Config()
    client, _ = fake_openai(cfg)
    result = summarize("# T\n\n" + docgen.PARAGRAPH, "T", cfg, client, verbose=False)
    assert "single pass" in footer(result, cfg, "t.md")


def test_footer_names_the_chunked_shape(fake_openai):
    cfg = Config()
    client, _ = fake_openai(cfg)
    result = summarize(docgen.flat_markdown(200), "T", cfg, client, verbose=False)
    assert "split by length" in footer(result, cfg, "t.md")


def test_footer_handles_an_empty_document_without_dividing_by_zero(fake_openai):
    cfg = Config()
    client, _ = fake_openai(cfg)
    result = summarize("# T\n\nword", "T", cfg, client, verbose=False)
    result.plan.total_words = 0
    assert "0%" in footer(result, cfg, "t.md")


# --- prompts reach the model unchanged -------------------------------------


def test_chapter_calls_use_the_chapter_system_prompt(fake_openai):
    from docsum import prompts

    client, fake = fake_openai()
    summarize(long_doc(3), "Book", Config(), client, verbose=False)
    assert fake.system_messages[0] == prompts.CHAPTER_SYSTEM
    assert fake.system_messages[-1] == prompts.OVERVIEW_SYSTEM


def test_single_call_uses_the_single_system_prompt(fake_openai):
    from docsum import prompts

    client, fake = fake_openai()
    summarize("# T\n\n" + docgen.PARAGRAPH, "T", Config(), client, verbose=False)
    assert fake.system_messages[0] == prompts.SINGLE_SYSTEM
