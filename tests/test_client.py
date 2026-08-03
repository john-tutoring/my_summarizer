"""docsum.client — parameter compatibility, error mapping, usage accounting.

Nothing here touches the network. `Client` resolves its SDK object lazily, so
tests set `client._client` directly.
"""

from __future__ import annotations

import openai
import pytest

from conftest import bad_request, status_error
from docsum.client import (
    AUTH_HELP,
    EFFORT_MAP,
    OPTIONAL_PARAMS,
    Client,
    SummarizeError,
    Usage,
    is_reasoning_model,
)
from docsum.config import Config


# --- model classification --------------------------------------------------


@pytest.mark.parametrize(
    "model, reasoning",
    [
        ("gpt-4.1", False),
        ("gpt-4.1-mini", False),
        ("gpt-4o", False),
        ("gpt-3.5-turbo", False),
        ("o1", True),
        ("o3", True),
        ("o4-mini", True),
        ("gpt-5", True),
        ("gpt-5-mini", True),
        ("gpt-5.4", True),
        ("gpt-6", True),
        ("GPT-5.4", True),  # case-insensitive
    ],
)
def test_is_reasoning_model(model, reasoning):
    assert is_reasoning_model(model) is reasoning


def test_effort_map_covers_every_config_value():
    from docsum.config import VALID_EFFORTS

    assert set(EFFORT_MAP) == set(VALID_EFFORTS)


def test_high_tier_efforts_collapse_to_high():
    assert EFFORT_MAP["xhigh"] == "high"
    assert EFFORT_MAP["max"] == "high"


# --- request shape ---------------------------------------------------------


def test_reasoning_effort_sent_only_for_reasoning_models(fake_openai):
    client, fake = fake_openai(Config(model="o3", effort="medium"))
    client.complete("sys", "user")
    assert fake.calls[0]["reasoning_effort"] == "medium"

    client, fake = fake_openai(Config(model="gpt-4.1"))
    client.complete("sys", "user")
    assert "reasoning_effort" not in fake.calls[0]


def test_request_streams_and_asks_for_usage(fake_openai):
    client, fake = fake_openai()
    client.complete("sys", "user")
    assert fake.calls[0]["stream"] is True
    assert fake.calls[0]["stream_options"] == {"include_usage": True}


def test_system_and_user_messages_are_ordered(fake_openai):
    client, fake = fake_openai()
    client.complete("SYSTEM TEXT", "USER TEXT")
    messages = fake.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": "SYSTEM TEXT"}
    assert messages[1] == {"role": "user", "content": "USER TEXT"}


def test_max_completion_tokens_comes_from_config(fake_openai):
    client, fake = fake_openai(Config(max_tokens=1234))
    client.complete("s", "u")
    assert fake.calls[0]["max_completion_tokens"] == 1234


# --- parameter fallback ----------------------------------------------------


def test_no_rejection_means_one_attempt(fake_openai):
    client, fake = fake_openai(Config(model="o3"))
    assert client.complete("s", "u") == "A summary."
    assert len(fake.calls) == 1


def test_rejecting_max_completion_tokens_falls_back_to_max_tokens(fake_openai):
    client, fake = fake_openai(Config(model="o3"), reject=("max_completion_tokens",))
    assert client.complete("s", "u") == "A summary."
    assert len(fake.calls) == 2
    assert "max_tokens" in fake.calls[1]
    assert "max_completion_tokens" not in fake.calls[1]


def test_rejecting_reasoning_effort_drops_it(fake_openai):
    client, fake = fake_openai(Config(model="o3"), reject=("reasoning_effort",))
    assert client.complete("s", "u") == "A summary."
    assert "reasoning_effort" not in fake.calls[-1]


def test_rejecting_both_converges(fake_openai):
    client, fake = fake_openai(
        Config(model="o3"), reject=("max_completion_tokens", "reasoning_effort")
    )
    assert client.complete("s", "u") == "A summary."
    assert len(fake.calls) == 3
    assert fake.sent_params(2) == {"max_tokens"}


def test_dropped_parameters_are_remembered_across_calls(fake_openai):
    """A second chapter must not repeat the discovery round-trip."""
    client, fake = fake_openai(Config(model="o3"), reject=("reasoning_effort",))
    client.complete("s", "u")
    first_round = len(fake.calls)
    client.complete("s", "u2")
    assert len(fake.calls) == first_round + 1


def test_unfixable_bad_request_surfaces_after_one_attempt(fake_openai):
    client, fake = fake_openai(raises=bad_request("Invalid value for 'messages': too long"))
    with pytest.raises(SummarizeError, match="request rejected"):
        client.complete("s", "u")
    assert len(fake.calls) == 1


