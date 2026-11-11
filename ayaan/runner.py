"""CLI process runner: no shell, process-group kill on timeout, cancel registry, spool files."""

import asyncio
import contextlib
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from ayaan.adapters import ADAPTERS, RunEvent

KILL_GRACE_S = 10  # injectable for tests
RUNNING: dict[str, asyncio.subprocess.Process] = {}
CANCELLED: set[str] = set()
_BG: set[asyncio.Task] = set()


@dataclass
class RunResult:
    outcome: str
    event: RunEvent
    exit_code: int | None
    tail: str


def _killpg(proc, sig):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


async def _terminate(proc):
    _killpg(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
    except TimeoutError:
        pass
    _killpg(proc, signal.SIGKILL)  # also reaps stragglers left in the group
    await proc.wait()


def kill_task(task_id: str) -> bool:
    proc = RUNNING.get(task_id)
    if proc is None:
        return False
    CANCELLED.add(task_id)
    t = asyncio.get_running_loop().create_task(_terminate(proc))
    _BG.add(t)
    t.add_done_callback(_BG.discard)
    return True


async def _feed(proc, prompt: str):
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        if prompt:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
        proc.stdin.close()


async def run_cli(
    *,
    tool: str,
    argv_tail: list[str],
    prompt: str,
    cwd: Path,
    timeout_s: int,
    log_path: Path,
    executable: str,
    env: dict,
    task_id: str | None = None,
) -> RunResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    in_argv = ADAPTERS[tool].prompt_in_argv
    proc = await asyncio.create_subprocess_exec(
        executable,
        *argv_tail,
        *([prompt] if in_argv else []),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env,
        start_new_session=True,
        limit=1 << 24,
    )
    if task_id:
        RUNNING[task_id] = proc
    lines: list[str] = []

    async def read():
        with log_path.open("w") as log:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                lines.append(line)
                log.write(line + "\n")
                log.flush()

    feeder = asyncio.create_task(_feed(proc, "" if in_argv else prompt))
    timed_out = cancelled = False

    async def pump():
        await read()
        await proc.wait()

    try:
        await asyncio.wait_for(pump(), timeout_s)
    except TimeoutError:
        timed_out = True
        await _terminate(proc)
    finally:
        feeder.cancel()
        if task_id:
            RUNNING.pop(task_id, None)
            cancelled = task_id in CANCELLED
            CANCELLED.discard(task_id)

    tail = "\n".join(lines)[-2000:]
    try:
        event = ADAPTERS[tool].parse(lines)
    except Exception:
        outcome = "CANCELLED" if cancelled else "TIMEOUT" if timed_out else "PARSE_ERROR"
        return RunResult(outcome, RunEvent(), proc.returncode, tail)
    if cancelled:
        outcome = "CANCELLED"
    elif timed_out:
        outcome = "TIMEOUT"
    elif proc.returncode == 0 and not event.json_seen and not event.limit_hit:
        outcome = "PARSE_ERROR"
    elif event.limit_hit:
        outcome = "LIMIT_HIT"
    elif proc.returncode == 0:
        outcome = "COMPLETED"
    else:
        outcome = "FAILED"
    return RunResult(outcome, event, proc.returncode, tail)


def write_spool(data_dir: Path, run_id: str, body: dict) -> Path:
    d = data_dir / "spool"
    d.mkdir(parents=True, exist_ok=True)
    final, tmp = d / f"{run_id}.json", d / f"{run_id}.json.tmp"
    tmp.write_text(json.dumps(body))
    os.replace(tmp, final)
    return final


def pending_spool(data_dir: Path) -> list[tuple[Path, dict]]:
    d = data_dir / "spool"
    return [(p, json.loads(p.read_text())) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
