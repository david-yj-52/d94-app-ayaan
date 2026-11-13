# d94-app-ayaan

Python service that runs AI CLI tasks (claude/codex/gemini/cursor) claimed from d94-query.
Spec: /Users/davidkim/Documents/Brain/projects/david94/feature/app_ayaan/phase1/2026-11-10-ayaan-phase1-design.md
TSTA-1 (skills, human requests, plan→dev): `docs/specs/2026-11-12-ayaan-skills-approvals-design.md`

| Module | Role |
|---|---|
| `ayaan/settings.py` | `Settings` (env prefix `AYAAN_`) |
| `ayaan/clients.py` | `QueryClient` (d94-query internal API), `EccClient` (notify) |
| `ayaan/rules.py` | Reset rules, policy resolution, config/tool/workspace validation, prompt rendering; skills (`resolve_skill`, `validate_skill`), `PLAN_TEMPLATE`/`DEV_TEMPLATE`/`PROTOCOL`, `parse_ask`, `plan_doc_path`, `permission_decision` (pure) |
| `ayaan/adapters.py` | Per-CLI argv and output parsing, `TESTED_VERSIONS` |
| `ayaan/runner.py` | CLI subprocess run, timeout, `kill_task`, result spool |
| `ayaan/quota.py` | Quota collectors (`collect_all`), budget rows |
| `ayaan/scheduler.py` | `Scheduler`: claim, run, lease, pause/resume, notifications, reports; WAITING (human requests), skill plugin check, answer-based resume prompt, PLAN done → `create_dev_task` |
| `ayaan/hook.py` | `ayaan-hook` console script: Claude PreToolUse hook → `POST /hook/pre-tool` (per-run token, localhost, fail-closed) |
| `ayaan/api.py` | `create_app()` factory: public API `/tsh/api/v1/ayaan/**` (auth `X-AYAAN-Token` or HS256 JWT), `/actuator/health`, lifespan (Eureka + scheduler) |
| `ayaan/plugins.py` | Claude plugin install from config `CLAUDE_PLUGINS` (`POST /plugins/sync`, `GET /plugins`, boot sync) |
| `ayaan/setup_check.py` | `GET /setup/check` steps 1..7 (presence/status only, never env values; 7 = CLI session retention) |
| `ayaan/fake_cli.py` | `FAKE` tool CLI for `AYAAN_PROFILE=test` |
| `tests/fakes.py` | `InMemoryQuery`, `FakeEcc`, `FakeClock`, `fake_runner` |

Dev: `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'`, `.venv/bin/pytest -q`, `.venv/bin/ruff check .`
