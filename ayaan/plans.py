"""Milestone plan state machine (spec 5.3), advanced one stage per scheduler tick.

PREPARING → RUNNING → MERGING → DEPLOYING → CLEANING → DONE; any failure → HALTED with
lastError "<STAGE>: <error>", which resume() parses to restart that stage. Every stage is
safe to re-run after a crash: git ops are idempotent and a DONE task whose task dir still
exists is the marker for "not yet merged into the plan branch".
"""

import logging
import shutil
from pathlib import Path

import httpx

from ayaan import git
from ayaan.clients import to_query_ts
from ayaan.rules import DEFAULT_TEMPLATE, render_prompt

log = logging.getLogger(__name__)

ACTIVE_TASK = ("RUNNING", "QUEUED", "PAUSED")
TASK_FAILED = "RUNNING: task "  # lastError prefix of a halt caused by a FAILED plan task
STOPPED = ("FAILED", "CANCELLED")  # plan task ended without DONE: the plan halts on it


def targets(issues: list[dict]) -> list[dict]:
    todo = [
        i for i in issues if i.get("issueTypeNm") != "Epic" and i.get("statusCategory") != "DONE"
    ]
    return sorted(todo, key=lambda i: i["issueNo"])


async def plan_tasks(query, plan_id: str) -> list[dict]:
    # ponytail: client-side filter; add a planId query param if task lists grow large
    return [t for t in await query.list_tasks() or [] if t.get("planId") == plan_id]


async def _workspace(sched, project_key: str) -> dict:
    ws = await sched.query.get_workspaces() or []
    return next((w for w in ws if w.get("projectKey") == project_key), {})


# ── stages: each returns (next stage, plan fields) ──


async def _preparing(sched, plan, root, repos, branch):
    if not repos:
        raise ValueError("workspace has no repos")
    for r in repos:
        await git.ensure_branch(root, r, branch, f"origin/{r['baseBranch']}")
    return "RUNNING", {}


async def _integrate(sched, task: dict, root: Path, repos: list[dict], branch: str) -> None:
    """Push + merge the task branch into the plan branch, then drop the task's worktrees."""
    key, tdir = task["issueKey"], Path(task["workspacePath"])
    for r in repos:
        path = tdir / r["repoNm"]
        if path.exists():
            if await git.has_commits(path, f"origin/{branch}"):
                await git.push_branch(path, f"ayaan/{key}")
                await git.merge_no_ff(root, r, f"ayaan/{key}", branch)
            await git.remove_worktree(path, git.repo_dir(root, r["repoNm"]))
        await git.delete_branch(root, r, f"ayaan/{key}")
    try:
        await sched.query.issue_done(key)
    except Exception:  # spec: a CIRA transition that fails is skipped and only logged
        log.warning("issue_done %s failed", key, exc_info=True)
    shutil.rmtree(tdir, ignore_errors=True)  # repo worktrees are gone; only CLI leftovers


async def _running(sched, plan, root, repos, branch):
    q = sched.query
    tasks = await plan_tasks(q, plan["id"])
    if any(t["status"] in ACTIVE_TASK for t in tasks):
        return "RUNNING", {}
    for t in tasks:
        if t["status"] == "DONE" and Path(t["workspacePath"]).exists():
            await _integrate(sched, t, root, repos, branch)
    failed = next((t for t in tasks if t["status"] in STOPPED), None)
    if failed and failed["status"] == "CANCELLED":
        raise RuntimeError(f"task {failed['issueKey']} CANCELLED")
    if failed:
        reason = ((failed.get("failReason") or "").splitlines() or [""])[0]
        raise RuntimeError(f"task {failed['issueKey']} FAILED: {reason}")
    done = {t["issueKey"] for t in tasks if t["status"] == "DONE"}
    issues = targets(await q.milestone_issues(plan["milestoneId"]) or [])
    nxt = next((i["issueKey"] for i in issues if i["issueKey"] not in done), None)
    if nxt is None:
        return "MERGING", {}
    ws = await _workspace(sched, plan["projectKey"])
    issue = await q.issue_by_key(nxt) or {}
    status, _ = await q.create_task(
        {
            "issueKey": nxt,
            "source": "API",
            "projectKey": plan["projectKey"],
            "workspacePath": str(git.task_dir(root, nxt)),
            "prompt": render_prompt(
                ws.get("promptTemplate") or DEFAULT_TEMPLATE,
                nxt,
                issue.get("title") or "",
                issue.get("content") or "",
            ),
            "toolCd": ws.get("defaultTool") or "CLAUDE",
            "planId": plan["id"],
        }
    )
    if status == 409:  # a standalone task holds the issue; wait for it to end
        log.info("plan %s waits: %s already has an active task", plan["id"], nxt)
        return "RUNNING", {}
    sched.wake()
    return "RUNNING", {"currentIssueKey": nxt}


