"""Quota collectors. Nothing here calls a model."""

import json
import logging
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ayaan.adapters import RunEvent
from ayaan.clients import EccClient, QueryClient, from_query_ts, to_query_ts
from ayaan.rules import resolve_resume_at
from ayaan.settings import Settings

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CURSOR_USAGE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
GEMINI_API = "https://cloudcode-pa.googleapis.com/v1internal:"
_GEMINI_CONST = re.compile(r'var (OAUTH_CLIENT_(?:ID|SECRET)) = "([^"]+)"')
_GEMINI_META = {
    "ideType": "IDE_UNSPECIFIED",
    "platform": "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI",
}
CLAUDE_WARNING = "Claude 비공식 usage 조회 실패 — 자동으로 껐습니다 (실행 결과 기반으로만 갱신)"
_WINDOW_KO = {
    "FIVE_HOUR": "5시간",
    "WEEKLY": "주간",
    "DAILY": "일간",
    "MONTHLY": "월간",
    "BUDGET": "예산",
}
_NAMES = {"five_hour": "FIVE_HOUR", "seven_day": "WEEKLY", "weekly": "WEEKLY"}


class QuotaUnavailable(Exception):
    pass


def _row(tool, window, used_pct, resets_at, source, now, **extra) -> dict:
    status = "UNKNOWN" if used_pct is None else ("EXHAUSTED" if used_pct >= 100 else "AVAILABLE")
    row = {
        "toolCd": tool,
        "windowCd": window,
        "usedPct": used_pct,
        "resetsAt": to_query_ts(resets_at) if resets_at else None,
        "status": status,
        "source": source,
        "observedAt": to_query_ts(now),
    }
    row.update(extra)
    return row


def _window_from_minutes(minutes) -> str | None:
    if not isinstance(minutes, (int, float)):
        return None
    if abs(minutes - 300) <= 30:
        return "FIVE_HOUR"
    if abs(minutes - 10080) <= 720:
        return "WEEKLY"
    if abs(minutes - 43200) <= 1440:  # codex free plan: one 30-day window
        return "MONTHLY"
    return None


def _token(path: Path) -> str:
    data = json.loads(path.read_text())
    tok = (data.get("claudeAiOauth") or data).get("accessToken")
    if not tok:
        raise QuotaUnavailable("no access token")
    return tok


async def collect_claude(http: httpx.AsyncClient, credentials_path: Path) -> list[dict]:
    try:
        headers = {
            "Authorization": f"Bearer {_token(credentials_path)}",
            "anthropic-beta": "oauth-2025-04-20",
        }
        resp = await http.get(USAGE_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        now = datetime.now(UTC)
        rows = []
        for key, window in (("five_hour", "FIVE_HOUR"), ("seven_day", "WEEKLY")):
            w = body.get(key)
            if not w or w.get("utilization") is None:
                continue
            at = from_query_ts(w["resets_at"]) if w.get("resets_at") else None
            rows.append(_row("CLAUDE", window, float(w["utilization"]), at, "USAGE_API", now))
    except QuotaUnavailable:
        raise
    except Exception as e:  # never include the token / request headers
        raise QuotaUnavailable(type(e).__name__) from None
    if not rows:
        raise QuotaUnavailable("no usable windows")
    return rows


async def collect_cursor(http: httpx.AsyncClient, auth_path: Path) -> list[dict]:
    """Cursor dashboard usage with the agent CLI's own token (unofficial, same as the IDE)."""
    try:
        tok = json.loads(Path(auth_path).read_text())["accessToken"]
        resp = await http.post(
            CURSOR_USAGE_URL,
            headers={
                "Authorization": f"Bearer {tok}",
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
            },
            content=b"{}",
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        pct = (body.get("planUsage") or {}).get("totalPercentUsed")
        end = body.get("billingCycleEnd")
        at = datetime.fromtimestamp(int(end) / 1000, UTC) if end else None
    except Exception as e:  # never include the token / request headers
        raise QuotaUnavailable(type(e).__name__) from None
    if pct is None:
        return []
    return [_row("CURSOR", "MONTHLY", float(pct), at, "USAGE_API", datetime.now(UTC))]


@lru_cache(maxsize=1)
def _gemini_oauth() -> dict:
    """gemini-cli's public installed-app OAuth client, read from the installed CLI bundle
    (kept out of the repo: push protection flags it, and it follows the CLI version)."""
    exe = shutil.which("gemini")
    found = {}
    for f in Path(exe).resolve().parent.glob("*.js") if exe else ():
        found.update(_GEMINI_CONST.findall(f.read_text(errors="ignore")))
        if len(found) == 2:
            return {
                "client_id": found["OAUTH_CLIENT_ID"],
                "client_secret": found["OAUTH_CLIENT_SECRET"],
            }
    raise QuotaUnavailable("gemini-cli oauth client not found")


async def collect_gemini(
    http: httpx.AsyncClient, creds_path: Path, model: str | None = None
) -> list[dict]:
    """Code Assist quota for a Google-login gemini-cli (oauth_creds.json; API-key mode has none)."""
    try:
        c = json.loads(Path(creds_path).read_text())
        tok = c.get("access_token")
        if not tok or (c.get("expiry_date") or 0) / 1000 < time.time() + 60:
            # ponytail: refreshed token is not written back; the CLI refreshes its own file
            r = await http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    **_gemini_oauth(),
                    "grant_type": "refresh_token",
                    "refresh_token": c["refresh_token"],
                },
                timeout=10,
            )
            r.raise_for_status()
            tok = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        r = await http.post(
            GEMINI_API + "loadCodeAssist", json={"metadata": _GEMINI_META}, headers=h, timeout=10
        )
        r.raise_for_status()
        project = r.json().get("cloudaicompanionProject")
        if not project:
            raise QuotaUnavailable("no code assist project")
        r = await http.post(
            GEMINI_API + "retrieveUserQuota", json={"project": project}, headers=h, timeout=10
        )
        r.raise_for_status()
        buckets = [
            b for b in r.json().get("buckets") or [] if b.get("remainingFraction") is not None
        ]
    except QuotaUnavailable:
        raise
    except Exception as e:  # never include the token / request headers
        raise QuotaUnavailable(type(e).__name__) from None
    # ponytail: one DAILY row (WINDOW_CD is 20 chars, model ids don't fit): the tool's model
    # bucket if set, else the least-used one, so a single exhausted model never blocks the CLI
    # that falls back to another. Per-model rows if the UI ever needs them.
    pick = [b for b in buckets if model and b.get("modelId") == model] or buckets
    if not pick:
        return []
    b = max(pick, key=lambda b: b["remainingFraction"])
    at = from_query_ts(b["resetTime"]) if b.get("resetTime") else None
    used = round((1 - float(b["remainingFraction"])) * 100, 1)
    return [_row("GEMINI", "DAILY", used, at, "USAGE_API", datetime.now(UTC))]


