"""Replays a fixture to stdout (AYAAN_FAKE_SCENARIO, AYAAN_FAKE_SCENARIO_FILE).

Scenarios: ok, limit, ask (AYAAN_ASK question), plan (writes docs/fake-plan.md).
"""

import os
import sys
from pathlib import Path


def _scenario() -> str:
    f = os.environ.get("AYAAN_FAKE_SCENARIO_FILE")
    if not f:
        return os.environ.get("AYAAN_FAKE_SCENARIO", "ok")
    p = Path(f)
    lines = p.read_text().splitlines() if p.exists() else []
    if not lines:
        return "ok"
    p.write_text("".join(line + "\n" for line in lines[1:]))
    return lines[0].strip() or "ok"


def main() -> int:
    sys.stdin.read()
    name = _scenario()
    if name == "plan":  # the plan scenario leaves a real plan file in the workspace (cwd)
        Path("docs").mkdir(exist_ok=True)
        Path("docs/fake-plan.md").write_text("# FAKE 기획서\n")
    sys.stdout.write((Path(__file__).parent / "fake_fixtures" / f"{name}.jsonl").read_text())
    return 1 if name == "limit" else 0


if __name__ == "__main__":
    sys.exit(main())
