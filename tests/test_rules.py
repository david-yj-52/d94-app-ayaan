from datetime import UTC, date, datetime, timedelta

import pytest

from ayaan.rules import (
    effective_policy,
    next_reset,
    render_prompt,
    resolve_resume_at,
    validate_config,
    validate_tool,
    validate_workspace,
)

NOW = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)  # = 2026-11-09 17:00 America/Los_Angeles


def test_daily_rule_next_midnight_la():
    assert next_reset("DAILY 00:00 America/Los_Angeles", NOW) == datetime(
        2026, 11, 10, 8, 0, tzinfo=UTC
    )


def test_monthly_rule_rolls_to_next_month():
    assert next_reset("MONTHLY 5", NOW).date() == date(2026, 12, 5)


def test_monthly_same_month_when_day_ahead():
    assert next_reset("MONTHLY 15", NOW) == datetime(2026, 11, 15, tzinfo=UTC)


def test_interval_rule():
    assert next_reset("INTERVAL 30m", NOW) == NOW + timedelta(minutes=30)
    assert next_reset("INTERVAL 2h", NOW) == NOW + timedelta(hours=2)


@pytest.mark.parametrize(
    "rule",
    [
        "",
        "MONTHLY 0",
        "MONTHLY 29",
        "DAILY 25:00 UTC",
        "DAILY 00:00 Nope/Zone",
        "INTERVAL 0m",
        "INTERVAL 5d",
        "WEEKLY 1",
    ],
)
def test_bad_rule_raises(rule):
    with pytest.raises(ValueError):
        next_reset(rule, NOW)


def test_resets_at_in_past_falls_back():
    assert resolve_resume_at(NOW - timedelta(hours=1), "INTERVAL 30m", NOW) == NOW + timedelta(
        minutes=30
    )


def test_resets_at_none_falls_back():
    assert resolve_resume_at(None, "INTERVAL 30m", NOW) == NOW + timedelta(minutes=30)


def test_resume_at_minimum_60s():
    assert resolve_resume_at(NOW + timedelta(seconds=5), "INTERVAL 30m", NOW) == NOW + timedelta(
        seconds=60
    )


def test_item_policy_overrides_global():
    assert effective_policy(
        {"exhaustPolicy": "SWITCH_TOOL", "fallbackTools": "CODEX"},
        {"EXHAUST_POLICY": "WAIT_SAME_TOOL", "FALLBACK_TOOL_ORDER": "CLAUDE,CODEX"},
    ) == ("SWITCH_TOOL", ["CODEX"])


def test_global_policy_when_item_unset():
    assert effective_policy(
        {}, {"EXHAUST_POLICY": "WAIT_SAME_TOOL", "FALLBACK_TOOL_ORDER": "CLAUDE, CODEX"}
    ) == ("WAIT_SAME_TOOL", ["CLAUDE", "CODEX"])


def test_validate_config_rejects_out_of_range():
    assert "MAX_RESUME_COUNT" in validate_config({"MAX_RESUME_COUNT": "0"})


def test_validate_config_accepts_valid_and_rejects_others():
    ok = {
        "EXHAUST_POLICY": "SWITCH_TOOL",
        "FALLBACK_TOOL_ORDER": "CLAUDE,CODEX",
        "RESUME_CHECK_INTERVAL_SEC": "30",
        "RESUME_GRACE_SEC": "0",
        "MAX_RESUME_COUNT": "100",
        "TASK_TIMEOUT_SEC": "14400",
        "QUOTA_POLL_INTERVAL_SEC": "60",
        "CLAUDE_USAGE_POLL": "true",
        "REPORT_CRON": "0 9 * * *",
        "NOTIFY_EVENTS": "PAUSED,DONE",
        "DEFAULT_PROJECT_KEY": "",
    }
    assert validate_config(ok) == {}
    assert validate_config({"REPORT_CRON": ""}) == {}
    bad = validate_config(
        {
            "EXHAUST_POLICY": "X",
            "FALLBACK_TOOL_ORDER": "CLAUDE,BOGUS",
            "RESUME_GRACE_SEC": "1801",
            "TASK_TIMEOUT_SEC": "abc",
            "CLAUDE_USAGE_POLL": "yes",
            "REPORT_CRON": "nope",
            "NOTIFY_EVENTS": "PAUSED,BAD",
            "UNKNOWN": "1",
        }
    )
    assert set(bad) == {
        "EXHAUST_POLICY",
        "FALLBACK_TOOL_ORDER",
        "RESUME_GRACE_SEC",
        "TASK_TIMEOUT_SEC",
        "CLAUDE_USAGE_POLL",
        "REPORT_CRON",
        "NOTIFY_EVENTS",
        "UNKNOWN",
    }


