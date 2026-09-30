from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from agentops import config

PERMISSIONS = ("read", "mutate", "control")


class ToolError(Exception):
    """The model called a tool incorrectly."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable
    permission: str


def obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def truncate(text: str, limit: int | None = None) -> str:
    limit = limit or config.TOOL_RESULT_MAX_CHARS
    if len(text) <= limit:
        return text
    half = limit // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n...[truncated {dropped} chars]...\n{text[-half:]}"


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def tool(self, name: str, description: str, parameters: dict, permission: str):
        assert permission in PERMISSIONS, permission

        def deco(fn):
            self._tools[name] = Tool(name, description, parameters, fn, permission)
            return fn

        return deco

    def permission(self, name: str) -> str | None:
        t = self._tools.get(name)
        return t.permission if t else None

    def schemas(self, allowed: Iterable[str]) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in (self._tools[n] for n in allowed)
        ]

    def call(self, name: str, args: dict, allowed: Iterable[str]) -> str:
        if name not in self._tools:
            raise ToolError(f"unknown tool: {name}")
        if name not in set(allowed):
            raise ToolError(f"tool not available to this agent: {name}")
        tool = self._tools[name]
        props = tool.parameters.get("properties", {})
        for key in args:
            if key not in props:
                raise ToolError(f"unexpected argument {key!r} for {name}")
        for key in tool.parameters.get("required", []):
            if key not in args:
                raise ToolError(f"missing required argument {key!r} for {name}")
        clean = {k: self._coerce(name, k, v, props[k]) for k, v in args.items()}
        return str(tool.func(**clean))

    @staticmethod
    def _coerce(tool: str, key: str, value, spec: dict):
        kind = spec.get("type")
        if kind == "string":
            if not isinstance(value, str):
                raise ToolError(f"argument {key!r} of {tool} must be a string")
            return value
        if kind == "integer":
            if isinstance(value, bool):
                raise ToolError(f"argument {key!r} of {tool} must be an integer")
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value)
            raise ToolError(f"argument {key!r} of {tool} must be an integer")
        return value
