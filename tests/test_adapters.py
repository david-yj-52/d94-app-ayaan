import json
from pathlib import Path

import pytest

from ayaan.adapters import ADAPTERS, TESTED_VERSIONS

ROOT = Path(__file__).parent.parent
FIXTURES = [
    p
    for p in sorted((ROOT / "tests/fixtures").glob("*/*.jsonl"))
    + sorted((ROOT / "ayaan/fake_fixtures").glob("*.jsonl"))
    if p.with_suffix(".expected.json").exists()
]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_adapter_parses_fixture(fixture):
    tool = "FAKE" if fixture.parent.name == "fake_fixtures" else fixture.parent.name.upper()
    expected = json.loads(fixture.with_suffix(".expected.json").read_text())
    ev = ADAPTERS[tool].parse(fixture.read_text().splitlines())
    assert ev.session_id == expected["session_id"]
    assert (ev.input_tokens, ev.output_tokens) == (
        expected["input_tokens"],
        expected["output_tokens"],
    )
    assert (ev.cache_read_tokens, ev.cache_write_tokens) == (
        expected["cache_read_tokens"],
        expected["cache_write_tokens"],
    )
    assert ev.cost_usd == expected["cost_usd"]
    assert ev.limit_hit is expected["limit_hit"]
    got = ev.resets_at.isoformat() if ev.resets_at else None
    want = expected["resets_at"]
    assert got == (want.replace("Z", "+00:00") if want else None)
    assert ev.result_line == expected["result_line"]


def test_argv_and_versions():
    assert set(ADAPTERS) == {"CLAUDE", "CODEX", "GEMINI", "CURSOR", "FAKE"}
    assert set(TESTED_VERSIONS) >= {"CLAUDE", "CODEX", "GEMINI"}
    c = ADAPTERS["CLAUDE"]
    assert "--dangerously-skip-permissions" in c.new_argv(None)
    assert c.resume_argv("sid", "m")[c.resume_argv("sid", "m").index("--resume") + 1] == "sid"
    assert ADAPTERS["CODEX"].new_argv(None)[-1] == "-"
    assert ADAPTERS["CODEX"].resume_argv("tid", None)[:3] == ["exec", "resume", "tid"]


def test_session_missing_detected():
    ev = ADAPTERS["CLAUDE"].parse(["No conversation found with session ID: abc"])
    assert ev.session_missing and not ev.limit_hit


def test_prompt_in_argv_only_for_cursor():
    assert {t for t, a in ADAPTERS.items() if a.prompt_in_argv} == {"CURSOR"}
