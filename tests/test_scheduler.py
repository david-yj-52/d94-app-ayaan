import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import commit, git
from fakes import (
    FakeClock,
    FakeEcc,
    InMemoryQuery,
    fail_result,
    fake_runner,
    limit_result,
    ok_result,
)

from ayaan.adapters import ADAPTERS
from ayaan.clients import to_query_ts
from ayaan.rules import workspace_preamble
from ayaan.runner import write_spool
from ayaan.scheduler import RESUME_PROMPT, SWITCH_NOTE, Scheduler, format_report
from ayaan.settings import Settings

NOW = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)  # 12:00 KST


@pytest.fixture
def settings(tmp_path, origin):
    exes = ",".join(f"{t}=/opt/cli/{t}" for t in ("claude", "codex", "gemini", "cursor"))
    return Settings(
        _env_file=None,
        data_dir=str(tmp_path),
        profile="test",
        cli_executables=exes,
        workspace_roots=str(tmp_path / "root"),
    )


async def _until(cond, timeout=10.0):
    """Waits for a condition; runs now prepare real git worktrees before the runner."""
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


def _setup(script, settings, ecc=None, gate=None, act=None):
    """Project P maps to one real repo r1 (conftest `origin`); tasks live under <root>/.tasks."""
    clock = FakeClock(NOW)
    q, ecc = InMemoryQuery.seeded(clock), ecc or FakeEcc()
    root = settings.workspace_root_paths[0]
    origin = Path(settings.data_dir) / "origin.git"  # conftest `origin` lives in tmp_path
    q.workspaces["P"] = {
        "projectKey": "P",
        "repos": [{"repoNm": "r1", "gitUrl": str(origin), "baseBranch": "main", "sortOrd": 1}],
    }
    q.task_root = str(root / ".tasks")
    runner = fake_runner(script, gate=gate, act=act)
    return q, ecc, clock, runner, Scheduler(q, ecc, settings, clock=clock, runner=runner)


async def test_limit_then_auto_resume_same_session(settings):
    q, ecc, clock, runner, s = _setup(
        [
            limit_result(session="s1", resets_at=NOW + timedelta(hours=1)),
            ok_result(session="s1", result_line="AYAAN_RESULT: done"),
        ],
        settings,
    )
    tid = q.add_task(tool="CLAUDE", status="QUEUED")
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "PAUSED" and q.task(tid)["cliSessionId"] == "s1"
    assert q.task(tid)["resumeAt"] == to_query_ts(NOW + timedelta(hours=1))
    assert ecc.events() == ["PAUSED"]
    clock.advance(3600 + 120 + 1)
    await s.tick()
    await s.drain()
    assert runner.calls[1].argv_tail == ADAPTERS["CLAUDE"].resume_argv("s1", None)
    assert runner.calls[1].prompt == RESUME_PROMPT
    assert q.task(tid)["status"] == "DONE" and q.task(tid)["resultSummary"] == "done"
    assert ecc.events() == ["PAUSED", "RESUMED", "DONE"]
    assert str(runner.calls[1].log_path).endswith(f"logs/{tid}/2.log")


async def test_switch_tool_policy_moves_to_next_available(settings):
    q, ecc, clock, runner, s = _setup([limit_result("s1", NOW + timedelta(hours=1))], settings)
    q.tools["CODEX"]["enabled"] = True
    tid = q.add_task(tool="CLAUDE", exhaustPolicy="SWITCH_TOOL", fallbackTools="CODEX")
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert (t["status"], t["toolCd"], t["cliSessionId"]) == ("QUEUED", "CODEX", None)
    # the next run on CODEX starts a new session with the hand-over note
    runner2 = fake_runner([ok_result("c1")])
    s.runner = runner2
    await s.tick()
    await s.drain()
    assert runner2.calls[0].argv_tail == ADAPTERS["CODEX"].new_argv(None)
    pre = workspace_preamble(q.workspaces["P"]["repos"], tid)
    assert runner2.calls[0].prompt == pre + "do it" + SWITCH_NOTE
    assert q.task(tid)["status"] == "DONE"


