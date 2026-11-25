import json

import httpx

import ayaan.antigravity as ag
from ayaan.adapters import ADAPTERS
from ayaan.quota import collect_antigravity

CONFIGS = [
    {
        "label": "Gemini 4 Hydrogen (High)",
        "modelOrAlias": {"model": "M525"},
        "quotaInfo": {"remainingFraction": 1, "resetTime": "2026-12-02T01:15:30Z"},
    },
    {
        "label": "Gemini 3.8 Flash (High)",
        "modelOrAlias": {"model": "M318"},
        "quotaInfo": {"remainingFraction": 0.25, "resetTime": "2026-12-02T01:15:30Z"},
    },
    {
        "label": "Gemini 3.8 Flash (Low)",
        "modelOrAlias": {"model": "M320"},
        "quotaInfo": {"remainingFraction": 0.75, "resetTime": "2026-12-02T01:15:30Z"},
    },
    {"label": "Gemini 4 Carbon", "modelOrAlias": {"model": "M670"}, "quotaInfo": {}},
]


def test_pick_model_by_label_or_id_else_least_used_flash():
    assert ag.pick_model(CONFIGS, "Gemini 4 Hydrogen (High)")["modelOrAlias"]["model"] == "M525"
    assert ag.pick_model(CONFIGS, "M318")["label"] == "Gemini 3.8 Flash (High)"
    assert ag.pick_model(CONFIGS, None)["label"] == "Gemini 3.8 Flash (Low)"
    assert ag.pick_model(CONFIGS, "gone-model")["label"] == "Gemini 3.8 Flash (Low)"


def test_adapter_argv_and_parse():
    a = ADAPTERS["ANTIGRAVITY"]
    assert a.new_argv(None) == ["-m", "ayaan.antigravity"]
    assert a.resume_argv("c1", "M318") == [
        "-m",
        "ayaan.antigravity",
        "--model",
        "M318",
        "--resume",
        "c1",
    ]
    ok = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "c1"}),
        json.dumps(
            {
                "type": "result",
                "session_id": "c1",
                "is_error": False,
                "result": "x\nAYAAN_RESULT: done",
            }
        ),
    ]
    ev = a.parse(ok)
    assert (ev.session_id, ev.result_line, ev.limit_hit) == ("c1", "done", False)
    err = [
        json.dumps(
            {
                "type": "result",
                "session_id": "c1",
                "is_error": True,
                "result": "RESOURCE_EXHAUSTED: quota exceeded",
            }
        )
    ]
    assert a.parse(err).limit_hit


async def test_collect_antigravity_row(monkeypatch):
    monkeypatch.setattr(ag, "ls_address", lambda: (1234, "csrf-x"))
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), csrf=req.headers["x-codeium-csrf-token"])
        return httpx.Response(
            200, json={"userStatus": {"cascadeModelConfigData": {"clientModelConfigs": CONFIGS}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        [row] = await collect_antigravity(http)
        [pinned] = await collect_antigravity(http, "Gemini 3.8 Flash (High)")
    assert seen == {
        "url": "http://127.0.0.1:1234/exa.language_server_pb.LanguageServerService/GetUserStatus",
        "csrf": "csrf-x",
    }
    assert (row["toolCd"], row["windowCd"], row["usedPct"]) == ("ANTIGRAVITY", "WEEKLY", 25.0)
    assert row["resetsAt"] == "2026-12-02T01:15:30" and row["source"] == "USAGE_API"
    assert pinned["usedPct"] == 75.0


def test_ls_address_skips_dead_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "DAEMON_DIR", tmp_path)
    (tmp_path / "hub_1.json").write_text(
        json.dumps({"pid": 999999999, "httpPort": 1, "csrfToken": "a"})
    )
    try:
        ag.ls_address()
        raise AssertionError
    except ag.Unavailable:
        pass
    import os

    (tmp_path / "hub_2.json").write_text(
        json.dumps({"pid": os.getpid(), "httpPort": 2, "csrfToken": "b"})
    )
    assert ag.ls_address() == (2, "b")
