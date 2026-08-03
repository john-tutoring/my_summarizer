"""Summarization strategy: one pass, or chapter-by-chapter then an overview.

Chapters run sequentially. Each call carries only its own section — plus, when
`carry_context` is on, a capped excerpt of the previous chapter's summary for
narrative continuity. Keeping the per-call context small is the whole reason
this is chaptered rather than one giant request.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import prompts, structure
from .client import Client
from .config import Config
from .structure import Plan, Section

CARRY_CONTEXT_WORDS = 400
OVERVIEW_TARGET_WORDS = 300

# Skip the API call when the requested summary would not even halve the
# section. The min_words floor otherwise produces "summaries" as long as — or
# longer than — a short section, which wastes a request and reads absurdly.
MIN_COMPRESSION = 0.5


@dataclass
class Result:
    markdown: str
    plan: Plan
    summary_words: int


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " …"


def _progress(message: str, verbose: bool) -> None:
    print(message, file=sys.stderr, flush=True)


def summarize(markdown: str, title: str, cfg: Config, client: Client, *, verbose: bool) -> Result:
    plan = structure.plan(markdown, chapter_threshold_words=cfg.chapter_threshold_words)

    if plan.mode == "single":
        return _single(plan, title, cfg, client, verbose=verbose)
    return _chaptered(plan, title, cfg, client, verbose=verbose)


def _single(plan: Plan, title: str, cfg: Config, client: Client, *, verbose: bool) -> Result:
    section = plan.sections[0]
    target = cfg.target_words(plan.total_words)
    _progress(f"  summarizing {plan.total_words:,} words -> ~{target} words", verbose)

    body = client.complete(
        prompts.SINGLE_SYSTEM,
        prompts.single_user(text=section.text, target_words=target),
    )

    markdown = f"# Summary of {title}\n\n{body}\n"
    return Result(markdown=markdown, plan=plan, summary_words=structure.word_count(body))


def _chaptered(plan: Plan, title: str, cfg: Config, client: Client, *, verbose: bool) -> Result:
    label = "chapter" if plan.mode == "chapters" else "part"
    total = len(plan.sections)
    _progress(
        f"  {plan.total_words:,} words, {total} {label}s "
        f"(summarizing each from its own text only)",
        verbose,
    )

    summaries: list[tuple[str, str]] = []
    previous = ""

    for index, section in enumerate(plan.sections, 1):
        target = cfg.target_words(section.word_count)
        heading = section.title or f"{label.title()} {index}"

        if target >= section.word_count * MIN_COMPRESSION:
            # Already shorter than a summary of it would be. Keep the text and
            # spend nothing; it still appears in the output under its heading.
            _progress(
                f"  [{index}/{total}] {heading} — {section.word_count:,} words, "
                f"kept as-is (too short to summarize)",
                verbose,
            )
            summaries.append((heading, section.body.strip()))
            continue

        _progress(
            f"  [{index}/{total}] {heading} — {section.word_count:,} words -> ~{target}",
            verbose,
        )

        body = client.complete(
            prompts.CHAPTER_SYSTEM,
            prompts.chapter_user(
                title=section.title,
                text=section.text,
                target_words=target,
                position=index,
                total=total,
                previous_summary=previous,
            ),
        )
        summaries.append((heading, body))
        previous = _truncate(body, CARRY_CONTEXT_WORDS) if cfg.carry_context else ""

    _progress("  [overview] writing the opening overview", verbose)
    overview = client.complete(
        prompts.OVERVIEW_SYSTEM,
        prompts.overview_user(
            title=title, summaries=summaries, target_words=OVERVIEW_TARGET_WORDS
        ),
    )

    return Result(
        markdown=_assemble(title, overview, summaries),
        plan=plan,
        summary_words=structure.word_count(overview)
        + sum(structure.word_count(body) for _, body in summaries),
    )


def _assemble(title: str, overview: str, summaries: list[tuple[str, str]]) -> str:
    parts = [f"# Summary of {title}", "## Overview", overview.strip()]
    for index, (heading, body) in enumerate(summaries, 1):
        parts.append(f"## {index}. {heading}")
        parts.append(body.strip())
    return "\n\n".join(parts).rstrip() + "\n"


def footer(result: Result, cfg: Config, source_name: str) -> str:
    """Provenance line appended to the summary file."""
    ratio = (result.summary_words / result.plan.total_words * 100) if result.plan.total_words else 0
    shape = {
        "single": "single pass",
        "chapters": f"{len(result.plan.sections)} chapters, summarized independently",
        "chunks": f"{len(result.plan.sections)} parts, split by length",
    }[result.plan.mode]
    return (
        f"\n---\n\n*Summarized from `{source_name}` by docsum using {cfg.model} "
        f"({shape}). {result.plan.total_words:,} words in, {result.summary_words:,} out "
        f"({ratio:.0f}%).*\n"
    )
