# d94-app-ayaan

Python service that runs AI CLI tasks (claude/codex/gemini/cursor) claimed from d94-query.
Spec: /Users/davidkim/Documents/Brain/projects/david94/feature/app_ayaan/phase1/2026-11-10-ayaan-phase1-design.md

| Module | Role |
|---|---|
| `ayaan/settings.py` | `Settings` (env prefix `AYAAN_`) |
| `ayaan/clients.py` | `QueryClient` (d94-query internal API), `EccClient` (notify) |
| `ayaan/api.py` | FastAPI app, `/actuator/health`, Eureka lifespan |

Dev: `python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'`, `.venv/bin/pytest -q`, `.venv/bin/ruff check .`
