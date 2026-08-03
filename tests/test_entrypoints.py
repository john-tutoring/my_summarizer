"""The `python -m doc2md` / `python -m docsum` entry points."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run_module(module: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_doc2md_module_entry_point(workdir):
    result = run_module("doc2md", "notes.txt", cwd=workdir)
    assert result.returncode == 0, result.stderr
    assert (workdir / "outputs" / "notes" / "notes.md").is_file()


def test_docsum_module_entry_point_shows_help(isolated_cwd):
    result = run_module("docsum", "--help", cwd=isolated_cwd)
    assert result.returncode == 0
    assert "Summarize a Markdown document" in result.stdout


def test_doc2md_module_help(isolated_cwd):
    result = run_module("doc2md", "--help", cwd=isolated_cwd)
    assert result.returncode == 0
    assert "positionally anchored images" in result.stdout


def test_console_scripts_are_declared():
    text = (REPO / "pyproject.toml").read_text()
    assert 'doc2md = "doc2md.cli:main"' in text
    assert 'docsum = "docsum.cli:main"' in text
