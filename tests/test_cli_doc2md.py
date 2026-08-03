"""doc2md CLI — selection parsing, the picker, exit codes, output layout."""

from __future__ import annotations

import shutil

import pytest

from doc2md.cli import (
    EXIT_CHECK_FAILED,
    EXIT_ERROR,
    EXIT_OK,
    OUTPUT_DIRNAME,
    find_convertible,
    main,
    parse_selection,
)


# --- selection parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, count, expected",
    [
        ("3", 6, [2]),
        ("1,3,5", 6, [0, 2, 4]),
        ("2-4", 6, [1, 2, 3]),
        ("4-2", 6, [1, 2, 3]),          # reversed range is accepted
        ("all", 3, [0, 1, 2]),
        ("ALL", 3, [0, 1, 2]),
        ("a", 3, [0, 1, 2]),
        ("*", 2, [0, 1]),
        ("1 3", 4, [0, 2]),             # spaces behave like commas
        ("2,2,2", 4, [1]),              # duplicates collapse
        ("3,1", 4, [2, 0]),             # order is preserved as typed
        ("1,,3", 4, [0, 2]),            # empty parts ignored
    ],
)
def test_parse_selection_accepts(raw, count, expected):
    assert parse_selection(raw, count) == expected


@pytest.mark.parametrize("raw", ["9", "0", "-1", "x", "2-", "1-99", "abc,2"])
def test_parse_selection_rejects(raw):
    with pytest.raises(ValueError):
        parse_selection(raw, 6)


def test_out_of_range_message_names_the_bounds():
    with pytest.raises(ValueError, match=r"out of range \(1-6\)"):
        parse_selection("7", 6)


# --- discovery -------------------------------------------------------------


def test_find_convertible_lists_only_supported_files(workdir):
    (workdir / "song.mp3").write_bytes(b"x")
    names = [p.name for p in find_convertible(workdir)]
    assert "review.pdf" in names
    assert "song.mp3" not in names


def test_find_convertible_is_sorted_case_insensitively(isolated_cwd):
    for name in ("b.txt", "A.txt", "c.txt"):
        (isolated_cwd / name).write_text("x")
    assert [p.name for p in find_convertible(isolated_cwd)] == ["A.txt", "b.txt", "c.txt"]


# --- conversion ------------------------------------------------------------


def test_convert_writes_into_the_outputs_directory(workdir):
    assert main(["review.pdf"]) == EXIT_OK
    produced = workdir / OUTPUT_DIRNAME / "annual-review-2026"
    assert (produced / "annual-review-2026.md").is_file()
    assert (produced / "images").is_dir()


def test_sources_are_not_polluted(workdir):
    main(["notes.txt"])
    entries = {p.name for p in workdir.iterdir() if p.is_dir()}
    assert entries == {OUTPUT_DIRNAME}


def test_outdir_overrides_the_default(workdir, tmp_path):
    target = tmp_path / "elsewhere"
    assert main(["notes.txt", "--outdir", str(target)]) == EXIT_OK
    assert (target / "notes" / "notes.md").is_file()


def test_multiple_files_in_one_run(workdir):
    assert main(["notes.txt", "stock.csv"]) == EXIT_OK
    root = workdir / OUTPUT_DIRNAME
    assert (root / "notes" / "notes.md").is_file()
    assert (root / "stock" / "stock.md").is_file()


def test_existing_output_requires_force(workdir, capsys):
    assert main(["notes.txt"]) == EXIT_OK
    assert main(["notes.txt"]) == EXIT_ERROR
    assert "already exists" in capsys.readouterr().err
    assert main(["notes.txt", "--force"]) == EXIT_OK


def test_slug_collision_within_one_run_is_disambiguated(workdir, docs):
    """Two different files can share a document title."""
    shutil.copy(docs["docx"], workdir / "copy.docx")
    assert main(["memo.docx", "copy.docx"]) == EXIT_OK
    root = workdir / OUTPUT_DIRNAME
    assert (root / "quarterly-memo").is_dir()
    assert (root / "quarterly-memo-copy").is_dir()


