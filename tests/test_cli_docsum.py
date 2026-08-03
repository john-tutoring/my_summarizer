"""docsum CLI — input guard, picker depth, output destination."""

from __future__ import annotations

from pathlib import Path

import pytest

import docgen
from docsum.cli import (
    EXIT_ERROR,
    EXIT_OK,
    OUTPUT_DIRNAME,
    _default_outdir,
    _title_from,
    build_parser,
    find_markdown,
    is_markdown,
    main,
    parse_selection,
)


@pytest.fixture
def converted(isolated_cwd):
    """A tree shaped like doc2md output: outputs/<slug>/<slug>.md."""
    target = isolated_cwd / OUTPUT_DIRNAME / "report"
    target.mkdir(parents=True)
    markdown = target / "report.md"
    markdown.write_text("# Report Title\n\n" + docgen.PARAGRAPH)
    return markdown


# --- input guard -----------------------------------------------------------


@pytest.mark.parametrize("name", ["a.md", "a.markdown", "A.MD"])
def test_markdown_is_recognized(tmp_path, name):
    assert is_markdown(tmp_path / name)


@pytest.mark.parametrize("name", ["a.pdf", "a.txt", "a.docx", "a"])
def test_non_markdown_is_rejected(tmp_path, name):
    assert not is_markdown(tmp_path / name)


