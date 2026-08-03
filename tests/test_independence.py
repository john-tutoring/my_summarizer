"""The two-package claim: neither tool depends on the other's stack.

This is the reason the project is split in two, so it is asserted directly —
statically against the source, and at runtime by making the other package's
dependencies unimportable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EXTRACTION_LIBS = ("pymupdf", "fitz", "docx", "pptx", "ebooklib", "markitdown", "markdownify", "bs4")


def python_sources(package: str) -> list[Path]:
    return sorted((REPO / package).rglob("*.py"))


# --- static ----------------------------------------------------------------


def test_doc2md_never_mentions_openai():
    offenders = [p for p in python_sources("doc2md") if "openai" in p.read_text()]
    assert offenders == [], f"doc2md must not reference openai: {offenders}"


def test_docsum_never_mentions_an_extraction_library():
    offenders = []
    for path in python_sources("docsum"):
        text = path.read_text()
        for lib in EXTRACTION_LIBS:
            if f"import {lib}" in text or f"from {lib}" in text:
                offenders.append((path.name, lib))
    assert offenders == [], f"docsum must not import extraction libraries: {offenders}"


def test_neither_package_imports_the_other():
    for package, other in (("doc2md", "docsum"), ("docsum", "doc2md")):
        for path in python_sources(package):
            text = path.read_text()
            assert f"import {other}" not in text, f"{path} imports {other}"
            assert f"from {other}" not in text, f"{path} imports {other}"


def test_requirements_files_do_not_overlap_on_the_heavy_deps():
    doc2md_reqs = (REPO / "requirements-doc2md.txt").read_text().lower()
    docsum_reqs = (REPO / "requirements-docsum.txt").read_text().lower()
    assert "openai" not in doc2md_reqs
    assert "pymupdf" not in docsum_reqs
    assert "markitdown" not in docsum_reqs


# --- runtime ---------------------------------------------------------------


def run_isolated(code: str, blocked: set[str]) -> subprocess.CompletedProcess:
    """Run code in a fresh interpreter with `blocked` packages unimportable."""
    preamble = f"""
import sys
BLOCKED = {sorted(blocked)!r}

class Blocker:
    def find_module(self, name, path=None):
        if name.split('.')[0] in BLOCKED:
            return self
        return None
    def load_module(self, name):
        raise ImportError(name + ' is blocked')

sys.meta_path.insert(0, Blocker())
"""
    return subprocess.run(
        [sys.executable, "-c", preamble + code],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_doc2md_converts_with_openai_unimportable(workdir):
    """A machine that has never installed openai must still convert documents."""
    code = f"""
import os
os.chdir({str(workdir)!r})
from doc2md.cli import main
raise SystemExit(main(['review.pdf', 'book.epub', 'memo.docx', '--check']))
"""
    result = run_isolated(code, {"openai"})
    assert result.returncode == 0, result.stderr
    assert (workdir / "outputs" / "annual-review-2026").is_dir()


def test_importing_doc2md_does_not_pull_in_openai():
    code = """
import doc2md.cli, doc2md.render, doc2md.extract
import doc2md.extract.pdf, doc2md.extract.epub, doc2md.extract.text
import sys
assert 'openai' not in sys.modules, 'doc2md imported openai'
print('ok')
"""
    result = run_isolated(code, {"openai"})
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_docsum_runs_with_every_extraction_library_blocked(isolated_cwd):
    """docsum must work on a machine with none of the document libraries."""
    markdown = isolated_cwd / "doc.md"
    markdown.write_text("# Title\n\n" + "word " * 500)

    code = f"""
import os
os.chdir({str(isolated_cwd)!r})
from docsum import structure
from docsum.cli import main
plan = structure.plan(open('doc.md').read(), chapter_threshold_words=20000)
assert plan.mode == 'single', plan.mode
# Reaching the credentials error proves the whole path ran.
raise SystemExit(main(['doc.md']))
"""
    result = run_isolated(code, set(EXTRACTION_LIBS))
    assert result.returncode == 1
    assert "no OpenAI credentials" in result.stderr


def test_importing_docsum_does_not_pull_in_extraction_libraries():
    code = """
import docsum.cli, docsum.runner, docsum.structure, docsum.client
import sys
leaked = [m for m in ('pymupdf', 'fitz', 'docx', 'pptx', 'ebooklib', 'markitdown')
          if m in sys.modules]
assert not leaked, leaked
print('ok')
"""
    result = run_isolated(code, set(EXTRACTION_LIBS))
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
