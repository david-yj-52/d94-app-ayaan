"""CLI adapters: argv builders and output parsers. Plain classes, same method names."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

# versions pinned in docs/cli-probe.md (Cursor installs latest; unverified)
TESTED_VERSIONS = {
    "CLAUDE": "2.1.322",
    "CODEX": "0.167.1",
    "GEMINI": "0.68.0",
    "CURSOR": "unverified",
}

LIMIT_PATTERNS = {
    "CLAUDE": re.compile(r"hit your limit|usage limit reached", re.I),
    "CODEX": re.compile(r"hit your usage limit", re.I),
    "GEMINI": re.compile(r"exhausted your capacity|quota will reset", re.I),
    "CURSOR": re.compile(r"hit your usage limit", re.I),
}
SESSION_MISSING = re.compile(
    r"no conversation found|session .*not found|no (saved )?session|no rollout found", re.I
)
_RESULT = re.compile(r"AYAAN_RESULT:\s*(.*)")


@dataclass
class RunEvent:
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    limit_hit: bool = False
    resets_at: datetime | None = None
    result_line: str | None = None
    session_missing: bool = False
    rate_limits: list[dict] = field(default_factory=list)
    json_seen: bool = False  # parse saw at least one JSON object


def _scan(lines):
    """-> (json objects, non-json text lines). Non-JSON lines (stderr, plain errors) are kept."""
    objs, plain = [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            o = None
        (objs if isinstance(o, dict) else plain).append(o if isinstance(o, dict) else line)
    return objs, plain


def _result_line(text):
    found = None
    for m in _RESULT.finditer(text or ""):
        found = m.group(1).strip()
    return found


def _finish(tool, ev, texts, text_for_result):
    """Shared tail: limit/session_missing from collected error texts, result_line."""
    blob = "\n".join(texts)
    if LIMIT_PATTERNS[tool].search(blob):
        ev.limit_hit = True
    if SESSION_MISSING.search(blob):
        ev.session_missing = True
    ev.result_line = _result_line(text_for_result)
    return ev


class Claude:
    prompt_in_argv = False
    tool = "CLAUDE"

    def _base(self, model):
        argv = ["-p", "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions"]
        return argv + (["--model", model] if model else [])

    def new_argv(self, model):
        return self._base(model)

    def resume_argv(self, session_id, model):
        return ["--resume", session_id] + self._base(model)

    def parse(self, lines):
        objs, plain = _scan(lines)
        ev, errs, result = RunEvent(), list(plain), ""
        for o in objs:
            ev.session_id = o.get("session_id") or ev.session_id
            t = o.get("type")
            if t == "rate_limit_event":
                info = o.get("rate_limit_info") or {}
                if info.get("resetsAt"):
                    at = datetime.fromtimestamp(info["resetsAt"], UTC)
                    ev.rate_limits.append(
                        {"window": info.get("rateLimitType"), "used_pct": None, "resets_at": at}
                    )
                    if info.get("status") == "rejected":
                        ev.limit_hit, ev.resets_at = True, at
                elif info.get("status") == "rejected":
                    ev.limit_hit = True
            elif t == "result":
                result = o.get("result") or ""
                u = o.get("usage") or {}
                ev.input_tokens = u.get("input_tokens", 0)  # excludes cache
                ev.output_tokens = u.get("output_tokens", 0)
                ev.cache_read_tokens = u.get("cache_read_input_tokens", 0)
                ev.cache_write_tokens = u.get("cache_creation_input_tokens", 0)
                ev.cost_usd = o.get("total_cost_usd")
                if o.get("is_error"):
                    errs.append(result)
        ev.json_seen = bool(objs)
        return _finish("CLAUDE", ev, errs, result)


class Codex:
    prompt_in_argv = False
    tool = "CODEX"
    _FLAGS = ["--json", "--dangerously-bypass-approvals-and-sandbox"]

    def new_argv(self, model):
        return (["exec"] + self._FLAGS + ["--skip-git-repo-check"]
                + (["-m", model] if model else []) + ["-"])

    def resume_argv(self, session_id, model):
        return ["exec", "resume", session_id] + self._FLAGS + (
            ["-m", model] if model else []) + ["-"]

    def parse(self, lines):
        objs, plain = _scan(lines)
        ev, errs, last = RunEvent(), list(plain), ""
        for o in objs:
            t = o.get("type")
            if t == "thread.started":
                ev.session_id = o.get("thread_id")
            elif t == "item.completed":
                item = o.get("item") or {}
                if item.get("type") == "agent_message":
                    last = item.get("text") or ""
            elif t == "turn.completed":
                u = o.get("usage") or {}
                # input_tokens already includes cached tokens
                ev.input_tokens = u.get("input_tokens", 0)
                ev.output_tokens = u.get("output_tokens", 0)
                ev.cache_read_tokens = u.get("cached_input_tokens", 0)
            elif t == "error":
                errs.append(o.get("message") or "")
            elif t == "turn.failed":
                errs.append((o.get("error") or {}).get("message") or "")
        # ponytail: codex limit text has a tz-less date, so resets_at stays None
        ev.json_seen = bool(objs)
        return _finish("CODEX", ev, errs, last)


class Gemini:
    prompt_in_argv = False
    tool = "GEMINI"

    def _base(self, model):
        # -p value is appended to stdin input; a blank (truthy) value forces headless mode
        return ["-o", "stream-json", "--yolo"] + (["-m", model] if model else []) + ["-p", " "]

    def new_argv(self, model):
        return self._base(model)

    def resume_argv(self, session_id, model):
        return ["--resume", session_id] + self._base(model)

    def parse(self, lines):
        objs, plain = _scan(lines)
        ev, errs, msgs = RunEvent(), list(plain), []
        for o in objs:
            t = o.get("type")
            if t == "init":
                ev.session_id = o.get("session_id")
            elif t == "message" and o.get("role") == "assistant":
                msgs.append(o.get("content") or "")
            elif t == "error":
                errs.append(o.get("message") or "")
            elif t == "result":
                s = o.get("stats") or {}
                # input_tokens includes cached
                ev.input_tokens = s.get("input_tokens", 0)
                ev.output_tokens = s.get("output_tokens", 0)
                ev.cache_read_tokens = s.get("cached", 0)
                if o.get("error"):
                    errs.append(o["error"].get("message") or "")
        ev.json_seen = bool(objs)
        return _finish("GEMINI", ev, errs, "".join(msgs))


class Cursor:
    # ponytail: prompt is the last argv item (no shell); Linux caps one arg at 128KB,
    # upgrade path: stdin if Cursor ever documents it
    prompt_in_argv = True
    tool = "CURSOR"

    def _base(self, model):
        return ["-p", "--force", "--output-format", "stream-json"] + (
            ["--model", model] if model else [])

    def new_argv(self, model):
        return self._base(model)

    def resume_argv(self, session_id, model):
        return ["--resume", session_id] + self._base(model)

    def parse(self, lines):
        objs, plain = _scan(lines)
        ev, errs, result = RunEvent(), list(plain), ""
        for o in objs:
            ev.session_id = o.get("session_id") or ev.session_id
            if o.get("type") == "result":
                result = o.get("result") or ""
                if o.get("is_error"):
                    errs.append(result)
        ev.json_seen = bool(objs)
        return _finish("CURSOR", ev, errs, result)


class Fake:
    prompt_in_argv = False
    tool = "FAKE"

    def new_argv(self, model):
        return ["-m", "ayaan.fake_cli"]

    def resume_argv(self, session_id, model):
        return ["-m", "ayaan.fake_cli"]

    def parse(self, lines):
        return Claude().parse(lines)


ADAPTERS = {
    "CLAUDE": Claude(),
    "CODEX": Codex(),
    "GEMINI": Gemini(),
    "CURSOR": Cursor(),
    "FAKE": Fake(),
}
