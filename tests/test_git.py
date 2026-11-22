import asyncio
import subprocess

import pytest
from conftest import commit, git

from ayaan import git as g
from ayaan.git import GitError, slug


def test_slug_korean_only_falls_back():
    assert slug("사용자 정의 컬럼", "80ae64d2-xxxx") == "80ae64d2"


def test_slug_ascii():
    assert slug("Task Columns v2!", "x") == "task-columns-v2"


def test_slug_collapses_and_strips():
    assert slug("--A__b  c--", "x") == "a-b-c"


async def test_ensure_clone_twice_is_fetch(tmp_path, repo):
    d = await g.ensure_clone(tmp_path, repo)
    assert d == tmp_path / ".repos" / "r1"
    assert (d / "README.md").exists()
    assert await g.ensure_clone(tmp_path, repo) == d


async def test_ensure_branch_noop_when_exists(tmp_path, repo, origin):
    await g.ensure_branch(tmp_path, repo, "ayaan/plan-M1", "origin/main")
    sha = git(origin, "rev-parse", "ayaan/plan-M1")
    await g.ensure_branch(tmp_path, repo, "ayaan/plan-M1", "origin/main")
    assert git(origin, "rev-parse", "ayaan/plan-M1") == sha


async def test_worktree_commit_push_then_clean(tmp_path, repo, origin):
    wt = await g.add_worktree(tmp_path, repo, "AY-1", "origin/main")
    assert wt == tmp_path / ".tasks" / "AY-1" / "r1"
    assert await g.add_worktree(tmp_path, repo, "AY-1", "origin/main") == wt
    assert not await g.dirty(wt)
    assert not await g.has_commits(wt, "origin/main")

    (wt / "a.txt").write_text("a\n")
    assert await g.dirty(wt)
    git(wt, "add", "a.txt")
    git(wt, "commit", "-q", "-m", "a")
    assert not await g.dirty(wt)
    assert await g.has_commits(wt, "origin/main")
    assert await g.unpushed(wt)

    await g.push_branch(wt, "ayaan/AY-1")
    assert not await g.unpushed(wt)
    assert git(origin, "rev-parse", "ayaan/AY-1") == git(wt, "rev-parse", "HEAD")

    await g.remove_worktree(wt, g.repo_dir(tmp_path, "r1"))
    assert not wt.exists()
    await g.remove_worktree(wt, g.repo_dir(tmp_path, "r1"))  # idempotent


async def _task_on_plan(tmp_path, repo, key="AY-1", name="a.txt", content="a\n"):
    await g.ensure_branch(tmp_path, repo, "ayaan/plan-M1", "origin/main")
    wt = await g.add_worktree(tmp_path, repo, key, "origin/ayaan/plan-M1")
    commit(wt, name, content)
    await g.push_branch(wt, f"ayaan/{key}")
    return wt


async def test_merge_no_ff_idempotent(tmp_path, repo, origin):
    await _task_on_plan(tmp_path, repo)
    assert await g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1")
    count = git(origin, "rev-list", "--count", "ayaan/plan-M1")
    assert git(origin, "rev-list", "--count", "--merges", "ayaan/plan-M1") == "1"
    assert not await g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1")
    assert git(origin, "rev-list", "--count", "ayaan/plan-M1") == count


async def test_rebase_ff_idempotent(tmp_path, repo, origin, seed):
    await _task_on_plan(tmp_path, repo)
    await g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1")
    commit(seed, "b.txt", "b\n")  # main moves on independently
    git(seed, "push", "-q", "origin", "main")

    assert await g.rebase_ff(tmp_path, repo, "ayaan/plan-M1")
    assert git(origin, "rev-parse", "main") == git(origin, "rev-parse", "ayaan/plan-M1")
    assert git(origin, "rev-list", "--count", "--merges", "main") == "1"  # merge kept
    files = git(origin, "ls-tree", "--name-only", "main").split()
    assert {"a.txt", "b.txt"} <= set(files)
    assert not await g.rebase_ff(tmp_path, repo, "ayaan/plan-M1")


async def test_rebase_ff_conflict_aborts(tmp_path, repo, origin, seed):
    await _task_on_plan(tmp_path, repo, name="README.md", content="plan\n")
    await g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1")
    commit(seed, "README.md", "main\n")
    git(seed, "push", "-q", "origin", "main")
    main_before = git(origin, "rev-parse", "main")

    with pytest.raises(GitError):
        await g.rebase_ff(tmp_path, repo, "ayaan/plan-M1")
    d = g.repo_dir(tmp_path, "r1")
    assert "rebase" not in git(d, "status")
    assert git(d, "status", "--porcelain") == ""
    assert git(origin, "rev-parse", "main") == main_before