async def test_switch_tool_none_available_pauses_until_earliest_reset(settings):
    q, ecc, clock, runner, s = _setup([limit_result("s1", NOW + timedelta(hours=3))], settings)
    q.tools["CODEX"]["enabled"] = True
    codex_reset = NOW + timedelta(hours=1)
    await q.put_quota(
        "CODEX", "FIVE_HOUR", {"status": "EXHAUSTED", "resetsAt": to_query_ts(codex_reset)}
    )
    tid = q.add_task(tool="CLAUDE", exhaustPolicy="SWITCH_TOOL", fallbackTools="CODEX")
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert t["status"] == "PAUSED"
    assert t["resumeAt"] == to_query_ts(codex_reset)
    assert (t["toolCd"], t["cliSessionId"]) == ("CODEX", None)
    assert ecc.events() == ["PAUSED"]


async def test_max_resume_count_fails(settings):
    q, ecc, clock, runner, s = _setup(
        [limit_result("s1", NOW + timedelta(hours=1)), limit_result("s1", None)], settings
    )
    q.config["MAX_RESUME_COUNT"] = "1"
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "PAUSED"
    clock.advance(3600 + 121)
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "FAILED"
    assert "MAX_RESUME_COUNT" in q.task(tid)["failReason"]
    assert ecc.events() == ["PAUSED", "RESUMED", "FAILED"]


async def test_failed_run_not_retried(settings):
    q, ecc, clock, runner, s = _setup([fail_result("line\n" * 20 + "boom")], settings)
    tid = q.add_task(tool="CLAUDE")
    for _ in range(3):
        await s.tick()
        await s.drain()
        clock.advance(600)
    assert len(runner.calls) == 1
    t = q.task(tid)
    assert t["status"] == "FAILED" and t["failReason"].startswith("FAILED")
    event, _, body, buttons = ecc.sent[-1]
    assert event == "FAILED" and body.count("line") <= 10 and "boom" in body
    assert buttons == [{"label": "재시도", "action": "resume", "taskId": tid}]


async def test_exhausted_tool_skipped_until_grace(settings):
    q, ecc, clock, runner, s = _setup([ok_result("s1")], settings)
    await q.put_quota(
        "CLAUDE",
        "FIVE_HOUR",
        {"status": "EXHAUSTED", "resetsAt": to_query_ts(NOW + timedelta(seconds=60))},
    )
    q.add_task(tool="CLAUDE")
    await s.tick()
    clock.advance(60 + 119)  # resetsAt + grace > now: still skipped
    await s.tick()
    await s.drain()
    assert runner.calls == []
    clock.advance(1)
    await s.tick()
    await s.drain()
    assert len(runner.calls) == 1


async def test_concurrency_respects_max(settings):
    gate = asyncio.Event()
    q, ecc, clock, runner, s = _setup([ok_result("a"), ok_result("b")], settings, gate=gate)
    a, b = q.add_task(tool="CLAUDE"), q.add_task(tool="CLAUDE")
    await s.tick()
    await _until(lambda: runner.calls)
    await s.tick()
    await asyncio.sleep(0.01)
    assert len(runner.calls) == 1 and q.task(b)["status"] == "QUEUED"
    gate.set()
    await s.drain()
    await s.tick()
    await s.drain()
    assert len(runner.calls) == 2
    assert q.task(a)["status"] == q.task(b)["status"] == "DONE"


