"""Pure rule functions: reset times, policy resolution, validation, prompt rendering."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

TOOLS = {"CLAUDE", "CODEX", "GEMINI", "CURSOR", "FAKE"}
NOTIFY = {"PAUSED", "RESUMED", "DONE", "FAILED", "QUOTA_EXHAUSTED", "QUOTA_RESTORED"}
POLICIES = {"WAIT_SAME_TOOL", "SWITCH_TOOL"}

_INT_RANGES = {
    "RESUME_CHECK_INTERVAL_SEC": (30, 3600),
    "RESUME_GRACE_SEC": (0, 1800),
    "MAX_RESUME_COUNT": (1, 100),
    "TASK_TIMEOUT_SEC": (300, 14400),
    "QUOTA_POLL_INTERVAL_SEC": (60, 3600),
}
_KNOWN = set(_INT_RANGES) | {
    "EXHAUST_POLICY",
    "FALLBACK_TOOL_ORDER",
    "CLAUDE_USAGE_POLL",
    "REPORT_CRON",
    "NOTIFY_EVENTS",
    "DEFAULT_PROJECT_KEY",
    "CLAUDE_PLUGINS",
}


def _utc(now: datetime) -> datetime:
    return now.astimezone(UTC)


def next_reset(rule: str, now: datetime) -> datetime:
    now = _utc(now)
    parts = (rule or "").split()
    try:
        if len(parts) == 3 and parts[0] == "DAILY":
            m = re.fullmatch(r"(\d{2}):(\d{2})", parts[1])
            if not m:
                raise ValueError
            h, mi = int(m[1]), int(m[2])
            if h > 23 or mi > 59:
                raise ValueError
            tz = ZoneInfo(parts[2])
            local = now.astimezone(tz)
            cand = local.replace(hour=h, minute=mi, second=0, microsecond=0)
            if cand <= local:
                # wall-clock arithmetic on the date keeps DST correct
                cand = (local.replace(hour=12) + timedelta(days=1)).replace(
                    hour=h, minute=mi, second=0, microsecond=0
                )
            return cand.astimezone(UTC)
        if len(parts) == 2 and parts[0] == "MONTHLY":
            day = int(parts[1])
            if not 1 <= day <= 28:
                raise ValueError
            cand = datetime(now.year, now.month, day, tzinfo=UTC)
            if cand <= now:
                cand = datetime(now.year + (now.month == 12), now.month % 12 + 1, day, tzinfo=UTC)
            return cand
        if len(parts) == 2 and parts[0] == "INTERVAL":
            m = re.fullmatch(r"(\d+)([mh])", parts[1])
            if not m or int(m[1]) < 1:
                raise ValueError
            return now + timedelta(**{"minutes" if m[2] == "m" else "hours": int(m[1])})
    except (ValueError, OSError, KeyError) as e:  # ZoneInfoNotFoundError is a KeyError
        raise ValueError(f"invalid reset rule: {rule!r}") from e
    raise ValueError(f"invalid reset rule: {rule!r}")


def resolve_resume_at(parsed: datetime | None, rule: str, now: datetime) -> datetime:
    now = _utc(now)
    at = _utc(parsed) if parsed is not None and _utc(parsed) > now else next_reset(rule, now)
    return max(at, now + timedelta(seconds=60))


def _split(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def effective_policy(task: dict, config: dict) -> tuple[str, list[str]]:
    policy = task.get("exhaustPolicy") or config.get("EXHAUST_POLICY")
    tools = task.get("fallbackTools") or config.get("FALLBACK_TOOL_ORDER") or ""
    return policy, _split(tools)


def validate_config(values: dict[str, str]) -> dict[str, str]:
    errs: dict[str, str] = {}
    for k, v in values.items():
        v = "" if v is None else str(v)
        if k not in _KNOWN:
            errs[k] = "unknown key"
        elif k in _INT_RANGES:
            lo, hi = _INT_RANGES[k]
            if not re.fullmatch(r"\d+", v.strip()) or not lo <= int(v) <= hi:
                errs[k] = f"must be an integer {lo}~{hi}"
        elif k == "EXHAUST_POLICY":
            if v not in POLICIES:
                errs[k] = f"must be one of {sorted(POLICIES)}"
        elif k == "FALLBACK_TOOL_ORDER":
            if not set(_split(v)) <= TOOLS:
                errs[k] = f"values must be in {sorted(TOOLS)}"
        elif k == "NOTIFY_EVENTS":
            if not set(_split(v)) <= NOTIFY:
                errs[k] = f"values must be in {sorted(NOTIFY)}"
        elif k == "CLAUDE_USAGE_POLL":
            if v not in ("true", "false"):
                errs[k] = "must be true or false"
        elif k == "REPORT_CRON":
            if v.strip() and not (len(v.split()) == 5 and croniter.is_valid(v)):
                errs[k] = "must be empty or a valid 5-field cron"
        elif k == "CLAUDE_PLUGINS":
            from ayaan.plugins import parse

            try:
                parse(v)
            except ValueError as e:
                errs[k] = str(e)
        # DEFAULT_PROJECT_KEY: any string
    return errs


def validate_tool(body: dict) -> dict[str, str]:
    errs: dict[str, str] = {}
    mode = body.get("authMode")
    if mode not in ("ACCOUNT", "API_KEY"):
        errs["authMode"] = "must be ACCOUNT or API_KEY"
    mc = body.get("maxConcurrency")
    if mc is not None and (isinstance(mc, bool) or not isinstance(mc, int) or not 1 <= mc <= 4):
        errs["maxConcurrency"] = "must be an integer 1~4"
    rule = body.get("fallbackResetRule")
    if rule not in (None, ""):  # blank = unset; the scheduler falls back to its default
        try:
            next_reset(str(rule), datetime.now(UTC))
        except ValueError:
            errs["fallbackResetRule"] = "invalid reset rule"
    ref = body.get("apiKeyRef")
    # checked in every mode: a pasted key value must never be stored (it is an env var NAME)
    if (mode == "API_KEY" or ref not in (None, "")) and not (
        isinstance(ref, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", ref)
    ):
        errs["apiKeyRef"] = "env var name (^[A-Z][A-Z0-9_]*$), required for API_KEY"
    bt = body.get("budgetTokens")
    if bt is not None and (isinstance(bt, bool) or not isinstance(bt, int) or bt < 1):
        errs["budgetTokens"] = "must be an integer >= 1"
    if body.get("budgetPeriod") not in (None, "", "DAILY", "MONTHLY"):
        errs["budgetPeriod"] = "must be DAILY or MONTHLY"
    return errs


def validate_workspace(body: dict, roots: tuple[Path, ...]) -> dict[str, str]:
    errs: dict[str, str] = {}
    raw = body.get("workspacePath")
    if not isinstance(raw, str) or not raw:
        errs["workspacePath"] = "required"
    else:
        p = Path(raw).resolve()
        if not any(p.is_relative_to(Path(r).resolve()) for r in roots):
            errs["workspacePath"] = "must be inside an allowed workspace root"
        elif not p.is_dir():
            errs["workspacePath"] = "directory does not exist"
    tpl = body.get("promptTemplate")
    if tpl is not None and not isinstance(tpl, str):
        errs["promptTemplate"] = "must be a string"
    elif tpl and set(re.findall(r"\{[^{}]*\}", tpl)) - {"{issueKey}", "{title}", "{content}"}:
        errs["promptTemplate"] = "only {issueKey}, {title}, {content} placeholders allowed"
    if body.get("defaultTool") not in (None, *TOOLS):
        errs["defaultTool"] = f"must be one of {sorted(TOOLS)}"
    return errs


def render_prompt(template: str, issue_key: str, title: str, content: str) -> str:
    # single pass, no str.format: values are never re-scanned and content may hold braces
    vals = {"{issueKey}": issue_key, "{title}": title, "{content}": content}
    return re.sub(r"\{issueKey\}|\{title\}|\{content\}", lambda m: vals[m[0]], template)
