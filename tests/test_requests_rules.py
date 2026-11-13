"""Pure rules for skills, permissions, AYAAN_ASK and plan doc paths (TSTA-1 spec 2, 5, 6, 7)."""

import json

import pytest

from ayaan.rules import (
    input_hash,
    parse_ask,
    permission_decision,
    plan_doc_path,
    resolve_skill,
    validate_permissions,
    validate_skill,
)
from ayaan.scheduler import format_report, format_request, request_buttons

SEED = [
    {"toolCd": "*", "category": c, "matcher": m, "decision": d, "priority": p}
    for c, m, d, p in [
        ("FILE_WRITE", "*", "ALLOW", 0),
        ("SHELL", "*", "ALLOW", 0),
        ("NETWORK", "*", "ALLOW", 0),
        ("MCP", "*", "ALLOW", 0),
        ("GIT_PUSH", "*", "ASK", 0),
        ("DEPLOY", "docker", "ASK", 0),
        ("FILE_WRITE", "**/.env*", "DENY", 10),
        ("SHELL", "rm -rf /", "DENY", 10),
    ]
]


@pytest.mark.parametrize(
    "tool_name,inp,want",
    [
        ("Read", {"file_path": "/x"}, "ALLOW"),
        ("Bash", {"command": "ls -la"}, "ALLOW"),
        ("Bash", {"command": "git   push origin main"}, "ASK"),
        ("Bash", {"command": "git pushx"}, "ALLOW"),  # token boundary
        ("Bash", {"command": "docker compose up -d"}, "ASK"),
        ("Bash", {"command": "rm -rf /"}, "DENY"),
        ("Bash", {"command": "rm -rf /tmp/build"}, "ALLOW"),
        ("Write", {"file_path": "/ws/app/.env.local"}, "DENY"),
        ("Edit", {"file_path": "/ws/app/main.py"}, "ALLOW"),
        ("WebFetch", {"url": "https://example.com/a"}, "ALLOW"),
        ("mcp__github__create_pr", {}, "ALLOW"),
    ],
)
def test_permission_decision_seed(tool_name, inp, want):
    assert permission_decision("CLAUDE", tool_name, inp, SEED)[0] == want


def test_tool_row_beats_star_then_priority_then_strictness():
    rows = SEED + [
        {"toolCd": "CLAUDE", "category": "GIT_PUSH", "matcher": "*", "decision": "ALLOW"}
    ]
    assert permission_decision("CLAUDE", "Bash", {"command": "git push"}, rows)[0] == "ALLOW"
    assert permission_decision("CODEX", "Bash", {"command": "git push"}, rows)[0] == "ASK"
    tie = [
        {"toolCd": "*", "category": "NETWORK", "matcher": "*", "decision": "ALLOW"},
        {"toolCd": "*", "category": "NETWORK", "matcher": "evil.com", "decision": "DENY"},
    ]
    assert permission_decision("CLAUDE", "WebFetch", {"url": "http://a.evil.com/x"}, tie) == (
        "DENY",
        "a.evil.com",
    )
    assert permission_decision("CLAUDE", "Bash", {"command": "x"}, [])[0] == "ALLOW"


def test_input_hash_is_order_independent_and_tool_bound():
    assert input_hash("Bash", {"a": 1, "b": 2}) == input_hash("Bash", {"b": 2, "a": 1})
    assert input_hash("Bash", {"a": 1}) != input_hash("Edit", {"a": 1})


def test_parse_ask():
    good = 'thinking\nAYAAN_ASK: {"question":"A?","options":["x","y"],"recommend":1,"context":"c"}'
    assert parse_ask(good) == {
        "question": "A?",
        "options": ["x", "y"],
        "recommend": 1,
        "context": "c",
    }
    assert parse_ask("AYAAN_RESULT: done") is None
    free = parse_ask('AYAAN_ASK: {"question":"why?","options":[],"recommend":3}')
    assert free["options"] == [] and free["recommend"] is None
    broken = parse_ask("long text\nAYAAN_ASK: {not json")
    assert broken["question"].endswith("{not json") and broken["options"] == []
    many = parse_ask("AYAAN_ASK: " + json.dumps({"question": "q", "options": list("abcdefg")}))
    assert len(many["options"]) == 5