async def _merging(sched, plan, root, repos, branch):
    for r in repos:  # already sorted by sortOrd
        await git.rebase_ff(root, r, branch)
    return "DEPLOYING", {}


async def _deploying(sched, plan, root, repos, branch):
    for r in repos:
        dep = r.get("deployBranch")
        if dep and await git.merged_since(root, r, branch, dep):
            await git.merge_no_ff(root, r, r["baseBranch"], dep)
    return "CLEANING", {}


async def _cleaning(sched, plan, root, repos, branch):
    for t in await plan_tasks(sched.query, plan["id"]):
        tdir = Path(t["workspacePath"])
        if not tdir.exists():
            continue
        kept = False
        for r in repos:  # same safety check as GC: nothing uncommitted, nothing unmerged
            path = tdir / r["repoNm"]
            if not path.exists():
                continue
            if await git.dirty(path) or await git.has_commits(path, f"origin/{r['baseBranch']}"):
                kept = True
                continue
            await git.remove_worktree(path, git.repo_dir(root, r["repoNm"]))
            await git.delete_branch(root, r, f"ayaan/{t['issueKey']}")
        if not kept:
            shutil.rmtree(tdir, ignore_errors=True)
    for r in repos:
        await git.delete_branch(root, r, branch)
    return "DONE", {}


STEPS = {
    "PREPARING": _preparing,
    "RUNNING": _running,
    "MERGING": _merging,
    "DEPLOYING": _deploying,
    "CLEANING": _cleaning,
}
NOTICES = {("PREPARING", "RUNNING"): "PLAN_STARTED", ("DEPLOYING", "CLEANING"): "PLAN_DEPLOYED"}
_TITLE = {"PLAN_STARTED": "계획 시작", "PLAN_DEPLOYED": "배포 완료", "PLAN_HALTED": "계획 중단"}


async def _notice(sched, event: str, plan: dict, detail: str = "") -> None:
    body = f"{plan['projectKey']} 마일스톤 {plan['milestoneKey']}\n{detail}".strip()
    await sched.notify(event, f"{_TITLE[event]}: {plan['milestoneKey']}", body)


async def step(sched, plan: dict) -> str:
    """Runs the current stage once; returns the stage the plan is in afterwards."""
    q, stage = sched.query, plan["stage"]
    fn = STEPS.get(stage)
    if fn is None:
        return stage
    root = sched.settings.workspace_root_paths[0]
    branch = f"ayaan/plan-{plan['milestoneKey']}"
    try:
        nxt, fields = await fn(sched, plan, root, await sched._repos(plan), branch)
    except httpx.HTTPError:
        raise  # d94-query unreachable: not a plan failure, the next tick retries the stage
    except Exception as e:
        log.warning("plan %s halted in %s", plan["id"], stage, exc_info=True)
        msg = git.redact(f"{stage}: {e}")[:2000]
        if not await q.plan_stage(plan["id"], stage, "HALTED", lastError=msg):
            return (await q.get_plan(plan["id"]))["stage"]
        await _notice(sched, "PLAN_HALTED", plan, msg)
        return "HALTED"
    if nxt == stage and not fields:
        return stage
    if not await q.plan_stage(plan["id"], stage, nxt, **fields):
        return (await q.get_plan(plan["id"]))["stage"]  # moved meanwhile (cancel, re-run)
    if event := NOTICES.get((stage, nxt)):
        await _notice(sched, event, plan)
    return nxt


