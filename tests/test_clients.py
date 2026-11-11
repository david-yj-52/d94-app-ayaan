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