async def test_recover_expired_lease_resumes_same_session(settings):
    q, ecc, clock, runner, s = _setup([ok_result("s9")], settings)
    tid = q.add_task(
        tool="CLAUDE",
        status="RUNNING",
        cliSessionId="s9",
        leaseOwner="old:1",
        leaseUntil=to_query_ts(NOW - timedelta(seconds=10)),
    )
    await s.boot()
    assert q.task(tid)["pauseReason"] == "RECOVERED"
    await s.tick()
    await s.drain()
    assert runner.calls[0].argv_tail == ADAPTERS["CLAUDE"].resume_argv("s9", None)
    assert q.task(tid)["status"] == "DONE"


async def test_spool_replayed_on_boot(settings, tmp_path):
    q, ecc, clock, runner, s = _setup([ok_result("s1")], settings)
    q.fail_finish = 1
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "DONE"  # finish_run failure does not block the transition
    spooled = list((tmp_path / "spool").glob("*.json"))
    assert len(spooled) == 1
    await s.boot()
    assert list((tmp_path / "spool").glob("*.json")) == []
    assert q.runs["r1"]["outcome"] == "COMPLETED" and q.runs["r1"]["cliSessionId"] == "s1"


async def test_spool_replay_keeps_file_on_failure(settings, tmp_path):
    q, ecc, clock, runner, s = _setup([], settings)
    await q.start_run("t0", 1, "CLAUDE")
    write_spool(tmp_path, "r1", {"outcome": "FAILED"})
    q.fail_finish = 1
    await s.boot()
    assert len(list((tmp_path / "spool").glob("*.json"))) == 1


async def test_config_change_applies_next_tick(settings):
    q, ecc, clock, runner, s = _setup(
        [limit_result("s1", NOW + timedelta(hours=1)), limit_result("s1", None)], settings
    )
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "PAUSED"
    q.config.update(EXHAUST_POLICY="SWITCH_TOOL", FALLBACK_TOOL_ORDER="CODEX")
    q.tools["CODEX"]["enabled"] = True
    clock.advance(3600 + 121)
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert (t["status"], t["toolCd"]) == ("QUEUED", "CODEX")


async def test_notify_failure_does_not_block(settings):
    q, ecc, clock, runner, s = _setup(
        [limit_result("s1", NOW + timedelta(hours=1))], settings, ecc=FakeEcc(fail=True)
    )
    await q.put_quota("CLAUDE", "DAILY", {"status": "AVAILABLE"})  # forces QUOTA_EXHAUSTED notify
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "PAUSED"
    assert q.quotas[("CLAUDE", "DAILY")]["status"] == "EXHAUSTED"
    assert "PAUSED" in ecc.events()


async def test_notify_events_filter(settings):
    q, ecc, clock, runner, s = _setup([ok_result("s1")], settings)
    q.config["NOTIFY_EVENTS"] = "PAUSED,FAILED"
    q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert ecc.events() == []


async def test_session_missing_retries_new_session_once(settings):
    q, ecc, clock, runner, s = _setup(
        [fail_result("No conversation found", session_missing=True), ok_result("s2")], settings
    )
    tid = q.add_task(
        tool="CLAUDE",
        status="PAUSED",
        cliSessionId="gone",
        resumeAt=to_query_ts(NOW - timedelta(hours=1)),
    )
    await s.tick()
    await s.drain()
    assert runner.calls[0].argv_tail == ADAPTERS["CLAUDE"].resume_argv("gone", None)
    assert runner.calls[1].argv_tail == ADAPTERS["CLAUDE"].new_argv(None)
    pre = workspace_preamble(q.workspaces["P"]["repos"], tid)
    assert runner.calls[1].prompt == pre + "do it" + SWITCH_NOTE
    t = q.task(tid)
    assert (t["status"], t["cliSessionId"]) == ("DONE", "s2")


async def test_cancelled_outcome_only_finishes_run(settings):
    from ayaan.adapters import RunEvent
    from ayaan.runner import RunResult

    q, ecc, clock, runner, s = _setup([RunResult("CANCELLED", RunEvent(), -15, "")], settings)
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    q.tasks[tid]["status"] = "CANCELLED"  # the API moved it while running
    await s.drain()
    assert q.task(tid)["status"] == "CANCELLED"
    assert q.runs["r1"]["outcome"] == "CANCELLED" and ecc.events() == []


