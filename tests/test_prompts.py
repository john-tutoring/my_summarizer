"""docsum.prompts — what actually reaches the model."""

from __future__ import annotations

from docsum import prompts


def test_chapter_prompt_states_the_section_is_isolated():
    """The model must be told not to speculate about unseen sections."""
    assert "ONE SECTION" in prompts.CHAPTER_SYSTEM
    assert "only this" in prompts.CHAPTER_SYSTEM


def test_chapter_prompt_asks_for_image_anchor_citations():
    assert "image anchors" in prompts.CHAPTER_SYSTEM
    assert "report-p012-img01" in prompts.CHAPTER_SYSTEM


def test_prompts_forbid_preamble():
    for system in (prompts.CHAPTER_SYSTEM, prompts.SINGLE_SYSTEM, prompts.OVERVIEW_SYSTEM):
        assert "no preamble" in system.lower()


def test_chapter_user_includes_position_and_target():
    text = prompts.chapter_user(
        title="Methods", text="body", target_words=500, position=2, total=7
    )
    assert "section 2 of 7" in text
    assert "Methods" in text
    assert "approximately 500 words" in text
    assert "<section>\nbody\n</section>" in text


def test_chapter_user_omits_previous_summary_when_empty():
    text = prompts.chapter_user(
        title="T", text="body", target_words=100, position=1, total=3, previous_summary=""
    )
    assert "previous_summary" not in text


def test_chapter_user_marks_previous_summary_as_context_only():
    text = prompts.chapter_user(
        title="T",
        text="body",
        target_words=100,
        position=2,
        total=3,
        previous_summary="EARLIER",
    )
    assert "<previous_summary>\nEARLIER\n</previous_summary>" in text
    assert "Do not summarize it again" in text


def test_chapter_user_without_a_title():
    text = prompts.chapter_user(title="", text="body", target_words=10, position=1, total=1)
    assert "Its heading is" not in text


def test_single_user_wraps_the_document():
    text = prompts.single_user(text="all of it", target_words=250)
    assert "approximately 250 words" in text
    assert "<document>\nall of it\n</document>" in text


def test_overview_user_lists_the_section_summaries():
    text = prompts.overview_user(
        title="Book", summaries=[("One", "first"), ("Two", "second")], target_words=300
    )
    assert 'Document title: "Book"' in text
    assert "## One\nfirst" in text
    assert "## Two\nsecond" in text
    assert "approximately 300 words" in text


def test_overview_user_handles_untitled_sections():
    text = prompts.overview_user(title="B", summaries=[("", "body")], target_words=10)
    assert "Section 1" in text
