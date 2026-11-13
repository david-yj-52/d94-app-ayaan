import io
import json

from ayaan import hook


def _opener(payload=None, exc=None):
    def open_(req, timeout):
        open_.req = req
        if exc:
            raise exc
        return io.BytesIO(json.dumps(payload).encode())

    return open_


def test_decide_forwards_and_maps_reply():
    op = _opener({"success": True, "data": {"decision": "allow", "reason": ""}})
    env = {"AYN_HOOK_TOKEN": "tok", "AYN_HOOK_URL": "http://127.0.0.1:9/h"}
    event = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert hook.decide(event, env, op) == ("allow", "")
    assert op.req.full_url == "http://127.0.0.1:9/h"
    assert op.req.get_header("X-hook-token") == "tok"
    assert json.loads(op.req.data) == {"toolName": "Bash", "input": {"command": "ls"}}


def test_decide_fails_closed():
    assert hook.decide({}, {}, _opener({}))[0] == "deny"  # no token
    env = {"AYN_HOOK_TOKEN": "t"}
    assert hook.decide({}, env, _opener(exc=OSError("down")))[0] == "deny"
    assert hook.decide({}, env, _opener({"data": {"decision": "maybe"}}))[0] == "deny"


def test_main_prints_hook_output(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.delenv("AYN_HOOK_TOKEN", raising=False)
    assert hook.main() == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