async def test_api_key_mode_env_and_budget(settings, monkeypatch):
    monkeypatch.setenv("AYAAN_ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AYAAN_API_TOKEN", "secret")
    q, ecc, clock, runner, s = _setup([ok_result("s1")], settings)
    q.tools["CLAUDE"].update(
        authMode="API_KEY",
        apiKeyRef="AYAAN_ANTHROPIC_API_KEY",
        budgetTokens=100,
        budgetPeriod="DAILY",
    )
    q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert runner.calls[0].env["ANTHROPIC_API_KEY"] == "sk-test"
    assert not [k for k in runner.calls[0].env if k.startswith("AYAAN_")]
    b = q.quotas[("CLAUDE", "BUDGET")]
    assert b["usedTokens"] == 110 and b["status"] == "EXHAUSTED"


async def test_pause_if_exhausted(settings):
    q, ecc, clock, runner, s = _setup([], settings)
    reset = NOW + timedelta(hours=2)
    await q.put_quota("CLAUDE", "WEEKLY", {"status": "EXHAUSTED", "resetsAt": to_query_ts(reset)})
    tid = q.add_task(tool="CLAUDE")
    t = await s.pause_if_exhausted(q.task(tid))
    assert t["status"] == "PAUSED" and t["resumeAt"] == to_query_ts(reset)
    assert t["pauseReason"] == "WEEKLY" and ecc.events() == ["PAUSED"]


async def test_report_message_contains_quota_and_paused(settings):
    quotas = [
        {
            "toolCd": "CLAUDE",
            "windowCd": "FIVE_HOUR",
            "usedPct": 87.5,
            "status": "AVAILABLE",
            "resetsAt": "2026-11-11T05:00:00",
            "source": "USAGE_API",
            "observedAt": "2026-11-11T02:55:00",
        }
    ]
    paused = [
        {
            "id": "t1",
            "issueKey": "CIRA-7",
            "toolCd": "CLAUDE",
            "prompt": "x",
            "resumeAt": "2026-11-11T06:30:00",
            "pauseReason": "FIVE_HOUR",
        }
    ]
    msg = format_report(quotas, {"RUNNING": 1, "QUEUED": 2, "PAUSED": 1}, paused)
    for part in (
        "CLAUDE",
        "5시간",
        "87.5%",
        "11-11 14:00",
        "USAGE_API",
        "11-11 11:55",
        "실행 1",
        "대기 2",
        "중단 1",
        "CIRA-7",
        "11-11 15:30",
    ):
        assert part in msg, part

    q, ecc, clock, runner, s = _setup([], settings)
    await s.send_report()
    assert ecc.events() == ["REPORT"]


async def test_start_stop_runs_tick_and_survives_errors(settings):
    q, ecc, clock, runner, s = _setup([ok_result("s1")], settings)
    q.add_task(tool="CLAUDE")
    calls = {"n": 0}
    real = q.get_quotas

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("blip")
        return await real()

    q.get_quotas = flaky
    await s.start()
    await asyncio.sleep(0.01)
    s.wake()  # first tick failed; wake triggers another
    await asyncio.sleep(0.05)
    await s.drain()
    await s.stop()
    assert len(runner.calls) == 1


async def test_recover_does_not_steal_own_live_task(settings):
    gate = asyncio.Event()
    q, ecc, clock, runner, s = _setup([ok_result("a"), ok_result("x")], settings, gate=gate)
    q.tools["CLAUDE"]["maxConcurrency"] = 2
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await _until(lambda: runner.calls)
    q.tasks[tid]["leaseUntil"] = to_query_ts(NOW - timedelta(seconds=1))  # query was down
    await s.tick()
    await asyncio.sleep(0.01)
    assert len(runner.calls) == 1
    t = q.task(tid)
    assert (t["status"], t["leaseOwner"]) == ("RUNNING", s.owner)
    gate.set()
    await s.drain()
    assert q.task(tid)["status"] == "DONE"


