"""Setup check (spec appendix B, steps 1..6). Reports presence and status only, never env values."""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ayaan.adapters import TESTED_VERSIONS
from ayaan.scheduler import EXE_MISSING
from ayaan.settings import Settings, child_env, resolve_exe

TIMEOUT_S = 10
# credential files written by each CLI's login, relative to HOME
LOGIN_FILES = {
    "CLAUDE": ".claude/.credentials.json",
    "CODEX": ".codex/auth.json",
    "GEMINI": ".gemini/oauth_creds.json",
    "CURSOR": ".cursor/cli-config.json",
}


def _item(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _step(n: int, items: list[dict], optional: bool = False) -> dict:
    return {
        "step": n,
        "ok": (optional or bool(items)) and all(i["ok"] for i in items),
        "items": items,
    }


async def _run(*args: str) -> tuple[int | None, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**child_env(), "GIT_TERMINAL_PROMPT": "0"},
        )
    except OSError:
        return None, "not found"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None, "timeout"
    return proc.returncode, out.decode(errors="replace").strip()


async def _http(url: str, headers: dict | None = None) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as h:
            resp = await h.get(url, headers=headers)
        return resp.is_success, f"HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        return False, type(e).__name__


async def _version(cd: str, exe: str | None) -> dict:
    if exe is None:
        return _item(f"{cd} version", False, EXE_MISSING)
    code, out = await _run(exe, "--version")
    tested = TESTED_VERSIONS.get(cd, "unverified")
    if code != 0:
        return _item(f"{cd} version", False, f"--version failed ({out if code is None else code})")
    installed = (out.splitlines() or [""])[0][:80]
    ok = tested == "unverified" or tested in installed
    return _item(f"{cd} version", ok, f"installed {installed}, tested {tested}")


async def _git(ws: dict, git: str | None) -> dict:
    path = Path(ws.get("workspacePath") or "")
    name = ws.get("projectKey") or str(path)
    if not path.is_dir():
        return _item(name, False, "workspace path does not exist")
    if git is None:
        return _item(name, False, EXE_MISSING)
    code, _ = await _run(git, "-C", str(path), "ls-remote", "--exit-code", "origin")
    # output is never returned: a remote error can echo a URL with embedded credentials
    return _item(name, code == 0, "git ls-remote ok" if code == 0 else "git ls-remote failed")


async def run_setup_check(query, settings: Settings) -> list[dict]:
    tools = await query.get_tools() or []
    workspaces = await query.get_workspaces() or []
    enabled = [
        t for t in tools if t.get("enabled") and (t["toolCd"] != "FAKE" or settings.fake_enabled)
    ]
    enabled_cds = {t["toolCd"] for t in enabled}

    # 1. CLI version + login
    cli = [t for t in enabled if t["toolCd"] != "FAKE"]
    exes = {t["toolCd"]: resolve_exe(t["toolCd"], settings) for t in cli}
    step1 = list(await asyncio.gather(*(_version(cd, exe) for cd, exe in exes.items())))
    for t in cli:
        if t.get("authMode") != "API_KEY":
            found = (Path.home() / LOGIN_FILES[t["toolCd"]]).is_file()
            step1.append(
                _item(f"{t['toolCd']} login", found, "logged in" if found else "not logged in")
            )

    # 2. API key variables (presence only)
    step2 = []
    for t in tools:
        if t.get("authMode") == "API_KEY":
            ref = t.get("apiKeyRef") or ""
            present = bool(ref and os.environ.get(ref))
            step2.append(_item(t["toolCd"], present, f"{ref} {'set' if present else 'missing'}"))

    # 3. workspace path + git remote
    git = resolve_exe("git", settings)
    step3 = list(await asyncio.gather(*(_git(w, git) for w in workspaces)))

    # 4. ECC health, 5. via gateway
    ecc_ok, ecc_detail = await _http(f"{settings.ecc_base_url.rstrip('/')}/actuator/health")
    u = urlsplit(settings.query_base_url)
    gateway = f"{u.scheme}://{u.netloc}"
    ayaan_ok, ayaan_detail = await _http(
        f"{gateway}/tsh/api/v1/ayaan/quotas", {"X-AYAAN-Token": settings.api_token}
    )
    gw_ecc_ok, gw_ecc_detail = await _http(f"{gateway}/ecc/actuator/health")

    # 6. reference data
    step6 = [
        _item("enabled tools", bool(enabled_cds), f"{len(enabled_cds)} enabled"),
        _item("workspace mappings", bool(workspaces), f"{len(workspaces)} mapped"),
    ]
    for w in workspaces:
        cd = w.get("defaultTool")
        on = cd in enabled_cds
        step6.append(
            _item(
                f"{w.get('projectKey')} defaultTool", on, f"{cd} {'enabled' if on else 'disabled'}"
            )
        )

    return [
        _step(1, step1),
        _step(2, step2, optional=True),
        _step(3, step3),
        _step(4, [_item("ECC health", ecc_ok, ecc_detail)]),
        _step(
            5,
            [
                _item("ayaan via gateway", ayaan_ok, ayaan_detail),
                _item("ECC via gateway", gw_ecc_ok, gw_ecc_detail),
            ],
        ),
        _step(6, step6),
    ]
