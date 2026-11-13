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

Run every setup command as `docker exec -u ayaan -it d94-app-ayaan …`: the image's default user is root (for the entrypoint), so a plain `docker exec` would write root-owned 0600 login/git files into `/home/ayaan` that uvicorn (uid 10001) cannot read.

The image starts as root only for `docker/entrypoint.sh`: it creates `/home/ayaan`, `/data` and `/workspace`, `chown`s `/home/ayaan` and `/data` (recursive) and `/workspace` (top level only) to uid 10001, sets `git config --system safe.directory '*'` (host-owned repos under `/workspace`), then `exec setpriv` drops to `ayaan` (10001) for uvicorn and every CLI. So root-owned host directories no longer break `claude` login, logs/spool or git. Repos inside `/workspace` must be writable by uid 10001 (or group/other).

## FAKE로 중단·재개 시험하기

Real CLIs are not called; the `FAKE` tool replays fixtures and pops one scenario line per run.

1. `.env`: `AYAAN_PROFILE=test`, `AYAAN_FAKE_SCENARIO_FILE=/data/fake-scenario`, then `docker compose up -d`.
2. d94-web 설정 → Tool 탭: `FAKE` 활성화, `FALLBACK_RESET_RULE` = `INTERVAL 2m`.
3. Workspace 탭: 대상 프로젝트 매핑의 `DEFAULT_TOOL` = `FAKE`.
4. `printf 'limit\nok\n' > ./data/fake-scenario` (host side of `/data`).
5. d94-web에서 작업 생성 (free prompt, 해당 프로젝트). Expected Telegram sequence: `PAUSED` (run 1 hits the limit) → about 2 minutes later `RESUMED` → `DONE` (run 2, same session).

## 사람에게 묻기 · 권한 승인 · 기획→개발 (TSTA-1)

설계: `docs/specs/2026-11-12-ayaan-skills-approvals-design.md`.

- **상태 `WAITING`**: 사람 응답 대기. 스케줄러는 절대 claim하지 않고 프로세스·리스도 없다. 답(Telegram 버튼·답장·`/answer`, 웹)이 곧 재개다(`WAITING → QUEUED`, 같은 CLI 세션).
- **질문**: 모델이 마지막 줄에 `AYAAN_ASK: {"question":…,"options":[…]}`를 내고 종료하면 QUESTION 요청이 생긴다. 규칙 블록은 Claude는 `--append-system-prompt`, 다른 CLI는 프롬프트 끝에 붙는다.
- **권한**: `SN_AYN_PERMISSION`(Tool·범주·매처·ALLOW/ASK/DENY)을 `rules.permission_decision`이 평가한다. Claude만 PreToolUse hook(`ayaan-hook`)으로 실행 중에 묻는다. ASK면 PERMISSION 요청을 만들고 거부한 뒤 "즉시 종료"를 지시하며, 승인은 같은 `(task, 입력 해시)` 호출 **1회**에만 유효하다. Codex·Gemini·Cursor는 아직 정책을 적용하지 않는다(기존 전체 허용 플래그 유지).
- **skill**: `SN_AYN_SKILL.INVOKE_TEXT`가 새 실행 프롬프트 맨 앞에 붙는다(작업 `skillCd` > Tool의 단계별 기본값, `NONE` = 사용 안 함). Claude skill의 플러그인이 설치되어 있지 않으면 `CLAUDE_PLUGINS` 동기화를 1회 시도하고, 그래도 없으면 `FAILED (SKILL_PLUGIN_MISSING)`로 끝낸다.
- **기획→개발**: 새 작업의 기본 단계는 `PLAN`이다. 기획이 `AYAAN_RESULT: <설계서 경로>`로 끝나면 DONE이 되고, 개발 작업이 `WAITING`(START 요청)으로 생긴다. Telegram/웹에서 [실행]을 눌러야 시작한다.
- **운영 지침**: 명령 접두어 매칭은 우회할 수 있다(`sh -c "git push"`). 정책은 실수를 막는 가드레일이지 샌드박스가 아니다. push·배포를 정말 막으려면 컨테이너에 그 자격증명을 두지 않는다(`gh` 토큰 권한 축소, 배포 키는 컨테이너 밖).
- **세션 보존**: 대기는 무기한이므로 설치 점검 7단계가 `~/.claude/settings.json`의 `cleanupPeriodDays`, `~/.gemini/settings.json`의 `general.sessionRetention.maxAge`가 365일 이상인지 확인한다(자동으로 바꾸지 않음). 세션을 잃으면 새로 실행하고, 이전 질문·답변을 프롬프트에 덧붙인다.

FAKE 시나리오 `ask`(질문)와 `plan`(`docs/fake-plan.md` 생성)도 쓸 수 있다: `printf 'plan\nask\nok\n' > ./data/fake-scenario`를 실행한 뒤 기획 작업을 등록하면 `PLAN_DONE` [실행] → `WAITING` 질문 → 답 → `DONE` 순서로 진행된다.
