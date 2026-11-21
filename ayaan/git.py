"""All git operations for the plan pipeline. Every call is idempotent (safe to re-run).

Layout: base clone `<root>/.repos/<repoNm>/`, task worktree `<root>/.tasks/<issueKey>/<repoNm>/`.
Merges/rebases run in the base clone on detached checkouts so they never touch branches
checked out in worktrees; pushes use explicit refspecs.
"""

import asyncio
import os
import re
from pathlib import Path


class GitError(Exception):
    def __init__(self, repo: str, cmd: str, stderr: str):
        super().__init__(f"[{repo}] git {cmd}: {stderr.strip()}")
        self.repo, self.cmd, self.stderr = repo, cmd, stderr


def slug(name: str, fallback_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or fallback_id[:8]


def repo_dir(root: Path, repo_nm: str) -> Path:
    return root / ".repos" / repo_nm


def task_dir(root: Path, issue_key: str) -> Path:
    return root / ".tasks" / issue_key


async def _run(cwd: Path, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode()


async def _git(cwd: Path, *args: str, repo: str | None = None) -> str:
    rc, out, err = await _run(cwd, *args)
    if rc:
        raise GitError(repo or cwd.name, " ".join(args), err)
    return out


async def _ok(cwd: Path, *args: str) -> bool:
    return (await _run(cwd, *args))[0] == 0


async def _remote_has(d: Path, branch: str) -> bool:
    return await _ok(d, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}")


async def ensure_clone(root: Path, repo: dict) -> Path:
    d = repo_dir(root, repo["repoNm"])
    if (d / ".git").exists():
        await _git(d, "fetch", "--prune", "origin")
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
        await _git(d.parent, "clone", repo["gitUrl"], d.name, repo=repo["repoNm"])
    return d


async def ensure_branch(root: Path, repo: dict, branch: str, start: str) -> None:
    d = await ensure_clone(root, repo)
    if not await _remote_has(d, branch):
        await _git(d, "push", "origin", f"{start}:refs/heads/{branch}")


async def add_worktree(root: Path, repo: dict, issue_key: str, start: str) -> Path:
    path = task_dir(root, issue_key) / repo["repoNm"]
    if path.exists():
        return path
    d = await ensure_clone(root, repo)
    await _git(d, "worktree", "prune")  # drop registration of a dir lost in a crash
    branch = f"ayaan/{issue_key}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if await _ok(d, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        await _git(d, "worktree", "add", str(path), branch)
    else:
        if await _ok(d, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"):
            start = f"origin/{branch}"  # resume already-pushed work
        await _git(d, "worktree", "add", "--no-track", "-b", branch, str(path), start)
    return path


async def dirty(path: Path) -> bool:
    return bool(await _git(path, "status", "--porcelain"))


async def has_commits(path: Path, base: str) -> bool:
    return int(await _git(path, "rev-list", "--count", f"{base}..HEAD")) > 0


async def push_branch(path: Path, branch: str) -> None:
    await _git(path, "push", "-u", "origin", f"HEAD:refs/heads/{branch}")


async def unpushed(path: Path) -> bool:
    if not await _ok(path, "rev-parse", "--verify", "--quiet", "@{u}"):
        return True
    return int(await _git(path, "rev-list", "--count", "@{u}..HEAD")) > 0


async def merge_no_ff(root: Path, repo: dict, source: str, target: str) -> bool:
    d = await ensure_clone(root, repo)
    src, tgt = f"origin/{source}", f"origin/{target}"
    if await _ok(d, "merge-base", "--is-ancestor", src, tgt):
        return False
    await _git(d, "checkout", "-q", "--detach", tgt)
    try:
        await _git(d, "merge", "--no-ff", "-m", f"Merge {source} into {target}", src)
    except GitError:
        await _run(d, "merge", "--abort")
        raise
    await _git(d, "push", "origin", f"HEAD:refs/heads/{target}")
    return True


async def rebase_ff(root: Path, repo: dict, branch: str) -> bool:
    d = await ensure_clone(root, repo)
    base = repo["baseBranch"]
    src, dst = f"origin/{branch}", f"origin/{base}"
    if await _ok(d, "merge-base", "--is-ancestor", src, dst):
        return False
    old = await _git(d, "rev-parse", src)
    await _git(d, "checkout", "-q", "--detach", src)
    try:
        await _git(d, "rebase", "--rebase-merges", dst)
    except GitError:
        await _run(d, "rebase", "--abort")
        raise
    # base: plain push = fast-forward only. branch: rewritten, so lease on the sha we rebased.
    await _git(
        d,
        "push",
        "--atomic",
        f"--force-with-lease=refs/heads/{branch}:{old}",
        "origin",
        f"HEAD:refs/heads/{base}",
        f"HEAD:refs/heads/{branch}",
    )
    return True


async def changed_since(root: Path, repo: dict, ref: str) -> bool:
    d = await ensure_clone(root, repo)
    return int(await _git(d, "rev-list", "--count", f"{ref}..origin/{repo['baseBranch']}")) > 0


async def delete_branch(root: Path, repo: dict, branch: str) -> None:
    d = repo_dir(root, repo["repoNm"])
    if await _ok(d, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        await _git(d, "branch", "-D", branch)
    if await _remote_has(d, branch):
        await _git(d, "push", "origin", "--delete", branch)


async def remove_worktree(path: Path, repo_root: Path) -> None:
    if path.exists():
        await _git(repo_root, "worktree", "remove", str(path))  # no --force: refuses dirty trees
    await _git(repo_root, "worktree", "prune")