def test_plan_doc_path(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/plan.md").write_text("x")
    ws = str(tmp_path)
    assert plan_doc_path("docs/plan.md", ws) == "docs/plan.md"
    assert plan_doc_path(f"`{tmp_path}/docs/plan.md` 설계 완료", ws) == "docs/plan.md"
    assert plan_doc_path("docs/missing.md", ws) is None
    assert plan_doc_path("../outside.md", ws) is None
    assert plan_doc_path("/etc/passwd", ws) is None
    assert plan_doc_path(None, ws) is None


def test_resolve_skill():
    skills = [
        {"skillCd": "B", "enabled": True},
        {"skillCd": "P", "enabled": True},
        {"skillCd": "X", "enabled": False},
    ]
    tool = {"planSkillCd": "B", "devSkillCd": "P"}
    assert resolve_skill({"phase": "PLAN"}, tool, skills)["skillCd"] == "B"
    assert resolve_skill({"phase": "DEV"}, tool, skills)["skillCd"] == "P"
    assert resolve_skill({"phase": "DEV", "skillCd": "B"}, tool, skills)["skillCd"] == "B"
    assert resolve_skill({"phase": "PLAN", "skillCd": "NONE"}, tool, skills) is None
    assert resolve_skill({"phase": "DEV", "skillCd": "X"}, tool, skills) is None
    assert resolve_skill({"phase": "DEV"}, {}, skills) is None


def test_validate_skill_and_permissions():
    spec = "ponytail@ponytail|https://github.com/a/b.git"
    body = {"skillNm": "n", "toolCd": "CLAUDE", "phase": "DEV", "invokeText": "/p"}
    assert validate_skill("OK_1", {**body, "pluginRef": "ponytail@ponytail"}, spec) == ({}, [])
    assert validate_skill("OK_1", {**body, "pluginRef": "x@y"}, spec) == (
        {},
        ["CLAUDE_PLUGINS에 없음"],
    )
    errs, _ = validate_skill("NONE", {**body, "toolCd": "CODEX", "pluginRef": "x@y"}, spec)
    assert set(errs) == {"skillCd", "pluginRef"}
    assert set(validate_skill("a-b", {}, spec)[0]) == {
        "skillCd",
        "skillNm",
        "toolCd",
        "phase",
        "invokeText",
    }
    assert validate_permissions(SEED) == {}
    bad = validate_permissions(
        [{"toolCd": "?", "category": "X", "matcher": "", "decision": "MAYBE"}]
    )
    assert set(bad) == {"0.toolCd", "0.category", "0.matcher", "0.decision"}
    assert validate_permissions({}) == {"body": "must be a list"}


def test_request_message_and_buttons():
    task = {"id": "t1", "issueKey": "CIRA-1"}
    req = {"id": "r1", "kind": "QUESTION", "question": "A?", "options": ["x", "y"], "recommend": 0}
    body = format_request(task, req)
    assert "1. x (추천)" in body and "요청 ID: r1" in body and "답장" in body
    btns = request_buttons("t1", req)
    assert btns[0] == {"label": "x", "action": "answer", "requestId": "r1", "choice": 0}
    assert btns[-1]["action"] == "cancel"
    start = request_buttons("t1", {"id": "r2", "kind": "START", "options": ["실행", "취소"]})
    assert [b["action"] for b in start] == ["answer", "answer"]
    report = format_report([], {"WAITING": 1}, [], [(task, "r1")])
    assert "응답 대기 1" in report and "요청 ID r1" in report
