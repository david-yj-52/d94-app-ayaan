# d94-app-ayaan

Python service that runs AI CLI tasks (claude/codex/cursor/antigravity; gemini kept but unused) claimed from d94-query.
Spec: /Users/davidkim/Documents/Brain/projects/david94/feature/app_ayaan/phase1/2026-11-10-ayaan-phase1-design.md

| Module | Role |
|---|---|
| `ayaan/settings.py` | `Settings` (env prefix `AYAAN_`) |
| `ayaan/clients.py` | `QueryClient` (d94-query internal API), `EccClient` (notify) |
| `ayaan/rules.py` | Reset rules, policy resolution, config/tool/workspace validation, prompt rendering |
| `ayaan/adapters.py` | Per-CLI argv and output parsing, `TESTED_VERSIONS` |
| `ayaan/runner.py` | CLI subprocess run, timeout, `kill_task`, result spool |
| `ayaan/quota.py` | Quota collectors (`collect_all`), budget rows |
| `ayaan/scheduler.py` | `Scheduler`: claim, run, lease, pause/resume, notifications, reports |
| `ayaan/api.py` | `create_app()` factory: public API `/tsh/api/v1/ayaan/**` (auth `X-AYAAN-Token` or HS256 JWT), `/actuator/health`, lifespan (Eureka + scheduler) |
| `ayaan/plans.py` | Milestone plan state machine (`advance_plans` from `tick`, `step`, `resume`, `targets`) |
| `ayaan/git.py` | Clone/worktree/branch/merge/rebase git ops for tasks and plans |
| `ayaan/plugins.py` | Claude plugin install from config `CLAUDE_PLUGINS` (`POST /plugins/sync`, `GET /plugins`, boot sync) |
| `ayaan/setup_check.py` | `GET /setup/check` steps 1..6 (presence/status only, never env values) |
| `ayaan/antigravity.py` | `ANTIGRAVITY` tool: `python -m` CLI and quota via the running Antigravity app's local language server (unofficial Connect-RPC) |
| `ayaan/fake_cli.py` | `FAKE` tool CLI for `AYAAN_PROFILE=test` |
| `tests/fakes.py` | `InMemoryQuery`, `FakeEcc`, `FakeClock`, `fake_runner` |

Dev: `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'`, `.venv/bin/pytest -q`, `.venv/bin/ruff check .`
