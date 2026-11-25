"""Antigravity as an ayaan tool, through the desktop app's local language server.

There is no headless Antigravity CLI: `agentapi` only starts folder-less conversations, so runs
use the same Connect-RPC calls as the app (StartCascade with the workspace, SendUserCascadeMessage,
poll GetCascadeTrajectory, read ConvertTrajectoryToMarkdown). Unofficial and version-bound: the
Antigravity app must be running and logged in. `python -m ayaan.antigravity [--model M]
[--resume ID]` reads the prompt on stdin, works in its cwd and prints Claude-style JSON lines.
"""

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

import httpx

DAEMON_DIR = Path.home() / ".gemini" / "antigravity" / "daemon"
_SVC = "exa.language_server_pb.LanguageServerService/"
_META = {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}
POLL_S, MAX_WAIT_S = 3.0, 4 * 3600  # runner's TASK_TIMEOUT_SEC kills us first


class Unavailable(Exception):
    pass


def ls_address() -> tuple[int, str]:
    """(http port, csrf token) of the running app's language server (newest live hub_*.json)."""
    for f in sorted(DAEMON_DIR.glob("hub_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text())
            os.kill(int(d["pid"]), 0)
            return int(d["httpPort"]), d["csrfToken"]
        except (OSError, ValueError, KeyError):
            continue
    raise Unavailable("Antigravity app is not running")


def _req(method: str, body: dict) -> tuple[str, dict, bytes]:
    port, csrf = ls_address()
    headers = {"Connect-Protocol-Version": "1", "x-codeium-csrf-token": csrf}
    return f"http://127.0.0.1:{port}/{_SVC}{method}", headers, json.dumps(body).encode()


def rpc(method: str, body: dict) -> dict:
    url, headers, data = _req(method, body)
    r = httpx.post(
        url, content=data, headers={**headers, "Content-Type": "application/json"}, timeout=30
    )
    r.raise_for_status()
    return r.json() if r.content else {}


async def rpc_async(http: httpx.AsyncClient, method: str, body: dict) -> dict:
    url, headers, data = _req(method, body)
    r = await http.post(
        url, content=data, headers={**headers, "Content-Type": "application/json"}, timeout=15
    )
    r.raise_for_status()
    return r.json() if r.content else {}


def model_configs(status: dict) -> list[dict]:
    us = status.get("userStatus") or status
    return ((us.get("cascadeModelConfigData") or {}).get("clientModelConfigs")) or []


def pick_model(configs: list[dict], want: str | None) -> dict | None:
    """Config whose label or model id is `want`; else the least-used Flash, else the least-used.
    ponytail: label/placeholder ids change between app versions, so match both and fall back."""
    if want:
        hit = [
            c
            for c in configs
            if want in (c.get("label"), (c.get("modelOrAlias") or {}).get("model"))
        ]
        if hit:
            return hit[0]
    rated = [c for c in configs if (c.get("quotaInfo") or {}).get("remainingFraction") is not None]
    flash = [c for c in rated if "flash" in (c.get("label") or "").lower()]
    pool = flash or rated or configs
    return (
        max(pool, key=lambda c: (c.get("quotaInfo") or {}).get("remainingFraction", 0))
        if pool
        else None
    )


def _out(**o) -> None:
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _last_response(markdown: str) -> str:
    parts = re.split(r"^### Planner Response\s*$", markdown, flags=re.M)
    return parts[-1].strip() if len(parts) > 1 else ""


def run(prompt: str, cwd: str, model: str | None, resume: str | None) -> int:
    cfg = pick_model(model_configs(rpc("GetUserStatus", {"metadata": _META})), model)
    if cfg is None:
        raise Unavailable("no Antigravity model available")
    cid = (
        resume
        or rpc(
            "StartCascade",
            {
                "source": "CORTEX_TRAJECTORY_SOURCE_AGENT_API",
                "workspaceUris": [Path(cwd).resolve().as_uri()],
            },
        )["cascadeId"]
    )
    _out(type="system", subtype="init", session_id=cid, model=cfg.get("label"))
    signal.signal(
        signal.SIGTERM,
        lambda *_: (rpc("CancelCascadeInvocation", {"cascadeId": cid}), sys.exit(143)),
    )
    before = int(rpc("GetCascadeTrajectory", {"cascadeId": cid}).get("numTotalSteps") or 0)
    rpc(
        "SendUserCascadeMessage",
        {
            "cascadeId": cid,
            "items": [{"text": prompt}],
            "cascadeConfig": {"plannerConfig": {"requestedModel": cfg.get("modelOrAlias") or {}}},
        },
    )
    deadline = time.monotonic() + MAX_WAIT_S
    while True:
        time.sleep(POLL_S)
        t = rpc("GetCascadeTrajectory", {"cascadeId": cid})
        n = int(t.get("numTotalSteps") or 0)
        # status can still read IDLE right after the send: wait until steps were added
        if n > before + 1 and t.get("status") != "CASCADE_RUN_STATUS_RUNNING":
            break
        if time.monotonic() > deadline:
            rpc("CancelCascadeInvocation", {"cascadeId": cid})
            _out(type="result", session_id=cid, is_error=True, result="antigravity wait timeout")
            return 1
    steps = (t.get("trajectory") or {}).get("steps") or []
    errors = [
        ((s.get("errorMessage") or {}).get("error") or {}).get("shortError") or "error"
        for s in steps[before:]
        if s.get("type") == "CORTEX_STEP_TYPE_ERROR_MESSAGE"
    ]
    md = rpc("ConvertTrajectoryToMarkdown", {"conversationId": cid}).get("markdown") or ""
    text = _last_response(md)
    _out(type="result", session_id=cid, is_error=bool(errors), result="\n".join(errors) or text)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ayaan.antigravity")
    p.add_argument("--model")
    p.add_argument("--resume")
    a = p.parse_args(argv)
    try:
        return run(sys.stdin.read(), os.getcwd(), a.model, a.resume)
    except (Unavailable, httpx.HTTPError, KeyError) as e:
        # ponytail: a resume id the app no longer knows surfaces as an HTTP error here
        _out(
            type="result", is_error=True, result=f"antigravity unavailable: {type(e).__name__} {e}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
