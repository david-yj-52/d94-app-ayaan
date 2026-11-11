"""In-memory doubles for scheduler tests. InMemoryQuery mirrors d94-query (Task 2) semantics."""

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ayaan.adapters import RunEvent
from ayaan.clients import from_query_ts, to_query_ts
from ayaan.runner import RunResult

SEED_CONFIG = {
    "EXHAUST_POLICY": "WAIT_SAME_TOOL",
    "FALLBACK_TOOL_ORDER": "CLAUDE,CODEX,GEMINI,CURSOR",
    "RESUME_CHECK_INTERVAL_SEC": "60",
    "RESUME_GRACE_SEC": "120",
    "MAX_RESUME_COUNT": "10",
    "TASK_TIMEOUT_SEC": "1800",
    "QUOTA_POLL_INTERVAL_SEC": "300",
    "CLAUDE_USAGE_POLL": "true",
    "REPORT_CRON": "0 9,18 * * *",
    "NOTIFY_EVENTS": "PAUSED,RESUMED,DONE,FAILED,QUOTA_EXHAUSTED,QUOTA_RESTORED",
    "DEFAULT_PROJECT_KEY": "",
}
SEED_RULES = {
    "CLAUDE": "INTERVAL 30m",
    "CODEX": "INTERVAL 30m",
    "GEMINI": "DAILY 00:00 America/Los_Angeles",
    "CURSOR": "INTERVAL 60m",
    "FAKE": "INTERVAL 1m",
}
TRANSITION_FIELDS = {
    "resumeAt",
    "pauseReason",
    "resumeCount",
    "cliSessionId",
    "toolCd",
    "resultSummary",
    "failReason",
    "leaseOwner",
    "leaseUntil",
    "startedAt",
    "finishedAt",
}


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class InMemoryQuery:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(UTC))
        self.config: dict[str, str] = {}
        self.tools: dict[str, dict] = {}
        self.quotas: dict[tuple[str, str], dict] = {}
        self.tasks: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.workspaces: dict[str, dict] = {}
        self.issues: dict[str, dict] = {}
        self.fail_finish = 0  # number of finish_run calls that raise

    @classmethod
    def seeded(cls, clock=None) -> "InMemoryQuery":
        q = cls(clock)
        q.config = dict(SEED_CONFIG)
        for cd, rule in SEED_RULES.items():
            q.tools[cd] = {
                "toolCd": cd,
                "enabled": cd == "CLAUDE",
                "authMode": "ACCOUNT",
                "planName": None,
                "apiKeyRef": None,
                "model": None,
                "maxConcurrency": 1,
                "fallbackResetRule": rule,
                "budgetTokens": None,
                "budgetPeriod": None,
            }
        return q

    # ── test helpers ──
    def add_task(self, **fields) -> str:
        tid = f"t{len(self.tasks) + 1}"
        if "tool" in fields:
            fields["toolCd"] = fields.pop("tool")
        self.tasks[tid] = {
            "id": tid,
            "issueKey": None,
            "source": "API",
            "projectKey": "P",
            "workspacePath": f"/ws/{tid}",
            "prompt": "do it",
            "toolCd": "CLAUDE",
            "exhaustPolicy": None,
            "fallbackTools": None,
            "status": "QUEUED",
            "cliSessionId": None,
            "resumeAt": None,
            "pauseReason": None,
            "resumeCount": 0,
            "leaseOwner": None,
            "leaseUntil": None,
            "resultSummary": None,
            "failReason": None,
            "startedAt": None,
            "finishedAt": None,
            "createdAt": to_query_ts(self.clock() + timedelta(microseconds=len(self.tasks))),
            **fields,
        }
        return tid

    def task(self, tid: str) -> dict:
        return self.tasks[tid]

    # ── QueryClient surface ──
    async def get_config(self):
        return dict(self.config)

    async def put_config(self, values):
        self.config.update(values)
        return dict(self.config)

    async def get_tools(self):
        return [dict(t) for t in self.tools.values()]

    async def put_tool(self, cd, body):
        self.tools[cd].update(body)
        return dict(self.tools[cd])

    async def get_workspaces(self):
        return [dict(w) for w in self.workspaces.values()]

    async def put_workspace(self, key, body):
        self.workspaces[key] = {**self.workspaces.get(key, {}), **body, "projectKey": key}
        return dict(self.workspaces[key])

    async def delete_workspace(self, key):
        self.workspaces.pop(key, None)

    async def get_quotas(self):
        return [dict(r) for r in self.quotas.values()]

    async def put_quota(self, tool, window, body):
        self.quotas[(tool, window)] = {"toolCd": tool, "windowCd": window, **body}
        return self.quotas[(tool, window)]

    async def create_task(self, body):
        key = body.get("issueKey")
        for t in self.tasks.values():
            if key and t["issueKey"] == key and t["status"] in ("QUEUED", "RUNNING", "PAUSED"):
                return 409, dict(t)
        tid = self.add_task(**body)
        return 201, dict(self.tasks[tid])

    async def list_tasks(self, status=None, tool=None):
        return [
            dict(t)
            for t in self.tasks.values()
            if (not status or t["status"] == status) and (not tool or t["toolCd"] == tool)
        ]

    async def get_task(self, task_id):
        t = dict(self.tasks[task_id])
        runs = [dict(r) for r in self.runs.values() if r["taskId"] == task_id]
        t["runs"] = runs
        t["tokenTotals"] = {
            "input": sum(r.get("inputTokens") or 0 for r in runs),
            "output": sum(r.get("outputTokens") or 0 for r in runs),
            "cacheRead": sum(r.get("cacheReadTokens") or 0 for r in runs),
            "cacheWrite": sum(r.get("cacheWriteTokens") or 0 for r in runs),
            "costUsd": None,
        }
        return t

    async def transition(self, task_id, expected, next, **fields):
        unknown = set(fields) - TRANSITION_FIELDS
        if unknown:  # d94-query answers 400
            raise ValueError(f"unknown field: {unknown}")
        t = self.tasks[task_id]
        if t["status"] != expected:
            return None
        t["status"] = next
        t.update(fields)
        return dict(t)

    async def claim(self, tool, owner, lease_s, grace_s):
        now = self.clock()
        busy = {t["workspacePath"] for t in self.tasks.values() if t["status"] == "RUNNING"}
        for t in sorted(self.tasks.values(), key=lambda t: t["createdAt"]):
            ready = t["status"] == "QUEUED" or (
                t["status"] == "PAUSED"
                and t["resumeAt"]
                and from_query_ts(t["resumeAt"]) + timedelta(seconds=grace_s) <= now
            )
            if t["toolCd"] == tool and ready and t["workspacePath"] not in busy:
                t.update(
                    status="RUNNING",
                    leaseOwner=owner,
                    leaseUntil=to_query_ts(now + timedelta(seconds=lease_s)),
                    startedAt=t["startedAt"] or to_query_ts(now),
                )
                return dict(t)
        return None

    async def extend_lease(self, task_id, owner, lease_s):
        t = self.tasks.get(task_id)
        if not t or t["status"] != "RUNNING" or t["leaseOwner"] != owner:
            return False
        t["leaseUntil"] = to_query_ts(self.clock() + timedelta(seconds=lease_s))
        return True

    async def recover(self):
        now, ids = self.clock(), []
        for t in self.tasks.values():
            expired = t["leaseUntil"] and from_query_ts(t["leaseUntil"]) < now
            if t["status"] == "RUNNING" and expired:
                t.update(
                    status="PAUSED",
                    pauseReason="RECOVERED",
                    resumeAt=to_query_ts(now),
                    leaseOwner=None,
                    leaseUntil=None,
                )
                ids.append(t["id"])
        return ids

    async def start_run(self, task_id, run_seq, tool):
        rid = f"r{len(self.runs) + 1}"
        self.runs[rid] = {
            "id": rid,
            "taskId": task_id,
            "runSeq": run_seq,
            "toolCd": tool,
            "startedAt": to_query_ts(self.clock()),
        }
        return dict(self.runs[rid])

    async def finish_run(self, run_id, body):
        if self.fail_finish > 0:
            self.fail_finish -= 1
            raise RuntimeError("query unavailable")
        self.runs[run_id].update({k: v for k, v in body.items() if v is not None})
        return dict(self.runs[run_id])

    async def issue_by_key(self, key):
        return self.issues.get(key)


