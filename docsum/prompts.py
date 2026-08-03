"""Prompts for the summarization passes.

Each chapter prompt is deliberately self-contained: the model is told it is
seeing one section in isolation and must not speculate about the rest. That is
what makes per-chapter summarization more accurate than one giant call — the
model is never asked to hold a whole book in working memory.
"""

from __future__ import annotations

CHAPTER_SYSTEM = """\
You write dense, accurate summaries of documents.

You are summarizing ONE SECTION of a longer document. You can see only this \
section. Summarize what is actually in front of you; do not speculate about \
what other sections contain, and do not refer to "the rest of the document".

Rules:
- Preserve concrete specifics: names, numbers, dates, findings, decisions, \
defined terms, and causal claims. Specifics are the value of a summary; \
generalities are not.
- Lead with substance. Do not open with "This section..." or "The author..." \
or any other framing sentence.
- Match the source's own shape: prose for prose, bullets for enumerations.
- Preserve the section's terminology rather than paraphrasing it into \
different words.
- If the section is mostly boilerplate with little content, say so briefly \
rather than padding to length.
- The section may contain image anchors like [report-p012-img01]. When you \
discuss what such a figure shows, cite its anchor in square brackets so the \
summary stays linked to the extracted image.
- Output only the summary. No preamble, no title, no meta-commentary.\
"""

SINGLE_SYSTEM = """\
You write dense, accurate summaries of documents.

Rules:
- Preserve concrete specifics: names, numbers, dates, findings, decisions, \
defined terms, and causal claims. Specifics are the value of a summary; \
generalities are not.
- Lead with substance. Do not open with "This document..." or any other \
framing sentence.
- Match the source's own shape: prose for prose, bullets for enumerations.
- Preserve the document's terminology rather than paraphrasing it into \
different words.
- The document may contain image anchors like [report-p012-img01]. When you \
discuss what such a figure shows, cite its anchor in square brackets.
- Output only the summary. No preamble, no title, no meta-commentary.\
"""

OVERVIEW_SYSTEM = """\
You write the opening overview for a summary of a long document.

You are given the per-section summaries that follow your overview. Write a \
single short passage that tells a reader what this document is, what it \
covers, and what its most important content is — the thing they would want if \
they read nothing else.

Rules:
- Do not enumerate the sections one by one; the section summaries follow and \
already do that.
- Be concrete. Name the actual subject matter, findings, and conclusions.
- Do not open with "This document..." — lead with the substance.
- Output only the overview. No heading, no preamble.\
"""


def chapter_user(
    *,
    title: str,
    text: str,
    target_words: int,
    position: int,
    total: int,
    previous_summary: str = "",
) -> str:
    parts = [f"This is section {position} of {total} of a document."]
    if title:
        parts.append(f'Its heading is: "{title}"')

    if previous_summary:
        parts.append(
            "For continuity only, here is your summary of the PREVIOUS section. "
            "Do not summarize it again and do not repeat its content:\n\n"
            f"<previous_summary>\n{previous_summary}\n</previous_summary>"
        )

    parts.append(
        f"Summarize the section below in approximately {target_words} words.\n\n"
        f"<section>\n{text}\n</section>"
    )
    return "\n\n".join(parts)


def single_user(*, text: str, target_words: int) -> str:
    return (
        f"Summarize the document below in approximately {target_words} words.\n\n"
        f"<document>\n{text}\n</document>"
    )


def overview_user(*, title: str, summaries: list[tuple[str, str]], target_words: int) -> str:
    joined = "\n\n".join(
        f"## {heading or f'Section {i + 1}'}\n{body}" for i, (heading, body) in enumerate(summaries)
    )
    return (
        f'Document title: "{title}"\n\n'
        f"Write an overview of approximately {target_words} words, based on these "
        f"section summaries.\n\n<section_summaries>\n{joined}\n</section_summaries>"
    )