def test_pdf_argument_tells_the_user_to_run_doc2md(workdir, capsys):
    assert main(["review.pdf"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "is not Markdown" in err
    assert "doc2md review.pdf" in err


def test_missing_file_is_reported(isolated_cwd, capsys):
    assert main(["nope.md"]) == EXIT_ERROR
    assert "not a file" in capsys.readouterr().err


def test_empty_markdown_is_reported(isolated_cwd, capsys):
    (isolated_cwd / "blank.md").write_text("   \n")
    assert main(["blank.md"]) == EXIT_ERROR
    assert "is empty" in capsys.readouterr().err


def test_missing_credentials_produce_a_readable_error(converted, capsys):
    assert main([str(converted)]) == EXIT_ERROR
    assert "no OpenAI credentials" in capsys.readouterr().err


# --- discovery -------------------------------------------------------------


def test_picker_finds_files_two_levels_down(converted, isolated_cwd):
    """doc2md writes outputs/<slug>/<slug>.md — two levels below the cwd."""
    found = find_markdown(isolated_cwd)
    assert converted in found


def test_picker_finds_files_in_the_cwd(isolated_cwd):
    (isolated_cwd / "top.md").write_text("# T\n")
    assert (isolated_cwd / "top.md") in find_markdown(isolated_cwd)


def test_existing_summaries_are_excluded(isolated_cwd):
    (isolated_cwd / "a.md").write_text("# A\n")
    (isolated_cwd / "a.summary.md").write_text("# S\n")
    names = [p.name for p in find_markdown(isolated_cwd)]
    assert "a.md" in names
    assert "a.summary.md" not in names


def test_hidden_directories_are_skipped(isolated_cwd):
    hidden = isolated_cwd / ".git"
    hidden.mkdir()
    (hidden / "x.md").write_text("# X\n")
    assert find_markdown(isolated_cwd) == []


def test_depth_limit_is_respected(isolated_cwd):
    deep = isolated_cwd / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep.md").write_text("# D\n")
    assert find_markdown(isolated_cwd) == []


# --- output destination ----------------------------------------------------


def test_summary_stays_beside_an_input_under_outputs(converted):
    assert _default_outdir(converted) == converted.parent


def test_summary_of_a_stray_file_goes_into_outputs(isolated_cwd):
    stray = isolated_cwd / "loose.md"
    stray.write_text("# L\n")
    assert _default_outdir(stray) == isolated_cwd / OUTPUT_DIRNAME


def test_absolute_path_outside_outputs_goes_into_outputs(isolated_cwd, tmp_path):
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("# E\n")
    assert _default_outdir(elsewhere) == isolated_cwd / OUTPUT_DIRNAME


# --- title -----------------------------------------------------------------


def test_title_comes_from_the_first_h1():
    assert _title_from("# Real Title\n\nbody", Path("file.md")) == "Real Title"


def test_title_falls_back_to_the_filename():
    assert _title_from("no headings here", Path("my-report.md")) == "my report"


def test_title_ignores_h2():
    assert _title_from("## Only H2\n", Path("name.md")) == "name"


def test_title_ignores_hash_in_a_code_fence():
    markdown = "```\n# not a title\n```\n\n# Real\n"
    assert _title_from(markdown, Path("f.md")) == "Real"


# --- argument parsing ------------------------------------------------------


def test_model_flag_overrides_config(converted, capsys):
    main([str(converted), "--model", "gpt-override", "-v"])
    assert "model=gpt-override" in capsys.readouterr().err


def test_list_models_flag_exists():
    args = build_parser().parse_args(["--list-models"])
    assert args.list_models is True


def test_selection_parser_matches_doc2md():
    from doc2md.cli import parse_selection as other

    for raw in ("2", "1,3", "2-4", "all"):
        assert parse_selection(raw, 5) == other(raw, 5)


# --- picker ----------------------------------------------------------------


def test_picker_cancels_on_empty_input(converted, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _="": "")
    assert main([]) == EXIT_OK


def test_picker_reports_when_nothing_is_found(isolated_cwd, capsys):
    assert main([]) == EXIT_OK
    assert "No Markdown files" in capsys.readouterr().err


def test_picker_suggests_doc2md_when_empty(isolated_cwd, capsys):
    main([])
    assert "doc2md" in capsys.readouterr().err


# --- --list-models ---------------------------------------------------------


def _client_listing(monkeypatch, models):
    """Wire docsum.cli.Client to a stub that returns `models`."""
    import types

    from docsum.client import Client

    class Stub(Client):
        def list_models(self):
            return sorted(models)

    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: Stub(**kwargs))


def test_list_models_prints_chat_models(isolated_cwd, monkeypatch, capsys):
    _client_listing(monkeypatch, ["gpt-4.1", "gpt-5.4", "text-embedding-3-small"])
    assert main(["--list-models"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "gpt-4.1" in out
    assert "gpt-5.4" in out
    assert "3 total" in out


def test_list_models_marks_the_configured_model(isolated_cwd, monkeypatch, capsys):
    _client_listing(monkeypatch, ["gpt-4.1", "gpt-5.4"])
    main(["--list-models"])
    out = capsys.readouterr().out
    assert "  * gpt-5.4" in out
    assert "currently configured" in out


def test_list_models_warns_when_the_configured_model_is_absent(
    isolated_cwd, monkeypatch, capsys
):
    _client_listing(monkeypatch, ["gpt-4.1"])
    main(["--list-models"])
    captured = capsys.readouterr()
    assert "is NOT in this list" in captured.err


def test_list_models_summarizes_non_chat_models(isolated_cwd, monkeypatch, capsys):
    _client_listing(monkeypatch, ["gpt-5.4", "whisper-1", "dall-e-3", "text-embedding-3-small"])
    main(["--list-models"])
    assert "non-chat models" in capsys.readouterr().out


def test_list_models_reports_an_empty_account(isolated_cwd, monkeypatch, capsys):
    _client_listing(monkeypatch, [])
    assert main(["--list-models"]) == EXIT_ERROR
    assert "no models" in capsys.readouterr().err


def test_list_models_surfaces_an_auth_failure(isolated_cwd, monkeypatch, capsys):
    from docsum.client import Client, SummarizeError

    class Failing(Client):
        def list_models(self):
            raise SummarizeError("docsum: no OpenAI credentials found.")

    monkeypatch.setattr("docsum.cli.Client", lambda **kwargs: Failing(**kwargs))
    assert main(["--list-models"]) == EXIT_ERROR
    assert "no OpenAI credentials" in capsys.readouterr().err


def test_list_models_does_not_need_a_file_argument(isolated_cwd, monkeypatch, capsys):
    """It must not fall through to the picker."""
    _client_listing(monkeypatch, ["gpt-5.4"])
    monkeypatch.setattr("builtins.input", lambda _="": pytest.fail("picker was invoked"))
    assert main(["--list-models"]) == EXIT_OK
