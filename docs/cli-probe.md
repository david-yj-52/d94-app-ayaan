# CLI probe (Task 0)

범례: **확인** = 설치한 패키지의 `--help`/`--version`, 또는 공식 문서에서 직접 확인. **합성** = 문서 형태를 따라 직접 작성했거나 추정한 값 (실제 CLI를 로그인해 실행하지 않음).
어떤 CLI도 로그인되어 있지 않아 LIVE 녹화는 하지 못했다. 모든 `tests/fixtures/**` 파일은 **합성**이다. 실제 로그인 후 재녹화하면 교체한다.

출처: `--help`는 scratchpad의 임시 prefix에 설치한 패키지에서 얻었다 (`npm i --prefix <tmp> ...`, 전역 설치 없음, 모델 호출·로그인 없음).
- Cursor: https://cursor.com/docs/cli/headless , https://cursor.com/docs/cli/reference/output-format , https://cursor.com/docs/cli/reference/parameters
- Codex: https://learn.chatgpt.com/docs/non-interactive-mode (developers.openai.com/codex/noninteractive 에서 리다이렉트)
- Gemini: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/headless.md , 번들 소스 `@google/gemini-cli/bundle/chunk-2OHBIZ5H.js` (JsonStreamEventType)
- Claude usage 엔드포인트: https://docs.rs/crate/cship/1.7.1/source/src/usage_limits.rs (커뮤니티, 공식 문서 없음)
- Codex 세션 로그: https://lin-guanguo.github.io/llm-memory-research/agent-cli/codex-session-file-schema/ , https://github.com/openai/codex/issues/28879 (커뮤니티)
- Codex 한도 문구: https://docs.rs/zag-agent/0.18.0/zag_agent/providers/codex_usage_limits/ , OpenAI 커뮤니티 글

## 요약 표

| 항목 | Claude Code | Codex | Gemini CLI | Cursor |
|---|---|---|---|---|
| 고정 버전 | 2.1.322 (확인, `npm view`/`--version`) | 0.167.1 (확인) | 0.68.0 (확인) | 미확인 (설치 스크립트가 최신 설치, 합성: 설치 후 `agent --version` 기록 필요) |
| 설치 | `npm i -g @anthropic-ai/claude-code@2.1.322` | `npm i -g @openai/codex@0.167.1` | `npm i -g @google/gemini-cli@0.68.0` | `curl https://cursor.com/install -fsS \| bash` |
| 실행 파일 | `claude` | `codex` | `gemini` | 문서는 `agent` 사용. `cursor-agent`도 설치 스크립트가 만든다고 알려짐 (합성, 설치 후 확인) |
| 헤드리스 로그인 | `claude setup-token` (장기 토큰, 구독 필요; 확인 `--help`), `claude auth login` (확인). env `CLAUDE_CODE_OAUTH_TOKEN` (합성) | `codex login` (확인) / `printenv OPENAI_API_KEY \| codex login --with-api-key` (확인) / env `CODEX_API_KEY` (확인, docs). device-auth 플래그는 이 버전 help에서 확인 못함 (합성) | 첫 실행 시 브라우저 OAuth, 헤드리스는 env `GEMINI_API_KEY` (합성, help에 로그인 서브커맨드 없음) | `agent login` (확인), `--api-key`/env `CURSOR_API_KEY` (확인) |
| 새 실행 argv | `claude -p --output-format stream-json --verbose --dangerously-skip-permissions --session-id <uuid>` (모두 확인 help; `--verbose` 필수 여부는 합성) | `codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C <dir> -` (확인) | `gemini -o stream-json --yolo -p "<prompt>"` (확인; `--session-id <uuid>` 로 id 지정 가능, 확인) | `agent -p --force --output-format stream-json "<prompt>"` (확인) |
| 재개 argv | `claude -p --resume <session_id> --output-format stream-json --verbose --dangerously-skip-permissions` (확인) | `codex exec resume <thread_id> --json --dangerously-bypass-approvals-and-sandbox -` (확인 help; `--json`을 resume 서브커맨드가 받는지는 합성) | `gemini --resume <id> -o stream-json --yolo -p ...` (help는 "latest" 또는 인덱스만 설명, UUID 허용 여부는 합성; 안전책: `--session-id`로 UUID를 직접 지정) | `agent -p --resume <chatId> --force --output-format stream-json "<prompt>"` (확인 parameters 문서) |
| 권한 생략 | `--dangerously-skip-permissions` (확인) 또는 `--permission-mode bypassPermissions` (확인) | `--dangerously-bypass-approvals-and-sandbox` (확인) | `--yolo` / `--approval-mode yolo` (확인) | `--force` / `--yolo` (확인) |
| 프롬프트 stdin | `-p`일 때 stdin을 프롬프트로 읽음 (합성, 널리 쓰이는 동작이나 help에 명시 없음). `--input-format text` 기본 (확인) | `-` 또는 프롬프트 생략 시 stdin (확인). 프롬프트와 함께 stdin이면 `<stdin>` 블록으로 덧붙음 (확인) | `-p` 값이 stdin 입력 뒤에 덧붙음 (확인 help) | 문서에 언급 없음 (합성: 인자로 전달; 긴 프롬프트는 인자 길이 한도 확인 필요) |
| 세션 id 위치 | 모든 이벤트의 `session_id` (init 포함) (합성, SDK 공개 형식) | `thread.started.thread_id` (확인 docs) | `init.session_id` (확인 docs) | 모든 이벤트의 `session_id` (확인 docs) |
| 토큰/비용 | `result.usage`, `result.total_cost_usd` (합성) | `turn.completed.usage` {input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens} (확인 docs) | `result.stats` {total_tokens, input_tokens, output_tokens, cached, input, duration_ms, tool_calls} (필드명은 합성, `stats` 존재만 확인) | 없음. result에는 duration_ms만 (확인 docs) |
| 결과 텍스트 | `result.result` (합성) | `item.completed` 중 `agent_message.text` (확인 docs) | `message(role=assistant)` 의 `content` (이벤트 종류 확인, 필드명 합성) | `result.result` (확인 docs) |