async def resume(sched, plan: dict) -> dict | None:
    """HALTED → RUNNING with the failed/cancelled task requeued (same task, R3), else → the
    stage named in lastError. None if the plan is not HALTED."""
    if plan["stage"] != "HALTED":
        return None
    failed = [t for t in await plan_tasks(sched.query, plan["id"]) if t["status"] in STOPPED]
    head = (plan.get("lastError") or "").split(":", 1)[0]
    nxt = "RUNNING" if failed else head if head in STEPS else "PREPARING"
    p = await sched.query.plan_stage(plan["id"], "HALTED", nxt)
    if p:
        now = to_query_ts(sched.clock())
        for t in failed:  # same fields as api.resume_task
            await sched.query.transition(
                t["id"], t["status"], "QUEUED", resumeAt=now, resumeCount=0
            )
        sched.wake()
    return p


async def advance_plans(sched) -> None:
    for plan in await sched.query.list_plans(active=True) or []:
        if plan["stage"] == "HALTED":
            # the task's own [재시도] requeued it: the plan follows once nothing is stopped
            if (plan.get("lastError") or "").startswith(TASK_FAILED):
                tasks = await plan_tasks(sched.query, plan["id"])
                if not any(t["status"] in STOPPED for t in tasks):
                    await sched.query.plan_stage(plan["id"], "HALTED", "RUNNING")
            continue
        await step(sched, plan)


# ── worktree GC (spec: never touches .repos/) ──

ENDED = ("DONE", "CANCELLED")


async def safe_remove(root: Path, issue_key: str, repos: list[dict]) -> bool:
    """Removes <root>/.tasks/<issue_key> unless a repo worktree is dirty or unpushed (or git
    refuses); in that case nothing is deleted and False is returned."""
    tdir = git.task_dir(root, issue_key)
    paths = [(tdir / r["repoNm"], r) for r in repos if (tdir / r["repoNm"]).exists()]
    known = {r["repoNm"] for r in repos}
    if tdir.is_dir() and any(  # a checkout we cannot vouch for: leave it alone
        c.name not in known and (c / ".git").exists() for c in tdir.iterdir()
    ):
        return False
    try:
        for path, r in paths:
            # unpushed commits matter only if the branch has work beyond its base
            if await git.dirty(path) or (
                await git.unpushed(path)
                and await git.has_commits(path, f"origin/{r['baseBranch']}")
            ):
                return False
        for path, r in paths:
            await git.remove_worktree(path, git.repo_dir(root, r["repoNm"]))
    except git.GitError:
        log.warning("worktree cleanup of %s held", tdir, exc_info=True)
        return False
    shutil.rmtree(tdir, ignore_errors=True)
    return True


async def removable(query, tasks: list[dict]) -> bool:
    """True if every given task ended and none belongs to a plan that is still active."""
    plan_ids = {t["planId"] for t in tasks if t.get("planId")}
    stages = {p["id"]: p["stage"] for p in await query.list_plans() or []}
    return all(t["status"] in ENDED for t in tasks) and all(
        stages.get(i, "DONE") in ENDED for i in plan_ids
    )


async def gc_worktrees(sched) -> list[str]:
    """Returns the folders kept because of uncommitted/unpushed work."""
    root = sched.settings.workspace_root_paths[0]
    base = root / ".tasks"
    if not base.is_dir():
        return []
    q = sched.query
    all_tasks = await q.list_tasks() or []
    by_dir: dict[str, list[dict]] = {}
    for t in all_tasks:
        by_dir.setdefault(Path(t["workspacePath"]).name, []).append(t)
    # ponytail: orphan folders have no project, so every mapped repo name is tried
    repos = {r["repoNm"]: r for w in await q.get_workspaces() or [] for r in w.get("repos") or []}
    held = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        if await removable(q, by_dir.get(d.name, [])) and not await safe_remove(
            root, d.name, list(repos.values())
        ):
            held.append(str(d))
    return held
