"""Public API (/tsh/api/v1/ayaan/**) for ECC and d94-web, plus the app lifespan."""

import asyncio
import base64
import binascii
import logging
import secrets
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Body, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ayaan import plugins
from ayaan.clients import EccClient, QueryClient, to_query_ts
from ayaan.quota import collect_all
from ayaan.rules import (
    NO_SKILL,
    PHASES,
    PLAN_TEMPLATE,
    POLICIES,
    TOOLS,
    input_hash,
    permission_decision,
    render_prompt,
    validate_config,
    validate_permissions,
    validate_skill,
    validate_tool,
    validate_workspace,
)
from ayaan.runner import kill_task
from ayaan.scheduler import Scheduler
from ayaan.settings import Settings, get_settings
from ayaan.setup_check import run_setup_check

log = logging.getLogger(__name__)

PREFIX = "/tsh/api/v1/ayaan"
SOURCES = {"TELEGRAM", "API", "WEB", "CIRA"}
DEFAULT_TEMPLATE = (
    "CIRA 이슈 {issueKey}: {title}\n\n{content}\n\n"
    "위 이슈를 구현하고 테스트·빌드를 통과시킨 뒤, "
    "저장소 규칙(CLAUDE.md/AGENTS.md)에 따라 커밋·배포까지 진행해.\n"
    "완료되면 마지막 줄에 'AYAAN_RESULT: <한 줄 요약>'을 출력해."
)


class ApiError(Exception):
    def __init__(self, status: int, message: str | None = None, data: Any = None):
        self.status, self.message, self.data = status, message, data


class TaskReq(BaseModel):
    issueKey: str | None = None
    prompt: str | None = None
    projectKey: str | None = None
    tool: str | None = None
    exhaustPolicy: str | None = None
    fallbackTools: str | list[str] | None = None
    source: str = "API"
    phase: str = "PLAN"  # every new task starts with planning unless DEV is asked (D7)
    skillCd: str | None = None


class AnswerReq(BaseModel):
    answer: str | None = None
    choice: int | None = None
    answeredBy: str | None = None


class HookReq(BaseModel):
    toolName: str
    input: dict[str, Any] = {}


LOCALHOST = {"127.0.0.1", "::1"}
# answers of fixed-choice requests: choice index -> decision
CHOICE_DECISION = {"PERMISSION": ("ALLOW", "DENY"), "START": ("ALLOW", "DENY")}


def ok(data: Any) -> dict:
    return {"success": True, "data": data}


def _task(t: dict | None) -> dict:
    if not t:
        raise ApiError(502, "d94-query returned no task")
    if "objId" in t:
        t["id"] = t.pop("objId")
    return t


def _invalid(errs: dict[str, str]) -> None:
    if errs:
        raise ApiError(422, data=errs)


def log_tail(runs: list[dict] | None, data_dir: str) -> str | None:
    if not runs:
        return None
    raw = max(runs, key=lambda r: r.get("runSeq") or 0).get("logPath")
    if not raw:
        return None
    path = Path(raw).resolve()
    if not path.is_relative_to(Path(data_dir).resolve()) or not path.is_file():
        return None
    with path.open(errors="replace") as f:
        return "".join(deque(f, 50))