## 한도 오류 원문

- Claude: `You've hit your limit · resets 4pm (UTC)` 형태의 result 텍스트 (`is_error: true`) 와 stream-json `rate_limit_event` (`rate_limit_info.status = "rejected"`, `resetsAt` epoch 초, `rateLimitType = "five_hour"`). 구버전은 `Claude AI usage limit reached|<epoch>`. 전부 **합성** (기억 기반, 현재 버전 미검증).
- Codex: `You've hit your usage limit. Try again at <로컬시각>.` 또는 `... try again in 4 days 2 hours 46 minutes`; `--json`에서 `error` / `turn.failed` 의 `message` 에 들어감 (확인: 검색 결과 두 곳 일치, 이벤트 모양은 합성). 시각은 타임존 없는 사람용 문자열이라 파싱이 취약, 아래 `rate_limits` 우선 권장.
- Gemini: `You have exhausted your capacity on this model. Your quota will reset after 3h12m5s.` 상대시간 (합성). 종료 코드 1 (확인 docs, 일반 API 오류). 입력 오류 42, 턴 초과 53.
- Cursor: 문서화된 원문 없음. `You've hit your usage limit...` 는 **합성**. 한도 판별은 `is_error:true` + 문구 매칭으로 하되 실제 문구 확보 필요.
- 거절된 요청이 한도에 잡히는지: 전 도구 **미확인** (로그인 불가). 스펙은 보수적으로 가정.

## Claude usage 엔드포인트 (커뮤니티 문서, 합성 예시)

`GET https://api.anthropic.com/api/oauth/usage`, 헤더 `Authorization: Bearer <access_token>`, `anthropic-beta: oauth-2025-04-20`. 공식 문서 없음. `utilization`은 0~100 퍼센트, `resets_at`은 ISO 8601 또는 null, 창 전체가 null일 수 있음(엔터프라이즈). 예시는 `tests/fixtures/claude/usage_api.json`.

## Codex 세션 로그 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`

`event_msg` 안 `payload.type == "token_count"` 에 `rate_limits.primary` (약 300분, 5시간), `rate_limits.secondary` (약 10080분, 7일), 각 `used_percent`, `window_minutes`, `resets_at` (Unix 초). 일부 버전은 `resets_in_seconds` 사용 (파서는 둘 다 처리). `total_token_usage` 는 누적값. 예시는 `tests/fixtures/codex/session.jsonl` (합성). 마지막 `token_count` 이벤트가 현재 상태.

## fixture 목록 (전부 합성)

`tests/fixtures/{claude,codex,gemini,cursor}/{ok,limit,resume}.jsonl` + `.expected.json`. ok/resume 의 최종 텍스트에 `AYAAN_RESULT: done`. resume 은 ok 와 같은 session id 를 쓴다. limit 의 `resets_at` 은 Claude만 값이 있고(`2026-11-11T16:00:00Z`), Codex 는 문구의 날짜에 타임존이 없고 Gemini/Cursor 는 상대시간/시각 없음이라 `null` (어댑터는 `resets_at` 을 추정하지 않고 None 유지). Cursor 의 토큰은 0, cost null.

## 토큰 규약

- Claude: `input_tokens` 는 캐시를 **제외** (`cache_read_input_tokens`/`cache_creation_input_tokens` 별도).
- Codex: `input_tokens` 에 `cached_input_tokens` 가 **포함**됨 (cache_read = cached_input_tokens).
- Gemini: `stats.input_tokens` 에 `cached` 가 **포함**됨 (cache_read = cached).
- Cursor: 토큰 정보 없음 (0).
