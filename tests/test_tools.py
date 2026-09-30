import pytest

from agentops.tools import ToolError, ToolRegistry, obj, truncate


def make_registry():
    reg = ToolRegistry()

    @reg.tool("echo", "Echo text", obj({"text": {"type": "string"}}, ["text"]), "read")
    def echo(text):
        return text

    @reg.tool("kill", "Kill pid", obj({"pid": {"type": "integer"}}, ["pid"]), "mutate")
    def kill(pid):
        return f"pid={pid!r}"

    return reg


def test_schema_format():
    schema = make_registry().schemas(["echo"])[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert schema["function"]["parameters"]["required"] == ["text"]


def test_call_ok_and_integer_coercion():
    reg = make_registry()
    assert reg.call("echo", {"text": "hi"}, ["echo"]) == "hi"
    assert reg.call("kill", {"pid": "42"}, ["kill"]) == "pid=42"
    assert reg.permission("kill") == "mutate"
    assert reg.permission("nope") is None


@pytest.mark.parametrize("name,args,allowed", [
    ("missing", {}, ["missing"]),
    ("echo", {"text": "x"}, ["kill"]),
    ("echo", {}, ["echo"]),
    ("echo", {"text": "x", "extra": 1}, ["echo"]),
    ("echo", {"text": 5}, ["echo"]),
    ("kill", {"pid": "abc"}, ["kill"]),
    ("kill", {"pid": True}, ["kill"]),
])
def test_call_errors(name, args, allowed):
    with pytest.raises(ToolError):
        make_registry().call(name, args, allowed)


def test_truncate_keeps_head_and_tail():
    text = "A" * 5000 + "B" * 5000
    out = truncate(text, limit=3000)
    assert len(out) <= 3060
    assert out.startswith("A") and out.endswith("B")
    assert "truncated" in out
    assert truncate("short", limit=3000) == "short"
