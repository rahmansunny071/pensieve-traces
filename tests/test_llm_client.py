"""OpenAICompatibleClient tests: fake opener only, no network."""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from pensieve_traces.builders.llm import ModelClientError, OpenAICompatibleClient

SECRET = "sk-test-SECRET-123"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, body=None, exc=None):
        self.body, self.exc, self.requests = body, exc, []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.exc:
            raise self.exc
        return FakeResponse(
            self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        )


OK = {"choices": [{"message": {"content": "hello"}}]}
MSGS = [{"role": "user", "content": "hi"}]


def test_request_shape_and_reply(monkeypatch) -> None:
    monkeypatch.setenv("KEYVAR", SECRET)
    op = Opener(OK)
    client = OpenAICompatibleClient("m1", api_key_env="KEYVAR", timeout=7, opener=op)
    assert client.chat(MSGS) == "hello"
    req, timeout = op.requests[0]
    assert req.full_url == "http://localhost:11434/v1/chat/completions"
    assert req.get_method() == "POST" and timeout == 7
    assert json.loads(req.data) == {"model": "m1", "messages": MSGS, "temperature": 0}
    # Unredirected, so urllib's redirect handler never forwards it to another host.
    assert req.unredirected_hdrs["Authorization"] == f"Bearer {SECRET}"
    assert "Authorization" not in req.headers


def test_no_key_header_without_env_name() -> None:
    op = Opener(OK)
    OpenAICompatibleClient("m", base_url="http://x/v1/", opener=op).chat(MSGS)
    req = op.requests[0][0]
    assert req.full_url == "http://x/v1/chat/completions"
    assert not req.unredirected_hdrs.get("Authorization")


def test_unset_env_var_names_variable_only(monkeypatch) -> None:
    monkeypatch.delenv("NOPE_KEY", raising=False)
    client = OpenAICompatibleClient("m", api_key_env="NOPE_KEY", opener=Opener(OK))
    with pytest.raises(ModelClientError, match="NOPE_KEY"):
        client.chat(MSGS)


@pytest.mark.parametrize(
    "op",
    [
        Opener(
            exc=urllib.error.HTTPError(
                "http://u", 401, "bad " + SECRET, {}, io.BytesIO(SECRET.encode())
            )
        ),
        Opener(exc=urllib.error.URLError(SECRET)),
        Opener(exc=TimeoutError(SECRET)),
        Opener(body=b"not json " + SECRET.encode()),
        Opener(body={"choices": []}),
        Opener(body={"choices": [{"message": {"content": None}}]}),
        Opener(body={"choices": [{"message": {"content": 5}}]}),
    ],
)
def test_errors_are_modelclienterror_without_secrets(op, monkeypatch, caplog) -> None:
    monkeypatch.setenv("KEYVAR", SECRET)
    client = OpenAICompatibleClient("m", api_key_env="KEYVAR", opener=op)
    with caplog.at_level(logging.DEBUG), pytest.raises(ModelClientError) as err:
        client.chat(MSGS)
    assert SECRET not in str(err.value) and SECRET not in repr(err.value)
    assert err.value.__cause__ is None
    assert err.value.__context__ is None or err.value.__suppress_context__
    assert SECRET not in caplog.text and SECRET not in repr(client)


def test_http_error_has_status() -> None:
    exc = urllib.error.HTTPError("http://u", 503, "x", {}, io.BytesIO(b"body"))
    with pytest.raises(ModelClientError, match="503"):
        OpenAICompatibleClient("m", opener=Opener(exc=exc)).chat(MSGS)
