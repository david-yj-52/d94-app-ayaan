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

## 결정: Cursor 프롬프트

Cursor 는 문서대로 프롬프트를 마지막 위치 인자로 넘긴다 (stdin 미사용, 어댑터 `prompt_in_argv = True`). 셸을 쓰지 않아 안전하다. 한계: Linux 인자 하나 128KB.

## 2026-11-18 스파이크 (설계 §10-0)

설계서 `docs/specs/2026-11-12-ayaan-skills-approvals-design.md` §10 0단계. 코드 변경 없음. 실행일 2026-11-19(UTC), 컨테이너 HOME `/home/ayaan`, 실험 파일은 전부 `/tmp/probe/**`.
버전: Claude Code 2.1.322(claude.ai 로그인), Codex 0.167.1(ChatGPT 로그인), Gemini 0.68.0(**로그인 안 됨**), Cursor agent 2026.11.11-043ef28(로그인).
범례: **실측** = 이번에 실제로 실행해 확인. **미확인** = 실행하지 못함.

### 요약

| # | 항목 | 결과 |
|---|---|---|
| C1 | Claude `-p --dangerously-skip-permissions --settings` + PreToolUse deny | **적용됨**(실측). bypassPermissions 모드에서도 도구 실행이 막힘 |
| C2 | `--append-system-prompt`가 `--resume`에서 유지 | **유지됨**(실측). 재개 시 플래그를 다시 주지 않아도 됨 |
| C3 | 프로토콜 블록(§5.1) → 마지막 줄 `AYAAN_ASK: {json}` | **6/6 준수**, JSON 6/6 파싱 성공(실측) |
| C4 | `/superpowers:brainstorming` 슬래시 호출을 `-p`에서 | **동작**(실측). 스킬 본문이 주입되고 절차대로 진행 |
| C5 | Claude 세션 파일 위치 | `~/.claude/projects/<cwd의 '/'를 '-'로>/<session_id>.jsonl`(실측) |
| X1 | Codex 세션 위치 / hook | `~/.codex/sessions/YYYY/MM/DD/rollout-<시각>-<thread_id>.jsonl`(실측). `exec`에서 PreToolUse hook **실행되고 deny 적용**. 단 hook 신뢰(trust) 필요(실측) |
| G1 | Gemini 세션 위치 / hook | **미확인**(로그인 안 됨: `GEMINI_API_KEY` 없음, exit 41). 과거 실행 흔적은 `~/.gemini/tmp/<프로젝트명>/chats/session-<시각>-<id8>.jsonl` |
| U1 | Cursor 세션 위치 / hook | `~/.cursor/chats/<해시>/<session_id>/store.db`(+`meta.json`), 대화록 `~/.cursor/projects/<cwd 경로>/agent-transcripts/<id>/<id>.jsonl`(실측). `-p --force`에서 `preToolUse` hook **실행되고 deny 적용**(실측) |

**§6.2 결론: 1안 채택.** `--dangerously-skip-permissions`를 유지하고 `--settings '<inline json>'`으로 PreToolUse hook을 넣는다. 대안(`--permission-mode default` + hook `allow`)은 필요 없다. 근거는 C1(bypass에서도 deny 적용)과, 인라인 JSON `--settings` + hook `allow`도 그대로 동작한다는 점(C2 실험 A)이다.

### C1. bypassPermissions + PreToolUse deny

