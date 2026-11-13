"""Human-in-the-loop flows: ask → WAITING → answer → resume, permission hook, plan → dev."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeClock, FakeEcc, InMemoryQuery, fake_runner, limit_result, ok_result
from fastapi.testclient import TestClient

from ayaan.api import create_app
from ayaan.rules import input_hash, parse_ask
from ayaan.scheduler import RESUME_PROMPT, Scheduler
from ayaan.settings import Settings

NOW = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)
P = "/tsh/api/v1/ayaan"
TOKEN = {"X-AYAAN-Token": "svc"}
PLUGINS = "ponytail@ponytail superpowers@superpowers-marketplace"


def ask_result(session):
    r = ok_result(session, "")
    r.event.result_line = None
    r.event.ask = parse_ask('AYAAN_ASK: {"question":"A or B?","options":["A","B"]}')
    return r


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None,
        api_token="svc",
        data_dir=str(tmp_path / "data"),
        workspace_roots=str(tmp_path),
        profile="test",
        cli_executables="claude=/opt/cli/claude",
    )
    clock = FakeClock(NOW)
    q, ecc = InMemoryQuery.seeded(clock), FakeEcc()
    s = Scheduler(q, ecc, settings, clock=clock, runner=fake_runner([]))
    s.plugin_list = _const(PLUGINS)
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    q.workspaces["P"] = {"projectKey": "P", "workspacePath": str(ws), "defaultTool": "CLAUDE"}
    app = create_app(settings, q, ecc, s, start_background=False)
    with TestClient(app, client=("127.0.0.1", 5000)) as c:
        yield c, q, ecc, s, ws


def _const(value):
    async def f(*_):
        f.calls += 1
        return value

    f.calls = 0
    return f


async def _run(s, *results):
    s.runner = fake_runner(list(results))
    await s.tick()
    await s.drain()
    return s.runner


async def test_ask_waits_answer_resumes_same_session(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws))
    await _run(s, ask_result("s1"))
    t = q.task(tid)
    assert (t["status"], t["cliSessionId"]) == ("WAITING", "s1")
    (req,) = q.requests.values()
    assert (req["kind"], req["status"], req["options"]) == ("QUESTION", "OPEN", ["A", "B"])
    event, _, body, buttons = ecc.sent[-1]
    assert event == "WAITING" and f"요청 ID: {req['id']}" in body
    assert buttons[1] == {"label": "B", "action": "answer", "requestId": req["id"], "choice": 1}
    # WAITING is never claimed, however long it waits
    s.clock.advance(86400 * 30)
    assert (await _run(s)).calls == []
    # scheduler restart: nothing lives in memory while waiting
    s2 = Scheduler(q, ecc, s.settings, clock=s.clock, runner=fake_runner([]))
    s2.plugin_list = _const(PLUGINS)
    r = c.post(
        f"{P}/requests/{req['id']}/answer", json={"choice": 1, "answeredBy": "tg:7"}, headers=TOKEN
    )
    assert r.status_code == 200 and q.task(tid)["status"] == "QUEUED"
    assert q.requests[req["id"]]["answeredBy"] == "tg:7"
    # second press of the same (or another) button: CAS lets only one through
    r2 = c.post(f"{P}/requests/{req['id']}/answer", json={"choice": 0}, headers=TOKEN)
    assert r2.status_code == 409 and r2.json()["data"]["answer"] == "B"
    runner = await _run(s2, ok_result("s1"))
    assert runner.calls[0].prompt == "사용자 답변 (질문: A or B?): B\n이어서 진행해."
    assert "--resume" in runner.calls[0].argv_tail
    assert q.requests[req["id"]]["status"] == "CONSUMED"
    assert q.task(tid)["status"] == "DONE" and q.task(tid)["resumeCount"] == 0


async def test_answer_validation(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws), status="WAITING")
    _, req = await q.create_request(tid, {"kind": "PERMISSION", "options": ["허용 1회", "거부"]})
    url = f"{P}/requests/{req['id']}/answer"
    assert c.post(url, json={"choice": 2}, headers=TOKEN).status_code == 422
    assert c.post(url, json={"answer": "ok"}, headers=TOKEN).status_code == 422  # not a QUESTION
    assert c.post(f"{P}/requests/nope/answer", json={"choice": 0}, headers=TOKEN).status_code == 404
    assert c.post(f"{P}/tasks/{tid}/resume", headers=TOKEN).status_code == 409
    assert c.post(url, json={"choice": 1}, headers=TOKEN).json()["data"]["decision"] == "DENY"
    assert q.task(tid)["status"] == "QUEUED"


async def test_cancel_waiting_closes_open_request(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws), status="WAITING")
    _, req = await q.create_request(tid, {"kind": "QUESTION"})
    assert c.post(f"{P}/tasks/{tid}/cancel", headers=TOKEN).status_code == 200
    assert q.task(tid)["status"] == "CANCELLED" and q.requests[req["id"]]["status"] == "CANCELLED"


async def test_hook_endpoint_guards(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws), status="RUNNING")
    s.hook_tokens["tok"] = (tid, "r1")
    body = {"toolName": "Bash", "input": {"command": "ls"}}
    assert c.post(f"{P}/hook/pre-tool", json=body).status_code == 401
    assert (
        c.post(f"{P}/hook/pre-tool", json=body, headers={"X-Hook-Token": "bad"}).status_code == 401
    )
    with TestClient(c.app) as remote:  # default client host is not localhost
        r = remote.post(f"{P}/hook/pre-tool", json=body, headers={"X-Hook-Token": "tok"})
        assert r.status_code == 403
    # the hook token never answers requests
    _, req = await q.create_request(tid, {"kind": "QUESTION"})
    r = c.post(
        f"{P}/requests/{req['id']}/answer", json={"answer": "x"}, headers={"X-Hook-Token": "tok"}
    )
    assert r.status_code == 401


async def test_permission_ask_wait_approve_once(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws))
    push = {"toolName": "Bash", "input": {"command": "git push origin main"}}
    hdr = {}

    async def runner(**kw):  # the CLI hits the hook mid-run, like ayaan-hook does
        runner.calls.append(kw)
        hdr["X-Hook-Token"] = kw["env"]["AYN_HOOK_TOKEN"]
        runner.replies.append(c.post(f"{P}/hook/pre-tool", json=runner.body, headers=hdr).json())
        return ok_result("s1", "AYAAN_RESULT: pushed" if len(runner.calls) > 1 else "")

    runner.calls, runner.replies, runner.body = [], [], push
    s.runner = runner
    await s.tick()
    await s.drain()
    assert runner.replies[0]["data"]["decision"] == "deny"
    assert "승인 대기" in runner.replies[0]["data"]["reason"]
    (req,) = q.requests.values()
    assert (req["kind"], req["inputHash"]) == ("PERMISSION", input_hash("Bash", push["input"]))
    assert q.task(tid)["status"] == "WAITING"  # the open request wins over COMPLETED
    assert ecc.sent[-1][0] == "WAITING" and "git push origin main" in ecc.sent[-1][2]
    assert s.hook_tokens == {}  # token dies with the run
    c.post(f"{P}/requests/{req['id']}/answer", json={"choice": 0}, headers=TOKEN)
    await s.tick()
    await s.drain()
    assert (
        runner.calls[1]["prompt"]
        == "승인됨: `Bash: git push origin main`을 다시 실행해 이어서 진행해."
    )
    assert runner.replies[1]["data"] == {"decision": "allow", "reason": "ayaan 승인됨"}
    assert q.requests[req["id"]]["status"] == "CONSUMED"
    assert q.task(tid)["status"] == "DONE"
    # the approval is spent: the same call asks again
    s.hook_tokens["t2"] = (tid, "r9")
    r = c.post(f"{P}/hook/pre-tool", json=push, headers={"X-Hook-Token": "t2"}).json()
    assert r["data"]["decision"] == "deny"
    r = c.post(
        f"{P}/hook/pre-tool",
        json={"toolName": "Write", "input": {"file_path": f"{ws}/.env"}},
        headers={"X-Hook-Token": "t2"},
    ).json()
    assert r["data"]["decision"] == "deny" and "DENY" in r["data"]["reason"]


async def test_limit_with_open_request_returns_to_waiting(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws))
    s.runner = fake_runner([limit_result("s1", NOW + timedelta(minutes=5))])

    async def hooked(**kw):
        await q.create_request(tid, {"kind": "PERMISSION", "runId": "x"})
        return await fake_runner([limit_result("s1", NOW + timedelta(minutes=5))])(**kw)

    s.runner = hooked
    await s.tick()
    await s.drain()
    assert q.task(tid)["status"] == "PAUSED"
    s.clock.advance(600)
    runner = await _run(s)
    assert runner.calls == [] and q.task(tid)["status"] == "WAITING"


async def test_plan_done_creates_waiting_dev_task_then_start(env):
    c, q, ecc, s, ws = env
    (ws / "docs/plan.md").write_text("plan")
    r = c.post(f"{P}/tasks", json={"prompt": "기능 X", "projectKey": "P"}, headers=TOKEN)
    plan_id = r.json()["data"]["id"]
    assert q.task(plan_id)["phase"] == "PLAN"
    runner = await _run(s, ok_result("s1", "AYAAN_RESULT: docs/plan.md 설계 완료"))
    assert runner.calls[0].prompt.startswith("/superpowers:brainstorming\n\n기능 X")
    plan = q.task(plan_id)
    assert (plan["status"], plan["planDocPath"]) == ("DONE", "docs/plan.md")
    (dev,) = [t for t in q.tasks.values() if t["parentTaskId"] == plan_id]
    assert (dev["status"], dev["phase"]) == ("WAITING", "DEV")
    assert dev["prompt"].startswith("docs/plan.md 기획서대로 구현")
    (start,) = [r for r in q.requests.values() if r["taskId"] == dev["id"]]
    assert start["kind"] == "START" and ecc.events()[-1] == "PLAN_DONE"
    assert [b["label"] for b in ecc.sent[-1][3]] == ["실행", "취소"]
    # a second dev task from the web button is refused
    assert c.post(f"{P}/tasks/{plan_id}/dev-task", headers=TOKEN).status_code == 409
    c.post(f"{P}/requests/{start['id']}/answer", json={"choice": 0}, headers=TOKEN)
    runner = await _run(s, ok_result("s2"))
    assert runner.calls[0].prompt.startswith("/ponytail:ponytail\n\ndocs/plan.md 기획서대로")
    assert q.requests[start["id"]]["status"] == "CONSUMED"
    assert q.task(dev["id"])["status"] == "DONE"


async def test_plan_without_valid_path_and_start_cancel(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws), phase="PLAN")
    await _run(s, ok_result("s1", "AYAAN_RESULT: ../outside.md"))
    assert q.task(tid)["planDocPath"] is None and "기획서 경로 확인 실패" in ecc.sent[-1][2]
    (dev,) = [t for t in q.tasks.values() if t["parentTaskId"] == tid]
    assert '기획 작업 결과("../outside.md")' in dev["prompt"]
    (start,) = q.requests.values()
    c.post(f"{P}/requests/{start['id']}/answer", json={"choice": 1}, headers=TOKEN)
    assert q.task(dev["id"])["status"] == "CANCELLED"


async def test_dev_task_creation_failure_is_notified_and_retryable(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws), phase="PLAN", issueKey="CIRA-9")
    q.add_task(workspacePath="/other", issueKey="CIRA-9", status="DONE")
    real = q.create_task

    async def broken(body):
        raise RuntimeError("query down")

    q.create_task = broken
    await _run(s, ok_result("s1"))
    assert q.task(tid)["status"] == "DONE"
    assert ecc.events()[-1] == "FAILED" and "개발 작업 만들기" in ecc.sent[-1][2]
    q.create_task = real
    r = c.post(f"{P}/tasks/{tid}/dev-task", headers=TOKEN)
    assert r.status_code == 201 and r.json()["data"]["status"] == "WAITING"


async def test_missing_skill_plugin_syncs_once_then_fails(env):
    c, q, ecc, s, ws = env
    tid = q.add_task(workspacePath=str(ws))
    s.plugin_list, s.plugin_sync = _const("superpowers@superpowers-marketplace"), _const([])
    runner = await _run(s)
    assert runner.calls == [] and s.plugin_sync.calls == 1
    t = q.task(tid)
    assert t["status"] == "FAILED" and t["failReason"] == "SKILL_PLUGIN_MISSING: ponytail@ponytail"
    assert ecc.events()[-1] == "FAILED"
    # installed by the sync: the next task runs, and the list is cached
    s.plugin_list = _const(PLUGINS)
    s._plugin_cache = None
    q.add_task(workspacePath=str(ws) + "2")
    q.add_task(workspacePath=str(ws) + "3")
    s.runner = fake_runner([ok_result("a"), ok_result("b")])
    for _ in range(2):
        await s.tick()
        await s.drain()
    assert len(s.runner.calls) == 2 and s.plugin_list.calls == 1


async def test_explicit_no_skill_and_resume_prompt_default(env):
    c, q, ecc, s, ws = env
    q.add_task(workspacePath=str(ws), skillCd="NONE")
    runner = await _run(s, ok_result("s1"))
    assert runner.calls[0].prompt == "do it"
    assert RESUME_PROMPT == await s._resume_prompt([])


def test_create_task_validates_phase_and_skill(env):
    c = env[0]
    base = {"prompt": "x", "projectKey": "P"}
    assert c.post(f"{P}/tasks", json={**base, "phase": "X"}, headers=TOKEN).status_code == 400
    assert c.post(f"{P}/tasks", json={**base, "skillCd": "NOPE"}, headers=TOKEN).status_code == 400
    r = c.post(f"{P}/tasks", json={**base, "phase": "DEV", "skillCd": "NONE"}, headers=TOKEN)
    assert r.status_code == 201 and r.json()["data"]["skillCd"] == "NONE"


def test_skill_permission_tool_reference_api(env):
    c, q = env[0], env[1]
    body = {
        "skillNm": "n",
        "toolCd": "CLAUDE",
        "phase": "DEV",
        "invokeText": "/x",
        "pluginRef": "a@b",
    }
    r = c.put(f"{P}/skills/MINE", json=body, headers=TOKEN)
    assert r.status_code == 200 and r.json()["data"]["warnings"] == ["CLAUDE_PLUGINS에 없음"]
    assert (
        c.put(f"{P}/skills/MINE", json={**body, "toolCd": "GEMINI"}, headers=TOKEN).status_code
        == 422
    )
    assert c.put(f"{P}/tools/CLAUDE", json={"devSkillCd": "NOPE"}, headers=TOKEN).status_code == 422
    r = c.put(f"{P}/tools/CLAUDE", json={"devSkillCd": "MINE", "planSkillCd": ""}, headers=TOKEN)
    assert r.status_code == 200 and q.tools["CLAUDE"]["planSkillCd"] is None
    c.delete(f"{P}/skills/MINE", headers=TOKEN)
    assert q.tools["CLAUDE"]["devSkillCd"] is None
    rows = [{"id": "p0", "toolCd": "*", "category": "GIT_PUSH", "matcher": "*", "decision": "DENY"}]
    r = c.put(f"{P}/permissions", json=rows, headers=TOKEN)
    assert r.status_code == 200 and q.permissions[0]["decision"] == "DENY"
    assert c.put(f"{P}/permissions", json=[{"toolCd": "*"}], headers=TOKEN).status_code == 422
