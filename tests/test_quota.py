import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from ayaan.adapters import RunEvent
from ayaan.quota import (
    QuotaUnavailable,
    apply_quotas,
    budget_quota,
    collect_all,
    collect_claude,
    collect_codex,
    expire_past_resets,
    quotas_from_event,
)
from ayaan.settings import Settings

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class FakeQuery:
    def __init__(self, quotas=None, config=None, tools=None):
        self.quotas, self.config, self.tools, self.puts = (
            quotas or [],
            config or {},
            tools or [],
            [],
        )

    async def get_quotas(self):
        return [dict(q) for q in self.quotas]

    async def put_quota(self, tool, window, body):
        self.puts.append((tool, window, body))
        self.quotas = [q for q in self.quotas if (q["toolCd"], q["windowCd"]) != (tool, window)]
        self.quotas.append({"toolCd": tool, "windowCd": window, **body})

    async def get_config(self):
        return self.config

    async def put_config(self, values):
        self.config.update(values)

    async def get_tools(self):
        return self.tools


class FakeEcc:
    def __init__(self):
        self.sent = []

    async def notify(self, event, title, body, buttons=None):
        self.sent.append((event, title, body))
        return True


def creds(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-secret"}}))
    return p


async def test_claude_usage_maps_windows(tmp_path):
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, json=json.loads((FIX / "claude/usage_api.json").read_text()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rows = await collect_claude(http, creds(tmp_path))
    assert seen["authorization"] == "Bearer tok-secret"
    assert seen["anthropic-beta"] == "oauth-2025-04-20"
    by = {r["windowCd"]: r for r in rows}
    assert by["FIVE_HOUR"]["status"] == "EXHAUSTED" and by["FIVE_HOUR"]["source"] == "USAGE_API"
    assert by["FIVE_HOUR"]["resetsAt"] == "2026-11-11T16:00:00"
    assert by["WEEKLY"]["usedPct"] == 47.0 and by["WEEKLY"]["status"] == "AVAILABLE"


async def test_claude_failure_disables_poll(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as h:
        try:
            await collect_claude(h, creds(tmp_path))
            raise AssertionError
        except QuotaUnavailable:
            pass
    query = FakeQuery(
        config={"CLAUDE_USAGE_POLL": "true"}, tools=[{"toolCd": "CLAUDE", "enabled": True}]
    )
    s = Settings(
        claude_credentials_path=str(tmp_path / "missing.json"), codex_sessions_dir=str(tmp_path)
    )
    warns = await collect_all(query, FakeEcc(), s, NOW)
    assert query.config["CLAUDE_USAGE_POLL"] == "false"
    assert warns == [
        "Claude 비공식 usage 조회 실패 — 자동으로 껐습니다 (실행 결과 기반으로만 갱신)"
    ]


def test_codex_reads_latest_session_snake_and_camel(tmp_path):
    old = tmp_path / "2026/11/old.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text((FIX / "codex/session.jsonl").read_text())
    os.utime(old, (1, 1))
    rows = {r["windowCd"]: r for r in collect_codex(tmp_path)}
    assert rows["FIVE_HOUR"]["usedPct"] == 100.0 and rows["FIVE_HOUR"]["status"] == "EXHAUSTED"
    assert rows["WEEKLY"]["usedPct"] == 15.0 and rows["FIVE_HOUR"]["source"] == "SESSION_LOG"
    new = tmp_path / "new.jsonl"
    ev = {
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "primary": {"usedPercent": 5, "windowMinutes": 300, "resetsAt": 1794412800}
            },
        }
    }
    new.write_text(json.dumps(ev))
    rows = collect_codex(tmp_path)
    assert [(r["windowCd"], r["usedPct"]) for r in rows] == [("FIVE_HOUR", 5.0)]
    assert collect_codex(tmp_path / "none") == []


def test_limit_event_sets_exhausted_with_rule():
    ev = RunEvent(limit_hit=True)
    (row,) = quotas_from_event("GEMINI", ev, "INTERVAL 5h", NOW)
    assert (row["windowCd"], row["status"], row["source"]) == ("DAILY", "EXHAUSTED", "LIMIT_ERROR")
    assert row["resetsAt"] == "2026-11-11T17:00:00"
    at = NOW + timedelta(hours=2)
    ev = RunEvent(
        limit_hit=True,
        resets_at=at,
        rate_limits=[{"window": "five_hour", "used_pct": None, "resets_at": at}],
    )
    (row,) = quotas_from_event("CLAUDE", ev, "INTERVAL 5h", NOW)
    assert row["windowCd"] == "FIVE_HOUR" and row["resetsAt"] == "2026-11-11T14:00:00"


def test_budget_exceeded_until_next_period():
    t = {"toolCd": "X", "authMode": "API_KEY", "budgetTokens": 1000, "budgetPeriod": "MONTHLY"}
    row = budget_quota(t, 1500, NOW)
    assert row["windowCd"] == "BUDGET" and row["status"] == "EXHAUSTED"
    assert row["resetsAt"] == "2026-12-01T00:00:00" and row["source"] == "ACCOUNTING"
    assert budget_quota(t, 10, NOW)["status"] == "AVAILABLE"
    assert budget_quota({**t, "authMode": "ACCOUNT"}, 1, NOW) is None
    daily = budget_quota({**t, "budgetPeriod": "DAILY"}, 1, NOW)
    assert daily["resetsAt"] == "2026-11-12T00:00:00"
    dec = budget_quota(t, 1, datetime(2026, 12, 31, 23, tzinfo=UTC))
    assert dec["resetsAt"] == "2027-01-01T00:00:00"


def test_budget_unknown_period_returns_none():
    t = {"toolCd": "X", "authMode": "API_KEY", "budgetTokens": 1000}
    assert budget_quota({**t, "budgetPeriod": "WEEKLY"}, 1, NOW) is None
    assert budget_quota({**t, "budgetPeriod": ""}, 1, NOW) is None
    assert budget_quota(t, 1, NOW) is None


def test_codex_unreadable_file_returns_empty(tmp_path):
    (tmp_path / "bad.jsonl").write_bytes(b"\xff\xfe\x00garbage")
    assert collect_codex(tmp_path) == []


def test_limit_event_invalid_rule_falls_back():
    (row,) = quotas_from_event("GEMINI", RunEvent(limit_hit=True), "bogus", NOW)
    assert row["status"] == "EXHAUSTED" and row["resetsAt"] == "2026-11-11T12:30:00"


async def test_transition_notifies_once():
    query, ecc = (
        FakeQuery(quotas=[{"toolCd": "CLAUDE", "windowCd": "FIVE_HOUR", "status": "AVAILABLE"}]),
        FakeEcc(),
    )
    row = {
        "toolCd": "CLAUDE",
        "windowCd": "FIVE_HOUR",
        "status": "EXHAUSTED",
        "resetsAt": "2026-11-11T16:00:00",
        "source": "USAGE_API",
    }
    ev = {"QUOTA_EXHAUSTED", "QUOTA_RESTORED"}
    await apply_quotas(query, ecc, "CLAUDE", [row], ev)
    await apply_quotas(query, ecc, "CLAUDE", [row], ev)
    assert len(ecc.sent) == 1
    assert ecc.sent[0][1] == "CLAUDE 5시간 한도 소진 — 11-12 01:00 재설정"
    await apply_quotas(query, ecc, "CLAUDE", [{**row, "status": "AVAILABLE"}], ev)
    assert ecc.sent[1][0] == "QUOTA_RESTORED"


def test_expired_reset_becomes_unknown():
    rows = [
        {"status": "EXHAUSTED", "resetsAt": "2026-11-11T11:00:00"},
        {"status": "EXHAUSTED", "resetsAt": "2026-11-11T13:00:00"},
    ]
    assert [r["status"] for r in expire_past_resets(rows, NOW)] == ["UNKNOWN", "EXHAUSTED"]
