"""Public API (/tsh/api/v1/ayaan/**) for ECC and d94-web, plus the app lifespan."""

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

from ayaan.clients import EccClient, QueryClient, to_query_ts
from ayaan.quota import collect_all
from ayaan.rules import (
    POLICIES,
    TOOLS,
    render_prompt,
    validate_config,
    validate_tool,
    validate_workspace,
)
from ayaan.runner import kill_task
from ayaan.scheduler import Scheduler
from ayaan.settings import Settings, get_settings
from ayaan.setup_check import run_setup_check

log = logging.getLogger(__name__)

PREFIX = "/tsh/api/v1/ayaan"
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


def ok(data: Any) -> dict:
    return {"success": True, "data": data}


def _task(t: dict) -> dict:
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

                eureka.init(
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
        try:
            yield
        finally:
            if start_background:
                await scheduler.stop()
            if eureka:
                eureka.stop()

    def require_caller(
        x_ayaan_token: str | None = Header(None),
        authorization: str | None = Header(None),
    ) -> None:
        if (
            x_ayaan_token is not None
            and settings.api_token
            and secrets.compare_digest(x_ayaan_token.encode(), settings.api_token.encode())
        ):
            return
        if authorization and authorization.startswith("Bearer ") and settings.jwt_secret_key:
            try:
                jwt.decode(
                    authorization.removeprefix("Bearer ").strip(),
                    base64.b64decode(settings.jwt_secret_key),
                    algorithms=["HS256"],
                    options={"require": ["exp"]},
                )
                return
            except (jwt.PyJWTError, binascii.Error, ValueError):
                pass
        raise ApiError(401, "unauthorized")

    app = FastAPI(title="D94 Ayaan", version="0.1.0", lifespan=lifespan)
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
        try:
            message = e.response.json().get("message")
        except ValueError:
            message = None
        status = e.response.status_code
        return JSONResponse({"success": False, "message": message or f"query {status}"}, status)

    @app.get("/actuator/health")
    async def health():
        return {"status": "UP", "service": settings.service_name}

    r = APIRouter(prefix=PREFIX, dependencies=[Depends(require_caller)])

    async def _workspace(project_key: str | None) -> dict | None:
        ws = await query.get_workspaces() or []
        return next((w for w in ws if w.get("projectKey") == project_key), None)

    # ── tasks ──
    @r.post("/tasks", status_code=201)
    async def create_task(req: TaskReq):
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
        cd = req.tool or ws.get("defaultTool") or "CLAUDE"
        tool = next((t for t in await query.get_tools() or [] if t["toolCd"] == cd), None)
        if not tool or not tool.get("enabled") or (cd == "FAKE" and not settings.fake_enabled):
            raise ApiError(400, f"tool {cd} is not enabled")
        if issue:
            prompt = render_prompt(
                ws.get("promptTemplate") or DEFAULT_TEMPLATE,
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
            t = await query.transition(task_id, expected, "QUEUED", resumeAt=now)
            if t:
                scheduler.wake()
                return ok(_task(t))
        raise ApiError(409, "only PAUSED or FAILED tasks can be resumed")

    @r.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        for expected in ("QUEUED", "PAUSED", "RUNNING"):
            t = await query.transition(task_id, expected, "CANCELLED")
            if t:
                if expected == "RUNNING":
                    kill_task(task_id)
                return ok(_task(t))
        raise ApiError(409, "only QUEUED, PAUSED or RUNNING tasks can be cancelled")

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

    @r.get("/tools")
    async def get_tools():
        return ok(await query.get_tools())

    @r.put("/tools/{cd}")
    async def put_tool(cd: str, body: dict[str, Any] = Body(...)):
        current = next((t for t in await query.get_tools() or [] if t["toolCd"] == cd), None)
        if current is None:
            raise ApiError(404, f"unknown tool: {cd}")
        body.pop("toolCd", None)
        _invalid(validate_tool({**current, **body}))  # PUT is partial in d94-query
        return ok(await query.put_tool(cd, body))

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

    @r.get("/setup/check")
    async def setup_check():
        return ok(await run_setup_check(query, settings))

    @r.post("/setup/test-notify")
    async def test_notify():
        try:
            sent = await ecc.notify(
                "TEST", "ayaan 테스트 알림", "ayaan → ECC 알림 연결이 정상입니다."
            )
        except Exception:
            log.warning("test notify failed", exc_info=True)
            sent = False
        return ok(bool(sent))

    app.include_router(r)
    return app


app = create_app()
