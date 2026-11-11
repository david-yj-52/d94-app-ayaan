"""Scheduler: claim per tool, run the CLI, extend leases, pause/resume on limits, report."""

import asyncio
import contextlib
import logging
import os
import socket
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from croniter import croniter

from ayaan.adapters import ADAPTERS, RunEvent
from ayaan.clients import from_query_ts, to_query_ts
from ayaan.quota import _WINDOW_KO, apply_quotas, budget_quota, collect_all, quotas_from_event
from ayaan.rules import NOTIFY, effective_policy, resolve_resume_at
from ayaan.runner import RunResult, kill_task, pending_spool, run_cli, write_spool

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
LEASE_S = 180
LEASE_EXTEND_S = 60
SETTLE_BACKOFF = (5, 15, 30, 60)  # then every 60 s
RESUME_PROMPT = "중단된 작업을 이어서 진행해. 작업공간의 현재 상태를 먼저 확인해."
SWITCH_NOTE = "\n\n작업공간에 이전 Tool이 진행한 변경사항이 있다. 먼저 확인하고 이어서 진행해."
DEFAULT_EXE = {"claude": "claude", "codex": "codex", "gemini": "gemini", "cursor": "cursor-agent"}
KEY_ENV = {
    "CLAUDE": "ANTHROPIC_API_KEY",
    "CODEX": "OPENAI_API_KEY",
    "GEMINI": "GEMINI_API_KEY",
    "CURSOR": "CURSOR_API_KEY",
}
_STATUS_KO = (("RUNNING", "실행"), ("QUEUED", "대기"), ("PAUSED", "중단"))


# ── message formatting ─────────────────────────────────────────────────────


def _kst(v) -> str:
    if not v:
        return "미정"
    dt = from_query_ts(v) if isinstance(v, str) else v
    return dt.astimezone(KST).strftime("%m-%d %H:%M")


def _label(task: dict) -> str:
    if task.get("issueKey"):
        return task["issueKey"]
    first = ((task.get("prompt") or "").strip().splitlines() or [""])[0]
    return first[:40] or task.get("id", "")


def _buttons(task_id: str) -> list[dict]:
    return [
        {"label": "지금 재개", "action": "resume", "taskId": task_id},
        {"label": "취소", "action": "cancel", "taskId": task_id},
    ]


def format_paused(task: dict, resume_at) -> str:
    reason = task.get("pauseReason") or "한도 소진"
    return (
        f"{_label(task)}\nTool: {task.get('toolCd')}\n"
        f"사유: {_WINDOW_KO.get(reason, reason)}\n재개 예정: {_kst(resume_at)} (KST)"
    )


