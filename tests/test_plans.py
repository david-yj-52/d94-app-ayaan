import itertools
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import IDENTITY, commit, git
from fakes import FakeClock, FakeEcc, InMemoryQuery, fail_result, fake_runner, ok_result

from ayaan import plans
from ayaan.scheduler import Scheduler
from ayaan.settings import Settings

NOW = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
ENDED = ("DONE", "HALTED", "CANCELLED")


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for k, v in IDENTITY.items():
        monkeypatch.setenv(k, v)


def _bare(tmp: Path, name: str) -> tuple[Path, Path]:
    """Bare origin with main and deploy on the same commit, plus a working clone."""
    bare, w = tmp / f"{name}.git", tmp / f"{name}-seed"
    git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    git(tmp, "clone", "-q", str(bare), str(w))
    git(w, "checkout", "-q", "-b", "main")
    commit(w, "README.md", "hello\n", "init")
    git(w, "push", "-q", "origin", "main", "main:deploy")
    return bare, w


def iss(no: int, typ: str = "Task", cat: str = "TODO") -> dict:
    return {"issueKey": f"TASKS-{no}", "issueNo": no, "issueTypeNm": typ, "statusCategory": cat}


_n = itertools.count()


def commit_r1(cwd):
    commit(Path(cwd) / "r1", f"{Path(cwd).name}.txt", f"{next(_n)}\n")


def _setup(tmp_path, issues, script=None, act=commit_r1, repos=("r1",)):
    settings = Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        profile="test",
        cli_executables="claude=/opt/cli/claude",
        workspace_roots=str(tmp_path / "root"),
    )
    q, ecc = InMemoryQuery.seeded(FakeClock(NOW)), FakeEcc()
    origins = {r: _bare(tmp_path, r) for r in repos}
    q.workspaces["P"] = {
        "projectKey": "P",
        "repos": [
            {
                "repoNm": r,
                "gitUrl": str(origins[r][0]),
                "baseBranch": "main",
                "deployBranch": "deploy",
                "sortOrd": i,
            }
            for i, r in enumerate(repos)
        ],
    }
    q.milestones["m1"] = issues
    runner = fake_runner(script or [ok_result(f"s{i}") for i in range(len(issues))], act=act)
    s = Scheduler(q, ecc, settings, clock=q.clock, runner=runner)
    return q, ecc, s, runner, origins, q.add_plan()


async def _run(s, q, pid, until=ENDED, n=40):
    for _ in range(n):
        await s.tick()
        await s.drain()
        if q.plan(pid)["stage"] in until:
            return q.plan(pid)
    raise AssertionError(f"plan stuck in {q.plan(pid)}")


def _files(bare: Path, ref: str) -> set[str]:
    return set(git(bare, "ls-tree", "--name-only", ref).split())


def test_targets_skip_epic_and_done_in_number_order():
    got = plans.targets([iss(10), iss(1, typ="Epic"), iss(3, cat="DONE"), iss(2)])
    assert [i["issueKey"] for i in got] == ["TASKS-2", "TASKS-10"]


async def test_full_plan_two_issues_merges_and_deploys(tmp_path):
    q, ecc, s, runner, origins, pid = _setup(tmp_path, [iss(10), iss(2)])
    p = await _run(s, q, pid)
    bare = origins["r1"][0]
    assert p["stage"] == "DONE" and p["endedAt"]
    assert [c.cwd.name for c in runner.calls] == ["TASKS-2", "TASKS-10"]
    assert {"TASKS-2.txt", "TASKS-10.txt"} <= _files(bare, "main")
    assert {"TASKS-2.txt", "TASKS-10.txt"} <= _files(bare, "deploy")
    assert git(bare, "branch", "--list", "ayaan/*") == ""
    assert q.done_issues == ["TASKS-2", "TASKS-10"]
    assert not (tmp_path / "root" / ".tasks" / "TASKS-2").exists()
    assert {t["planId"] for t in q.tasks.values()} == {pid}
    assert {"PLAN_STARTED", "PLAN_DEPLOYED"} <= set(ecc.events())


async def test_epic_and_done_issues_skipped(tmp_path):
    q, _, s, runner, _, pid = _setup(tmp_path, [iss(1, typ="Epic"), iss(2, cat="DONE"), iss(3)])
    assert (await _run(s, q, pid))["stage"] == "DONE"
    assert [c.cwd.name for c in runner.calls] == ["TASKS-3"]


