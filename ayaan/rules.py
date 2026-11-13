"""Pure rule functions: reset times, policy resolution, validation, prompt rendering."""

import fnmatch
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

TOOLS = {"CLAUDE", "CODEX", "GEMINI", "CURSOR", "FAKE"}
NOTIFY = {
    "PAUSED",
    "RESUMED",
    "DONE",
    "FAILED",
    "QUOTA_EXHAUSTED",
    "QUOTA_RESTORED",
    "WAITING",
    "PLAN_DONE",
}
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
    if mode == "API_KEY" and not (isinstance(ref, str) and re.fullmatch(r"[A-Z0-9_]+", ref)):
        errs["apiKeyRef"] = "required for API_KEY, must match ^[A-Z0-9_]+$"
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


# ── skills ─────────────────────────────────────────────────────────────────

PHASES = {"PLAN", "DEV"}
SKILL_PHASES = PHASES | {"ANY"}
NO_SKILL = "NONE"
PLUGIN_REF = re.compile(r"[\w.-]+@[\w.-]+")


def validate_skill(cd: str, body: dict, plugins_spec: str) -> tuple[dict[str, str], list[str]]:
    """-> (errors, warnings). A plugin missing from CLAUDE_PLUGINS only warns: install state is
    the runtime's, checked again right before a run."""
    errs: dict[str, str] = {}
    if not re.fullmatch(r"[A-Z0-9_]{1,50}", cd or "") or cd == NO_SKILL:
        errs["skillCd"] = "must match ^[A-Z0-9_]{1,50}$ and not be NONE"
    if not str(body.get("skillNm") or "").strip():
        errs["skillNm"] = "required"
    if body.get("toolCd") not in TOOLS:
        errs["toolCd"] = f"must be one of {sorted(TOOLS)}"
    if body.get("phase") not in SKILL_PHASES:
        errs["phase"] = f"must be one of {sorted(SKILL_PHASES)}"
    text = body.get("invokeText")
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        errs["invokeText"] = "required, at most 500 characters"
    ref = body.get("pluginRef") or None
    warnings = []
    if ref is not None:
        if body.get("toolCd") != "CLAUDE":
            errs["pluginRef"] = "only CLAUDE skills can reference a plugin"
        elif not PLUGIN_REF.fullmatch(str(ref)):
            errs["pluginRef"] = "must be plugin@marketplace"
        else:
            from ayaan.plugins import parse

            try:
                listed = {f"{p}@{m}" for p, m, _ in parse(plugins_spec)}
            except ValueError:
                listed = set()
            if ref not in listed:
                warnings.append("CLAUDE_PLUGINS에 없음")
    return errs, warnings


def resolve_skill(task: dict, tool: dict, skills: list[dict]) -> dict | None:
    """task.skillCd > tool default for the task phase > none. NONE = explicitly no skill.
    A missing or disabled skill runs without one."""
    cd = task.get("skillCd") or tool.get(
        "planSkillCd" if task.get("phase") == "PLAN" else "devSkillCd"
    )
    if not cd or cd == NO_SKILL:
        return None
    return next((s for s in skills if s["skillCd"] == cd and s.get("enabled", True)), None)


# ── plan → dev ─────────────────────────────────────────────────────────────

PLAN_TEMPLATE = (
    "CIRA 이슈 {issueKey}: {title}\n\n{content}\n\n"
    "위 이슈의 기획·설계서를 작성해. 코드는 수정하지 마. 설계서는 작업공간에 파일로 저장하고 "
    "마지막 줄에 'AYAAN_RESULT: <설계서 경로>'를 출력해."
)
DEV_TEMPLATE = (
    "{plan} 기획서대로 구현하고 테스트·빌드를 통과시켜. 저장소 규칙(CLAUDE.md/AGENTS.md)을 따라. "
    "완료되면 마지막 줄에 'AYAAN_RESULT: <한 줄 요약과 PR 링크>'를 출력해."
)
PROTOCOL = (
    "[ayaan 규칙]\n"
    "- 사용자 결정이 꼭 필요하면 추측하지 말고, 지금까지의 작업을 저장한 뒤 마지막 줄에\n"
    '  AYAAN_ASK: {"question":"...","options":["...","..."],"recommend":0,"context":"..."}\n'
    "  한 줄만 출력하고 종료해. options는 0~5개, 자유 답변이면 [].\n"
    '- 도구 실행이 "ayaan 승인 대기"로 거부되면 다른 우회 방법을 쓰지 말고 즉시 종료해.\n'
    "- 완료되면 마지막 줄에 AYAAN_RESULT: <한 줄 요약>"
)


def plan_doc_path(result: str | None, workspace: str) -> str | None:
    """First token of the plan's AYAAN_RESULT, if it names an existing file inside the workspace."""
    token = (result or "").split(maxsplit=1)[0] if (result or "").strip() else ""
    token = token.strip("`'\"")
    if not token:
        return None
    root = Path(workspace).resolve()
    p = (root / token).resolve()  # an absolute token replaces root
    return str(p.relative_to(root)) if p.is_relative_to(root) and p.is_file() else None


_ASK = re.compile(r"AYAAN_ASK:\s*(.*)")


