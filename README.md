# d94-app-ayaan

Runs AI CLI tasks (claude / codex / gemini / cursor) for CIRA issues or free prompts, pauses them when a tool's quota runs out and resumes them in the same CLI session after the reset. State lives in d94-query; notifications go through ECC (Telegram); d94-web and ECC call the public API at `/tsh/api/v1/ayaan/**` via the gateway.

Spec: `Brain/projects/david94/feature/app_ayaan/phase1/2026-11-10-ayaan-phase1-design.md` (API §5.1, security §8.2, setup appendix B).

## Run locally

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env   # fill in tokens
.venv/bin/uvicorn ayaan.api:app --port 8080
.venv/bin/pytest -q && .venv/bin/ruff check .
```

`AYAAN_PROFILE=test` enables the `FAKE` tool (no real CLI calls). Deployment: `docker compose up -d` (see `docker-compose.yml`).

## Environment (`AYAAN_` prefix)

| Variable | Purpose |
|---|---|
| `QUERY_BASE_URL`, `QUERY_SERVICE_TOKEN` | d94-query internal API (its scheme+host+port is also the gateway used by setup step 5) |
| `ECC_BASE_URL`, `ECC_TOKEN` | ECC notify |
| `API_TOKEN` | service callers send it as `X-AYAAN-Token`; empty rejects all service calls |
| `JWT_SECRET_KEY` | base64 HS256 key for web JWTs (`Authorization: Bearer`) |
| `WORKSPACE_ROOTS` | comma list of allowed workspace roots |
| `CLI_EXECUTABLES` | optional `claude=/path,codex=/path,...` overrides |
| `DATA_DIR` | logs and result spool |
| `PROFILE` | `test` enables `FAKE` |
| `EUREKA_ENABLED`, `EUREKA_URL`, `EUREKA_INSTANCE_HOST`, `EUREKA_INSTANCE_PORT` | service registration |

API keys for `authMode=API_KEY` tools are plain env vars named by the tool's `apiKeyRef` (e.g. `AYAAN_ANTHROPIC_API_KEY`); they are never stored or returned.
