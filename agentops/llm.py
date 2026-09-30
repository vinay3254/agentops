from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import openai
from dotenv import load_dotenv
from openai import OpenAI


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    cost: float
    raw_message: dict = field(default_factory=dict)


class OpenRouterClient:
    def __init__(self, model: str | None = None, client=None, sleep=time.sleep):
        load_dotenv()  # never overrides real environment variables
        self.model = model or os.getenv("AGENTOPS_MODEL")
        if not self.model:
            raise LLMError("AGENTOPS_MODEL is not set")
        if client is None:
            key = os.getenv("OPENROUTER_API_KEY")
            if not key:
                raise LLMError("OPENROUTER_API_KEY is not set")
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1", api_key=key, max_retries=0, timeout=60.0
            )
        self.client = client
        self._sleep = sleep

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=0,
                    extra_body={"usage": {"include": True}},
                )
                if not getattr(resp, "choices", None):
                    last = LLMError("provider returned no choices")
                    if attempt < 2:
                        self._sleep(2 ** attempt)
                    continue
                return self._parse(resp)
            except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as e:
                last = e
                if attempt < 2:
                    self._sleep(2 ** attempt)
            except openai.APIStatusError as e:
                # Non-retryable (401/400/404...). Drop the SDK exception: it carries the request.
                raise LLMError(f"LLM request rejected: HTTP {e.status_code}") from None
        if isinstance(last, LLMError):
            raise LLMError("provider returned no choices after 3 attempts")
        detail = f" (HTTP {last.status_code})" if isinstance(last, openai.APIStatusError) else ""
        raise LLMError(f"LLM call failed after 3 attempts: {type(last).__name__}{detail}")

    @staticmethod
    def _parse(resp) -> LLMResponse:
        if not getattr(resp, "choices", None):
            raise LLMError("provider returned no choices")
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        ]
        usage = getattr(resp, "usage", None)
        extra = getattr(usage, "model_extra", None) or {}
        raw: dict = {"role": "assistant", "content": msg.content}
        if calls:
            raw["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        return LLMResponse(
            text=msg.content or "",
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", None) or 0,
            completion_tokens=getattr(usage, "completion_tokens", None) or 0,
            cost=float(extra.get("cost") or 0.0),
            raw_message=raw,
        )
