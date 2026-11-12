"""Install the Claude Code plugins listed in config CLAUDE_PLUGINS.

Format: `plugin@market|https-git-url`, comma-separated.
"""

import asyncio
import re

from ayaan.settings import Settings, child_env, resolve_exe

DEFAULT = (
    "ponytail@ponytail|https://github.com/JCAO98/PonyTaill_AgentSkill.git,"
    "superpowers@superpowers-marketplace|https://github.com/obra/superpowers-marketplace.git"
)
ENTRY = re.compile(r"([\w.-]+)@([\w.-]+)\|(https://[\w./~-]+)")
TIMEOUT_S = 120


def parse(spec: str) -> list[tuple[str, str, str]]:
    """Return (plugin, marketplace, url) per entry; ValueError names the first bad entry."""
    out = []
    for raw in (spec or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        m = ENTRY.fullmatch(entry)
        if not m:
            raise ValueError(f"invalid entry: {entry} (format plugin@marketplace|https://git-url)")
        out.append(m.groups())
    return out


async def _run(exe: str, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        exe,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=child_env(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "timeout"
    return proc.returncode, out.decode(errors="replace")[-500:]


def _done(rc: int, out: str) -> bool:
    # ponytail: the CLI has no idempotent flag, so match its "already" message
    return rc == 0 or "already" in out.lower()


async def sync(spec: str, settings: Settings, run=_run) -> list[dict]:
    """Add each marketplace and install each plugin; safe to repeat."""
    entries = parse(spec)
    exe = resolve_exe("claude", settings)
    if not exe:
        return [
            {"plugin": f"{p}@{m}", "ok": False, "detail": "실행 파일을 찾을 수 없음"}
            for p, m, _ in entries
        ]
    results = []
    for plugin, market, url in entries:
        name = f"{plugin}@{market}"
        rc, out = await run(exe, "plugin", "marketplace", "add", url)
        if not _done(rc, out):
            results.append(
                {
                    "plugin": name,
                    "ok": False,
                    "detail": f"marketplace add 실패: {out.strip()[-200:]}",
                }
            )
            continue
        rc, out = await run(exe, "plugin", "install", name)
        ok = _done(rc, out)
        results.append({"plugin": name, "ok": ok, "detail": "설치됨" if ok else out.strip()[-200:]})
    return results


async def installed(settings: Settings, run=_run) -> str:
    exe = resolve_exe("claude", settings)
    if not exe:
        return "실행 파일을 찾을 수 없음"
    return (await run(exe, "plugin", "list"))[1]
