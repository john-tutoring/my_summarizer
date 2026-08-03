"""Shared fixtures.

Two things matter here beyond convenience:

1. **Isolation.** Both CLIs write a default config into the cwd, and both config
   loaders search `~/.config/`. Without redirecting HOME and the cwd, running
   the suite would read and write the developer's real files. `isolated_cwd`
   does both and is applied automatically to every test.

2. **No network, ever.** `docsum.client.Client` resolves its SDK object lazily
   through `_ensure()`, so tests inject `client._client` directly. Nothing in
   this suite needs an API key or a socket.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import openai
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import docgen  # noqa: E402


# --- isolation -------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test in a scratch cwd with a scratch HOME.

    Autouse: a test that forgets this would silently clobber real config.
    """
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Ensure no test can accidentally reach the real API."""
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT"):
        monkeypatch.delenv(name, raising=False)


# --- document fixtures (session-scoped: generation is the slow part) -------


@pytest.fixture(scope="session")
def docs(tmp_path_factory) -> dict[str, Path]:
    """Every sample document, built once for the whole run."""
    root = tmp_path_factory.mktemp("docs")
    chart = root / "chart.png"
    chart.write_bytes(docgen.png_bytes())
    fig1 = root / "fig1.png"
    fig1.write_bytes(docgen.png_bytes((120, 30, 200)))

    built = {
        "chart_png": chart,
        "fig1_png": fig1,
        "pdf": docgen.make_pdf(root / "review.pdf"),
        "pdf_encrypted": docgen.make_encrypted_pdf(root / "locked.pdf"),
        "docx": docgen.make_docx(root / "memo.docx", chart),
        "pptx": docgen.make_pptx(root / "deck.pptx", chart),
        "epub": docgen.make_epub(root / "book.epub"),
        "odt": docgen.make_odt(root / "memo.odt"),
        "rtf": docgen.make_rtf(root / "legacy.rtf"),
        "html": docgen.make_html(root / "page.html"),
        "markdown": docgen.make_markdown(root / "guide.md"),
        "txt": docgen.make_txt(root / "notes.txt"),
        "csv": docgen.make_csv(root / "stock.csv"),
        "xlsx": docgen.make_xlsx(root / "sheet.xlsx"),
    }
    return built


@pytest.fixture
def workdir(docs, isolated_cwd) -> Path:
    """A scratch cwd populated with copies of every sample document.

    Copies rather than the originals, because conversion writes alongside them
    and the session fixtures must stay pristine.
    """
    import shutil

    for path in docs.values():
        shutil.copy(path, isolated_cwd / path.name)
    return isolated_cwd


@pytest.fixture
def cfg():
    from doc2md.config import Config

    return Config()


# --- fake OpenAI plumbing --------------------------------------------------


def _chunk(text: str | None = None, finish: str | None = None, usage=None):
    """One streaming chunk shaped like the SDK's."""
    choices = []
    if text is not None or finish is not None:
        delta = types.SimpleNamespace(content=text)
        choices = [types.SimpleNamespace(delta=delta, finish_reason=finish)]
    return types.SimpleNamespace(choices=choices, usage=usage)


def usage_chunk(prompt_tokens: int = 10, completion_tokens: int = 5):
    return _chunk(usage=types.SimpleNamespace(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    ))


def bad_request(message: str) -> openai.BadRequestError:
    """A real BadRequestError, which the SDK requires an httpx.Response to build."""
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {"message": message}})
    return openai.BadRequestError(message, response=response, body={"error": {"message": message}})


def status_error(cls, status: int, message: str):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body={"error": {"message": message}})


@dataclass
class FakeCompletions:
    """Records requests and returns scripted responses.

    `reject` names parameters that make this fake raise "Unsupported parameter",
    which is how the client's fallback logic is driven.
    """

    reply: str = "A summary."
    reject: tuple[str, ...] = ()
    finish: str = "stop"
    raises: Exception | None = None
    echo_target: bool = False
    calls: list[dict] = field(default_factory=list)

    def create(self, **params):
        self.calls.append(params)
        if self.raises is not None:
            raise self.raises
        for name in self.reject:
            if name in params:
                raise bad_request(f"Unsupported parameter: '{name}' is not supported with this model.")

        text = self.reply
        if self.echo_target:
            # Reply with exactly the requested number of words, so ratio maths
            # can be asserted deterministically.
            user = params["messages"][1]["content"]
            count = int(user.split("approximately ")[1].split(" words")[0])
            text = " ".join(["word"] * count)
        return [_chunk(text), _chunk(finish=self.finish), usage_chunk()]

    # -- assertion helpers --

    @property
    def user_messages(self) -> list[str]:
        return [c["messages"][1]["content"] for c in self.calls]

    @property
    def system_messages(self) -> list[str]:
        return [c["messages"][0]["content"] for c in self.calls]

    def sent_params(self, index: int = 0) -> set[str]:
        interesting = {"max_completion_tokens", "max_tokens", "reasoning_effort", "temperature"}
        return {k for k in self.calls[index] if k in interesting}


@pytest.fixture
def fake_openai():
    """Factory: build a docsum Client wired to a FakeCompletions."""
    from docsum.client import Client
    from docsum.config import Config

    def build(config: "Config | None" = None, **kwargs) -> tuple["Client", FakeCompletions]:
        completions = FakeCompletions(**kwargs)
        client = Client(cfg=config or Config())
        client._client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=completions),
            models=types.SimpleNamespace(list=lambda: []),
        )
        return client, completions

    return build


class ImportBlocker:
    """Makes named top-level packages unimportable, for the independence tests."""

    def __init__(self, blocked: set[str]) -> None:
        self.blocked = blocked

    def find_module(self, name, path=None):  # noqa: D102 - legacy finder protocol
        if name.split(".")[0] in self.blocked:
            return self
        return None

    def load_module(self, name):  # noqa: D102
        raise ImportError(f"{name} is blocked for this test")