def format_done(task: dict, totals: dict, duration: timedelta) -> str:
    t = totals or {}
    cache = (t.get("cacheRead") or 0) + (t.get("cacheWrite") or 0)
    mins = int(duration.total_seconds() // 60)
    return (
        f"{_label(task)}\n결과: {task.get('resultSummary') or '-'}\n"
        f"토큰: 입력 {t.get('input') or 0} / 출력 {t.get('output') or 0} / 캐시 {cache}\n"
        f"소요: {mins // 60}시간 {mins % 60}분, 재개 {task.get('resumeCount') or 0}회"
    )


def format_failed(task: dict, tail: str) -> str:
    last = "\n".join((tail or "").splitlines()[-10:])
    reason = (task.get("failReason") or "").splitlines()[:1]
    return f"{_label(task)}\nTool: {task.get('toolCd')}\n사유: {''.join(reason)}\n로그:\n{last}"


def format_report(quotas: list[dict], counts: dict, paused: list[dict]) -> str:
    lines = ["[한도]"]
    for q in sorted(quotas, key=lambda q: (q["toolCd"], q["windowCd"])):
        pct = "?" if q.get("usedPct") is None else f"{float(q['usedPct']):g}%"
        lines.append(
            f"{q['toolCd']} {_WINDOW_KO.get(q['windowCd'], q['windowCd'])}: {pct} "
            f"({q.get('status')}), reset {_kst(q.get('resetsAt'))}, "
            f"{q.get('source') or '-'} {_kst(q.get('observedAt'))} 관측"
        )
    lines.append("[작업] " + ", ".join(f"{ko} {counts.get(st, 0)}" for st, ko in _STATUS_KO))
    for t in paused:
        lines.append(f"- {_label(t)} ({t.get('toolCd')}) 재개 예정 {_kst(t.get('resumeAt'))}")
    return "\n".join(lines)


# ── scheduler ──────────────────────────────────────────────────────────────


def _blocking(tool: str, quotas: list[dict], now: datetime, grace: int) -> dict | None:
    """The EXHAUSTED row for `tool` that blocks longest (resetsAt + grace > now), or None."""
    rows = [
        r
        for r in quotas
        if r.get("toolCd") == tool
        and r.get("status") == "EXHAUSTED"
        and r.get("resetsAt")
        and from_query_ts(r["resetsAt"]) + timedelta(seconds=grace) > now
    ]
    return max(rows, key=lambda r: from_query_ts(r["resetsAt"]), default=None)


def _int(cfg: dict, key: str, default: int) -> int:
    try:
        return int(cfg.get(key) or default)
    except ValueError:
        return default


class Scheduler:
    def __init__(self, query, ecc, settings, clock=lambda: datetime.now(UTC), runner=run_cli):
        self.query, self.ecc, self.settings = query, ecc, settings
        self.clock, self.runner = clock, runner
        self.owner = f"{socket.gethostname()}:{os.getpid()}"
        self.running: dict[str, int] = defaultdict(int)
        self.warnings: list[str] = []
        self._cfg: dict = {}
        self._tasks: set[asyncio.Task] = set()
        self._loops: list[asyncio.Task] = []
        self._wake = asyncio.Event()
        self._active: set[str] = set()  # task ids inside _execute in this process
        self._lost: set[str] = set()  # task ids whose lease is no longer ours
        self.sleep = asyncio.sleep  # settle backoff; injectable for tests

    # ── notifications ──
    async def _config(self) -> dict:
        self._cfg = await self.query.get_config() or {}
        return self._cfg

    async def _notify(self, event, title, body, buttons=None) -> bool:
        raw = self._cfg.get("NOTIFY_EVENTS")
        events = NOTIFY if raw is None else {e.strip() for e in raw.split(",") if e.strip()}
        if event not in events:
            return False
        try:
            return bool(await self.ecc.notify(event, title, body, buttons))
        except Exception:
            log.warning("notify %s failed", event, exc_info=True)
            return False

    async def notify(self, event, title, body, buttons=None) -> bool:
        """Never-raising ecc stand-in handed to quota.apply_quotas / collect_all."""
        return await self._notify(event, title, body, buttons)

    # ── loops ──
    async def start(self) -> None:
        self._loops = [
            asyncio.create_task(self._tick_loop()),
            asyncio.create_task(self._collect_loop()),
            asyncio.create_task(self._report_loop()),
        ]

    async def stop(self) -> None:
        # ponytail: running CLI processes are left to the container stop; leases expire and
        # the next boot resumes them
        for t in self._loops + list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._loops, *self._tasks, return_exceptions=True)
        self._loops = []

    def wake(self) -> None:
        self._wake.set()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _tick_loop(self):
        while True:
            self._wake.clear()
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")
            interval = _int(self._cfg, "RESUME_CHECK_INTERVAL_SEC", 60)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), interval)

    async def _collect_loop(self):
        while True:
            try:
                self.warnings = await collect_all(self.query, self, self.settings, self.clock())
            except Exception:
                log.exception("quota collection failed")
            await asyncio.sleep(_int(self._cfg, "QUOTA_POLL_INTERVAL_SEC", 300))

    async def _report_loop(self):
        # ponytail: polls REPORT_CRON every 30 s so config edits apply without restart;
        # empty value sends nothing
        last = self.clock()
        while True:
            await asyncio.sleep(30)
            now = self.clock()
            try:
                cron = (self._cfg.get("REPORT_CRON") or "").strip()
                if cron and croniter(cron, last.astimezone(KST)).get_next(datetime) <= now:
                    await self.send_report()
            except Exception:
                log.exception("report failed")
            last = now

    async def send_report(self) -> None:
        tasks = await self.query.list_tasks() or []
        counts: dict[str, int] = defaultdict(int)
        for t in tasks:
            counts[t["status"]] += 1
        paused = [t for t in tasks if t["status"] == "PAUSED"]
        body = format_report(await self.query.get_quotas() or [], counts, paused)
        try:  # not an immediate event: NOTIFY_EVENTS does not filter it
            await self.ecc.notify("REPORT", "ayaan 정기 리포트", body)
        except Exception:
            log.warning("report notify failed", exc_info=True)

    # ── boot / tick ──
    async def boot(self) -> None:
        for path, body in pending_spool(Path(self.settings.data_dir)):
            try:
                await self.query.finish_run(path.stem, body)
                path.unlink()
            except Exception:
                log.warning("spool replay failed for %s", path.name, exc_info=True)
        await self._recover()
        self.wake()

    async def _recover(self) -> None:
        # Also runs every tick: a restart shorter than the lease leaves RUNNING rows that are
        # not yet expired at boot. Recovered tasks are requeued so the next tick resumes them.
        # An expired lease on a task this process is still running (query outage > lease) is
        # taken straight back instead of requeued, so it is never claimed and run twice.
        now = self.clock()
        for tid in await self.query.recover() or []:
            if tid in self._active:
                await self.query.transition(
                    tid,
                    "PAUSED",
                    "RUNNING",
                    leaseOwner=self.owner,
                    leaseUntil=to_query_ts(now + timedelta(seconds=LEASE_S)),
                )
            else:
                await self.query.transition(tid, "PAUSED", "QUEUED", resumeAt=to_query_ts(now))

    async def tick(self) -> None:
        await self._recover()
        cfg = await self._config()
        now, grace = self.clock(), _int(cfg, "RESUME_GRACE_SEC", 120)
        quotas = await self.query.get_quotas() or []
        for tool in await self.query.get_tools() or []:
            cd = tool["toolCd"]
            if not tool.get("enabled") or (cd == "FAKE" and not self.settings.fake_enabled):
                continue
            if _blocking(cd, quotas, now, grace):
                continue
            for _ in range((tool.get("maxConcurrency") or 1) - self.running[cd]):
                task = await self.query.claim(cd, self.owner, LEASE_S, grace)
                if not task:
                    break
                self.running[cd] += 1
                self._active.add(task["id"])
                t = asyncio.create_task(self._execute(task, tool))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

    async def pause_if_exhausted(self, task: dict) -> dict:
        cfg = await self._config()
        row = _blocking(
            task["toolCd"],
            await self.query.get_quotas() or [],
            self.clock(),
            _int(cfg, "RESUME_GRACE_SEC", 120),
        )
        if not row or task.get("status") != "QUEUED":
            return task
        t = await self.query.transition(
            task["id"], "QUEUED", "PAUSED", resumeAt=row["resetsAt"], pauseReason=row["windowCd"]
        )
        if not t:
            return task
        await self._notify(
            "PAUSED", f"중단: {_label(t)}", format_paused(t, row["resetsAt"]), _buttons(t["id"])
        )
        return t

    # ── execution ──
    async def _execute(self, task: dict, tool: dict) -> None:
        tid, cd = task["id"], task["toolCd"]
        lease = asyncio.create_task(self._keep_lease(tid))  # held until settle is done
        try:
            cfg = await self._config()
            session = task.get("cliSessionId")
            if session:
                n, mx = task.get("resumeCount") or 0, _int(cfg, "MAX_RESUME_COUNT", 10)
                await self._notify(
                    "RESUMED",
                    f"재개: {_label(task)}",
                    f"{_label(task)}\nTool: {cd}\n재개 {n}/{mx}",
                )
            res, rows = await self._run_once(task, tool, cfg, session)
            retry = res.event.session_missing and res.outcome not in ("COMPLETED", "CANCELLED")
            if session and retry:
                session = None  # the old session is gone: one fresh run on the same tool
                res, rows = await self._run_once(task, tool, cfg, None)
            await self._settle_retrying(task, res, rows, res.event.session_id or session)
        except Exception as e:
            log.exception("execute %s failed", tid)
            with contextlib.suppress(Exception):
                await self.query.transition(
                    tid,
                    "RUNNING",
                    "FAILED",
                    failReason=f"ERROR: {type(e).__name__}: {e}",
                    finishedAt=to_query_ts(self.clock()),
                )
        finally:
            lease.cancel()
            self.running[cd] -= 1
            self._active.discard(tid)
            self._lost.discard(tid)

    async def _settle_retrying(self, task, res, rows, sid) -> None:
        """Query transport errors retry with backoff while the lease is still ours."""
        attempt = 0
        while True:
            try:
                return await self._settle(task, res, rows, sid)
            except httpx.HTTPError:
                if task["id"] in self._lost:
                    log.warning("settle of %s abandoned: lease lost", task["id"])
                    return
                delay = SETTLE_BACKOFF[min(attempt, len(SETTLE_BACKOFF) - 1)]
                log.warning("settle of %s failed; retry in %ss", task["id"], delay, exc_info=True)
                attempt += 1
                await self.sleep(delay)

    def _env(self, tool: dict) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in KEY_ENV.values()}
        if tool.get("authMode") == "API_KEY" and tool["toolCd"] in KEY_ENV:
            env[KEY_ENV[tool["toolCd"]]] = os.environ[tool["apiKeyRef"]]  # never logged
        return env

    async def _keep_lease(self, tid: str) -> None:
        while True:
            await asyncio.sleep(LEASE_EXTEND_S)
            try:
                ok = await self.query.extend_lease(tid, self.owner, LEASE_S)
            except Exception:
                log.warning("lease extend failed for %s", tid, exc_info=True)
                continue  # transient: the lease outlives two missed extensions
            if not ok:
                # recover() may have flipped our own task to PAUSED a moment before _recover
                # takes it back: only a task that is no longer RUNNING under us is lost
                with contextlib.suppress(Exception):
                    t = await self.query.get_task(tid) or {}
                    if t.get("status") == "RUNNING" and t.get("leaseOwner") == self.owner:
                        continue
                self._lost.add(tid)  # cancelled or taken over: stop the process
                kill_task(tid)
                return

    async def _run_once(self, task: dict, tool: dict, cfg: dict, session: str | None):
        tid, cd = task["id"], task["toolCd"]
        runs = (await self.query.get_task(tid) or {}).get("runs") or []
        seq = max((r.get("runSeq") or 0 for r in runs), default=0) + 1
        adapter, model = ADAPTERS[cd], tool.get("model") or None
        if session:
            argv, prompt = adapter.resume_argv(session, model), RESUME_PROMPT
        else:  # earlier runs without a session to resume: another tool/session left changes
            argv, prompt = adapter.new_argv(model), task["prompt"] + (SWITCH_NOTE if runs else "")
        if cd == "FAKE":
            exe = sys.executable
        else:
            exe = self.settings.executable_map.get(cd.lower()) or DEFAULT_EXE[cd.lower()]
        data_dir = Path(self.settings.data_dir)
        log_path = data_dir / "logs" / tid / f"{seq}.log"
        env = self._env(tool)
        run = await self.query.start_run(tid, seq, cd)
        try:
            res = await self.runner(
                tool=cd,
                argv_tail=argv,
                prompt=prompt,
                cwd=Path(task["workspacePath"]),
                timeout_s=_int(cfg, "TASK_TIMEOUT_SEC", 1800),
                log_path=log_path,
                executable=exe,
                env=env,
                task_id=tid,
            )
        except Exception as e:  # still close the run record
            log.exception("runner failed for %s", tid)
            res = RunResult("FAILED", RunEvent(), None, f"{type(e).__name__}: {e}")
        ev, now = res.event, self.clock()
        body = {
            "outcome": res.outcome,
            "inputTokens": ev.input_tokens,
            "outputTokens": ev.output_tokens,
            "cacheReadTokens": ev.cache_read_tokens,
            "cacheWriteTokens": ev.cache_write_tokens,
            "costUsd": ev.cost_usd,
            "exitCode": res.exit_code,
            "logPath": str(log_path),
            "cliSessionId": ev.session_id,
            "endedAt": to_query_ts(now),
        }
        spool = write_spool(data_dir, run["id"], body)
        try:
            await self.query.finish_run(run["id"], body)
            spool.unlink()
        except Exception:
            log.warning("finish_run failed; kept in spool %s", spool.name, exc_info=True)
        rows = quotas_from_event(cd, ev, tool.get("fallbackResetRule") or "INTERVAL 30m", now)
        try:
            b = await self._budget_row(tool, ev, now)
            if b:
                rows.append(b)
            if rows:
                await apply_quotas(self.query, self, cd, rows, NOTIFY)
        except Exception:
            log.warning("quota update failed for %s", cd, exc_info=True)
        return res, rows

    async def _budget_row(self, tool: dict, ev, now: datetime) -> dict | None:
        if tool.get("authMode") != "API_KEY" or not tool.get("budgetTokens"):
            return None
        # ponytail: running total = current BUDGET row usedTokens + this run (no per-period
        # token sum endpoint in d94-query); a row whose reset passed starts a new period.
        # Read-modify-write: concurrent runs on the same API_KEY tool may lose increments;
        # upgrade path is a query-side per-period SUM over TASK_RUN.
        prev = next(
            (
                r
                for r in await self.query.get_quotas() or []
                if r["toolCd"] == tool["toolCd"] and r["windowCd"] == "BUDGET"
            ),
            None,
        )
        used = 0
        if prev and prev.get("resetsAt") and from_query_ts(prev["resetsAt"]) > now:
            used = prev.get("usedTokens") or 0
        return budget_quota(tool, used + ev.input_tokens + ev.output_tokens, now)

    async def _settle(self, task: dict, res, rows: list[dict], sid: str | None) -> None:
        cfg = await self._config()
        tid, now, ev = task["id"], self.clock(), res.event
        if res.outcome == "CANCELLED":
            return  # the API already moved the task; only the run record is finished
        if res.outcome == "COMPLETED":
            t = await self.query.transition(
                tid,
                "RUNNING",
                "DONE",
                resultSummary=ev.result_line,
                cliSessionId=sid,
                finishedAt=to_query_ts(now),
            )
            if t:
                detail = await self.query.get_task(tid) or {}
                started = from_query_ts(t["startedAt"]) if t.get("startedAt") else now
                await self._notify(
                    "DONE",
                    f"완료: {_label(t)}",
                    format_done(t, detail.get("tokenTotals") or {}, now - started),
                )
        elif res.outcome == "LIMIT_HIT":
            await self._on_limit(task, cfg, ev, rows, sid, now)
        else:
            await self._fail(task, f"{res.outcome}\n{res.tail}", res.tail, sid, now)

    async def _fail(self, task, reason, tail, sid, now) -> None:
        t = await self.query.transition(
            task["id"],
            "RUNNING",
            "FAILED",
            failReason=reason,
            cliSessionId=sid,
            finishedAt=to_query_ts(now),
        )
        if t:
            await self._notify(
                "FAILED",
                f"실패: {_label(t)}",
                format_failed(t, tail),
                [{"label": "재시도", "action": "resume", "taskId": t["id"]}],
            )

    @staticmethod
    def _free(tool: str, quotas: list[dict], now: datetime, grace: int) -> bool:
        # EXHAUSTED with no known reset counts as not free for switching
        unknown = any(
            r.get("toolCd") == tool and r.get("status") == "EXHAUSTED" and not r.get("resetsAt")
            for r in quotas
        )
        return not unknown and not _blocking(tool, quotas, now, grace)

    async def _on_limit(self, task, cfg, ev, rows, sid, now) -> None:
        tid, cd = task["id"], task["toolCd"]
        count, mx = (task.get("resumeCount") or 0) + 1, _int(cfg, "MAX_RESUME_COUNT", 10)
        if count > mx:
            await self._fail(task, f"MAX_RESUME_COUNT {mx} exceeded", "", sid, now)
            return
        window = next((r["windowCd"] for r in rows if r["status"] == "EXHAUSTED"), "DAILY")
        tools = {t["toolCd"]: t for t in await self.query.get_tools() or []}
        try:
            resume_at = resolve_resume_at(
                ev.resets_at, tools.get(cd, {}).get("fallbackResetRule") or "INTERVAL 30m", now
            )
        except ValueError:
            resume_at = now + timedelta(minutes=30)
        policy, cands = effective_policy(task, cfg)
        fields = {"resumeCount": count, "pauseReason": window}
        if policy == "SWITCH_TOOL":
            quotas = await self.query.get_quotas() or []
            grace = _int(cfg, "RESUME_GRACE_SEC", 120)
            others = [
                c
                for c in cands
                if c != cd
                and tools.get(c, {}).get("enabled")
                and (c != "FAKE" or self.settings.fake_enabled)
            ]
            free = next((c for c in others if self._free(c, quotas, now, grace)), None)
            if free:
                t = await self.query.transition(
                    tid, "RUNNING", "QUEUED", toolCd=free, cliSessionId=None, **fields
                )
                if t:
                    await self._notify(
                        "PAUSED",
                        f"전환: {_label(t)}",
                        f"{_label(t)}\n{cd} 한도 소진({_WINDOW_KO.get(window, window)}) → "
                        f"{free}로 전환",
                    )
                    self.wake()
                return
            # nothing free: wait for whichever candidate (current tool included) resets first
            options = [(resume_at, cd)] + [
                (from_query_ts(row["resetsAt"]), c)
                for c in others
                if (row := _blocking(c, quotas, now, grace))
            ]
            resume_at, chosen = min(options)
            if chosen != cd:
                fields.update(toolCd=chosen)
                sid = None
        t = await self.query.transition(
            tid, "RUNNING", "PAUSED", resumeAt=to_query_ts(resume_at), cliSessionId=sid, **fields
        )
        if t:
            await self._notify(
                "PAUSED", f"중단: {_label(t)}", format_paused(t, resume_at), _buttons(tid)
            )