async def test_changed_since(tmp_path, repo, seed):
    d = await g.ensure_clone(tmp_path, repo)
    ref = git(d, "rev-parse", "origin/main")
    assert not await g.changed_since(tmp_path, repo, ref)
    commit(seed, "c.txt", "c\n")
    git(seed, "push", "-q", "origin", "main")
    assert await g.changed_since(tmp_path, repo, ref)


async def test_delete_branch_missing_ok(tmp_path, repo, origin):
    await g.ensure_clone(tmp_path, repo)
    await g.delete_branch(tmp_path, repo, "ayaan/nope")
    await g.ensure_branch(tmp_path, repo, "ayaan/plan-M1", "origin/main")
    await g.delete_branch(tmp_path, repo, "ayaan/plan-M1")
    assert "ayaan/plan-M1" not in git(origin, "branch", "--list")


async def test_concurrent_merges_same_repo(tmp_path, repo, origin):
    await _task_on_plan(tmp_path, repo, "AY-1", "a.txt")
    await _task_on_plan(tmp_path, repo, "AY-2", "b.txt")
    results = await asyncio.gather(
        g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1"),
        g.merge_no_ff(tmp_path, repo, "ayaan/AY-2", "ayaan/plan-M1"),
    )
    assert results == [True, True]
    assert git(origin, "rev-list", "--count", "--merges", "ayaan/plan-M1") == "2"


async def test_merge_recovers_from_crashed_merge_state(tmp_path, repo, origin, seed):
    await _task_on_plan(tmp_path, repo, "AY-1", "README.md", "plan\n")
    await g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1")
    commit(seed, "README.md", "main\n")
    git(seed, "push", "-q", "origin", "main")
    d = await g.ensure_clone(tmp_path, repo)
    git(d, "checkout", "-q", "--detach", "origin/main")
    with pytest.raises(subprocess.CalledProcessError):  # simulated crash mid-merge
        git(d, "merge", "--no-ff", "origin/ayaan/plan-M1")
    assert (d / ".git" / "MERGE_HEAD").exists()

    await _task_on_plan(tmp_path, repo, "AY-2", "b.txt")
    assert await g.merge_no_ff(tmp_path, repo, "ayaan/AY-2", "ayaan/plan-M1")
    assert not (d / ".git" / "MERGE_HEAD").exists()


async def test_merge_conflict_reports_conflict_and_aborts(tmp_path, repo):
    await _task_on_plan(tmp_path, repo, "AY-1", "README.md", "one\n")
    await _task_on_plan(tmp_path, repo, "AY-2", "README.md", "two\n")
    assert await g.merge_no_ff(tmp_path, repo, "ayaan/AY-1", "ayaan/plan-M1")
    with pytest.raises(GitError, match="CONFLICT"):  # CONFLICT is on stdout, stderr empty
        await g.merge_no_ff(tmp_path, repo, "ayaan/AY-2", "ayaan/plan-M1")
    assert not (g.repo_dir(tmp_path, "r1") / ".git" / "MERGE_HEAD").exists()


async def test_git_command_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "GIT_TIMEOUT_S", 0.3)
    with pytest.raises(GitError, match="timed out"):
        await g._git(tmp_path, "-c", "alias.hang=!sleep 30", "hang")


async def test_git_never_prompts(tmp_path):
    out = await g._git(tmp_path, "-c", "alias.env=!echo $GIT_SSH_COMMAND", "env")
    assert "BatchMode=yes" in out


def test_git_error_hides_url_credentials():
    e = GitError("r1", "fetch", "fatal: unable to access 'https://bob:s3cret@host/r.git/'")
    assert "s3cret" not in str(e) and "https://***@host/r.git" in str(e)


async def test_fetch_follows_changed_git_url(tmp_path, repo, origin):
    await g.ensure_clone(tmp_path, repo)
    moved = tmp_path / "moved.git"
    origin.rename(moved)
    await g.ensure_clone(tmp_path, {**repo, "gitUrl": str(moved)})
    assert git(tmp_path / ".repos" / "r1", "remote", "get-url", "origin") == str(moved)


async def test_delete_branch_without_clone_is_noop(tmp_path, repo):
    await g.delete_branch(tmp_path, repo, "ayaan/X-1")
    assert not (tmp_path / ".repos").exists()
