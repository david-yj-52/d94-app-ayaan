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


# ── worktree GC / mapping migration ──


async def _wt(q, s, origins, key, status, plan_id=None, push=False, edit=None):
    """A task whose worktree exists under <root>/.tasks/<key>/r1."""
    from ayaan import git as g

    root = s.settings.workspace_root_paths[0]
    repo = q.workspaces["P"]["repos"][0]
    path = await g.add_worktree(root, repo, key, "origin/main")
    if edit:
        commit(path, "x.txt", "x\n")
    if push:
        git(path, "push", "-q", "-u", "origin", f"HEAD:refs/heads/ayaan/{key}")
    q.add_task(issueKey=key, status=status, planId=plan_id, workspacePath=str(path.parent))
    return path.parent


async def test_gc_removes_done_plan_worktrees(tmp_path):
    q, _, s, _, origins, pid = _setup(tmp_path, [])
    q.plan(pid)["stage"] = "DONE"
    d = await _wt(q, s, origins, "TASKS-1", "DONE", pid)
    root = s.settings.workspace_root_paths[0]
    assert await plans.gc_worktrees(s) == []
    assert not d.exists() and (root / ".repos" / "r1" / ".git").exists()


async def test_gc_skips_unpushed_and_reports(tmp_path):
    q, ecc, s, _, origins, _ = _setup(tmp_path, [])
    d = await _wt(q, s, origins, "TASKS-1", "DONE", edit=True)  # commit, never pushed
    s.gc_held = await plans.gc_worktrees(s)
    assert s.gc_held == [str(d)] and d.exists()
    await s.send_report()
    assert f"정리 보류: {d}" in ecc.sent[-1][2]


async def test_gc_keeps_running_task_and_active_plan(tmp_path):
    q, _, s, _, origins, pid = _setup(tmp_path, [])
    a = await _wt(q, s, origins, "TASKS-1", "RUNNING")
    b = await _wt(q, s, origins, "TASKS-2", "DONE", pid)  # plan still PREPARING
    assert await plans.gc_worktrees(s) == []
    assert a.exists() and b.exists()


async def test_gc_removes_orphan_and_pushed_clean_dirs(tmp_path):
    q, _, s, _, origins, _ = _setup(tmp_path, [])
    d = await _wt(q, s, origins, "TASKS-1", "CANCELLED", push=True)
    q.tasks.clear()  # folder with no linked task at all
    assert await plans.gc_worktrees(s) == []
    assert not d.exists()


async def test_migrate_workspace_creates_repo_row(tmp_path, origin):
    clone = tmp_path / "myrepo"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    q = InMemoryQuery.seeded(FakeClock(NOW))
    q.workspaces["P"] = {
        "projectKey": "P",
        "workspacePath": str(clone),
        "defaultTool": "CODEX",
        "promptTemplate": "custom {issueKey}",
    }
    q.workspaces["Q"] = {"projectKey": "Q", "workspacePath": str(tmp_path / "gone")}
    settings = Settings(_env_file=None, data_dir=str(tmp_path), workspace_roots=str(tmp_path))
    s = Scheduler(q, FakeEcc(), settings, clock=q.clock)
    await s.migrate_workspaces()
    await s.migrate_workspaces()  # idempotent
    assert q.workspaces["P"]["repos"] == [
        {"repoNm": "myrepo", "gitUrl": str(origin), "baseBranch": "main", "sortOrd": 0}
    ]
    assert not q.workspaces["Q"].get("repos")
    p = q.workspaces["P"]
    assert (p["promptTemplate"], p["defaultTool"], p["workspacePath"]) == (
        "custom {issueKey}",
        "CODEX",
        str(clone),
    )


async def test_gc_removes_never_pushed_worktree_without_commits(tmp_path):
    q, _, s, _, origins, _ = _setup(tmp_path, [])
    d = await _wt(q, s, origins, "TASKS-1", "DONE")  # no upstream, nothing beyond origin/main
    assert await plans.gc_worktrees(s) == []
    assert not d.exists()


async def test_cancelled_plan_task_halts_and_resume_requeues(tmp_path):
    q, ecc, s, runner, _, pid = _setup(tmp_path, [iss(2)])
    tid = q.add_task(
        issueKey="TASKS-2",
        status="CANCELLED",
        planId=pid,
        workspacePath=str(tmp_path / "root" / ".tasks" / "TASKS-2"),
    )
    p = await _run(s, q, pid)
    assert p["stage"] == "HALTED" and p["lastError"] == "RUNNING: task TASKS-2 CANCELLED"
    await s.tick()  # no auto-unhalt loop, no second task for the issue
    assert q.plan(pid)["stage"] == "HALTED" and len(q.tasks) == 1
    assert ecc.events().count("PLAN_HALTED") == 1
    assert (await plans.resume(s, q.plan(pid)))["stage"] == "RUNNING"
    assert q.task(tid)["status"] == "QUEUED"
    assert (await _run(s, q, pid))["stage"] == "DONE"


async def test_repo_added_mid_plan_does_not_halt(tmp_path):
    hook = {}
    q, _, s, _, origins, pid = _setup(tmp_path, [iss(1), iss(2)], act=lambda c: hook["f"](c))
    r2 = _bare(tmp_path, "r2")[0]

    def act(cwd):
        commit_r1(cwd)
        repos = q.workspaces["P"]["repos"]
        if len(repos) == 1:  # added while the first task runs: no .repos/r2 yet
            repos.append({"repoNm": "r2", "gitUrl": str(r2), "baseBranch": "main", "sortOrd": 9})

    hook["f"] = act
    p = await _run(s, q, pid)
    assert p["stage"] == "DONE", p.get("lastError")
    assert {"TASKS-1.txt", "TASKS-2.txt"} <= _files(origins["r1"][0], "deploy")
