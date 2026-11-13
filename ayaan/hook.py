"""`ayaan-hook`: Claude PreToolUse hook. Asks ayaan whether this tool call may run.

stdin: Claude hook JSON. stdout: hookSpecificOutput.permissionDecision. Stdlib only; any failure
(ayaan down, no token, bad reply) denies: the gate fails closed.
"""

import json
import os
import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8080/tsh/api/v1/ayaan/hook/pre-tool"


def decide(event: dict, env=os.environ, opener=urllib.request.urlopen) -> tuple[str, str]:
    token = env.get("AYN_HOOK_TOKEN")
    if not token:
        return "deny", "ayaan hook token missing"
    body = json.dumps(
        {"toolName": event.get("tool_name") or "", "input": event.get("tool_input") or {}}
    ).encode()
    req = urllib.request.Request(
        env.get("AYN_HOOK_URL") or DEFAULT_URL,
        data=body,
        headers={"Content-Type": "application/json", "X-Hook-Token": token},
    )
    try:
        with opener(req, timeout=25) as resp:
            data = json.loads(resp.read()).get("data") or {}
    except Exception as e:
        return "deny", f"ayaan 연결 실패: {type(e).__name__}"
    decision = data.get("decision")
    if decision not in ("allow", "deny"):
        return "deny", "ayaan 응답 오류"
    return decision, str(data.get("reason") or "")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    decision, reason = decide(event)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
