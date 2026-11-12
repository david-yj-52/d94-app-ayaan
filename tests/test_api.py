import base64
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from fakes import FakeEcc, InMemoryQuery
from fastapi.testclient import TestClient

from ayaan import api
from ayaan.api import create_app
from ayaan.clients import to_query_ts
from ayaan.scheduler import Scheduler
from ayaan.settings import Settings

P = "/tsh/api/v1/ayaan"
SECRET = base64.b64encode(b"k" * 32).decode()
TOKEN = {"X-AYAAN-Token": "svc-token"}


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None,
        api_token="svc-token",
        jwt_secret_key=SECRET,
        jwt_allowed_subjects="u, admin@d94",
        data_dir=str(tmp_path / "data"),
        workspace_roots=str(tmp_path),
        query_base_url="http://127.0.0.1:1/tsh/api/v1/internal/ayaan",
        ecc_base_url="http://127.0.0.1:1/ecc",
        cli_executables="claude=/nonexistent/claude",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "ws").mkdir()
    q, ecc = InMemoryQuery.seeded(), FakeEcc()
    q.workspaces["CIRA"] = {
        "projectKey": "CIRA",
        "workspacePath": str(tmp_path / "ws"),
        "defaultTool": "CLAUDE",
        "promptTemplate": "{issueKey} {title}\n{content}",
    }
    q.issues["CIRA-1"] = {
        "issueKey": "CIRA-1",
        "title": "Fix",
        "content": "keep {x} literal",
        "projectKey": "CIRA",
    }
    s = Scheduler(q, ecc, settings)
    app = create_app(settings, q, ecc, s, start_background=False)
    with TestClient(app) as client:
        yield client, q, ecc, s, settings, tmp_path