def test_unsupported_file_reports_and_exits_nonzero(workdir, capsys):
    (workdir / "song.mp3").write_bytes(b"x")
    assert main(["song.mp3"]) == EXIT_ERROR
    assert "unsupported file type" in capsys.readouterr().err


def test_one_bad_file_does_not_stop_the_batch(workdir, capsys):
    (workdir / "song.mp3").write_bytes(b"x")
    assert main(["song.mp3", "notes.txt"]) == EXIT_ERROR
    assert (workdir / OUTPUT_DIRNAME / "notes" / "notes.md").is_file()


def test_missing_file_is_reported(workdir, capsys):
    assert main(["nope.pdf"]) == EXIT_ERROR
    assert "not a file" in capsys.readouterr().err


# --- --check ---------------------------------------------------------------


def test_check_passes_on_a_clean_conversion(workdir):
    assert main(["book.epub", "--check"]) == EXIT_OK


def test_check_failure_uses_its_own_exit_code(workdir, monkeypatch, capsys):
    """Exit 2 is reserved for an anchor inconsistency.

    A real conversion cannot produce one on demand — every run rebuilds the
    output directory — so the verifier is stubbed to report a problem, which
    is what exercises the CLI's handling of it.
    """
    monkeypatch.setattr("doc2md.cli.check_anchors", lambda _p: ["fabricated problem"])
    assert main(["notes.txt", "--check"]) == EXIT_CHECK_FAILED

    err = capsys.readouterr().err
    assert "--check failed" in err
    assert "fabricated problem" in err


def test_check_reports_success_when_verbose(workdir, capsys):
    assert main(["notes.txt", "--check", "-v"]) == EXIT_OK
    assert "anchors consistent" in capsys.readouterr().out


def test_check_detects_an_orphan_image(workdir):
    """The verifier itself, against a genuinely inconsistent directory."""
    from doc2md.render import check_anchors

    main(["review.pdf"])
    produced = workdir / OUTPUT_DIRNAME / "annual-review-2026"
    (produced / "images" / "orphan.png").write_bytes(b"x")

    problems = check_anchors(produced / "annual-review-2026.md")
    assert any("orphan" in p for p in problems)


# --- picker ----------------------------------------------------------------


def test_picker_converts_the_selected_file(workdir, monkeypatch, capsys):
    files = find_convertible(workdir)
    index = next(i for i, p in enumerate(files, 1) if p.name == "notes.txt")
    monkeypatch.setattr("builtins.input", lambda _="": str(index))

    assert main([]) == EXIT_OK
    assert (workdir / OUTPUT_DIRNAME / "notes" / "notes.md").is_file()
    # Only the selected file was converted.
    assert len(list((workdir / OUTPUT_DIRNAME).iterdir())) == 1


def test_picker_cancels_on_empty_input(workdir, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "")
    assert main([]) == EXIT_OK
    assert not (workdir / OUTPUT_DIRNAME).exists()


def test_picker_cancels_on_interrupt(workdir, monkeypatch):
    def interrupt(_=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert main([]) == EXIT_OK


def test_picker_rejects_bad_input_without_converting(workdir, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _="": "99")
    assert main([]) == EXIT_OK
    assert "out of range" in capsys.readouterr().err
    assert not (workdir / OUTPUT_DIRNAME).exists()


def test_picker_reports_when_nothing_is_convertible(isolated_cwd, capsys):
    assert main([]) == EXIT_OK
    assert "No convertible files" in capsys.readouterr().err


# --- config ----------------------------------------------------------------


def test_default_config_is_written_on_first_run(workdir, capsys):
    main(["notes.txt"])
    assert (workdir / "doc2md.yaml").is_file()
    assert "wrote default config" in capsys.readouterr().out


def test_unknown_config_keys_are_warned_about(workdir, capsys):
    (workdir / "doc2md.yaml").write_text("bogus_key: 1\n")
    main(["notes.txt"])
    assert "unknown key" in capsys.readouterr().err
