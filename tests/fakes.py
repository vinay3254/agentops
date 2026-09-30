import itertools
import json

from agentops.llm import LLMResponse, ToolCall

_ids = itertools.count(1)


def reply(text: str = "", calls=()) -> LLMResponse:
    """Build an LLMResponse. calls: iterable of (tool_name, args_dict_or_raw_json_string)."""
    tcs = []
    for name, args in calls:
        raw = args if isinstance(args, str) else json.dumps(args)
        tcs.append(ToolCall(id=f"call_{next(_ids)}", name=name, arguments=raw))
    message = {"role": "assistant", "content": text or None}
    if tcs:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in tcs
        ]
    return LLMResponse(text=text, tool_calls=tcs, prompt_tokens=100, completion_tokens=20,
                       cost=0.001, raw_message=message)


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []  # number of messages seen at each call

    def complete(self, messages, tools):
        self.calls.append(len(messages))
        if not self.replies:
            raise RuntimeError("script exhausted")
        return self.replies.pop(0)


class FakeExecutor:
    def __init__(self, states=None):
        self.states = states or {"gateway": "running", "api": "running", "worker": "running", "redis": "running"}
        self.actions = []
        self.raise_on = {}

    def _act(self, name, *args):
        self.actions.append((name, *args))
        if name in self.raise_on:
            raise self.raise_on[name]

    def status(self):
        return {s: {"state": st, "exit_code": 0} for s, st in self.states.items()}

    def logs(self, service, tail=50):
        self._act("logs", service, tail)
        return "log line\n" * 400

    def diagnostic(self, service, command):
        self._act("diagnostic", service, command)
        return f"ran {command}"

    def restart(self, service):
        self._act("restart", service)

    def start(self, service):
        self._act("start", service)

    def set_env(self, service, key, value):
        self._act("set_env", service, key, value)
        return "ok"

    def cleanup_files(self, service, path):
        self._act("cleanup_files", service, path)
        return f"removed {path}"

    def kill_process(self, service, pid):
        self._act("kill_process", service, pid)
        return f"killed {pid}"
