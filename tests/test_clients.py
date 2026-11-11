import httpx

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
