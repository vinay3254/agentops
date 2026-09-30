from types import SimpleNamespace

import httpx
import openai
import pytest

from agentops.llm import LLMError, OpenRouterClient


def fake_response(content="hello", tool_calls=None, cost=0.002):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10, model_extra={"cost": cost})
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeOpenAI:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.kwargs = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def conn_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x"))


def test_parses_text_tool_calls_and_usage():
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_logs", arguments='{"service":"api"}'))
    fake = FakeOpenAI([fake_response(content=None, tool_calls=[tc])])
    llm = OpenRouterClient(model="m", client=fake, sleep=lambda s: None)
    resp = llm.complete([{"role": "user", "content": "x"}], [{"type": "function"}])
    assert resp.tool_calls[0].name == "read_logs"
    assert resp.tool_calls[0].arguments == '{"service":"api"}'
    assert resp.prompt_tokens == 50 and resp.completion_tokens == 10 and resp.cost == 0.002
    assert resp.raw_message["tool_calls"][0]["function"]["name"] == "read_logs"
    assert fake.kwargs[0]["model"] == "m" and fake.kwargs[0]["temperature"] == 0


def test_plain_text_response():
    llm = OpenRouterClient(model="m", client=FakeOpenAI([fake_response("done")]), sleep=lambda s: None)
    resp = llm.complete([], [])
    assert resp.text == "done" and resp.tool_calls == []
    assert resp.raw_message == {"role": "assistant", "content": "done"}


def test_retries_then_succeeds():
    sleeps = []
    fake = FakeOpenAI([conn_error(), conn_error(), fake_response()])
    llm = OpenRouterClient(model="m", client=fake, sleep=sleeps.append)
    assert llm.complete([], []).text == "hello"
    assert sleeps == [1, 2]


def test_gives_up_after_three_attempts():
    fake = FakeOpenAI([conn_error(), conn_error(), conn_error()])
    llm = OpenRouterClient(model="m", client=fake, sleep=lambda s: None)
    with pytest.raises(LLMError):
        llm.complete([], [])


def test_missing_key_and_model(monkeypatch):
    monkeypatch.setattr("agentops.llm.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AGENTOPS_MODEL", "m")
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterClient()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k" * 10)
    monkeypatch.delenv("AGENTOPS_MODEL", raising=False)
    with pytest.raises(LLMError, match="AGENTOPS_MODEL"):
        OpenRouterClient()


def status_error(cls, code):
    resp = httpx.Response(code, request=httpx.Request("POST", "http://x"))
    return cls("boom", response=resp, body=None)


def test_real_client_constructed_without_sdk_retries(monkeypatch):
    calls = []
    monkeypatch.setattr("agentops.llm.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("agentops.llm.OpenAI", lambda **kw: calls.append(kw) or object())
    monkeypatch.setenv("OPENROUTER_API_KEY", "k" * 10)
    monkeypatch.setenv("AGENTOPS_MODEL", "m")
    OpenRouterClient()
    assert len(calls) == 1
    assert calls[0]["max_retries"] == 0
    assert calls[0]["timeout"] == 60.0
    assert calls[0]["base_url"] == "https://openrouter.ai/api/v1"


def _resp_with_usage(usage):
    message = SimpleNamespace(content="hi", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


@pytest.mark.parametrize(
    "usage, tokens, cost",
    [
        (None, (0, 0), 0.0),
        (SimpleNamespace(prompt_tokens=None, completion_tokens=7, model_extra={"cost": 0.5}), (0, 7), 0.5),
        (SimpleNamespace(prompt_tokens=3, completion_tokens=None, model_extra={"cost": 0.5}), (3, 0), 0.5),
        (SimpleNamespace(prompt_tokens=3, completion_tokens=4, model_extra=None), (3, 4), 0.0),
        (SimpleNamespace(prompt_tokens=3, completion_tokens=4, model_extra={"cost": None}), (3, 4), 0.0),
    ],
)
def test_missing_usage_fields_default_to_zero(usage, tokens, cost):
    llm = OpenRouterClient(model="m", client=FakeOpenAI([_resp_with_usage(usage)]), sleep=lambda s: None)
    resp = llm.complete([], [])
    assert (resp.prompt_tokens, resp.completion_tokens) == tokens
    assert resp.cost == cost


@pytest.mark.parametrize("choices", [[], None])
def test_no_choices_is_retried_then_raises_llm_error(choices):
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, model_extra={})
    fake = FakeOpenAI([SimpleNamespace(choices=choices, usage=usage) for _ in range(3)])
    sleeps = []
    llm = OpenRouterClient(model="m", client=fake, sleep=sleeps.append)
    with pytest.raises(LLMError, match="no choices after 3 attempts"):
        llm.complete([], [])
    assert len(fake.kwargs) == 3 and sleeps == [1, 2]


def test_empty_choices_then_valid_response_is_returned():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, model_extra={})
    fake = FakeOpenAI([SimpleNamespace(choices=[], usage=usage), fake_response("ok")])
    sleeps = []
    llm = OpenRouterClient(model="m", client=fake, sleep=sleeps.append)
    assert llm.complete([], []).text == "ok"
    assert len(fake.kwargs) == 2 and sleeps == [1]


@pytest.mark.parametrize(
    "cls, code", [(openai.AuthenticationError, 401), (openai.BadRequestError, 400), (openai.NotFoundError, 404)]
)
def test_non_retryable_status_error_is_sanitized(cls, code):
    fake = FakeOpenAI([status_error(cls, code)])
    sleeps = []
    llm = OpenRouterClient(model="m", client=fake, sleep=sleeps.append)
    with pytest.raises(LLMError, match=f"HTTP {code}") as ei:
        llm.complete([], [])
    assert len(fake.kwargs) == 1 and sleeps == []
    assert ei.value.__cause__ is None and ei.value.__suppress_context__ is True


def test_exhausted_5xx_includes_status_code():
    fake = FakeOpenAI([status_error(openai.InternalServerError, 503) for _ in range(3)])
    sleeps = []
    llm = OpenRouterClient(model="m", client=fake, sleep=sleeps.append)
    with pytest.raises(LLMError, match="503"):
        llm.complete([], [])
    assert len(fake.kwargs) == 3 and sleeps == [1, 2]