class FakeEcc:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple] = []

    async def notify(self, event, title, body, buttons=None):
        self.sent.append((event, title, body, buttons))
        if self.fail:
            raise RuntimeError("ecc down")
        return True

    def events(self) -> list[str]:
        return [s[0] for s in self.sent]


def fake_runner(script: list[RunResult], gate=None):
    """Returns queued results in order; records kwargs in .calls. Optional asyncio.Event gate."""
    script = list(script)

    async def run(**kw):
        run.calls.append(SimpleNamespace(**kw))
        if gate is not None:
            await gate.wait()
        return copy.deepcopy(script.pop(0))

    run.calls = []
    return run


def ok_result(session: str | None, result_line: str = "AYAAN_RESULT: done") -> RunResult:
    ev = RunEvent(
        session_id=session,
        input_tokens=100,
        output_tokens=10,
        result_line=result_line.removeprefix("AYAAN_RESULT:").strip(),  # as the adapter does
        json_seen=True,
    )
    return RunResult("COMPLETED", ev, 0, "ok")


def limit_result(session: str | None, resets_at: datetime | None) -> RunResult:
    ev = RunEvent(session_id=session, limit_hit=True, resets_at=resets_at, json_seen=True)
    return RunResult("LIMIT_HIT", ev, 1, "You've hit your limit")


def fail_result(tail: str = "boom", session_missing: bool = False) -> RunResult:
    return RunResult("FAILED", RunEvent(session_missing=session_missing), 1, tail)