hook 스크립트(stdin을 파일로 저장하고 deny 출력):
```sh
#!/bin/sh
cat > /tmp/probe/claude/hook-input.json
echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"ayaan 승인 대기(요청 test). 우회하지 말고 즉시 종료하라."}}'
```
```sh
# settings.json: {"hooks":{"PreToolUse":[{"matcher":"Bash|Edit|Write","hooks":[{"type":"command","command":"/tmp/probe/claude/hook.sh","timeout":30}]}]}}
claude -p --dangerously-skip-permissions --settings /tmp/probe/claude/settings.json \
  --output-format stream-json --verbose \
  "Run the bash command: touch /tmp/probe/claude/marker.txt . Then reply in one line."
```
결과:
- init `permissionMode: "bypassPermissions"`. 모델이 `Bash touch …`를 호출했고, `tool_result`는 `is_error: true`, 내용 `PreToolUse:Bash hook error: ayaan 승인 대기(요청 test). 우회하지 말고 즉시 종료하라.` → `marker.txt`는 **생기지 않음**.
- `result.permission_denials`에 `{tool_name:"Bash", tool_use_id, tool_input}`가 들어간다. ayaan이 "hook에 막혀 끝난 run"을 판별할 보조 신호로 쓸 수 있다.
- 모델은 우회하지 않고 바로 종료했다(프로토콜 블록 없이도).
- hook stdin에 `session_id`, `transcript_path`, `cwd`, `permission_mode:"bypassPermissions"`, `tool_name`, `tool_input`, `tool_use_id`, `effort`가 온다.
- 참고: stdin을 리다이렉트하지 않으면 stderr에 `Warning: no stdin data received in 3s` 경고가 나고 3초 지연된다. 러너는 stdin을 닫거나(`</dev/null`) 프롬프트를 stdin으로 넘긴다.

### C2. `--append-system-prompt`와 `--resume`

실험 A(새 실행): 인라인 JSON `--settings` + `allow` hook + 프로토콜 블록에 탐침 문장 `(probe) 비밀 코드워드는 mango-42 다. 묻기 전에는 절대 말하지 마.`를 덧붙여 `--append-system-prompt`로 넘겼다.
```sh
claude -p --dangerously-skip-permissions \
  --settings '{"hooks":{"PreToolUse":[{"matcher":"Bash|Edit|Write","hooks":[{"type":"command","command":"/tmp/probe/claude/allow.sh","timeout":30}]}]}}' \
  --append-system-prompt "$BLOCK" --disallowedTools AskUserQuestion \
  --output-format stream-json --verbose "touch /tmp/probe/claude/allowed.txt 실행해."
```
→ hook 1회 호출, `allow`로 실행됨(`allowed.txt` 생성). 마지막 줄 `AYAAN_RESULT: touch /tmp/probe/claude/allowed.txt 실행 완료`.

실험 B(재개, `--append-system-prompt`·`--settings` 없이):
```sh
claude -p --resume 0dd4ddee-82a7-4b21-96a6-7a3f5ee8e115 --dangerously-skip-permissions \
  --output-format stream-json --verbose "시스템 프롬프트에 적힌 비밀 코드워드가 뭐야? 한 줄로."
```
→ `mango-42` 답변 + 마지막 줄 `AYAAN_RESULT: 코드워드 mango-42 응답`. 코드워드는 대화 기록에 나온 적이 없으므로 시스템 프롬프트가 복원된 것이다.
- 세션 파일에 `attachment.type:"prompt_snapshot"`(전체 system prompt, append 포함)과 `api-request-shape`가 저장된다. 재개 run은 새 snapshot을 쓰지 않고 기존 것을 재사용했다.
- 결론: ayaan은 재개 실행에 `--append-system-prompt`를 **다시 넘기지 않는다**. (다시 넘겼을 때 중복/덮어쓰기 동작은 시험하지 않았다.)

### C3. `AYAAN_ASK` 준수

프롬프트: `/tmp/probe/claude/readme/README.md 를 작성해. 문서 언어(한국어/영어)는 사용자만 정할 수 있고 아직 정해지지 않았다.`(1차 시도는 끝에 "파일은 만들지 마."를 붙였다). 모두 `--disallowedTools AskUserQuestion`. 블록은 §5.1 원문 그대로.

| run | 블록 위치 | 마지막 줄 | JSON |
|---|---|---|---|
| 3a | `--append-system-prompt` (1차 문구) | `AYAAN_ASK: {"question":"README.md를 어떤 언어로 작성할까요?","options":["한국어","영어","한국어+영어 병기"],"recommend":0,"context":…}` | ok |
| 3b | 프롬프트 끝 (1차 문구) | `AYAAN_ASK: {…"options":["한국어","English","한국어 + English 병기"]…}` | ok |
| 3c1, 3c2 | `--append-system-prompt` | `AYAAN_ASK: {…options 3개…}` | ok |
| 3d1, 3d2 | 프롬프트 끝 | `AYAAN_ASK: {…options 2개…}` | ok |

