"""All git operations for the plan pipeline. Every call is idempotent (safe to re-run).

Layout: base clone `<root>/.repos/<repoNm>/`, task worktree `<root>/.tasks/<issueKey>/<repoNm>/`.
Merges/rebases run in the base clone on detached checkouts so they never touch branches
checked out in worktrees; pushes use explicit refspecs. Every touch of a base clone holds a
per-clone asyncio.Lock (one process owns `<root>`).
"""

import asyncio
import os
import re
import signal
from pathlib import Path

# ponytail: one cap for every git call (a big clone must fit); make it a setting if not
GIT_TIMEOUT_S = 600.0
_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")


def redact(text: str) -> str:
    """`scheme://user:pass@host` → `scheme://***@host`: git echoes remote URLs in errors."""
    return _USERINFO.sub(r"\1***@", text)


class GitError(Exception):
    def __init__(self, repo: str, cmd: str, stderr: str):
        stderr = redact(stderr)
        super().__init__(f"[{repo}] git {redact(cmd)}: {stderr.strip()}")
        self.repo, self.cmd, self.stderr = repo, redact(cmd), stderr


_locks: dict[Path, asyncio.Lock] = {}


def _lock(d: Path) -> asyncio.Lock:
    # ponytail: in-process only; add a file lock if several processes ever share a root
    return _locks.setdefault(d.resolve(), asyncio.Lock())


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
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"},
        start_new_session=True,  # own process group: a timeout also kills ssh/hooks
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_S)
    except TimeoutError:
        os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise GitError(cwd.name, " ".join(args), f"timed out after {GIT_TIMEOUT_S:g}s") from None
    return proc.returncode, out.decode().strip(), err.decode()


async def _git(cwd: Path, *args: str, repo: str | None = None) -> str:
    rc, out, err = await _run(cwd, *args)
    if rc:
        raise GitError(repo or cwd.name, " ".join(args), err.strip() or out)
    return out


async def _ok(cwd: Path, *args: str) -> bool:
    return (await _run(cwd, *args))[0] == 0


async def _remote_has(d: Path, branch: str) -> bool:
    args = ("ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}")
    rc, out, err = await _run(d, *args)
    if rc not in (0, 2):  # 2 = no matching ref
        raise GitError(d.name, " ".join(args), err.strip() or out)
    return rc == 0


async def _clone_or_fetch(root: Path, repo: dict) -> Path:
    """Caller holds the clone lock."""
    d = repo_dir(root, repo["repoNm"])
    if (d / ".git").exists():
        await _git(d, "remote", "set-url", "origin", repo["gitUrl"])  # mapping may have moved
        await _git(d, "fetch", "--prune", "origin")
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
        await _git(d.parent, "clone", "--", repo["gitUrl"], d.name, repo=repo["repoNm"])
    return d


async def _detach(d: Path, ref: str) -> None:
    """Clear merge/rebase state left by a crash, then check out `ref` detached."""
    await _run(d, "merge", "--abort")
    await _run(d, "rebase", "--abort")
    await _git(d, "checkout", "-q", "-f", "--detach", ref)


async def ensure_clone(root: Path, repo: dict) -> Path:
    async with _lock(repo_dir(root, repo["repoNm"])):
        return await _clone_or_fetch(root, repo)


async def ensure_branch(root: Path, repo: dict, branch: str, start: str) -> None:
    async with _lock(repo_dir(root, repo["repoNm"])):
        d = await _clone_or_fetch(root, repo)
        if not await _remote_has(d, branch):
            await _git(d, "push", "origin", f"{start}:refs/heads/{branch}")


async def add_worktree(root: Path, repo: dict, issue_key: str, start: str) -> Path:
    path = task_dir(root, issue_key) / repo["repoNm"]
    if path.exists():
        return path
    async with _lock(repo_dir(root, repo["repoNm"])):
        d = await _clone_or_fetch(root, repo)
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
    async with _lock(repo_dir(root, repo["repoNm"])):
        return await _merge_no_ff(await _clone_or_fetch(root, repo), source, target)


async def _merge_no_ff(d: Path, source: str, target: str) -> bool:
    src, tgt = f"origin/{source}", f"origin/{target}"
    if await _ok(d, "merge-base", "--is-ancestor", src, tgt):
        return False
    await _detach(d, tgt)
    try:
        await _git(d, "merge", "--no-ff", "-m", f"Merge {source} into {target}", src)
    except GitError:
        await _run(d, "merge", "--abort")
        raise
    await _git(d, "push", "origin", f"HEAD:refs/heads/{target}")
    return True


async def rebase_ff(root: Path, repo: dict, branch: str) -> bool:
    async with _lock(repo_dir(root, repo["repoNm"])):
        return await _rebase_ff(await _clone_or_fetch(root, repo), branch, repo["baseBranch"])


async def _rebase_ff(d: Path, branch: str, base: str) -> bool:
    src, dst = f"origin/{branch}", f"origin/{base}"
    if not await _ok(d, "rev-parse", "--verify", "--quiet", src):
        return False  # repo mapped after PREPARING: the plan never touched it
    if await _ok(d, "merge-base", "--is-ancestor", src, dst):
        return False
    old = await _git(d, "rev-parse", src)
    await _detach(d, src)
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
    # ponytail: rev-list is read-only; fetch above is under the lock
    return int(await _git(d, "rev-list", "--count", f"{ref}..origin/{repo['baseBranch']}")) > 0


async def merged_since(root: Path, repo: dict, plan_branch: str, since: str) -> bool:
    """True if origin/<base> holds a `Merge ... into <plan_branch>` commit missing from
    origin/<since>. Merge messages survive `rebase --rebase-merges`, so this is how a repo
    "changed by the plan" is told apart from foreign commits on base, with no stored state."""
    d = await ensure_clone(root, repo)
    rng = f"origin/{since}..origin/{repo['baseBranch']}"
    return int(await _git(d, "rev-list", "--count", f"--grep=into {plan_branch}$", rng)) > 0


async def delete_branch(root: Path, repo: dict, branch: str) -> None:
    d = repo_dir(root, repo["repoNm"])
    if not (d / ".git").exists():
        return  # never cloned here (repo mapped later): nothing of ours to delete
    async with _lock(d):
        if await _ok(d, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
            await _git(d, "branch", "-D", branch)
        if await _remote_has(d, branch):
            await _git(d, "push", "origin", "--delete", branch)


async def remove_worktree(path: Path, repo_root: Path) -> None:
    if path.exists():
        await _git(repo_root, "worktree", "remove", str(path))  # no --force: refuses dirty trees
    await _git(repo_root, "worktree", "prune")
