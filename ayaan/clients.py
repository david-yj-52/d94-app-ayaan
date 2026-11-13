import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from ayaan.settings import Settings


def _q(part) -> str:
    """One URL path segment: ids and keys may not add segments or a query string."""
    return quote(str(part), safe="")


def to_query_ts(dt: datetime) -> str:
    """Naive-UTC string d94-query (Java LocalDateTime) accepts."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def from_query_ts(s: str) -> datetime:
    """Parse a timestamp as UTC; accepts with or without Z/offset/fraction."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class QueryClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=settings.query_base_url.rstrip("/"),
            headers={"X-Service-Token": settings.query_service_token},
            transport=transport,
            timeout=30,
        )

    async def _call(self, method: str, path: str, ok=(200, 201), **kw) -> httpx.Response:
        resp = await self._http.request(method, path, **kw)
        if resp.status_code not in ok:
            resp.raise_for_status()
        return resp

    async def _data(self, method: str, path: str, **kw):
        resp = await self._call(method, path, **kw)
        return resp.json().get("data") if resp.content else None

    async def get_config(self) -> dict[str, str]:
        return await self._data("GET", "/config")

    async def put_config(self, values: dict) -> dict:
        return await self._data("PUT", "/config", json=values)

    async def get_tools(self) -> list[dict]:
        return await self._data("GET", "/tools")

    async def put_tool(self, cd: str, body: dict):
        return await self._data("PUT", f"/tools/{_q(cd)}", json=body)

    async def get_workspaces(self) -> list[dict]:
        return await self._data("GET", "/workspaces")

    async def put_workspace(self, key: str, body: dict):
        return await self._data("PUT", f"/workspaces/{_q(key)}", json=body)

    async def delete_workspace(self, key: str):
        return await self._data("DELETE", f"/workspaces/{_q(key)}")

    async def get_quotas(self) -> list[dict]:
        return await self._data("GET", "/quotas")

    async def put_quota(self, tool: str, window: str, body: dict):
        return await self._data("PUT", f"/quotas/{_q(tool)}/{_q(window)}", json=body)

    async def create_task(self, body: dict) -> tuple[int, dict]:
        resp = await self._call("POST", "/tasks", ok=(201, 409), json=body)
        return resp.status_code, resp.json().get("data")

    async def list_tasks(self, status: str | None = None, tool: str | None = None) -> list[dict]:
        params = {k: v for k, v in (("status", status), ("tool", tool)) if v}
        return await self._data("GET", "/tasks", params=params)

    async def get_task(self, task_id: str) -> dict:
        return await self._data("GET", f"/tasks/{_q(task_id)}")

    async def transition(self, task_id: str, expected: str, next: str, **fields) -> dict | None:
        body = {"expectedStatus": expected, "nextStatus": next, "fields": fields}
        resp = await self._call(
            "POST", f"/tasks/{_q(task_id)}/transition", ok=(200, 409), json=body
        )
        return resp.json().get("data") if resp.status_code == 200 else None

    async def claim(self, tool: str, owner: str, lease_s: int, grace_s: int) -> dict | None:
        body = {
            "toolCd": tool,
            "leaseOwner": owner,
            "leaseSeconds": lease_s,
            "graceSeconds": grace_s,
        }
        resp = await self._call("POST", "/tasks/claim", ok=(200, 204), json=body)
        return resp.json().get("data") if resp.status_code == 200 else None

    async def extend_lease(self, task_id: str, owner: str, lease_s: int) -> bool:
        body = {"leaseOwner": owner, "leaseSeconds": lease_s}
        resp = await self._call("POST", f"/tasks/{_q(task_id)}/lease", ok=(200, 409), json=body)
        return resp.status_code == 200

    async def recover(self) -> list[str]:
        return await self._data("POST", "/tasks/recover", json={})

    async def start_run(self, task_id: str, run_seq: int, tool: str) -> dict:
        return await self._data(
            "POST", f"/tasks/{_q(task_id)}/runs", json={"runSeq": run_seq, "toolCd": tool}
        )

    async def finish_run(self, run_id: str, body: dict):
        return await self._data("PUT", f"/runs/{_q(run_id)}", json=body)

    # ── skills / permissions ──
    async def get_skills(self) -> list[dict]:
        return await self._data("GET", "/skills")

    async def put_skill(self, cd: str, body: dict) -> dict:
        return await self._data("PUT", f"/skills/{_q(cd)}", json=body)

    async def delete_skill(self, cd: str):
        return await self._data("DELETE", f"/skills/{_q(cd)}")

    async def get_permissions(self) -> list[dict]:
        return await self._data("GET", "/permissions")

    async def put_permissions(self, rows: list[dict]) -> list[dict]:
        return await self._data("PUT", "/permissions", json=rows)

    # ── requests (human answers) ──
    async def create_request(self, task_id: str, body: dict) -> tuple[int, dict]:
        """409 = the task already has an OPEN request (returned as data)."""
        resp = await self._call("POST", f"/tasks/{_q(task_id)}/requests", ok=(201, 409), json=body)
        return resp.status_code, resp.json().get("data")

    async def list_requests(
        self, status: str | None = None, task_id: str | None = None
    ) -> list[dict]:
        params = {k: v for k, v in (("status", status), ("taskId", task_id)) if v}
        return await self._data("GET", "/requests", params=params)

    async def get_request(self, req_id: str) -> dict | None:
        resp = await self._call("GET", f"/requests/{_q(req_id)}", ok=(200, 404))
        return resp.json().get("data") if resp.status_code == 200 else None

    async def _cas(self, path: str, body: dict | None = None) -> dict | None:
        """CAS endpoints: None when the request was not in the expected status (409)."""
        resp = await self._call("POST", path, ok=(200, 409), json=body or {})
        return resp.json().get("data") if resp.status_code == 200 else None

    async def answer_request(self, req_id: str, body: dict) -> dict | None:
        return await self._cas(f"/requests/{_q(req_id)}/answer", body)

    async def consume_request(self, req_id: str) -> dict | None:
        return await self._cas(f"/requests/{_q(req_id)}/consume")

    async def cancel_request(self, req_id: str) -> dict | None:
        return await self._cas(f"/requests/{_q(req_id)}/cancel")

    async def issue_by_key(self, key: str) -> dict | None:
        resp = await self._call("GET", f"/issues/by-key/{_q(key)}", ok=(200, 404))
        return resp.json().get("data") if resp.status_code == 200 else None


class EccClient:
    DELAYS = (1, 5, 30)

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ):
        self._http = httpx.AsyncClient(
            base_url=settings.ecc_base_url.rstrip("/"),
            headers={"X-ECC-Token": settings.ecc_token},
            transport=transport,
            timeout=15,
        )
        self._sleep = sleep

    async def notify(
        self,
        event: str,
        title: str,
        body: str,
        buttons: list[dict] | None = None,
        retries: bool = True,
    ) -> bool:
        payload = {"event": event, "title": title, "body": body, "buttons": buttons or []}
        for delay in (*(self.DELAYS if retries else ()), None):
            try:
                resp = await self._http.post("/api/v1/notify", json=payload)
                if resp.is_success:
                    return True
            except Exception:  # notify must never raise
                pass
            if delay is not None:
                await self._sleep(delay)
        return False