- 6/6 모두 결과 텍스트(`result.result`)의 **마지막 줄**에 한 줄짜리 마커를 냈고, 키는 항상 `question/options/recommend/context`였다. 파일은 만들지 않았다.
- 마커 앞에 사람이 읽을 설명 문단이 붙는다. 파서는 "마지막 매치"만 보면 된다(§5.2-1 그대로).
- C4의 brainstorming 실행도 자유 답변 질문을 `"options":[]`로 냈다.
- 표본이 작다(6회, 한 가지 시나리오). 실제 운영에서 깨진 JSON이 나오면 §3.1의 대체 경로(마지막 1,000자)를 쓴다.

### C4. 플러그인 슬래시 호출

```sh
claude -p --dangerously-skip-permissions --disallowedTools AskUserQuestion --append-system-prompt "$BLOCK" \
  --output-format stream-json --verbose "/superpowers:brainstorming

/tmp/probe/claude 에 간단한 할 일 목록 CLI를 만들고 싶다."
```
- init 이벤트의 `slash_commands`에 `superpowers:brainstorming` 등 플러그인 명령이 나열된다(플러그인 설치 여부 확인에 쓸 수 있음).
- 세션 파일에 `<command-name>/superpowers:brainstorming</command-name>`과 `Base directory for this skill: /home/ayaan/.claude/plugins/cache/superpowers-marketplace/superpowers/7.0.2/skills/brainstorming`(스킬 본문)이 기록됐다.
- 모델은 "Superpowers의 brainstorming 절차로…"라며 디렉터리를 살펴본 뒤 질문 하나를 하고 `AYAAN_ASK: {"question":"이 할 일 목록 CLI는 어떤 상황에서, 무엇을 위해 쓰려는 건가요? …","options":[],…}`로 끝났다(2턴, $0.29).
- 결론: §2.5의 `INVOKE_TEXT + "\n\n" + 본문` 방식이 `-p`에서 그대로 동작한다. brainstorming과 AYAAN_ASK가 함께 잘 맞물린다.

### C5. Claude 세션 파일

`/home/ayaan/.claude/projects/-tmp-probe-claude/<session_id>.jsonl` (cwd `/tmp/probe/claude`). hook 입력의 `transcript_path`로도 확인했다. HOME 아래이므로 바인드 마운트로 보존된다.

### X1. Codex

```sh
codex exec --dangerously-bypass-hook-trust --json --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox -C /tmp/probe/codex \
  -c 'hooks.PreToolUse=[{matcher=".*",hooks=[{type="command",command="/tmp/probe/codex/hook.sh",timeout=30}]}]' \
  "Run the shell command: touch /tmp/probe/codex/marker.txt . Then reply in one line."
```
- `--dangerously-bypass-hook-trust` **없이** 같은 명령을 돌리면 hook이 조용히 실행되지 않고(경고도 없음, `--strict-config`도 통과) `marker.txt`가 만들어졌다. Codex는 hook을 "리뷰(신뢰)된 것만" 실행한다(바이너리 문구: `hooks need review before they can run`, 설정 `hooks.state."<id>".trusted_hash`).
- 플래그를 주면 hook이 실행되고 deny가 적용됐다: stderr `Command blocked by PreToolUse hook: ayaan 승인 대기(요청 test). 즉시 종료하라.. Command: touch …`, `marker.txt` 없음, 모델은 "a pre-tool hook blocked the command pending approval."로 종료.
- 플래그를 쓰면 `--json` 스트림 앞에 `item.type:"error"` 이벤트(`` `--dangerously-bypass-hook-trust` is enabled… ``)가 2개 나온다. 어댑터가 이를 실패로 보면 안 된다.
- hook stdin은 Claude와 거의 같은 형태다: `session_id`, `turn_id`, `transcript_path`, `cwd`, `hook_event_name:"PreToolUse"`, `model`, `permission_mode:"bypassPermissions"`, `tool_name:"Bash"`, `tool_input.command`, `tool_use_id`. deny 출력도 Claude 형식(`hookSpecificOutput.permissionDecision`)을 그대로 받는다. `deny`에는 비어 있지 않은 `permissionDecisionReason`이 필수다(바이너리 문구).
- 세션: `/home/ayaan/.codex/sessions/2026/11/19/rollout-2026-11-19T00-49-22-<thread_id>.jsonl`.
- 함의: §6.4의 "hook 지원이 확인되면 `ayaan-hook`으로 옮긴다"의 조건을 Codex는 충족한다. 다만 `--dangerously-bypass-hook-trust`를 쓸지, 설정 파일에 `trusted_hash`를 미리 써 둘지는 11단계에서 정한다.