async def test_zero_targets_goes_to_done_without_merge(tmp_path):
    q, _, s, runner, origins, pid = _setup(tmp_path, [iss(1, typ="Epic")])
    bare = origins["r1"][0]
    assert (await _run(s, q, pid))["stage"] == "DONE"
    assert runner.calls == []
    assert git(bare, "rev-list", "--count", "main") == "1"
    assert git(bare, "branch", "--list", "ayaan/*") == ""


async def test_task_failed_halts_then_resume_requeues_same_task(tmp_path):
    q, ecc, s, runner, origins, pid = _setup(
        tmp_path, [iss(2)], script=[fail_result("tests red"), ok_result("s1")]
    )
    p = await _run(s, q, pid)
    assert p["stage"] == "HALTED" and "TASKS-2" in p["lastError"]
    assert "PLAN_HALTED" in ecc.events()
    plan = await plans.resume(s, q.plan(pid))
    assert plan["stage"] == "RUNNING"
    assert (await _run(s, q, pid))["stage"] == "DONE"
    assert len(q.tasks) == 1 and next(iter(q.tasks.values()))["status"] == "DONE"
    assert "TASKS-2.txt" in _files(origins["r1"][0], "deploy")


async def test_rebase_conflict_halts_keeps_worktree(tmp_path):
    def act(cwd):
        commit(Path(cwd) / "r1", "README.md", "task\n")
        seed = tmp_path / "r1-seed"
        commit(seed, "README.md", "other\n")
        git(seed, "push", "-q", "origin", "main")

    q, _, s, _, _, pid = _setup(tmp_path, [iss(1)], act=act)
    p = await _run(s, q, pid)
    assert p["stage"] == "HALTED"
    assert p["lastError"].startswith("MERGING:") and "r1" in p["lastError"]
    assert (tmp_path / "root" / ".tasks").exists()
    assert (await plans.resume(s, q.plan(pid)))["stage"] == "MERGING"


async def test_restart_during_merging_is_idempotent(tmp_path):
    q, _, s, _, origins, pid = _setup(tmp_path, [iss(1)])
    await _run(s, q, pid, until=("MERGING",))
    stale, bare = dict(q.plan(pid)), origins["r1"][0]
    assert await plans.step(s, stale) == "DEPLOYING"
    count = git(bare, "rev-list", "--count", "main")
    assert await plans.step(s, stale) == "DEPLOYING"  # re-run after a "crash": no-op
    assert git(bare, "rev-list", "--count", "main") == count
    assert q.plan(pid)["stage"] == "DEPLOYING"


async def test_unchanged_repo_not_deployed(tmp_path):
    q, _, s, _, origins, pid = _setup(tmp_path, [iss(1)], repos=("r1", "r2"))
    r2, r2_seed = origins["r2"]
    commit(r2_seed, "elsewhere.txt", "x\n")  # on main but not deployed, not from the plan
    git(r2_seed, "push", "-q", "origin", "main")
    deploy_before = git(r2, "rev-parse", "deploy")
    assert (await _run(s, q, pid))["stage"] == "DONE"
    assert git(r2, "rev-parse", "deploy") == deploy_before
    assert "TASKS-1.txt" in _files(origins["r1"][0], "deploy")


async def test_task_retry_button_unhalts_plan(tmp_path):
    q, _, s, _, _, pid = _setup(tmp_path, [iss(2)], script=[fail_result(), ok_result("s1")])
    assert (await _run(s, q, pid))["stage"] == "HALTED"
    tid = next(iter(q.tasks))
    await q.transition(tid, "FAILED", "QUEUED", resumeAt=None, resumeCount=0)  # [재시도]
    assert (await _run(s, q, pid, until=("DONE", "CANCELLED")))["stage"] == "DONE"


async def test_merge_error_halt_does_not_auto_resume(tmp_path):
    q, ecc, s, _, _, pid = _setup(tmp_path, [iss(1)])
    q.workspaces["P"]["repos"][0]["gitUrl"] = str(tmp_path / "missing.git")
    p = await _run(s, q, pid)
    assert p["stage"] == "HALTED" and p["lastError"].startswith("PREPARING:")
    await s.tick()
    assert q.plan(pid)["stage"] == "HALTED" and ecc.events().count("PLAN_HALTED") == 1
