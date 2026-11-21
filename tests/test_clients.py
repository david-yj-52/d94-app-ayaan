import json

import httpx
from fakes import InMemoryQuery

from ayaan.clients import EccClient, QueryClient
from ayaan.settings import Settings

S = Settings(query_service_token="qt", ecc_token="et")


def _409(task_id):
    return lambda r: httpx.Response(409, json={"success": False, "data": {"id": task_id}})


async def test_transition_409_returns_none():
    t = httpx.MockTransport(_409("t1"))
    client = QueryClient(S, transport=t)
    assert await client.transition("t1", "QUEUED", "RUNNING", leaseOwner="a") is None


async def test_create_task_returns_existing_on_409():
    t = httpx.MockTransport(_409("old"))
    assert await QueryClient(S, transport=t).create_task({"x": 1}) == (409, {"id": "old"})


async def test_notify_retries_then_false():
    calls, sleeps = [], []

    async def sleep(s):
        sleeps.append(s)

    t = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500))
    assert await EccClient(S, transport=t, sleep=sleep).notify("e", "t", "b") is False
    assert len(calls) == 4
    assert sleeps == [1, 5, 30]
    assert calls[0].headers["X-ECC-Token"] == "et"


async def test_notify_without_retries_tries_once():
    calls = []
    t = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500))
    assert await EccClient(S, transport=t).notify("e", "t", "b", retries=False) is False
    assert len(calls) == 1


async def test_delete_workspace_204_returns_none():
    t = httpx.MockTransport(lambda r: httpx.Response(204))
    assert await QueryClient(S, transport=t).delete_workspace("p") is None


async def test_notify_never_raises_on_unexpected_error():
    def boom(r):
        raise ValueError("boom")

    async def sleep(s):
        pass

    client = EccClient(S, transport=httpx.MockTransport(boom), sleep=sleep)
    assert await client.notify("e", "t", "b") is False


async def test_path_params_are_quoted():
    seen = []
    t = httpx.MockTransport(lambda r: seen.append(r.url.raw_path) or httpx.Response(404))
    await QueryClient(S, transport=t).issue_by_key("../tasks?x=1")
    assert seen[0].endswith(b"/issues/by-key/..%2Ftasks%3Fx%3D1")


async def test_plan_stage_409_returns_none():
    t = httpx.MockTransport(_409("p1"))
    assert await QueryClient(S, transport=t).plan_stage("p1", "PREPARING", "DONE") is None


async def test_plan_stage_body_and_200():
    seen = []

    def h(r):
        seen.append((r.url.path, json.loads(r.content)))
        return httpx.Response(200, json={"data": {"id": "p1", "stage": "HALTED"}})

    c = QueryClient(S, transport=httpx.MockTransport(h))
    got = await c.plan_stage("p1", "PREPARING", "HALTED", lastError="x")
    assert got["stage"] == "HALTED"
    assert seen[0][0].endswith("/plans/p1/stage")
    assert seen[0][1] == {"expectedStage": "PREPARING", "nextStage": "HALTED", "lastError": "x"}


async def test_create_plan_409_returns_status():
    t = httpx.MockTransport(_409("old"))
    assert await QueryClient(S, transport=t).create_plan("P", "m1", "M-1") == (409, {"id": "old"})


async def test_milestone_issues_path():
    seen = []

    def h(r):
        seen.append(r.url.path)
        return httpx.Response(200, json={"data": [{"issueKey": "P-1"}]})

    c = QueryClient(S, transport=httpx.MockTransport(h))
    assert await c.milestone_issues("m1") == [{"issueKey": "P-1"}]
    assert seen[0].endswith("/milestones/m1/issues")


async def test_list_plans_params_and_issue_done():
    seen = []

    def h(r):
        seen.append(r)
        return httpx.Response(200, json={"data": {"changed": True} if "done" in r.url.path else []})

    c = QueryClient(S, transport=httpx.MockTransport(h))
    await c.list_plans(project_key="P", active=True)
    assert dict(seen[0].url.params) == {"projectKey": "P", "active": "true"}
    assert await c.issue_done("P-1") is True
    assert seen[1].url.path.endswith("/issues/by-key/P-1/done")


async def test_in_memory_plan_semantics():
    q = InMemoryQuery()
    st, plan = await q.create_plan("P", "m1", "M-1")
    assert st == 201 and plan["stage"] == "PREPARING"
    assert (await q.create_plan("P", "m2", "M-2"))[0] == 409
    assert await q.plan_stage(plan["id"], "WRONG", "DONE") is None
    h = await q.plan_stage(plan["id"], "PREPARING", "HALTED", lastError="e")
    assert h["lastError"] == "e"
    r = await q.plan_stage(plan["id"], "HALTED", "RUNNING")
    assert r["lastError"] is None
    d = await q.plan_stage(plan["id"], "RUNNING", "DONE")
    assert d["endedAt"]
    assert await q.plan_stage(plan["id"], "DONE", "RUNNING") is None
    assert (await q.create_plan("P", "m2", "M-2"))[0] == 201


async def test_in_memory_milestone_issues_sorted_by_issue_no():
    q = InMemoryQuery()
    q.milestones["m1"] = [{"issueKey": "P-10", "issueNo": 10}, {"issueKey": "P-2", "issueNo": 2}]
    assert [i["issueNo"] for i in await q.milestone_issues("m1")] == [2, 10]