def test_validate_tool():
    assert (
        validate_tool(
            {"authMode": "ACCOUNT", "maxConcurrency": 1, "fallbackResetRule": "INTERVAL 30m"}
        )
        == {}
    )
    assert validate_tool({"authMode": "API_KEY", "apiKeyRef": "MY_KEY", "budgetTokens": 5}) == {}
    bad = validate_tool(
        {
            "authMode": "API_KEY",
            "apiKeyRef": "bad-key",
            "maxConcurrency": 5,
            "fallbackResetRule": "x",
            "budgetTokens": 0,
        }
    )
    assert set(bad) == {"apiKeyRef", "maxConcurrency", "fallbackResetRule", "budgetTokens"}
    assert "apiKeyRef" in validate_tool({"authMode": "API_KEY"})
    pasted = "AQ.Ab8_fake-key-value"  # ACCOUNT mode used to accept anything here
    assert "apiKeyRef" in validate_tool({"authMode": "ACCOUNT", "apiKeyRef": pasted})
    assert validate_tool({"authMode": "ACCOUNT", "apiKeyRef": None}) == {}
    assert "authMode" in validate_tool({"authMode": "NOPE"})


def test_workspace_outside_root_rejected(tmp_path):
    root = tmp_path / "root"
    (root / "proj").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    roots = (root,)
    assert validate_workspace({"workspacePath": str(root / "proj")}, roots) == {}
    assert "workspacePath" in validate_workspace({"workspacePath": str(tmp_path / "other")}, roots)
    assert "workspacePath" in validate_workspace(
        {"workspacePath": str(root / "proj" / ".." / ".." / "other")}, roots
    )
    assert "workspacePath" in validate_workspace({"workspacePath": str(root / "missing")}, roots)


def test_workspace_prompt_template_placeholders(tmp_path):
    (tmp_path / "p").mkdir()
    body = {"workspacePath": str(tmp_path / "p"), "promptTemplate": "{issueKey} {title} {content}"}
    assert validate_workspace(body, (tmp_path,)) == {}
    body["promptTemplate"] = "{issueKey} {other}"
    assert "promptTemplate" in validate_workspace(body, (tmp_path,))


def test_render_prompt_keeps_braces_in_content():
    assert render_prompt("{issueKey}:{content}", "CIRA-1", "t", "a {b} c") == "CIRA-1:a {b} c"


def test_tool_blank_reset_rule_is_unset_and_budget_period_enum():
    base = {"authMode": "ACCOUNT"}
    assert validate_tool({**base, "fallbackResetRule": ""}) == {}
    assert set(validate_tool({**base, "fallbackResetRule": 5})) == {"fallbackResetRule"}
    for ok in ("", None, "DAILY", "MONTHLY"):
        assert validate_tool({**base, "budgetPeriod": ok}) == {}
    assert set(validate_tool({**base, "budgetPeriod": "WEEKLY"})) == {"budgetPeriod"}


def test_workspace_non_string_template_and_default_tool(tmp_path):
    (tmp_path / "p").mkdir()
    body = {"workspacePath": str(tmp_path / "p"), "promptTemplate": 5}
    assert set(validate_workspace(body, (tmp_path,))) == {"promptTemplate"}
    body = {"workspacePath": str(tmp_path / "p"), "defaultTool": "NOPE"}
    assert set(validate_workspace(body, (tmp_path,))) == {"defaultTool"}