def collect_codex(sessions_dir: Path) -> list[dict]:
    files = [f for f in Path(sessions_dir).glob("**/*.jsonl") if f.is_file()]
    if not files:
        return []
    latest = max(files, key=lambda f: f.stat().st_mtime)
    limits = None
    try:
        lines = latest.read_text().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        log.warning("codex session log unreadable: %s", type(e).__name__)
        return []
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        p = o.get("payload") or {}
        if p.get("type") == "token_count":
            limits = p.get("rate_limits") or p.get("rateLimits") or limits
    if not limits:
        return []
    now, rows = datetime.now(UTC), []
    for slot, default in (("primary", "FIVE_HOUR"), ("secondary", "WEEKLY")):
        w = limits.get(slot)
        if not w:
            continue
        pct = w.get("used_percent", w.get("usedPercent"))
        mins = w.get("window_minutes", w.get("windowMinutes"))
        ts = w.get("resets_at", w.get("resetsAt"))
        if pct is None:
            continue
        at = datetime.fromtimestamp(ts, UTC) if ts else None
        rows.append(
            _row("CODEX", _window_from_minutes(mins) or default, float(pct), at, "SESSION_LOG", now)
        )
    return rows


def quotas_from_event(tool: str, ev: RunEvent, rule: str, now: datetime) -> list[dict]:
    rows: dict[str, dict] = {}
    for rl in ev.rate_limits:
        w = _NAMES.get(str(rl.get("window") or "").lower()) or _window_from_minutes(
            rl.get("window_minutes")
        )
        if w:
            rows[w] = _row(tool, w, rl.get("used_pct"), rl.get("resets_at"), "CLI_EVENT", now)
    if ev.limit_hit:
        w = next((k for k, r in rows.items() if r["status"] == "EXHAUSTED"), None)
        if w is None:
            w = next(iter(rows), "DAILY") if len(rows) == 1 else "DAILY"
        try:
            at = resolve_resume_at(ev.resets_at, rule, now)
        except (
            ValueError
        ):  # ponytail: bad FALLBACK_RESET_RULE -> retry in 30 min, no per-rule recovery
            at = now + timedelta(minutes=30)
        rows[w] = {**_row(tool, w, 100.0, at, "LIMIT_ERROR", now), "status": "EXHAUSTED"}
    return list(rows.values())


