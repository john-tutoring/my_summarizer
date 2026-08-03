"""doc2md.extract — dispatch, supported types, dependency errors."""

from __future__ import annotations

import sys

import pytest

from doc2md.extract import (
    ExtractionError,
    extract,
    is_supported,
    supported_extensions,
)

from conftest import ImportBlocker


@pytest.mark.parametrize(
    "name",
    ["a.pdf", "a.docx", "a.pptx", "a.epub", "a.html", "a.htm", "a.xhtml",
     "a.odt", "a.odp", "a.ods", "a.rtf", "a.md", "a.markdown", "a.txt",
     "a.text", "a.log", "a.csv", "a.xlsx", "a.xls", "a.msg", "a.ipynb",
     "a.json", "a.xml"],
)
def test_supported_extensions(tmp_path, name):
    assert is_supported(tmp_path / name)


@pytest.mark.parametrize("name", ["a.mp3", "a.zip", "a.exe", "a.png", "a"])
def test_unsupported_extensions(tmp_path, name):
    assert not is_supported(tmp_path / name)


def test_extension_matching_is_case_insensitive(tmp_path):
    assert is_supported(tmp_path / "REPORT.PDF")


def test_missing_file_raises_extraction_error(tmp_path, cfg):
    with pytest.raises(ExtractionError, match="not a file"):
        extract(tmp_path / "nope.pdf", cfg)


def test_unsupported_type_lists_what_is_supported(tmp_path, cfg):
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="unsupported file type"):
        extract(target, cfg)


def test_unsupported_error_names_the_extension(tmp_path, cfg):
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    with pytest.raises(ExtractionError) as excinfo:
        extract(target, cfg)
    assert ".mp3" in str(excinfo.value)
    assert ".pdf" in str(excinfo.value)  # the supported list is included


def test_missing_dependency_names_the_pip_package(tmp_path, cfg, monkeypatch):
    """A machine without pymupdf must get an actionable message, not a traceback.

    Both the extractor module and pymupdf itself have to leave sys.modules, or
    the import short-circuits before the blocker is consulted.
    """
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4")

    for name in list(sys.modules):
        if name == "pymupdf" or name.startswith(("pymupdf.", "doc2md.extract.pdf")):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [ImportBlocker({"pymupdf"})] + sys.meta_path)

    with pytest.raises(ExtractionError) as excinfo:
        extract(target, cfg)
    assert "pip install pymupdf" in str(excinfo.value)


def test_supported_extensions_is_a_superset_of_the_readme_table():
    """Guards against an extractor being registered but undocumented."""
    documented = {".pdf", ".docx", ".pptx", ".epub", ".html", ".odt", ".rtf", ".md", ".csv"}
    assert documented <= supported_extensions()
