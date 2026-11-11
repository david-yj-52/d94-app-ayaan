import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from ayaan import runner
from ayaan.runner import pending_spool, run_cli, write_spool

ROOT = Path(__file__).parent.parent


async def _run(tmp_path, argv, prompt="hi", timeout_s=10, tool="FAKE", env=None, **kw):
    return await run_cli(
        tool=tool,
        argv_tail=argv,
        prompt=prompt,
        cwd=ROOT,
        timeout_s=timeout_s,
        log_path=tmp_path / "logs" / "run.log",
        executable=sys.executable,
        env={**os.environ, **(env or {})},
        **kw,
    )


async def test_completed_run_writes_log_and_parses(tmp_path):
    r = await _run(tmp_path, ["-m", "ayaan.fake_cli"], env={"AYAAN_FAKE_SCENARIO": "ok"})
    assert r.outcome == "COMPLETED" and r.exit_code == 0
    assert r.event.result_line == "done"
    n = len((ROOT / "ayaan/fake_fixtures/ok.jsonl").read_text().splitlines())
    assert len((tmp_path / "logs/run.log").read_text().splitlines()) == n


async def test_limit_run_is_limit_hit(tmp_path):
    r = await _run(tmp_path, ["-m", "ayaan.fake_cli"], env={"AYAAN_FAKE_SCENARIO": "limit"})
    assert r.outcome == "LIMIT_HIT" and r.exit_code == 1


async def test_scenario_file_consumed(tmp_path):
    f = tmp_path / "sc"
    f.write_text("limit\nok\n")
    env = {"AYAAN_FAKE_SCENARIO_FILE": str(f)}
    assert (await _run(tmp_path, ["-m", "ayaan.fake_cli"], env=env)).outcome == "LIMIT_HIT"
    assert (await _run(tmp_path, ["-m", "ayaan.fake_cli"], env=env)).outcome == "COMPLETED"
    assert (await _run(tmp_path, ["-m", "ayaan.fake_cli"], env=env)).outcome == "COMPLETED"


async def test_auth_error_is_failed_not_limit(tmp_path):
    r = await _run(tmp_path, ["-c", "import sys; print('Not logged in'); sys.exit(1)"])
    assert r.outcome == "FAILED" and "Not logged in" in r.tail


async def test_timeout_kills_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "KILL_GRACE_S", 0.3)
    script = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "print(p.pid,flush=True);time.sleep(60)"
    )
    r = await _run(tmp_path, ["-c", script], timeout_s=1)
    assert r.outcome == "TIMEOUT"
    pid = int(r.tail.split()[0])
    with pytest.raises(ProcessLookupError):
        for _ in range(20):  # child may linger as zombie briefly
            os.kill(pid, 0)
            await asyncio.sleep(0.1)
        os.kill(pid, 0)
        raise AssertionError("child alive")


async def test_long_prompt_goes_through_stdin(tmp_path):
    r = await _run(
        tmp_path,
        ["-c", "import sys; print(len(sys.stdin.read()))"],
        prompt="x" * 200_000,
    )
    assert r.tail.strip() == "200000"


async def test_kill_task(tmp_path):
    t = asyncio.create_task(
        _run(tmp_path, ["-c", "import time; time.sleep(60)"], task_id="t1", timeout_s=30)
    )
    for _ in range(50):
        if "t1" in runner.RUNNING:
            break
        await asyncio.sleep(0.05)
    assert runner.kill_task("t1") is True
    r = await asyncio.wait_for(t, 5)
    assert r.outcome == "CANCELLED" and "t1" not in runner.RUNNING
    assert "t1" not in runner.CANCELLED
    assert runner.kill_task("t1") is False


def test_spool_roundtrip(tmp_path):
    assert pending_spool(tmp_path) == []
    p = write_spool(tmp_path, "r1", {"a": 1})
    assert p == tmp_path / "spool/r1.json"
    assert pending_spool(tmp_path) == [(p, {"a": 1})]
    assert json.loads(p.read_text()) == {"a": 1}
    p.unlink()
    assert pending_spool(tmp_path) == []


async def test_exit0_with_unrecognized_output_is_parse_error(tmp_path):
    r = await _run(tmp_path, ["-c", "print('hello')"])
    assert r.outcome == "PARSE_ERROR" and r.exit_code == 0


async def test_cursor_prompt_is_last_argv_item(tmp_path):
    r = await _run(
        tmp_path, ["-c", "import sys; print(sys.argv[-1])"], prompt="do the thing", tool="CURSOR"
    )
    assert r.tail.strip() == "do the thing"