def create_app(
    settings: Settings | None = None,
    query=None,
    ecc=None,
    scheduler=None,
    start_background: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    query = query or QueryClient(settings)
    ecc = ecc or EccClient(settings)
    scheduler = scheduler or Scheduler(query, ecc, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eureka = None
        if start_background:
            if settings.eureka_enabled:
                from py_eureka_client import eureka_client as eureka

                await eureka.init_async(  # sync init() fails inside a running event loop
                    eureka_server=settings.eureka_url,
                    app_name=settings.service_name,
                    instance_host=settings.eureka_instance_host,
                    instance_port=settings.eureka_instance_port,
                )
            try:
                await scheduler.boot()
            except Exception:  # query down at boot: every tick recovers again
                log.exception("scheduler boot failed")
            await scheduler.start()
            app.state.plugin_sync = asyncio.create_task(_boot_sync())  # never blocks boot
        try:
            yield
        finally:
            if start_background:
                await scheduler.stop()
            if eureka:
                await eureka.stop_async()

    def require_caller(
        x_ayaan_token: str | None = Header(None),
        authorization: str | None = Header(None),
    ) -> str:
        """-> caller id: "service" (ECC token) or "web:<sub>" (JWT)."""
        if (
            x_ayaan_token is not None
            and settings.api_token
            and secrets.compare_digest(x_ayaan_token.encode(), settings.api_token.encode())
        ):
            return "service"
        if authorization and authorization.startswith("Bearer ") and settings.jwt_secret_key:
            try:
                claims = jwt.decode(
                    authorization.removeprefix("Bearer ").strip(),
                    base64.b64decode(settings.jwt_secret_key),
                    algorithms=["HS256"],
                    options={"require": ["exp"]},
                )
            except (jwt.PyJWTError, binascii.Error, ValueError):
                raise ApiError(401, "unauthorized") from None
            # d94 registration is open: a valid signature alone must not run code here
            if claims.get("sub") not in settings.allowed_subjects:
                raise ApiError(403, "forbidden")
            return f"web:{claims['sub']}"
        raise ApiError(401, "unauthorized")

    app = FastAPI(
        title="D94 Ayaan",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings, app.state.query, app.state.ecc = settings, query, ecc
    app.state.scheduler = scheduler

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        body = {"success": False, "data": e.data} if e.data is not None else None
        return JSONResponse(body or {"success": False, "message": e.message}, e.status)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_: Request, e: RequestValidationError):
        errs = {
            ".".join(str(x) for x in err["loc"][1:]) or "body": err["msg"] for err in e.errors()
        }
        return JSONResponse({"success": False, "data": errs}, 422)

    @app.exception_handler(httpx.HTTPStatusError)
    async def _query_error(_: Request, e: httpx.HTTPStatusError):
        status = e.response.status_code
        if status in (401, 403):  # our service token, not the caller: never echo it as 401
            msg = "d94-query 인증 실패 (AYAAN_QUERY_SERVICE_TOKEN 확인)"
            return JSONResponse({"success": False, "message": msg}, 502)
        try:
            message = e.response.json().get("message")
        except ValueError:
            message = None
        return JSONResponse({"success": False, "message": message or f"query {status}"}, status)

    @app.exception_handler(httpx.TransportError)
    async def _query_down(_: Request, e: httpx.TransportError):
        return JSONResponse({"success": False, "message": "d94-query 연결 실패"}, 502)

    @app.get("/actuator/health")
    async def health():
        return {"status": "UP", "service": settings.service_name}

    # ── Claude PreToolUse hook (ayaan-hook). Not require_caller: a per-run token, localhost only.
    # The token can only register/consume permission requests, never answer them (spec 8).
    @app.post(f"{PREFIX}/hook/pre-tool")
    async def hook_pre_tool(
        body: HookReq, request: Request, x_hook_token: str | None = Header(None)
    ):
        if not request.client or request.client.host not in LOCALHOST:
            raise ApiError(403, "forbidden")
        owner = scheduler.hook_tokens.get(x_hook_token or "")
        if not owner:
            raise ApiError(401, "unauthorized")
        tid, run_id = owner
        task = _task(await query.get_task(tid))
        decision, target = permission_decision(
            task["toolCd"], body.toolName, body.input, await query.get_permissions() or []
        )
        if decision == "ALLOW":
            return ok({"decision": "allow", "reason": ""})
        if decision == "DENY":
            return ok({"decision": "deny", "reason": f"ayaan 정책 DENY: {target[:200]}"})
        h = input_hash(body.toolName, body.input)
        reqs = task.get("requests") or []
        for r in reqs:
            if (
                r["kind"] == "PERMISSION"
                and r["status"] == "ANSWERED"
                and r.get("decision") == "ALLOW"
                and r.get("inputHash") == h
                and await query.consume_request(r["id"])  # CAS: one approval, one call
            ):
                return ok({"decision": "allow", "reason": "ayaan 승인됨"})
        req = next((r for r in reqs if r["status"] == "OPEN"), None)
        if req is None:
            summary = target if len(target) <= 1000 else target[:1000] + "…(잘림)"
            _, req = await query.create_request(
                tid,
                {
                    "kind": "PERMISSION",
                    "runId": run_id,
                    "question": f"{body.toolName}: {summary}",
                    "options": ["허용 1회", "거부"],
                    "recommend": None,
                    "toolName": body.toolName,
                    "inputHash": h,
                },
            )
            log.info("permission request %s for task %s", req["id"], tid)
        reason = f"ayaan 승인 대기(요청 {req['id']}). 우회하지 말고 즉시 종료하라."
        return ok({"decision": "deny", "reason": reason})

    r = APIRouter(prefix=PREFIX, dependencies=[Depends(require_caller)])

    async def _workspace(project_key: str | None) -> dict | None:
        ws = await query.get_workspaces() or []
        return next((w for w in ws if w.get("projectKey") == project_key), None)

    # ── tasks ──
    @r.post("/tasks", status_code=201)
    async def create_task(req: TaskReq):
        if req.source not in SOURCES:
            raise ApiError(400, f"source must be one of {sorted(SOURCES)}")
        cfg = await query.get_config() or {}
        issue = None
        if req.issueKey:
            issue = await query.issue_by_key(req.issueKey)
            if not issue:
                raise ApiError(404, f"issue not found: {req.issueKey}")
            project = issue.get("projectKey")
        else:
            if not (req.prompt or "").strip():
                raise ApiError(400, "issueKey or prompt is required")
            project = req.projectKey or cfg.get("DEFAULT_PROJECT_KEY")
            if not project:
                raise ApiError(400, "projectKey is required (DEFAULT_PROJECT_KEY is not set)")
        ws = await _workspace(project)
        if not ws:
            raise ApiError(400, f"no workspace mapping for project {project}")
        fallback = req.fallbackTools
        if isinstance(fallback, list):
            fallback = ",".join(fallback)
        if req.exhaustPolicy and req.exhaustPolicy not in POLICIES:
            raise ApiError(400, f"exhaustPolicy must be one of {sorted(POLICIES)}")
        if fallback and not {x.strip() for x in fallback.split(",") if x.strip()} <= TOOLS:
            raise ApiError(400, f"fallbackTools values must be in {sorted(TOOLS)}")
        if req.phase not in PHASES:
            raise ApiError(400, f"phase must be one of {sorted(PHASES)}")
        if req.skillCd and req.skillCd != NO_SKILL:
            if req.skillCd not in {s["skillCd"] for s in await query.get_skills() or []}:
                raise ApiError(400, f"unknown skill: {req.skillCd}")
        cd = req.tool or ws.get("defaultTool") or "CLAUDE"
        tool = next((t for t in await query.get_tools() or [] if t["toolCd"] == cd), None)
        if not tool or not tool.get("enabled") or (cd == "FAKE" and not settings.fake_enabled):
            raise ApiError(400, f"tool {cd} is not enabled")
        if issue:
            template = ws.get("promptTemplate") or DEFAULT_TEMPLATE
            prompt = render_prompt(
                PLAN_TEMPLATE if req.phase == "PLAN" else template,
                issue["issueKey"],
                issue.get("title") or "",
                issue.get("content") or "",
            )
        else:
            prompt = req.prompt
        status, task = await query.create_task(
            {
                "issueKey": req.issueKey,
                "source": req.source,
                "projectKey": project,
                "workspacePath": ws["workspacePath"],
                "prompt": prompt,
                "toolCd": cd,
                "exhaustPolicy": req.exhaustPolicy,
                "fallbackTools": fallback,
                "phase": req.phase,
                "skillCd": req.skillCd,
            }
        )
        if status == 409:
            raise ApiError(409, data=_task(task))
        task = await scheduler.pause_if_exhausted(_task(task))
        scheduler.wake()
        return ok(task)

    @r.get("/tasks")
    async def list_tasks(status: str | None = None, tool: str | None = None):
        return ok([_task(t) for t in await query.list_tasks(status, tool) or []])

    @r.get("/tasks/{task_id}")
    async def get_task(task_id: str):
        t = _task(await query.get_task(task_id))
        t["logTail"] = log_tail(t.get("runs"), settings.data_dir)
        return ok(t)

    @r.post("/tasks/{task_id}/resume")
    async def resume_task(task_id: str):
        now = to_query_ts(scheduler.clock())
        for expected in ("PAUSED", "FAILED"):
            fields = {"resumeAt": now} | ({"resumeCount": 0} if expected == "FAILED" else {})
            t = await query.transition(task_id, expected, "QUEUED", **fields)
            if t:
                scheduler.wake()
                return ok(_task(t))
        if (await query.get_task(task_id) or {}).get("status") == "WAITING":
            raise ApiError(409, "답변이 필요합니다 (응답 대기 중인 작업은 답변이 곧 재개입니다)")
        raise ApiError(409, "only PAUSED or FAILED tasks can be resumed")

    @r.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        for expected in ("QUEUED", "PAUSED", "RUNNING", "WAITING"):
            t = await query.transition(task_id, expected, "CANCELLED")
            if t:
                if expected == "RUNNING":
                    kill_task(task_id)
                for r in await query.list_requests("OPEN", task_id) or []:
                    await query.cancel_request(r["id"])
                return ok(_task(t))
        raise ApiError(409, "only QUEUED, PAUSED, RUNNING or WAITING tasks can be cancelled")

    @r.post("/tasks/{task_id}/dev-task", status_code=201)
    async def dev_task(task_id: str):
        plan = _task(await query.get_task(task_id))
        if plan.get("phase") != "PLAN" or plan.get("status") != "DONE":
            raise ApiError(409, "only DONE planning tasks can create a dev task")
        children = [
            t
            for t in await query.list_tasks() or []
            if t.get("parentTaskId") == task_id and t.get("status") != "CANCELLED"
        ]
        if children:
            raise ApiError(409, data=_task(children[0]))
        return ok(await scheduler.create_dev_task(plan))

    # ── human requests ──
    @r.get("/requests")
    async def list_requests(status: str | None = None, taskId: str | None = None):
        return ok(await query.list_requests(status, taskId) or [])

    @r.post("/requests/{req_id}/answer")
    async def answer_request(req_id: str, body: AnswerReq, caller: str = Depends(require_caller)):
        req = await query.get_request(req_id)
        if not req:
            raise ApiError(404, f"request not found: {req_id}")
        if req["status"] != "OPEN":
            raise ApiError(409, data=req)
        opts, kind = req.get("options") or [], req["kind"]
        decision = None
        if body.choice is not None:  # callback_data is untrusted: range-check the index
            if not 0 <= body.choice < len(opts):
                raise ApiError(422, data={"choice": f"must be 0~{len(opts) - 1}"})
            answer = opts[body.choice]
            if kind in CHOICE_DECISION:
                decision = CHOICE_DECISION[kind][body.choice]
        elif kind == "QUESTION" and (body.answer or "").strip():
            answer = body.answer.strip()[:4000]
        else:
            raise ApiError(422, data={"answer": "choice required (free text only for QUESTION)"})
        # ECC (service token) passes the Telegram user; a web caller is its JWT subject
        by = (body.answeredBy or "service") if caller == "service" else caller
        done = await query.answer_request(
            req_id, {"answer": answer, "decision": decision, "answeredBy": by[:100]}
        )
        if not done:  # someone answered first (CAS)
            raise ApiError(409, data=await query.get_request(req_id))
        log.info("request %s (%s) answered", req_id, kind)
        tid = req["taskId"]
        if kind == "START" and decision == "DENY":
            await query.transition(tid, "WAITING", "CANCELLED")
        else:
            # not WAITING yet (answered before the CLI exited): settle requeues it
            now = to_query_ts(scheduler.clock())
            if await query.transition(tid, "WAITING", "QUEUED", resumeAt=now):
                scheduler.wake()
        return ok(done)

    # ── quotas ──
    async def _quotas() -> dict:
        return {"quotas": await query.get_quotas() or [], "warnings": scheduler.warnings}

    @r.get("/quotas")
    async def get_quotas():
        return ok(await _quotas())

    @r.post("/quotas/refresh")
    async def refresh_quotas():
        scheduler.warnings = await collect_all(query, scheduler, settings, scheduler.clock())
        return ok(await _quotas())

    # ── reference data ──
    @r.get("/config")
    async def get_config():
        return ok(await query.get_config())

    @r.put("/config")
    async def put_config(values: dict[str, Any] = Body(...)):
        _invalid(validate_config(values))
        clean = {k: "" if v is None else str(v) for k, v in values.items()}
        return ok(await query.put_config(clean))

    @r.get("/skills")
    async def get_skills():
        return ok(await query.get_skills())

    @r.put("/skills/{cd}")
    async def put_skill(cd: str, body: dict[str, Any] = Body(...)):
        body.pop("skillCd", None)
        spec = (await query.get_config() or {}).get("CLAUDE_PLUGINS", plugins.DEFAULT)
        errs, warnings = validate_skill(cd, body, spec)
        _invalid(errs)
        body["pluginRef"] = body.get("pluginRef") or None
        return ok({"skill": await query.put_skill(cd, body), "warnings": warnings})

    @r.delete("/skills/{cd}")
    async def delete_skill(cd: str):
        return ok(await query.delete_skill(cd))

    @r.get("/permissions")
    async def get_permissions():
        return ok(await query.get_permissions())

    @r.put("/permissions")
    async def put_permissions(rows: list[Any] = Body(...)):
        _invalid(validate_permissions(rows))
        clean = [{k: v for k, v in r.items() if k != "id"} for r in rows]
        return ok(await query.put_permissions(clean))

    @r.get("/tools")
    async def get_tools():
        return ok(await query.get_tools())

    @r.put("/tools/{cd}")
    async def put_tool(cd: str, body: dict[str, Any] = Body(...)):
        current = next((t for t in await query.get_tools() or [] if t["toolCd"] == cd), None)
        if current is None:
            raise ApiError(404, f"unknown tool: {cd}")
        body.pop("toolCd", None)
        merged = {**current, **body}
        errs = validate_tool(merged)
        skills = {s["skillCd"] for s in await query.get_skills() or []}
        for k in ("planSkillCd", "devSkillCd"):
            if merged.get(k) and merged[k] not in skills:
                errs[k] = "unknown skill"
            merged[k] = merged.get(k) or None
        _invalid(errs)
        merged.pop("toolCd", None)
        return ok(await query.put_tool(cd, merged))  # d94-query PUT replaces every field

    @r.get("/workspaces")
    async def get_workspaces():
        return ok(await query.get_workspaces())

    @r.put("/workspaces/{key}")
    async def put_workspace(key: str, body: dict[str, Any] = Body(...)):
        _invalid(validate_workspace(body, settings.workspace_root_paths))
        body["workspacePath"] = str(Path(body["workspacePath"]).resolve())
        body.pop("projectKey", None)
        return ok(await query.put_workspace(key, body))

    @r.delete("/workspaces/{key}")
    async def delete_workspace(key: str):
        return ok(await query.delete_workspace(key))

    # ── ECC preview, setup ──
    @r.get("/issues/{issue_key}")
    async def issue_preview(issue_key: str):
        issue = await query.issue_by_key(issue_key)
        ws = issue and await _workspace(issue.get("projectKey"))
        if not ws:
            raise ApiError(404, f"issue or workspace mapping not found: {issue_key}")
        return ok(
            {
                "issueKey": issue["issueKey"],
                "title": issue.get("title"),
                "projectKey": issue.get("projectKey"),
                "workspacePath": ws["workspacePath"],
                "defaultTool": ws.get("defaultTool"),
            }
        )

    async def _sync_plugins() -> list[dict]:
        spec = (await query.get_config() or {}).get("CLAUDE_PLUGINS", plugins.DEFAULT)
        try:
            return await plugins.sync(spec, settings)
        except ValueError as e:
            raise ApiError(400, message=str(e)) from e

    async def _boot_sync() -> None:
        try:
            log.info("plugin sync: %s", await _sync_plugins())
        except Exception:
            log.exception("plugin sync failed")

    @r.post("/plugins/sync")
    async def sync_plugins():
        return ok(await _sync_plugins())

    @r.get("/plugins")
    async def list_plugins():
        return ok({"output": await plugins.installed(settings)})

    @r.get("/setup/check")
    async def setup_check():
        return ok(await run_setup_check(query, settings))

    @r.post("/setup/test-notify")
    async def test_notify():
        try:
            sent = await asyncio.wait_for(
                ecc.notify(
                    "TEST",
                    "ayaan 테스트 알림",
                    "ayaan → ECC 알림 연결이 정상입니다.",
                    retries=False,
                ),
                10,
            )
        except Exception:
            log.warning("test notify failed", exc_info=True)
            sent = False
        return ok(bool(sent))

    app.include_router(r)
    return app


app = create_app()