def budget_quota(tool_row: dict, period_tokens: int, now: datetime) -> dict | None:
    budget = tool_row.get("budgetTokens")
    if tool_row.get("authMode") != "API_KEY" or not budget:
        return None
    exceeded = period_tokens >= budget
    today = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    period = tool_row.get("budgetPeriod")
    if period == "DAILY":
        at = today + timedelta(days=1)
    elif period == "MONTHLY":
        at = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    else:
        return None
    row = _row(
        tool_row["toolCd"],
        "BUDGET",
        round(period_tokens * 100 / budget, 1),
        at,
        "ACCOUNTING",
        now,
        usedTokens=period_tokens,
        limitTokens=budget,
    )
    row["status"] = "EXHAUSTED" if exceeded else "AVAILABLE"
    return row


def expire_past_resets(rows: list[dict], now: datetime) -> list[dict]:
    out = []
    for r in rows:
        if (
            r.get("status") == "EXHAUSTED"
            and r.get("resetsAt")
            and from_query_ts(r["resetsAt"]) <= now
        ):
            r = {**r, "status": "UNKNOWN"}
        out.append(r)
    return out


def _kst(ts: str | None) -> str:
    return (
        from_query_ts(ts).astimezone(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M")
        if ts
        else "미정"
    )


async def apply_quotas(
    query: QueryClient, ecc: EccClient, tool: str, rows: list[dict], notify_events: set[str]
) -> None:
    prev = {(r["toolCd"], r["windowCd"]): r.get("status") for r in await query.get_quotas() or []}
    for r in rows:
        body = {k: v for k, v in r.items() if k not in ("toolCd", "windowCd")}
        await query.put_quota(tool, r["windowCd"], body)
        old, new = prev.get((tool, r["windowCd"])), r["status"]
        label = f"{tool} {_WINDOW_KO.get(r['windowCd'], r['windowCd'])} 한도"
        if new == "EXHAUSTED" and old == "AVAILABLE" and "QUOTA_EXHAUSTED" in notify_events:
            msg = f"{label} 소진 — {_kst(r.get('resetsAt'))} 재설정"
            await ecc.notify("QUOTA_EXHAUSTED", msg, msg)
        elif new == "AVAILABLE" and old == "EXHAUSTED" and "QUOTA_RESTORED" in notify_events:
            msg = f"{label} 복구"
            await ecc.notify("QUOTA_RESTORED", msg, msg)


async def collect_all(
    query: QueryClient, ecc: EccClient, settings: Settings, now: datetime
) -> list[str]:
    warnings: list[str] = []
    cfg = await query.get_config() or {}
    events = {e.strip() for e in cfg.get("NOTIFY_EVENTS", "").split(",") if e.strip()}
    tools = {t["toolCd"]: t for t in await query.get_tools() or []}

    # persist EXHAUSTED -> UNKNOWN for rows whose reset has passed (no notification; the next
    # collected/observed row settles the real status)
    current = await query.get_quotas() or []
    for old, new in zip(current, expire_past_resets(current, now)):
        if new["status"] != old["status"]:
            await query.put_quota(
                new["toolCd"],
                new["windowCd"],
                {k: v for k, v in new.items() if k not in ("toolCd", "windowCd")},
            )

    if tools.get("CLAUDE", {}).get("enabled") and cfg.get("CLAUDE_USAGE_POLL") == "true":
        try:
            async with httpx.AsyncClient() as http:
                rows = await collect_claude(http, Path(settings.claude_credentials_path))
            await apply_quotas(query, ecc, "CLAUDE", rows, events)
        except QuotaUnavailable as e:
            log.warning("claude usage poll failed: %s", e)
            await query.put_config({"CLAUDE_USAGE_POLL": "false"})
            warnings.append(CLAUDE_WARNING)
    if tools.get("CODEX", {}).get("enabled"):
        rows = collect_codex(Path(settings.codex_sessions_dir))
        if rows:
            await apply_quotas(query, ecc, "CODEX", rows, events)
    for cd, fn, path in (
        ("GEMINI", collect_gemini, settings.gemini_creds_path),
        ("CURSOR", collect_cursor, settings.cursor_auth_path),
    ):
        t = tools.get(cd) or {}
        if not t.get("enabled") or t.get("authMode") == "API_KEY":  # API_KEY: BUDGET rows
            continue
        try:
            async with httpx.AsyncClient() as http:
                args = (t.get("model"),) if cd == "GEMINI" else ()
                rows = await fn(http, Path(path), *args)
        except QuotaUnavailable as e:
            log.warning("%s usage poll failed: %s", cd.lower(), e)
            continue
        if rows:
            await apply_quotas(query, ecc, cd, rows, events)
    return warnings
