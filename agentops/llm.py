from __future__ import annotations

from dataclasses import dataclass, field


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
