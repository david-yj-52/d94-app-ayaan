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


@dataclass
class RunResult:
    outcome: str
    event: RunEvent
    exit_code: int | None
    tail: str


def _killpg(proc, sig):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


def kill_task(task_id: str) -> bool:
    proc = RUNNING.get(task_id)
    if proc is None:
        return False
    _killpg(proc, signal.SIGTERM)
    return True


async def _feed(proc, prompt: str):
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
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
    proc = await asyncio.create_subprocess_exec(
        executable,
        *argv_tail,
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

    feeder = asyncio.create_task(_feed(proc, prompt))
    timed_out = False
    try:
        await asyncio.wait_for(read(), timeout_s)
        await proc.wait()
    except TimeoutError:
        timed_out = True
        _killpg(proc, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
        except TimeoutError:
            pass
        _killpg(proc, signal.SIGKILL)  # also reaps stragglers left in the group
        await proc.wait()
    finally:
        feeder.cancel()
        if task_id:
            RUNNING.pop(task_id, None)

    tail = "\n".join(lines)[-2000:]
    try:
        event = ADAPTERS[tool].parse(lines)
    except Exception:
        outcome = "TIMEOUT" if timed_out else "PARSE_ERROR"
        return RunResult(outcome, RunEvent(), proc.returncode, tail)
    if timed_out:
        outcome = "TIMEOUT"
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