### G1. Gemini

```sh
gemini -o json -p "Reply with exactly: pong"
# → exit 41, "When using Gemini API, you must specify the GEMINI_API_KEY environment variable."
```
`~/.gemini/settings.json`이 `security.auth.selectedType: "gemini-api-key"`인데 키가 없다. hook(`BeforeTool`) 실행 여부와 세션 위치는 **미확인**. 기존 파일로 보면 `~/.gemini/tmp/<프로젝트 이름>/chats/session-<시각>-<id8>.jsonl`에 저장되는 것으로 보인다(이번 실행으로 확인한 것은 아님). 로그인 후 다시 측정한다.

### U1. Cursor

HOME을 건드리지 않으려고 작업공간 `/tmp/probe/cursor/.cursor/hooks.json`을 썼다:
```json
{"version":1,"hooks":{"beforeShellExecution":[{"command":"/tmp/probe/cursor/hook.sh"}],"preToolUse":[{"command":"/tmp/probe/cursor/hook.sh"}]}}
```
hook 출력: `{"permission":"deny","continue":true,"userMessage":"…","agentMessage":"ayaan 승인 대기(요청 test). 즉시 종료하라."}`
```sh
agent -p --force --trust --output-format stream-json --workspace /tmp/probe/cursor \
  "Run the shell command: touch /tmp/probe/cursor/marker.txt . Then reply in one line."
```
- `preToolUse` hook이 1회 실행됐고(`tool_name:"Shell"`) deny가 적용됐다. `marker.txt` 없음, 결과 "Shell was blocked by a preToolUse hook, so `marker.txt` was not created." `beforeShellExecution`은 preToolUse에서 막혀 호출되지 않았다.
- hook stdin: `conversation_id`, `session_id`, `tool_name`, `tool_input`, `tool_use_id`, `hook_event_name:"preToolUse"`, `cursor_version`, `workspace_roots`, `user_email`(계정 이메일이 들어감 — 로그에 남기지 않는다), `transcript_path: null`.
- 세션: `~/.cursor/chats/<해시 32자>/<session_id>/{store.db,meta.json}`, 대화록 `~/.cursor/projects/tmp-probe-cursor/agent-transcripts/<id>/<id>.jsonl`.
- 주의: 실행만 해도 CLI가 `~/.cursor/cli-config.json`을 다시 쓴다. 이 파일에는 `permissions` 외에 `authInfo`, `model` 등도 있다. §6.4대로 이 파일에 권한을 쓸 때는 **`permissions` 키만 병합**해야 하며 통째로 덮어쓰면 로그인 정보가 날아간다.
- 함의: Cursor도 hook 기반 `ayaan-hook`으로 옮길 수 있다(형식만 Cursor식 `permission`으로 변환). 작업공간 `.cursor/hooks.json`은 사용자 저장소 파일이라 HOME의 `~/.cursor/hooks.json`을 쓰는 쪽을 11단계에서 검토한다.

### 설계에 반영할 것

1. §6.2: 1안 확정(bypass 유지 + 인라인 `--settings` PreToolUse hook). §12의 "0단계 결과에 따른 권한 모드 선택"은 닫는다.
2. §5.1: 블록을 `--append-system-prompt`로 넘기고 재개 시에는 다시 넘기지 않는다.
3. §3.2/§6.4: Codex·Cursor는 hook이 비대화형에서 동작하므로 "1차 불가"를 "가능(Codex는 hook 신뢰 처리 필요)"으로 고칠 수 있다. 1차 범위를 넓힐지는 사용자 결정 사항이다.
4. §5.3: 세션 위치 확정 — Claude/Codex/Cursor 모두 `/home/ayaan` 아래. Gemini만 미확인.
5. 비용 참고: Claude 실험 1회 $0.04~$0.29(`total_cost_usd`).
