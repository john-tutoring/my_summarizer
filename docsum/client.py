"""Thin wrapper over the OpenAI Chat Completions API.

Chat Completions rather than Responses: it is the surface supported across the
widest range of model generations, so switching `model` in the config is far
less likely to need a code change.

Always streams. Summaries of long chapters can run for a while, and a
non-streaming request at these token limits risks an HTTP timeout.

Model-parameter compatibility varies by generation — reasoning models reject
`temperature`, older models want `max_tokens` where newer ones want
`max_completion_tokens`. Rather than hard-code a table that goes stale, the
client sends the modern parameter set and retries once without whatever
parameter the API names in a 400.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import openai

from .config import Config

AUTH_HELP = (
    "docsum: no OpenAI credentials found.\n"
    "  Set an API key:  export OPENAI_API_KEY='sk-...'\n"
    "  Keys are created at https://platform.openai.com/api-keys"
)

# Models that take a reasoning_effort parameter. Everything else rejects it.
REASONING_MODEL_RE = re.compile(r"^(o\d|gpt-5|gpt-6)", re.IGNORECASE)

# docsum's effort vocabulary is the superset; OpenAI accepts a narrower set.
EFFORT_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# Parameter names the API may reject depending on the model generation.
OPTIONAL_PARAMS = ("max_completion_tokens", "max_tokens", "reasoning_effort", "temperature")


class SummarizeError(Exception):
    """Raised for a failure the user should see as a message, not a traceback."""


def is_reasoning_model(model: str) -> bool:
    return bool(REASONING_MODEL_RE.match(model.strip()))


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, usage) -> None:
        if usage is None:
            return
        self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.output_tokens += getattr(usage, "completion_tokens", 0) or 0

    def __str__(self) -> str:
        return (
            f"{self.calls} request(s), "
            f"{self.input_tokens:,} input + {self.output_tokens:,} output tokens"
        )


@dataclass
class Client:
    cfg: Config
    verbose: bool = False
    usage: Usage = field(default_factory=Usage)
    _client: "openai.OpenAI | None" = None
    _dropped: set[str] = field(default_factory=set)

    def _ensure(self) -> "openai.OpenAI":
        if self._client is None:
            try:
                # Bare constructor: reads OPENAI_API_KEY (and OPENAI_BASE_URL,
                # OPENAI_ORG_ID, OPENAI_PROJECT) from the environment.
                self._client = openai.OpenAI(max_retries=5)
            except openai.OpenAIError as exc:
                raise SummarizeError(f"{AUTH_HELP}\n  ({exc})") from exc
        return self._client

    def list_models(self) -> list[str]:
        """Model ids available to this key, newest-looking first."""
        client = self._ensure()
        try:
            return sorted(m.id for m in client.models.list())
        except openai.AuthenticationError as exc:
            raise SummarizeError(f"{AUTH_HELP}\n  ({exc})") from exc
        except openai.APIConnectionError as exc:
            raise SummarizeError(f"docsum: could not reach the API: {exc}") from exc
        except openai.APIStatusError as exc:
            raise SummarizeError(f"docsum: API error {exc.status_code}: {exc}") from exc

    def _build_params(self, system: str, user: str) -> dict:
        params: dict = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": self.cfg.max_tokens,
        }
        if is_reasoning_model(self.cfg.model):
            params["reasoning_effort"] = EFFORT_MAP.get(self.cfg.effort, "medium")

        for name in self._dropped:
            params.pop(name, None)
        # max_tokens is the older spelling; only used as a fallback below.
        if "max_completion_tokens" in self._dropped and "max_tokens" not in self._dropped:
            params["max_tokens"] = self.cfg.max_tokens
        return params

    def complete(self, system: str, user: str) -> str:
        """One request. Returns the response text."""
        for _ in range(len(OPTIONAL_PARAMS) + 1):
            params = self._build_params(system, user)
            try:
                return self._run(params)
            except openai.BadRequestError as exc:
                offender = self._unsupported_param(exc, params)
                if offender is None:
                    raise SummarizeError(f"docsum: request rejected: {exc}") from exc
                self._dropped.add(offender)
                if self.verbose:
                    print(f"docsum: {self.cfg.model} rejected {offender}; retrying without it")
        raise SummarizeError("docsum: could not find a parameter set this model accepts")

    def _run(self, params: dict) -> str:
        client = self._ensure()
        parts: list[str] = []
        finish_reason = ""

        try:
            stream = client.chat.completions.create(**params)
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    self.usage.add(chunk.usage)
                for choice in chunk.choices or []:
                    delta = getattr(choice, "delta", None)
                    if delta is not None and getattr(delta, "content", None):
                        parts.append(delta.content)
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
        except openai.BadRequestError:
            # Must reach complete(), which decides whether to drop an
            # unsupported parameter and retry. BadRequestError subclasses
            # APIStatusError, so without this it would be caught below.
            raise
        except openai.AuthenticationError as exc:
            raise SummarizeError(f"{AUTH_HELP}\n  ({exc})") from exc
        except openai.PermissionDeniedError as exc:
            raise SummarizeError(
                f"docsum: this key cannot use {self.cfg.model!r}. "
                f"Run 'docsum --list-models' to see what it can use.\n  ({exc})"
            ) from exc
        except openai.NotFoundError as exc:
            raise SummarizeError(
                f"docsum: model {self.cfg.model!r} not found. "
                f"Run 'docsum --list-models' to see available models.\n  ({exc})"
            ) from exc
        except openai.RateLimitError as exc:
            raise SummarizeError(
                f"docsum: rate limited or out of quota. Check your billing at "
                f"https://platform.openai.com/usage\n  ({exc})"
            ) from exc
        except openai.APIConnectionError as exc:
            raise SummarizeError(f"docsum: could not reach the API: {exc}") from exc
        except openai.APIStatusError as exc:
            raise SummarizeError(f"docsum: API error {exc.status_code}: {exc}") from exc

        self.usage.calls += 1
        text = "".join(parts).strip()

        if not text:
            if finish_reason == "length":
                raise SummarizeError(
                    "docsum: the model produced no text before hitting the token limit. "
                    "Raise max_tokens in docsum.yaml (reasoning models spend part of it "
                    "on internal reasoning)."
                )
            if finish_reason == "content_filter":
                raise SummarizeError("docsum: the response was blocked by a content filter.")
            raise SummarizeError("docsum: the model returned an empty response")

        if finish_reason == "length":
            text += "\n\n*[summary truncated: hit the token limit — raise max_tokens in docsum.yaml]*"
        return text

    @staticmethod
    def _unsupported_param(exc: openai.BadRequestError, params: dict) -> str | None:
        """Name the parameter a 400 complains about, if it is one we can drop."""
        message = str(exc).lower()
        if "unsupported" not in message and "not supported" not in message:
            return None
        for name in OPTIONAL_PARAMS:
            if name in params and name in message:
                return name
        return None