def parse_ask(text: str | None) -> dict | None:
    """Last AYAAN_ASK marker -> {question, options, recommend, context}. Broken JSON falls back to
    the raw tail as a free-text question (safe side)."""
    found = None
    for m in _ASK.finditer(text or ""):
        found = m.group(1).strip()
    if found is None:
        return None
    try:
        o = json.loads(found)
        if not isinstance(o, dict) or not str(o.get("question") or "").strip():
            raise ValueError
    except ValueError:
        return {"question": (text or "")[-1000:], "options": [], "recommend": None, "context": None}
    opts = [str(x) for x in o.get("options") or [] if str(x).strip()][:5]
    rec = o.get("recommend")
    if not (isinstance(rec, int) and not isinstance(rec, bool) and 0 <= rec < len(opts)):
        rec = None
    ctx = o.get("context")
    return {
        "question": str(o["question"])[:2000],
        "options": [x[:100] for x in opts],
        "recommend": rec,
        "context": str(ctx)[:2000] if ctx else None,
    }


# ── permissions ────────────────────────────────────────────────────────────

CATEGORIES = {"FILE_WRITE", "SHELL", "GIT_PUSH", "DEPLOY", "NETWORK", "MCP"}
DECISIONS = ("ALLOW", "ASK", "DENY")  # ascending severity: ties go to the stricter one
_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
HOOK_MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|WebSearch|mcp__.*"


def validate_permissions(rows) -> dict[str, str]:
    if not isinstance(rows, list):
        return {"body": "must be a list"}
    errs: dict[str, str] = {}
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            errs[str(i)] = "must be an object"
            continue
        if r.get("toolCd") not in TOOLS | {"*"}:
            errs[f"{i}.toolCd"] = "must be * or a tool code"
        if r.get("category") not in CATEGORIES:
            errs[f"{i}.category"] = f"must be one of {sorted(CATEGORIES)}"
        m = r.get("matcher")
        if not isinstance(m, str) or not m.strip() or len(m) > 300:
            errs[f"{i}.matcher"] = "required, at most 300 characters"
        if r.get("decision") not in DECISIONS:
            errs[f"{i}.decision"] = f"must be one of {list(DECISIONS)}"
        p = r.get("priority", 0)
        if p is not None and (isinstance(p, bool) or not isinstance(p, int)):
            errs[f"{i}.priority"] = "must be an integer"
    return errs


def _prefix(cmd: str, matcher: str) -> bool:
    # token boundary: "git push" matches "git push origin", not "git pushx"
    return cmd == matcher or cmd.startswith(matcher + " ")


def _matches(category: str, matcher: str, target: str) -> bool:
    if matcher == "*":
        return True
    if category in ("SHELL", "GIT_PUSH", "DEPLOY"):
        return _prefix(target, " ".join(matcher.split()))
    if category == "NETWORK":
        return target == matcher or target.endswith("." + matcher)
    return fnmatch.fnmatchcase(target, matcher)  # FILE_WRITE glob, MCP server:tool


def classify(tool_name: str, tool_input: dict, policies: list[dict]) -> tuple[str, str] | None:
    """Claude tool call -> (category, target); None = read-only, always allowed."""
    inp = tool_input or {}
    if tool_name in _WRITE_TOOLS:
        return "FILE_WRITE", str(inp.get("file_path") or inp.get("notebook_path") or "")
    if tool_name == "Bash":
        cmd = " ".join(str(inp.get("command") or "").split())
        if _prefix(cmd, "git push"):
            return "GIT_PUSH", cmd
        deploy = (p["matcher"] for p in policies if p.get("category") == "DEPLOY")
        if any(m != "*" and _prefix(cmd, " ".join(m.split())) for m in deploy):
            return "DEPLOY", cmd
        return "SHELL", cmd
    if tool_name == "WebFetch":
        m = re.match(r"\w+://([^/:?#]+)", str(inp.get("url") or ""))
        return "NETWORK", (m[1].lower() if m else "")
    if tool_name == "WebSearch":
        return "NETWORK", ""
    if tool_name.startswith("mcp__"):
        _, _, rest = tool_name.partition("mcp__")
        server, _, name = rest.partition("__")
        return "MCP", f"{server}:{name}"
    return None


def permission_decision(
    tool: str, tool_name: str, tool_input: dict, policies: list[dict]
) -> tuple[str, str]:
    """-> (ALLOW|ASK|DENY, target). Tool rows beat * rows, then higher PRIORITY, then stricter.
    ponytail: no matching row = ALLOW (today's behaviour); seed rows cover every category.
    Prefix matching is a guardrail, not a sandbox (`sh -c "git push"` slips through)."""
    c = classify(tool_name, tool_input, policies)
    if c is None:
        return "ALLOW", ""
    category, target = c
    rows = [
        p
        for p in policies
        if p.get("category") == category
        and p.get("toolCd") in (tool, "*")
        and p.get("decision") in DECISIONS
        and _matches(category, p.get("matcher") or "", target)
    ]
    if not rows:
        return "ALLOW", target
    best = max(
        rows,
        key=lambda p: (
            p["toolCd"] != "*",
            p.get("priority") or 0,
            DECISIONS.index(p["decision"]),
        ),
    )
    return best["decision"], target


def input_hash(tool_name: str, tool_input: dict) -> str:
    raw = tool_name + json.dumps(tool_input or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()