def test_optional_params_list_matches_what_is_sent(fake_openai):
    client, fake = fake_openai(Config(model="o3"))
    client.complete("s", "u")
    assert fake.sent_params(0) <= set(OPTIONAL_PARAMS)


# --- error mapping ---------------------------------------------------------


def test_authentication_error_gives_setup_help(fake_openai):
    client, _ = fake_openai(
        raises=status_error(openai.AuthenticationError, 401, "Incorrect API key")
    )
    with pytest.raises(SummarizeError) as excinfo:
        client.complete("s", "u")
    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_permission_error_points_at_list_models(fake_openai):
    client, _ = fake_openai(
        Config(model="gpt-9"),
        raises=status_error(openai.PermissionDeniedError, 403, "no access"),
    )
    with pytest.raises(SummarizeError, match="--list-models"):
        client.complete("s", "u")


def test_not_found_names_the_model(fake_openai):
    client, _ = fake_openai(
        Config(model="gpt-nope"),
        raises=status_error(openai.NotFoundError, 404, "missing"),
    )
    with pytest.raises(SummarizeError, match="gpt-nope"):
        client.complete("s", "u")


def test_rate_limit_mentions_billing(fake_openai):
    client, _ = fake_openai(raises=status_error(openai.RateLimitError, 429, "slow down"))
    with pytest.raises(SummarizeError, match="billing"):
        client.complete("s", "u")


def test_connection_error_is_readable(fake_openai):
    request = openai.APIConnectionError(request=None).request if False else None
    client, _ = fake_openai(raises=openai.APIConnectionError(request=request))
    with pytest.raises(SummarizeError, match="could not reach the API"):
        client.complete("s", "u")


def test_other_status_errors_include_the_code(fake_openai):
    client, _ = fake_openai(raises=status_error(openai.InternalServerError, 500, "boom"))
    with pytest.raises(SummarizeError, match="API error 500"):
        client.complete("s", "u")


def test_missing_credentials_message_is_actionable():
    assert "OPENAI_API_KEY" in AUTH_HELP
    assert "platform.openai.com" in AUTH_HELP


# --- response handling -----------------------------------------------------


def test_empty_response_raises(fake_openai):
    client, _ = fake_openai(reply="")
    with pytest.raises(SummarizeError, match="empty response"):
        client.complete("s", "u")


def test_truncated_response_is_flagged_inline(fake_openai):
    client, _ = fake_openai(reply="partial text", finish="length")
    result = client.complete("s", "u")
    assert result.startswith("partial text")
    assert "summary truncated" in result
    assert "max_tokens" in result


def test_empty_with_length_finish_explains_the_token_budget(fake_openai):
    client, _ = fake_openai(reply="", finish="length")
    with pytest.raises(SummarizeError, match="before hitting the token limit"):
        client.complete("s", "u")


def test_content_filter_is_reported(fake_openai):
    client, _ = fake_openai(reply="", finish="content_filter")
    with pytest.raises(SummarizeError, match="content filter"):
        client.complete("s", "u")


# --- usage -----------------------------------------------------------------


def test_usage_accumulates_across_calls(fake_openai):
    client, _ = fake_openai()
    client.complete("s", "u")
    client.complete("s", "u")
    assert client.usage.calls == 2
    assert client.usage.input_tokens == 20
    assert client.usage.output_tokens == 10


def test_usage_string_is_human_readable():
    usage = Usage(input_tokens=1234, output_tokens=567, calls=3)
    text = str(usage)
    assert "3 request(s)" in text
    assert "1,234" in text


def test_usage_ignores_a_missing_usage_object():
    usage = Usage()
    usage.add(None)
    assert usage.input_tokens == 0


def test_failed_attempts_do_not_count_as_calls(fake_openai):
    """A rejected parameter round-trip is one logical request, not two."""
    client, _ = fake_openai(Config(model="o3"), reject=("reasoning_effort",))
    client.complete("s", "u")
    assert client.usage.calls == 1


# --- construction ----------------------------------------------------------


def test_missing_credentials_raise_summarize_error(monkeypatch):
    client = Client(cfg=Config())
    with pytest.raises(SummarizeError, match="no OpenAI credentials"):
        client.complete("s", "u")


def test_list_models_sorts_ids(fake_openai):
    import types

    client, _ = fake_openai()
    client._client.models = types.SimpleNamespace(
        list=lambda: [types.SimpleNamespace(id="b"), types.SimpleNamespace(id="a")]
    )
    assert client.list_models() == ["a", "b"]