async def test_settle_retries_through_query_outage(settings):
    q, ecc, clock, runner, s = _setup([ok_result("s7")], settings)
    slept = []

    async def no_sleep(sec):
        slept.append(sec)

    s.sleep = no_sleep
    real, fails = q.transition, {"n": 2}

    async def flaky(*a, **kw):
        if fails["n"]:
            fails["n"] -= 1
            raise httpx.ConnectError("down")
        return await real(*a, **kw)

    q.transition = flaky
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert (t["status"], t["cliSessionId"]) == ("DONE", "s7")
    assert slept == [5, 15]


async def test_runner_exception_still_finishes_run(settings):
    q, ecc, clock, runner, s = _setup([], settings)  # empty script: runner raises IndexError
    tid = q.add_task(tool="CLAUDE")
    await s.tick()
    await s.drain()
    assert q.runs["r1"]["outcome"] == "FAILED"
    assert q.task(tid)["status"] == "FAILED" and "IndexError" in q.task(tid)["failReason"]


async def test_switch_skips_exhausted_without_reset_and_fake(settings):
    q, ecc, clock, runner, s = _setup([limit_result("s1", NOW + timedelta(hours=1))], settings)
    s.settings = settings.model_copy(update={"profile": ""})
    q.tools["CODEX"]["enabled"] = q.tools["FAKE"]["enabled"] = True
    await q.put_quota("CODEX", "WEEKLY", {"status": "EXHAUSTED", "resetsAt": None})
    tid = q.add_task(tool="CLAUDE", exhaustPolicy="SWITCH_TOOL", fallbackTools="CODEX,FAKE")
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert (t["status"], t["toolCd"]) == ("PAUSED", "CLAUDE")


async def test_unresolvable_executable_fails_task(settings):
    settings.cli_executables = "claude=relative/claude"
    q, ecc, clock, runner, s = _setup([ok_result("s1")], settings)
    tid = q.add_task()
    await s.tick()
    await s.drain()
    assert runner.calls == []
    assert q.task(tid)["status"] == "FAILED"
    assert "실행 파일을 찾을 수 없음" in q.task(tid)["failReason"]


def test_cli_env_drops_ayaan_vars_but_fake_keeps_scenario(settings, monkeypatch):
    monkeypatch.setenv("AYAAN_JWT_SECRET_KEY", "k")
    monkeypatch.setenv("AYAAN_FAKE_SCENARIO_FILE", "/data/fake-scenario")
    s = _setup([], settings)[-1]
    assert not [k for k in s._env({"toolCd": "CLAUDE"}) if k.startswith("AYAAN_")]
    fake = s._env({"toolCd": "FAKE"})
    assert fake["AYAAN_FAKE_SCENARIO_FILE"] == "/data/fake-scenario"
    assert "AYAAN_JWT_SECRET_KEY" not in fake


# ── worktree execution ──


def _ws_setup(script, settings, act=None, **task):
    q, ecc, clock, runner, s = _setup(script, settings, act=act)
    tid = q.add_task(
        tool="CLAUDE", issueKey="TASKS-1", workspacePath=f"{q.task_root}/TASKS-1", **task
    )
    return q, ecc, runner, s, tid


def _commit_in_r1(cwd):
    commit(Path(cwd) / "r1", "x.txt", "x\n")


async def test_execute_prepares_worktrees_then_runs(settings):
    seen = []

    def act(cwd):
        seen.append((Path(cwd) / "r1").is_dir())

    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings, act=act)
    await s.tick()
    await s.drain()
    assert seen == [True]
    assert runner.calls[0].cwd == settings.workspace_root_paths[0] / ".tasks" / "TASKS-1"
    assert git(runner.calls[0].cwd / "r1", "branch", "--show-current") == "ayaan/TASKS-1"


