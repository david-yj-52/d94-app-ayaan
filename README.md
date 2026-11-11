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
| `JWT_SECRET_KEY` | d94-query's base64 `JWT_SECRET_KEY` (same value as d94-query `.env`), HS256 key for web JWTs (`Authorization: Bearer`) |
| `JWT_ALLOWED_SUBJECTS` | comma list of JWT `sub` values (d94 login email) allowed to call the API. d94 registration is open, so any other valid JWT gets 403; empty rejects every JWT (the service token still works) |
| `WORKSPACE_ROOTS` | comma list of allowed workspace roots |
| `CLI_EXECUTABLES` | optional `claude=/path,codex=/path,...` overrides |
| `DATA_DIR` | logs and result spool |
| `PROFILE` | `test` enables `FAKE` |
| `EUREKA_ENABLED`, `EUREKA_URL`, `EUREKA_INSTANCE_HOST`, `EUREKA_INSTANCE_PORT` | service registration |

API keys for `authMode=API_KEY` tools are plain env vars named by the tool's `apiKeyRef` (e.g. `AYAAN_ANTHROPIC_API_KEY`); they are never stored or returned.

## Container user and bind mounts

The image starts as root only for `docker/entrypoint.sh`: it creates `/home/ayaan`, `/data` and `/workspace`, `chown`s `/home/ayaan` and `/data` (recursive) and `/workspace` (top level only) to uid 10001, sets `git config --system safe.directory '*'` (host-owned repos under `/workspace`), then `exec setpriv` drops to `ayaan` (10001) for uvicorn and every CLI. So root-owned host directories no longer break `claude` login, logs/spool or git. Repos inside `/workspace` must be writable by uid 10001 (or group/other).

## FAKE로 중단·재개 시험하기

Real CLIs are not called; the `FAKE` tool replays fixtures and pops one scenario line per run.

1. `.env`: `AYAAN_PROFILE=test`, `AYAAN_FAKE_SCENARIO_FILE=/data/fake-scenario`, then `docker compose up -d`.
2. d94-web 설정 → Tool 탭: `FAKE` 활성화, `FALLBACK_RESET_RULE` = `INTERVAL 2m`.
3. Workspace 탭: 대상 프로젝트 매핑의 `DEFAULT_TOOL` = `FAKE`.
4. `printf 'limit\nok\n' > ./data/fake-scenario` (host side of `/data`).
5. d94-web에서 작업 생성 (free prompt, 해당 프로젝트). Expected Telegram sequence: `PAUSED` (run 1 hits the limit) → about 2 minutes later `RESUMED` → `DONE` (run 2, same session).