def _jwt(exp_delta: int, key=b"k" * 32, sub="u") -> dict:
    tok = jwt.encode({"sub": sub, "exp": int(time.time()) + exp_delta}, key, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def test_requires_auth(env):
    c = env[0]
    assert c.get("/actuator/health").status_code == 200
    assert c.get(f"{P}/tasks").status_code == 401
    assert c.get(f"{P}/tasks", headers={"X-AYAAN-Token": "wrong"}).status_code == 401
    assert c.get(f"{P}/tasks", headers=_jwt(60, key=b"x" * 32)).status_code == 401
    assert c.get(f"{P}/tasks", headers=_jwt(-60)).status_code == 401
    assert c.get(f"{P}/tasks", headers=_jwt(60)).status_code == 200
    r = c.get(f"{P}/tasks", headers=TOKEN)
    assert r.status_code == 200 and r.json() == {"success": True, "data": []}


def test_empty_api_token_rejects_empty_header(env):
    c, settings = env[0], env[4]
    settings.api_token = ""
    assert c.get(f"{P}/tasks", headers={"X-AYAAN-Token": ""}).status_code == 401


def test_create_by_issue_key_renders_prompt(env):
    c, q = env[0], env[1]
    r = c.post(f"{P}/tasks", json={"issueKey": "CIRA-1", "source": "WEB"}, headers=TOKEN)
    assert r.status_code == 201
    t = r.json()["data"]
    assert t["prompt"] == "CIRA-1 Fix\nkeep {x} literal"
    assert t["status"] == "QUEUED" and t["toolCd"] == "CLAUDE" and t["id"] in q.tasks
    assert t["workspacePath"] == q.workspaces["CIRA"]["workspacePath"]


def test_create_duplicate_issue_409_returns_existing(env):
    c = env[0]
    first = c.post(f"{P}/tasks", json={"issueKey": "CIRA-1", "source": "WEB"}, headers=TOKEN)
    r = c.post(f"{P}/tasks", json={"issueKey": "CIRA-1", "source": "WEB"}, headers=TOKEN)
    assert r.status_code == 409
    assert r.json()["success"] is False and r.json()["data"]["id"] == first.json()["data"]["id"]


def test_create_errors(env):
    c, q = env[0], env[1]
    r = c.post(f"{P}/tasks", json={"issueKey": "NOPE-1", "source": "WEB"}, headers=TOKEN)
    assert r.status_code == 404 and r.json()["success"] is False
    q.issues["X-1"] = {"issueKey": "X-1", "title": "t", "content": "", "projectKey": "X"}
    assert c.post(f"{P}/tasks", json={"issueKey": "X-1"}, headers=TOKEN).status_code == 400
    body = {"issueKey": "CIRA-1", "tool": "CODEX", "source": "WEB"}  # CODEX disabled
    assert c.post(f"{P}/tasks", json=body, headers=TOKEN).status_code == 400


def test_create_on_exhausted_tool_is_paused(env):
    c, q, ecc = env[0], env[1], env[2]
    reset = to_query_ts(datetime.now(UTC) + timedelta(hours=1))
    q.quotas[("CLAUDE", "FIVE_HOUR")] = {
        "toolCd": "CLAUDE",
        "windowCd": "FIVE_HOUR",
        "status": "EXHAUSTED",
        "resetsAt": reset,
    }
    r = c.post(f"{P}/tasks", json={"issueKey": "CIRA-1", "source": "WEB"}, headers=TOKEN)
    assert r.status_code == 201
    t = r.json()["data"]
    assert t["status"] == "PAUSED" and t["pauseReason"] == "FIVE_HOUR" and t["resumeAt"] == reset
    assert "PAUSED" in ecc.events()


def test_task_without_default_project_400(env):
    c, q = env[0], env[1]
    r = c.post(f"{P}/tasks", json={"prompt": "do x", "source": "TELEGRAM"}, headers=TOKEN)
    assert r.status_code == 400 and r.json()["success"] is False
    q.config["DEFAULT_PROJECT_KEY"] = "CIRA"
    r = c.post(f"{P}/tasks", json={"prompt": "do x", "source": "TELEGRAM"}, headers=TOKEN)
    assert r.status_code == 201
    assert r.json()["data"]["prompt"] == "do x" and r.json()["data"]["projectKey"] == "CIRA"


def test_put_config_invalid_422_with_field_errors(env):
    c, q = env[0], env[1]
    r = c.put(f"{P}/config", json={"MAX_RESUME_COUNT": "0", "BOGUS": "1"}, headers=TOKEN)
    assert r.status_code == 422
    assert r.json()["success"] is False and set(r.json()["data"]) == {"MAX_RESUME_COUNT", "BOGUS"}
    assert q.config["MAX_RESUME_COUNT"] == "10"
    r = c.put(f"{P}/config", json={"MAX_RESUME_COUNT": "5"}, headers=TOKEN)
    assert r.status_code == 200 and r.json()["data"]["MAX_RESUME_COUNT"] == "5"


def test_put_tool_and_workspace_validation(env):
    c, q, tmp = env[0], env[1], env[5]
    r = c.put(f"{P}/tools/CLAUDE", json={"maxConcurrency": 9}, headers=TOKEN)
    assert r.status_code == 422 and "maxConcurrency" in r.json()["data"]
    r = c.put(f"{P}/tools/CLAUDE", json={"maxConcurrency": 2}, headers=TOKEN)
    assert r.status_code == 200 and q.tools["CLAUDE"]["maxConcurrency"] == 2
    bad = {"workspacePath": "/etc", "defaultTool": "CLAUDE"}
    r = c.put(f"{P}/workspaces/D94", json=bad, headers=TOKEN)
    assert r.status_code == 422 and "workspacePath" in r.json()["data"]
    ok = {"workspacePath": str(tmp / "ws"), "defaultTool": "CLAUDE", "promptTemplate": None}
    assert c.put(f"{P}/workspaces/D94", json=ok, headers=TOKEN).status_code == 200
    assert c.delete(f"{P}/workspaces/D94", headers=TOKEN).status_code == 200
    assert "D94" not in q.workspaces


def test_cancel_running_kills_process(env, monkeypatch):
    c, q = env[0], env[1]
    killed = []
    monkeypatch.setattr(api, "kill_task", lambda tid: killed.append(tid) or True)
    tid = q.add_task(status="RUNNING")
    r = c.post(f"{P}/tasks/{tid}/cancel", headers=TOKEN)
    assert r.status_code == 200 and r.json()["data"]["status"] == "CANCELLED"
    assert killed == [tid]
    queued = q.add_task(status="QUEUED")
    assert c.post(f"{P}/tasks/{queued}/cancel", headers=TOKEN).status_code == 200
    assert killed == [tid]
    assert c.post(f"{P}/tasks/{queued}/cancel", headers=TOKEN).status_code == 409


def test_resume_failed_requeues(env):
    c, q = env[0], env[1]
    tid = q.add_task(status="FAILED", resumeCount=10)
    r = c.post(f"{P}/tasks/{tid}/resume", headers=TOKEN)
    assert r.status_code == 200 and r.json()["data"]["status"] == "QUEUED"
    assert q.task(tid)["resumeAt"] and q.task(tid)["resumeCount"] == 0
    assert c.post(f"{P}/tasks/{tid}/resume", headers=TOKEN).status_code == 409


def test_task_detail_log_tail_only_inside_data_dir(env):
    c, q, tmp = env[0], env[1], env[5]
    tid = q.add_task()
    log = tmp / "data" / "logs" / "r.log"
    log.parent.mkdir()
    log.write_text("".join(f"line{i}\n" for i in range(80)))
    q.runs["r1"] = {"id": "r1", "taskId": tid, "runSeq": 1, "logPath": str(log)}
    d = c.get(f"{P}/tasks/{tid}", headers=TOKEN).json()["data"]
    assert d["logTail"].splitlines() == [f"line{i}" for i in range(30, 80)]
    assert d["tokenTotals"]["input"] == 0 and len(d["runs"]) == 1
    outside = tmp / "secret.log"
    outside.write_text("secret")
    q.runs["r2"] = {"id": "r2", "taskId": tid, "runSeq": 2, "logPath": str(outside)}
    assert c.get(f"{P}/tasks/{tid}", headers=TOKEN).json()["data"]["logTail"] is None


def test_quotas_include_scheduler_warnings(env):
    c, s = env[0], env[3]
    s.warnings = ["claude usage failed"]
    d = c.get(f"{P}/quotas", headers=TOKEN).json()["data"]
    assert d == {"quotas": [], "warnings": ["claude usage failed"]}


def test_issue_preview(env):
    c, q = env[0], env[1]
    d = c.get(f"{P}/issues/CIRA-1", headers=TOKEN).json()["data"]
    assert d == {
        "issueKey": "CIRA-1",
        "title": "Fix",
        "projectKey": "CIRA",
        "workspacePath": q.workspaces["CIRA"]["workspacePath"],
        "defaultTool": "CLAUDE",
    }
    assert c.get(f"{P}/issues/NOPE-1", headers=TOKEN).status_code == 404


def test_setup_check_hides_api_key_value(env, monkeypatch):
    c, q = env[0], env[1]
    monkeypatch.setenv("AYAAN_ANTHROPIC_API_KEY", "sk-very-secret-value")
    q.tools["CLAUDE"].update(authMode="API_KEY", apiKeyRef="AYAAN_ANTHROPIC_API_KEY")
    r = c.get(f"{P}/setup/check", headers=TOKEN)
    assert r.status_code == 200
    assert "sk-very-secret-value" not in r.text and "svc-token" not in r.text
    steps = r.json()["data"]
    assert [s["step"] for s in steps] == [1, 2, 3, 4, 5, 6]
    assert steps[1]["ok"] is True and steps[1]["items"][0]["name"] == "CLAUDE"
    assert steps[0]["ok"] is False  # /nonexistent/claude
    assert steps[5]["ok"] is True


def test_test_notify(env):
    c, ecc = env[0], env[2]
    r = c.post(f"{P}/setup/test-notify", headers=TOKEN)
    assert r.json() == {"success": True, "data": True} and ecc.events() == ["TEST"]
    assert ecc.retries == [False]


def test_setup_check_reports_missing_executable(env):
    c, settings = env[0], env[4]
    settings.cli_executables = "claude=claude"  # relative: rejected
    steps = c.get(f"{P}/setup/check", headers=TOKEN).json()["data"]
    item = next(i for i in steps[0]["items"] if i["name"] == "CLAUDE version")
    assert item == {"name": "CLAUDE version", "ok": False, "detail": "실행 파일을 찾을 수 없음"}


def test_jwt_alg_none_hs512_and_missing_exp_rejected(env):
    c = env[0]
    exp = int(time.time()) + 60
    none_tok = jwt.encode({"sub": "u", "exp": exp}, None, algorithm="none")
    hs512 = jwt.encode({"sub": "u", "exp": exp}, b"k" * 64, algorithm="HS512")
    no_exp = jwt.encode({"sub": "u"}, b"k" * 32, algorithm="HS256")
    for tok in (none_tok, hs512, no_exp):
        r = c.get(f"{P}/tasks", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401


def test_invalid_source_400(env):
    c = env[0]
    r = c.post(f"{P}/tasks", json={"issueKey": "CIRA-1", "source": "EVIL"}, headers=TOKEN)
    assert r.status_code == 400 and r.json()["success"] is False


def test_missing_task_from_query_502(env):
    c, q = env[0], env[1]

    async def none(*a, **k):
        return None

    q.get_task = none
    r = c.get(f"{P}/tasks/x", headers=TOKEN)
    assert r.status_code == 502 and r.json()["success"] is False and r.json()["message"]


def test_docs_disabled(env):
    c = env[0]
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


def test_jwt_subject_allowlist(env):
    c, settings = env[0], env[4]
    assert c.get(f"{P}/tasks", headers=_jwt(60, sub="admin@d94")).status_code == 200
    r = c.get(f"{P}/tasks", headers=_jwt(60, sub="stranger@x"))
    assert r.status_code == 403 and r.json()["success"] is False
    settings.jwt_allowed_subjects = ""
    assert c.get(f"{P}/tasks", headers=_jwt(60, sub="u")).status_code == 403
    assert c.get(f"{P}/tasks", headers=TOKEN).status_code == 200


def test_query_auth_and_transport_errors_502(env):
    c, q = env[0], env[1]
    req = httpx.Request("GET", "http://q/tasks")

    async def denied(*a, **k):
        raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(401, request=req))

    q.list_tasks = denied
    r = c.get(f"{P}/tasks", headers=TOKEN)
    assert r.status_code == 502 and "AYAAN_QUERY_SERVICE_TOKEN" in r.json()["message"]

    async def down(*a, **k):
        raise httpx.ConnectError("refused", request=req)

    q.list_tasks = down
    r = c.get(f"{P}/tasks", headers=TOKEN)
    assert r.status_code == 502 and r.json() == {"success": False, "message": "d94-query 연결 실패"}


def test_put_tool_forwards_merged_body(env):
    c, q = env[0], env[1]
    q.tools["CLAUDE"]["model"] = "opus"
    sent = []
    orig = q.put_tool

    async def spy(cd, body):
        sent.append(body)
        return await orig(cd, body)

    q.put_tool = spy
    r = c.put(f"{P}/tools/CLAUDE", json={"model": None}, headers=TOKEN)
    assert r.status_code == 200
    assert sent[0]["model"] is None and sent[0]["enabled"] == q.tools["CLAUDE"]["enabled"]
    assert "toolCd" not in sent[0] and set(sent[0]) >= {"maxConcurrency", "authMode"}


def test_lifespan_eureka_uses_async_api(tmp_path, monkeypatch):
    from py_eureka_client import eureka_client

    calls = []

    async def init_async(**kw):
        calls.append(("init", kw["app_name"]))

    async def stop_async():
        calls.append(("stop",))

    def sync(*a, **k):
        raise AssertionError("sync eureka call inside the event loop")

    monkeypatch.setattr(eureka_client, "init_async", init_async)
    monkeypatch.setattr(eureka_client, "stop_async", stop_async)
    monkeypatch.setattr(eureka_client, "init", sync)
    monkeypatch.setattr(eureka_client, "stop", sync)
    settings = Settings(_env_file=None, eureka_enabled=True, data_dir=str(tmp_path))

    class Sched:
        async def boot(self): ...
        async def start(self): ...
        async def stop(self): ...

    app = create_app(settings, InMemoryQuery.seeded(), FakeEcc(), Sched())
    with TestClient(app):
        pass
    assert calls == [("init", "d94-app-ayaan"), ("stop",)]


async def test_setup_probe_env_drops_ayaan_vars(monkeypatch):
    from ayaan import setup_check

    monkeypatch.setenv("AYAAN_JWT_SECRET_KEY", "k")
    code, out = await setup_check._run("/usr/bin/env")
    assert code == 0 and "GIT_TERMINAL_PROMPT=0" in out and "AYAAN_" not in out


def test_gemini_login_detected_by_either_credential_file(tmp_path, monkeypatch):
    from ayaan import setup_check

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "gemini-credentials.json").write_text("{}")
    files = setup_check.LOGIN_FILES["GEMINI"]
    assert any((tmp_path / f).is_file() for f in files)