async def test_prompt_has_preamble(settings, tmp_path, origin):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings)
    await s.tick()
    await s.drain()
    p = runner.calls[0].prompt
    assert p.startswith("[작업 환경]") and "- r1/\n" in p and p.endswith("do it")


async def test_resume_prompt_has_no_preamble(settings, tmp_path, origin):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings, cliSessionId="s0")
    await s.tick()
    await s.drain()
    assert runner.calls[0].prompt == RESUME_PROMPT


async def test_standalone_done_pushes_branch(settings, tmp_path, origin):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings, act=_commit_in_r1)
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "DONE"
    assert git(origin, "branch", "--list", "ayaan/TASKS-1") != ""


async def test_standalone_without_commits_does_not_push(settings, tmp_path, origin):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings)
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "DONE"
    assert git(origin, "branch", "--list", "ayaan/TASKS-1") == ""


async def test_plan_task_starts_from_plan_branch_and_does_not_push(settings, origin, seed):
    git(seed, "checkout", "-q", "-b", "ayaan/plan-M-1")
    commit(seed, "plan.txt", "p\n")
    git(seed, "push", "-q", "origin", "ayaan/plan-M-1")
    seen = []

    def act(cwd):
        seen.append((Path(cwd) / "r1" / "plan.txt").exists())
        _commit_in_r1(cwd)

    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings, act=act)
    q.task(tid)["planId"] = q.add_plan(milestoneKey="M-1")
    await s.tick()
    await s.drain()
    assert seen == [True] and q.task(tid)["status"] == "DONE"
    assert git(origin, "branch", "--list", "ayaan/TASKS-1") == ""  # Task 5 pushes plan tasks


async def test_done_with_dirty_worktree_fails(settings, tmp_path, origin):
    def act(cwd):
        (Path(cwd) / "r1" / "loose.txt").write_text("x\n")

    q, ecc, runner, s, tid = _ws_setup([ok_result("s1")], settings, act=act)
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert t["status"] == "FAILED" and "uncommitted changes in r1" in t["failReason"]
    assert git(origin, "branch", "--list", "ayaan/TASKS-1") == ""


async def test_prepare_failure_fails_task(settings, tmp_path, origin):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings)
    q.workspaces["P"]["repos"][0]["gitUrl"] = str(tmp_path / "missing.git")
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert t["status"] == "FAILED" and t["failReason"].startswith("prepare: ")
    assert runner.calls == []


async def test_workspace_without_repos_fails_prepare(settings):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings)
    q.workspaces["P"]["repos"] = []
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert t["status"] == "FAILED" and t["failReason"] == "prepare: workspace has no repos"
    assert runner.calls == []


async def test_plan_without_milestone_key_fails_prepare(settings):
    q, _, runner, s, tid = _ws_setup([ok_result("s1")], settings)
    q.task(tid)["planId"] = q.add_plan(milestoneKey=None)
    await s.tick()
    await s.drain()
    t = q.task(tid)
    assert t["status"] == "FAILED" and t["failReason"] == "prepare: plan pl1 has no milestoneKey"
    assert runner.calls == []


async def test_legacy_workspace_path_runs_in_place_without_worktrees(settings, tmp_path):
    """A task created before plan pipeline points at the old shared clone: keep old behaviour."""
    legacy = tmp_path / "workspace" / "d94-query"
    legacy.mkdir(parents=True)
    q, _, clock, runner, s = _setup([ok_result("s1")], settings)
    tid = q.add_task(tool="CLAUDE", issueKey="TASKS-1", workspacePath=str(legacy))
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "DONE"
    assert runner.calls[0].cwd == legacy and runner.calls[0].prompt == "do it"
    assert not (settings.workspace_root_paths[0] / ".tasks").exists()
