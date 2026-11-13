# ayaan — Skill 기준정보 · 의사결정/Permission 요청 · 기획→개발 2단계 설계

- 이슈: CIRA TSTA-1 · 작성 2026-11-13 · 대상: d94-app-ayaan, d94-app-query, d94-app-ecc, d94-app-web
- 상태: **설계(초안)**. 이 문서는 코드 변경 없이 설계만 담는다.
- 작성 방식: brainstorming 절차를 무인 모드로 진행했다. 사람에게 물어야 할 지점마다 추천안으로 진행하고, 그 결정을 아래 목록에 모았다.

---

## 0. 사용자 결정 필요 목록

본문은 모두 **추천안** 기준으로 썼다. 대안을 고르면 표시된 절만 바뀐다.

| # | 질문 | 추천안 | 대안 | 추천 이유 | 영향 절 |
|---|---|---|---|---|---|
| D1 | 사람 응답을 기다리는 상태를 어떻게 표현할까? | 새 상태 `WAITING`을 추가하고, 무엇을 기다리는지는 `SN_AYN_REQUEST.KIND`로 구분한다 | (a) `PAUSED` + `PAUSE_REASON='INPUT'` (b) 상태는 그대로 두고 플래그 컬럼 추가 | 현재 `claim`은 `RESUME_AT`이 지난 `PAUSED`를 자동으로 가져간다. 사람 대기를 `PAUSED`로 두면 그 조건과 충돌하고, `RESUME_AT=NULL` 같은 암묵 규칙에 기대게 된다. 상태값을 따로 두면 화면·리포트·인덱스에서 한도 대기와 저절로 구분된다 | §4 |
| D2 | AI가 질문을 밖으로 보내는 방식 | **질문하고 종료 → 응답 후 같은 세션 재개**. 결과 마지막 줄의 `AYAAN_ASK: {json}` 마커로 받는다 | (a) MCP `ask` 도구를 블로킹 호출하고 프로세스를 띄운 채 대기 (b) MCP 도구는 등록만 하고 즉시 반환 | 4개 CLI 모두 `--resume`을 지원하고, 기존 `AYAAN_RESULT:` 규약과 같은 방식이다. 대기 중 프로세스가 없으므로 무기한 대기와 컨테이너 재시작에 강하다. 블로킹 방식은 리스·타임아웃·재시작 시 유실 문제가 생긴다 | §3.1, §5 |
| D3 | 권한 게이트 구현 | Claude는 **PreToolUse hook**(`ayaan-hook` 명령)이 ayaan 정책 엔드포인트에 묻는다. 다른 CLI는 각자의 정적 권한 설정으로 변환하고 `ASK`는 `DENY`로 낮춘다 | (a) Claude `--permission-prompt-tool` + MCP 서버 (b) 현행 유지(모든 권한 허용) | hook은 `-p` 모드에서 동작하고, MCP 서버 프로세스 없이 명령 하나로 끝난다. 다른 CLI의 hook 지원은 버전에 따라 다르거나 미확인이라 1차 범위에서 뺀다 | §3.2, §6 |
| D4 | 승인 1건이 허용하는 범위 | **바로 그 호출 1회**(도구 + 입력 해시 일치) | (a) 같은 세션 안에서 같은 규칙 전체 (b) 승인 시 정책에 영구 반영 | 가장 좁다. 위변조·재사용 방지(§8)와 같은 장치로 처리된다. "항상 허용"은 웹 정책 화면에서 따로 한다 | §6.3 |
| D5 | `WAITING` 중에 작업공간을 점유할까? | **점유하지 않는다.** 재개 프롬프트가 작업공간 상태를 먼저 확인하게 한다(현행 `RESUME_PROMPT`) | 점유한다(같은 경로의 다른 작업은 대기) | 대기가 무기한이라 점유하면 작업공간이 무기한 막힌다. 기획 단계는 주로 문서만 쓰므로 충돌 위험이 낮다 | §4.3 |
| D6 | skill의 플러그인이 설치되지 않았을 때 | 실행 직전에 `CLAUDE_PLUGINS` 동기화를 1회 시도한다. 그래도 없으면 `FAILED`(사유: skill 플러그인 없음)로 끝내고 알림을 보낸다 | (a) skill 없이 그냥 실행 (b) skill 저장 단계에서 차단만 하기 | skill 없이 조용히 돌면 기획·개발 품질이 달라지는데 그 사실이 드러나지 않는다. 저장 단계에서는 경고만 한다(설치는 실행 환경 상태라 저장 시점과 다를 수 있음) | §2.4 |
| D7 | CIRA 이슈 작업(`/run`, 웹 이슈 선택)도 기획부터 시작할까? | **모든 신규 작업의 기본 단계는 `PLAN`**. 웹 폼과 명령(`/run KEY tool dev`)에서 `DEV` 바로 실행을 고를 수 있다 | 자유 프롬프트만 `PLAN`, 이슈 작업은 현행대로 바로 구현 | 이슈 요청이 기획 요청과 같다고 보는 편이 단순하고, 승인 없는 구현·배포를 줄인다 | §7 |
| D8 | 기본 개발 skill | 예시대로 **`ponytail:ponytail`**. 프롬프트에 기획서 경로와 "기획서대로 구현"을 넣는다 | `superpowers:executing-plans` / `superpowers:subagent-driven-development` | 요청서에 적힌 예시를 따른다. ponytail은 "최소 구현" 성향의 모드이고 계획 실행 절차는 아니므로, 다른 것을 원하면 기준정보에서 바꾸면 된다 | §2.5 |
| D9 | 기획서 위치를 어떻게 알아낼까? | 기획 작업 결과의 **`AYAAN_RESULT: <경로>`**. 작업공간 안에 있는 존재하는 파일이어야 한다 | (a) 고정 경로 규칙 `docs/specs/*` 중 최신 파일 (b) 결과 텍스트 전체를 DB에 저장 | 이미 쓰는 규약이다. 검증이 쉽고 파일을 재활용할 수 있다 | §7.2 |
| D10 | 자유 텍스트 답변 방법 | 질문 메시지에 Telegram **답장(reply)** 하거나 `/answer <요청ID> <내용>`. 선택지가 있으면 버튼도 쓴다 | 버튼만 | 기획 질문은 선택지로 다 담기지 않는다. 답장 방식은 요청 ID를 원문 메시지에서 다시 읽으므로 ECC에 상태가 필요 없다 | §9 |
| D11 | 누가 답할 수 있나? | 기존 `ECC_TELEGRAM_ALLOWED_USER_IDS` 전원과 웹 `JWT_ALLOWED_SUBJECTS` 전원. 먼저 답한 1건만 유효하다 | 특정 1인만 승인 가능(권한 승인 전용 allowlist) | 현재 사용자가 1인 운영이다. 다중 사용자가 되면 `ECC_APPROVER_USER_IDS`를 추가한다 | §8 |
| D12 | 오래 대기하는 요청 상기 | 정기 리포트(`REPORT_CRON`)에 `WAITING` 목록과 요청 ID를 넣는다 | 대기 N시간마다 재알림 | 새 루프나 설정이 필요 없다 | §9.3 |
| D13 | CLI 세션 보존 기간 | 컨테이너 HOME의 CLI 설정으로 보존 기간을 **365일**로 늘리도록 설치 점검(setup)이 확인·안내한다(Claude `cleanupPeriodDays`, Gemini `general.sessionRetention.maxAge`. 자동으로 덮어쓰지 않음). 그래도 세션을 잃으면 기존 `session_missing` 대체 경로로 새로 실행하고, 질문·답변 이력을 프롬프트에 덧붙인다 | 기본값(30일) 유지 | 대기는 무기한인데 기본 보존은 30일이다(Claude 기본값은 미확인, Gemini 30d는 확인) | §5 |
| D14 | 이 설계서의 반영 방식 | 이슈 본문은 "main에 직접 push"를 요구하지만, 이번 실행 지침은 main 직접 push를 금지하고 PR을 요구한다. **`ayaan/TSTA-1` 브랜치 + PR**로 올렸다 | main 직접 커밋 | 더 안전한 쪽 지침을 따랐다. 머지 여부는 사용자가 정한다 | — |

---

## 1. 의도와 범위

### 1.1 왜
지금 ayaan은 CLI를 **권한 전부 허용 + 무인**(`--dangerously-skip-permissions`, `--yolo`, `--force`)으로 돌린다. 그래서 두 가지 문제가 있다.
1. 결정이 필요한 순간에 AI가 혼자 추측해서 진행한다(지금 이 이슈의 프롬프트도 "추천안으로 진행"을 강제한다).
2. push·배포 같은 되돌리기 어려운 동작에 사람이 끼어들 지점이 없다.

이번 기능은 사람을 **Telegram으로 루프에 다시 넣는다.** 동시에 "기획 → (사람 확인) → 개발"이라는 작업 흐름을 기준정보(skill)로 표준화한다.

### 1.2 목표
- G1 skill 기준정보: 등록·수정·설명, 플러그인 연결, Tool별 기본값, 작업별 선택(작업 설정이 우선).
- G2 의사결정 요청: AI 질문 → Telegram → 무기한 대기 → 답 → 같은 세션 재개.
- G3 Permission 요청: 동작 범주별 정책(ALLOW/ASK/DENY)을 Tool별로 관리하고, ASK는 G2와 같은 경로로 승인받는다.
- G4 기획→개발: 등록 = 기획 요청. 기획이 끝나면 개발 작업을 자동 등록하되 `[실행]`을 누를 때까지 대기한다.

### 1.3 비목표 (이번에 안 함)
- 모든 CLI에서 동일한 수준의 권한 게이트(§3 한계 참고). 1차는 Claude만 완전 지원한다.
- 대기 중 CLI 프로세스를 살려 두는 블로킹 대화.
- "항상 허용" 버튼으로 정책 자동 학습, 단계(PLAN/DEV)별 권한 정책 분리(필요해지면 `SN_AYN_PERMISSION.PHASE` 컬럼 추가).
- 다중 사용자 승인 권한 구분(D11).

### 1.4 실패로 볼 것 (기술적으로 돌아가도)
- 승인 없이 ASK 동작이 실행됨. 같은 승인 버튼이 두 번 먹힘.
- 대기 중인 작업을 스케줄러가 몰래 재개함.
- 질문을 보냈는데 컨테이너 재시작 뒤 답해도 이어지지 않음.
- skill이 빠진 채로 아무 표시 없이 실행됨.

---

## 2. 현재 구조 요약 (변경 대상)

| 영역 | 현재 | 이번 변경 |
|---|---|---|
| 상태 | `SN_AYN_TASK.STATUS` = QUEUED/RUNNING/PAUSED/DONE/FAILED/CANCELLED. `claim`: `QUEUED` 또는 `PAUSED AND RESUME_AT+grace<=now`. `recover`: 리스 만료 RUNNING → PAUSED(RECOVERED). 유니크: 이슈당 활성(Q/R/P) 1건, 작업공간당 RUNNING 1건 | `WAITING` 추가(§4) |
| 기준정보 | `SN_AYN_CONFIG`(키-값), `SN_AYN_TOOL`, `SN_AYN_WORKSPACE`, `SN_AYN_TOOL_QUOTA` | `SN_AYN_SKILL`, `SN_AYN_PERMISSION`, `SN_AYN_REQUEST` 신설. TOOL·TASK 컬럼 추가 |
| 플러그인 | `CLAUDE_PLUGINS` = `plugin@market\|https-url,...`, 부팅 시와 `POST /plugins/sync`로 `claude plugin marketplace add` / `install`. `GET /plugins`는 `claude plugin list` 원문 | skill이 이 목록의 항목을 참조(§2.4) |
| 실행 | `adapters.py`가 CLI별 argv 생성(전부 권한 우회 플래그). 새 실행은 `task.prompt`, 재개는 `RESUME_PROMPT`. 결과 마지막 `AYAAN_RESULT:` | 프롬프트에 skill 호출 앞붙이기, 질문 프로토콜, Claude hook |
| 알림 | `EccClient.notify(event,title,body,buttons)` → ECC `POST /api/v1/notify`. 버튼 action은 `resume`/`cancel`만(`Literal`). callback `ayn:<action>:<taskId>` | `answer` 버튼, 답장·`/answer` 명령 |
| 웹 | `/ayaan`, `/ayaan/tasks`(새 작업 폼), `/ayaan/tasks/[id]`, `/ayaan/settings`(탭: 전역/Tool/작업공간), `/ayaan/setup` | Skill·권한 탭, 폼 드롭다운, 요청 패널 |

### 2.4 skill ↔ 플러그인 연결
- `SN_AYN_SKILL.PLUGIN_REF` = `plugin@marketplace`(예: `superpowers@superpowers-marketplace`) 또는 NULL(CLI 내장, 또는 플러그인이 아닌 지시문 skill).
- **저장 시**: `PLUGIN_REF`가 현재 `CLAUDE_PLUGINS` 항목 중에 없으면 422 대신 **경고**(`warnings: ["CLAUDE_PLUGINS에 없음"]`)를 돌려준다. 웹은 "플러그인 목록에 추가" 안내를 띄운다.
- **실행 직전**(`_run_once`, Claude이고 `PLUGIN_REF`가 있을 때): `plugins.installed()` 출력에 이름이 있는지 본다. 결과는 프로세스 메모리에 10분 캐시한다(`ponytail:` 캐시, `plugin list` 호출 비용 절감). 없으면 `plugins.sync()`를 1회 실행하고 다시 확인한다. 그래도 없으면 run을 만들지 않고 `FAILED`(`failReason="SKILL_PLUGIN_MISSING: <ref>"`)로 끝낸 뒤 알림을 보낸다(D6).
- Claude가 아닌 Tool의 skill은 `PLUGIN_REF`가 NULL이어야 한다(저장 검증). 해당 CLI의 플러그인·확장 설치 자동화는 범위 밖이다.

### 2.5 skill 호출 방식
- `SN_AYN_SKILL.INVOKE_TEXT`는 프롬프트 맨 앞에 붙는 문자열이다. Claude는 `/superpowers:brainstorming`, `/ponytail:ponytail`처럼 슬래시 호출을 쓰고, 다른 CLI는 자연어 지시문("먼저 요구사항을 정리하고 설계서를 써라…")을 쓴다.
- 최종 프롬프트는 `INVOKE_TEXT + "\n\n" + 본문 + "\n\n" + 프로토콜 블록(§5.1)`이다. 재개할 때는 skill을 다시 붙이지 않는다(세션에 이미 있음).
- 시드(V530): `BRAINSTORM`(CLAUDE, PLAN, `/superpowers:brainstorming`, `superpowers@superpowers-marketplace`), `PONYTAIL`(CLAUDE, DEV, `/ponytail:ponytail`, `ponytail@ponytail`), `EXEC_PLAN`(CLAUDE, DEV, `/superpowers:executing-plans`, `superpowers@superpowers-marketplace`). `SN_AYN_TOOL` CLAUDE의 기본값은 PLAN=`BRAINSTORM`, DEV=`PONYTAIL`(D8).
- 우선순위: `task.skillCd` → `tool.planSkillCd`/`tool.devSkillCd`(단계별) → 없으면 skill 없이 실행. `skillCd='NONE'`이면 명시적으로 skill을 쓰지 않는다.

---

## 3. CLI별 질문·권한 요청 전달 방법과 한계

출처: 공식 문서(2026-11-13 조회). Claude: code.claude.com/docs `headless`, `hooks`, `cli-reference`, `mcp`, `permission-modes`. Codex: learn.chatgpt.com/docs(구 developers.openai.com/codex) `non-interactive-mode`, `config-reference`, `hooks`, `extend/mcp`. Gemini: github google-gemini/gemini-cli `docs/cli/headless.md`, `docs/reference/policy-engine.md`, `docs/hooks/*`, `docs/tools/mcp-server.md`, `docs/cli/cli-reference.md`. Cursor: cursor.com/docs `cli/headless`, `cli/reference/permissions`, `cli/mcp`, `agent/hooks`.
**주의:** 문서는 최신판 기준이다. ayaan이 고정한 버전(Claude 2.1.322, Codex 0.167.1, Gemini 0.68.0, Cursor 미고정)에서 그대로 동작하는지는 **구현 0단계 실측으로 확인**해야 한다(`docs/cli-probe.md`에 기록).

### 3.1 질문(의사결정)

| CLI | 문서상 방법 | 이번 설계 |
|---|---|---|
| Claude | `AskUserQuestion` 도구가 있다(답할 때까지 열려 있음, `askUserQuestionTimeout`). `-p`에서의 기본 동작은 **미확인**. `--permission-prompts none`이면 이 도구가 제거된다(확인) | `--disallowedTools AskUserQuestion`으로 끄고 `AYAAN_ASK` 마커를 쓴다 |
| Codex | 헤드리스 질문 도구 문서 없음(**미확인**). MCP elicitation은 `approval_policy.granular.mcp_elicitations`로 노출 가능(요약 확인, exec에서의 동작 미확인) | `AYAAN_ASK` 마커 |
| Gemini | `ask_user` 류 결정은 정책엔진에서 "비대화형 모드에서는 deny"(확인). 질문 도구 자체는 **미확인** | `AYAAN_ASK` 마커 |
| Cursor | 문서 없음(**미확인**) | `AYAAN_ASK` 마커 |

→ **공통 방식**: 모든 CLI에서 "결과 마지막 줄 `AYAAN_ASK: {json}` 출력 후 종료"를 쓴다. 텍스트 규약이라 모델이 지키는지에 달려 있다는 한계가 있다. 마커 JSON이 깨지면 원문 마지막 1,000자를 질문으로 그대로 보낸다(안전 측). 마커도 `AYAAN_RESULT`도 없이 정상 종료하면 기존대로 DONE(결과 없음) 처리한다.

### 3.2 권한

| CLI | 문서상 수단 | 실행 중 사람 승인 가능? | 이번 설계 |
|---|---|---|---|
| Claude | `--permission-prompt-tool mcp__srv__tool`(`-p` 전용, 확인). 입력·응답 스키마(`behavior`/`updatedInput`/`message`)는 **미확인**. hooks: `PreToolUse`의 `hookSpecificOutput.permissionDecision` = allow/deny/ask/defer(요약 확인), `PermissionRequest` hook(`decision.behavior`). hook은 `-p`에서 실행되고 `--bare`면 생략(확인). command hook 기본 타임아웃 600s(확인). 모드: default(Manual)/acceptEdits/plan/auto/dontAsk/bypassPermissions. 2.1.302+ `-p` 기본은 Manual이라 허용 규칙·hook·prompt-tool이 없으면 거부(확인). MCP 도구 타임아웃 `MCP_TOOL_TIMEOUT`(요약 확인) | hook이 즉시 deny하고 "승인 대기, 턴 종료"를 지시한다 → 재개 시 승인분 1회 allow | **PreToolUse hook**(D3) |
| Codex | sandbox `read-only`(exec 기본)/`workspace-write`/`danger-full-access`. approval `on-request`(대화형)/`never`(비대화형). exec에서 승인이 필요할 때의 동작 **미확인**. `PermissionRequest`/`PreToolUse` hook 존재(`features.hooks`, PreToolUse `ask` 미지원), exec에서의 실행 여부 **미확인**. MCP `tool_timeout_sec` 기본 60 | 미확인 → 1차 불가 | sandbox로 거칠게 매핑: SHELL·FILE_WRITE ALLOW → `workspace-write`, NETWORK/GIT_PUSH/DEPLOY ALLOW 있음 → `danger-full-access`(현행), 하나라도 DENY → `workspace-write`. ASK=DENY |
| Gemini | `--approval-mode default/auto_edit/yolo/plan`. 정책엔진 `~/.gemini/policies/*.toml`(toolName, commandPrefix, decision allow/deny/ask_user, priority). 비대화형에서 ask_user = deny(확인). `BeforeTool` hook(`decision` allow/deny)이 있으나 비대화형 실행 여부 **미확인**. `--allowed-tools`는 deprecated | 1차 불가 | 정책 TOML 생성(ALLOW→allow, DENY/ASK→deny), `--approval-mode default` |
| Cursor | `-p`에서 `--force`가 없으면 "승인이 필요한 것은 거부"(확인). `~/.cursor/cli-config.json` / `<project>/.cursor/cli.json`의 `permissions.allow/deny`(`Shell(git)`, `Write(glob)`, `Mcp(s:t)`), deny 우선(확인). hooks(`beforeShellExecution` 등 `permission` allow/deny/ask)의 CLI 적용은 **미확인** | 1차 불가 | `--force`를 빼고 HOME 쪽 `~/.cursor/cli-config.json`의 `permissions`를 쓴다(작업공간의 `.cursor/cli.json`은 사용자 저장소 파일이라 건드리지 않음. Tool당 정책이 하나라 전역 파일이면 충분) |

**핵심 한계(사용자가 알아야 할 것)**
1. 명령 접두어 매칭은 **우회할 수 있다**(`sh -c "git push"`, 스크립트 파일, 별칭). 정책은 "실수 방지 가드레일"이지 악의적 모델에 대한 샌드박스가 아니다. push·배포를 정말 막으려면 컨테이너에 그 자격증명을 두지 않는 것이 최종 방어선이다(예: `gh` 토큰 권한 축소, 배포 키는 컨테이너 밖에 둠).
2. Codex·Gemini·Cursor는 1차에서 ASK를 지원하지 않는다. 그 Tool로 ASK 정책을 저장하면 화면에 "이 Tool은 ASK를 지원하지 않아 DENY로 동작" 경고를 띄운다.
3. 마커 방식 질문은 모델 준수에 의존한다. Claude는 skill(brainstorming)이 질문 지점을 명확히 만들어 준다.

---

## 4. 상태 모델

### 4.1 상태 추가
```
QUEUED ──claim──▶ RUNNING ──완료──────────────▶ DONE
  ▲                  │ ├─한도──────────────────▶ PAUSED ──resumeAt 경과/지금 재개──▶ (claim)
  │                  │ ├─실패──────────────────▶ FAILED
  │                  │ └─열린 요청 있음 ─────────▶ WAITING
  │                                                │
  └──────────── 답변/승인/[실행] (WAITING→QUEUED) ◀─┘
  (어느 상태에서든) 취소 ─▶ CANCELLED      ※ 개발 작업은 생성 시 WAITING(KIND=START)
```
- `WAITING`: 사람 응답을 기다린다. **스케줄러는 절대 claim하지 않는다**(claim SQL 조건이 `QUEUED`/`PAUSED`뿐이라 수정 불필요). 리스 없음, 프로세스 없음.
- 무엇을 기다리는지는 그 작업의 `OPEN` 상태 `SN_AYN_REQUEST`의 `KIND`(QUESTION/PERMISSION/START)로 안다.
- `PAUSED`(한도·복구 대기)와의 구분: 상태값이 다르다. `PAUSED`는 기계가 시각(`RESUME_AT`)에 맞춰 스스로 재개하고, `WAITING`은 사람만 재개할 수 있다. `RESUME_COUNT`/`MAX_RESUME_COUNT`는 한도 재개 횟수 전용이며 답변 재개는 세지 않는다.
- 취소: `WAITING → CANCELLED` 허용. 열린 요청은 `CANCELLED`로 닫는다.
- 재개(`/resume`)는 WAITING에는 쓰지 않는다(409 "답변이 필요합니다"). 답이 곧 재개다.

### 4.2 DB 제약
- `ux_ayn_task_active_issue`의 조건을 `STATUS IN ('QUEUED','RUNNING','PAUSED','WAITING')`으로 다시 만든다.
- `ux_ayn_task_running_ws`는 그대로 둔다(D5: WAITING은 작업공간을 점유하지 않음).
- `SN_AYN_REQUEST`: 작업당 `OPEN` 1건(부분 유니크 `ux_ayn_req_open_task ON (TASK_ID) WHERE STATUS='OPEN'`). 한 run에서 두 번째 ASK가 오면 hook이 "이미 승인 대기 중"으로 거부하고 종료를 지시한다.

### 4.3 전이 규칙 (ayaan `_settle`)
CLI 종료 후 판정 순서:
1. `CANCELLED` → 기존대로.
2. 그 작업의 `OPEN` 요청이 있으면(권한 hook이 등록함) → `RUNNING→WAITING`(outcome 무관, 단 `LIMIT_HIT`이면 한도 처리를 우선하고 요청은 OPEN으로 남긴다. 한도가 풀려 재개되면 어차피 승인 대기를 다시 겪으므로, 한도 재개 시 OPEN 요청이 있으면 claim 직후 WAITING으로 되돌린다).
3. `COMPLETED`이고 결과에 `AYAAN_ASK` → 요청(QUESTION) 생성 → `RUNNING→WAITING`.
4. `COMPLETED` + PLAN 단계 → DONE + 개발 작업 생성(§7).
5. 나머지는 현행대로.

---

## 5. 의사결정 요청 흐름 (D2)

### 5.1 프로토콜 블록 (모든 새 실행 프롬프트 끝에 덧붙임)
```
[ayaan 규칙]
- 사용자 결정이 꼭 필요하면 추측하지 말고, 지금까지의 작업을 저장한 뒤 마지막 줄에
  AYAAN_ASK: {"question":"...","options":["...","..."],"recommend":0,"context":"..."}
  한 줄만 출력하고 종료해. options는 0~5개, 자유 답변이면 [].
- 도구 실행이 "ayaan 승인 대기"로 거부되면 다른 우회 방법을 쓰지 말고 즉시 종료해.
- 완료되면 마지막 줄에 AYAAN_RESULT: <한 줄 요약>
```
Claude에서는 이 블록을 프롬프트 대신 `--append-system-prompt`로 넘긴다(확인된 플래그). 재개 실행에도 유지되게 하기 위해서다.

### 5.2 순서
1. 실행 결과에 `AYAAN_ASK` → `adapters._finish`가 `RunEvent.ask`(dict)를 채운다(`_result_line`과 같은 방식, 마지막 매치).
2. ayaan → query `POST /tasks/{id}/requests` {kind:QUESTION, runId, question, options, recommend, context} → `REQ_ID`(uuid hex 12자).
3. `RUNNING→WAITING`(`cliSessionId` 저장) → ECC notify `WAITING` 이벤트. 본문은 질문, 선택지, 추천, `요청 ID: <REQ_ID>`이고 버튼은 선택지별 `answer`.
4. 사용자 답(버튼·답장·`/answer`·웹) → ayaan `POST /requests/{id}/answer` → query CAS `OPEN→ANSWERED`(answer, answeredBy, answeredAt) → `WAITING→QUEUED`(resumeAt=now) → `wake()`.
5. claim → `_run_once`: 세션이 있고 소비 전 `ANSWERED` 요청이 있으면 프롬프트를 `RESUME_PROMPT` 대신 `"사용자 답변 (질문: <q>): <answer>\n이어서 진행해."`로 쓰고, 요청을 `CONSUMED`로 바꾼다.
6. 세션을 잃었으면(`session_missing`) 기존 대체 경로로 새로 실행하고, 원 프롬프트에 그 작업의 모든 질문·답변 이력을 덧붙인다(D13).

### 5.3 오래 대기 · 재시작
- 대기 중에는 프로세스·리스·메모리 상태가 없다. 상태는 모두 d94-query(작업·요청)와 CLI 세션 파일(HOME 바인드 마운트 `/home/ayaan` → 재시작해도 보존)에 있다.
- 세션 파일 위치: Claude `~/.claude/projects/…`(일반적으로 알려진 위치, 문서 미확인), Codex `~/.codex/sessions/…`(기존 quota 수집기가 사용), Gemini·Cursor는 **미확인**(HOME 아래로 추정, 0단계에서 확인). 모두 `/home/ayaan` 아래여야 하며, 0단계에서 확인한다.
- 보존 기간: entrypoint가 아니라 **설치 점검(setup) 단계 7**로 Claude `~/.claude/settings.json`의 `cleanupPeriodDays: 365`, Gemini `~/.gemini/settings.json`의 `general.sessionRetention.maxAge: "365d"`를 확인·안내한다. 사용자 HOME 설정을 자동으로 덮어쓰지 않는다.
- 답변이 ayaan 다운 중에 오면 ECC가 "ayaan 연결 실패"로 답하고(현행 `guard_ayaan`), 사용자가 다시 누른다. 요청은 OPEN 그대로다.
- 실행 도중 재시작되면 기존 `recover`가 처리한다(RUNNING→PAUSED(RECOVERED)→재개). hook이 이미 등록한 OPEN 요청이 있으면 §4.3-2 규칙으로 WAITING이 된다.

---

## 6. Permission 요청 흐름 (D3, D4)

### 6.1 정책 기준정보 `SN_AYN_PERMISSION`
| 컬럼 | 설명 |
|---|---|
| `OBJ_ID` | PK |
| `TOOL_CD` | `CLAUDE`… 또는 `*`(전체 기본) |
| `CATEGORY` | `FILE_WRITE`, `SHELL`, `GIT_PUSH`, `DEPLOY`, `NETWORK`, `MCP` |
| `MATCHER` | 범주 안에서의 대상. SHELL/GIT_PUSH/DEPLOY는 명령 접두어(`git push`, `docker compose up`, `gh pr merge`), FILE_WRITE는 glob(`**/.env*`), NETWORK는 도메인, MCP는 `server:tool`. `*` = 범주 전체 |
| `DECISION` | `ALLOW` / `ASK` / `DENY` |
| `PRIORITY` | 높은 값 우선. 같으면 DENY > ASK > ALLOW |
| `DESCRIPTION` | 웹 표시용 설명 |

평가는 ayaan 한 곳(`rules.permission_decision(tool, tool_name, tool_input, policies)`, 순수 함수)에서 한다. Tool별 행이 `*` 행보다 우선이고, 매치가 없으면 범주 기본값을 따른다.
시드: `* FILE_WRITE * ALLOW`, `* SHELL * ALLOW`, `* NETWORK * ALLOW`, `* MCP * ALLOW`, `* GIT_PUSH * ASK`, `* DEPLOY docker ASK`, `* DEPLOY kubectl ASK`, `* FILE_WRITE **/.env* DENY`, `* SHELL "rm -rf /" DENY`.
Claude 도구 → 범주 매핑: `Edit|Write|MultiEdit|NotebookEdit` → FILE_WRITE. `Bash`는 명령 앞부분이 `git push`면 GIT_PUSH, DEPLOY 매처에 맞으면 DEPLOY, 나머지는 SHELL. `WebFetch|WebSearch` → NETWORK. `mcp__*` → MCP. 그 외(Read, Glob, Grep 등 읽기)는 항상 허용.

### 6.2 Claude 배선
- argv: `--dangerously-skip-permissions`는 **유지**하고, `--settings '<inline json>'`으로 PreToolUse hook(matcher `Bash|Edit|Write|MultiEdit|NotebookEdit|WebFetch|WebSearch|mcp__.*`, command `ayaan-hook`, timeout 30)을 넣는다. 0단계에서 "bypassPermissions에서도 PreToolUse deny가 적용되는지"를 실측한다. 적용되지 않으면 `--permission-mode default` + hook `allow`로 바꾼다(문서상 Manual 모드는 hook이 승인할 수 있음).
- `ayaan-hook`: 이미지에 넣는 파이썬 콘솔 스크립트(`pyproject` entry point). stdin의 hook JSON을 `POST http://127.0.0.1:8080/tsh/api/v1/ayaan/hook/pre-tool`로 전달하고, 응답 `{decision, reason}`를 `hookSpecificOutput.permissionDecision`으로 출력한다. ayaan에 연결할 수 없으면 **deny**(fail-closed).
- 실행 환경 변수(`_env`): `AYN_TASK_ID`, `AYN_RUN_ID`, `AYN_HOOK_TOKEN`(run마다 `secrets.token_urlsafe(32)`, ayaan 메모리 `{token: (taskId, runId)}`에 보관, run 종료 시 폐기). `AYAAN_*`는 `child_env`가 지우므로 접두어를 다르게 한다.
- 엔드포인트 판정:
  - ALLOW → allow. DENY → deny(사유: "ayaan 정책 DENY").
  - ASK → 같은 작업에 `APPROVED` + 미소비 + `INPUT_HASH` 일치 요청이 있으면 `CONSUMED` 처리 후 allow. 없으면 PERMISSION 요청 생성(OPEN, toolName, 입력 요약 1,000자, `INPUT_HASH=sha256(tool_name+정규화 JSON 입력)`), deny(사유: "ayaan 승인 대기(요청 <id>). 우회하지 말고 즉시 종료하라.").
  - 이미 OPEN 요청이 있으면 deny(같은 사유).
- 이후 흐름은 §5와 같다. Telegram 버튼은 [허용 1회] / [거부]이고, 재개 프롬프트는 "승인됨: `<명령 요약>`을 다시 실행해 이어서 진행해." 또는 "거부됨: 이 동작 없이 진행하거나 대안을 제시해."이다.

### 6.3 승인 범위 (D4)
승인은 `(TASK_ID, INPUT_HASH)` 1회다. 모델이 재개 후 명령을 조금 바꾸면(인자 순서 등) 해시가 달라져 다시 승인을 묻는다. 의도된 보수적 동작이다.

### 6.4 다른 CLI (1차: 정적 변환)
`adapters.<Tool>.permission_args(policies)`가 run 직전에 argv/설정 파일을 만든다. ASK는 DENY로 다룬다.
- Codex: sandbox 선택만 한다(§3.2 표). `--dangerously-bypass-approvals-and-sandbox`는 "모든 범주 ALLOW이고 DENY 없음"일 때만 유지한다.
- Gemini: `~/.gemini/policies/ayaan.toml`을 매 run 덮어쓴다(`commandPrefix`, `decision`). `--yolo` 대신 `--approval-mode default`를 쓴다. 미허용 확인 요청은 비대화형에서 deny된다(확인).
- Cursor: `~/.cursor/cli-config.json`의 `permissions`를 쓰고 `--force`를 뺀다.
- 각 Tool의 hook 지원이 0단계 또는 이후 실측으로 확인되면 Claude와 같은 `ayaan-hook`으로 옮긴다(엔드포인트는 Tool 무관 입력을 받도록 `tool`, `toolName`, `input`으로 정규화).

---

## 7. 기획 → 개발 2단계

### 7.1 작업 등록
- `SN_AYN_TASK.PHASE` = `PLAN`(기본) / `DEV`. `TaskReq`에 `phase`, `skillCd`를 추가한다. Telegram `/task`는 PLAN이고, `/run KEY [tool] [dev]`는 마지막 인자 `dev`면 DEV다(D7).
- PLAN 프롬프트: 자유 프롬프트는 그대로 쓰고, 이슈는 새 기본 템플릿 `PLAN_TEMPLATE`을 쓴다("CIRA 이슈 {issueKey}: {title}\n\n{content}\n\n위 이슈의 기획·설계서를 작성해. 코드는 수정하지 마. 설계서는 작업공간에 파일로 저장하고 마지막 줄에 'AYAAN_RESULT: <설계서 경로>'를 출력해."). 작업공간 매핑의 `PROMPT_TEMPLATE`은 DEV용으로 유지한다.

### 7.2 기획 완료 처리
1. PLAN 작업 COMPLETED → `resultSummary`에서 경로를 추출한다. 첫 토큰이 상대·절대 경로이고, 작업공간 안에 있으며 파일이 존재해야 한다. 아니면 경로 없이 진행하고 개발 프롬프트에 기획 작업의 결과 요약을 넣는다(알림에 "기획서 경로 확인 실패" 표시).
2. 기획 작업 `RUNNING→DONE`(`planDocPath` 저장).
3. 개발 작업을 생성한다. `status=WAITING`, `phase=DEV`, `parentTaskId=<기획 id>`, 같은 이슈·작업공간·Tool, `skillCd=NULL`(실행 시 Tool의 DEV 기본값이 적용됨; 기획 작업에 지정 DEV skill을 넣는 칸은 만들지 않음 — YAGNI), 프롬프트는 `DEV_TEMPLATE`("{planDocPath} 기획서대로 구현하고 테스트·빌드를 통과시켜. 저장소 규칙(CLAUDE.md/AGENTS.md)을 따라. 완료되면 'AYAAN_RESULT: <요약과 PR 링크>'"). 이슈 작업이고 작업공간 매핑에 `PROMPT_TEMPLATE`이 있으면 그 렌더링 결과 뒤에 기획서 경로 문장을 덧붙인다.
4. START 요청(OPEN)을 만들고 ECC notify `PLAN_DONE`을 보낸다. 본문은 기획 요약, 기획서 경로, 결정 필요 건수(결과에 있으면)이고 버튼은 [실행] [취소].
5. [실행] → START 요청 ANSWERED(choice 0) → `WAITING→QUEUED`. [취소] → `WAITING→CANCELLED`.
- 2와 3 사이 실패(개발 작업 생성 실패): 기획 작업은 DONE으로 두고 FAILED 알림 "개발 작업 생성 실패, 웹에서 [개발 작업 만들기]"를 보낸다. 웹 상세에 그 버튼(`POST /tasks/{id}/dev-task`, 같은 로직)을 둔다.
- 이슈당 활성 작업 1건 유니크 제약: 기획이 먼저 DONE이 된 뒤 개발이 WAITING으로 생기므로 충돌이 없다.

---

## 8. 보안

| 위협 | 대책 |
|---|---|
| 허용되지 않은 Telegram 사용자의 응답 | 기존 불변식: 모든 명령·callback을 `from_user.id ∈ ECC_TELEGRAM_ALLOWED_USER_IDS`로 검사한다. 답장·`/answer`도 같다 |
| callback_data 위조(수정된 클라이언트는 임의 data를 보낼 수 있음) | callback_data는 신뢰하지 않는다. `ayn:ans:<reqId>:<n>`(≤64B, 비밀값 없음)를 받으면 ayaan이 ① 요청 존재 ② `STATUS=OPEN` ③ `n`이 선택지 범위 안 ④ KIND별 허용값(PERMISSION 0/1, START 0/1)을 검증한다. `reqId`는 추측 불가(uuid hex 12 = 48bit)지만, 그것이 유일한 방어선은 아니다(allowlist가 1차 방어선) |
| 재사용(같은 버튼 두 번, 오래된 메시지) | query에서 `UPDATE … SET STATUS='ANSWERED' … WHERE OBJ_ID=:id AND STATUS='OPEN'` CAS 1회만 성공한다. 이후에는 409 "이미 처리됨"을 반환하고 ECC가 메시지를 "처리됨(누가, 언제)"으로 고친다. 권한 승인은 다시 `APPROVED→CONSUMED` CAS로 1회만 쓰인다 |
| 승인한 것과 다른 동작 실행 | 승인은 `INPUT_HASH`에 묶인다(§6.3). Telegram 메시지에 실제 명령·파일 경로를 보여 준다(1,000자 제한, 잘리면 "…(잘림)" 표시) |
| 실행 중인 모델이 스스로 승인 | hook 토큰(`AYN_HOOK_TOKEN`)은 **요청 등록·판정 조회만** 할 수 있고 답변 API는 쓸 수 없다. 답변 API는 기존 `require_caller`(ECC 서비스 토큰 또는 허용 JWT)만 받는다. hook 엔드포인트는 `127.0.0.1`에서 온 요청만 받는다(`request.client.host` 검사). 모델이 토큰으로 가짜 요청을 등록해도 사람에게 질문이 하나 더 가는 것뿐이다 |
| 응답자 기록 | `ANSWERED_BY`에 Telegram은 `tg:<user_id>`, 웹은 `web:<sub>`를 남긴다. ECC가 서비스 토큰으로 호출할 때 body의 `answeredBy`로 전달한다(ECC는 신뢰 경계 안쪽) |
| 로그 노출 | ECC 불변식 5(프롬프트·메시지 원문을 로그에 남기지 않음)를 질문·답변에도 적용한다. ayaan 로그에는 reqId·kind만 남긴다 |
| 우회 가능한 정책 | §3 한계 1. 자격증명 최소화를 운영 지침(README)에 적는다 |

---

## 9. 저장소별 변경점

### 9.1 d94-app-query (V530 마이그레이션 하나)
> 기준 브랜치는 `origin/main`이다(ayaan 테이블 V500·V520이 있음). 작업공간의 로컬 체크아웃은 오래된 `master`(Initial commit)에 있으니 구현 전에 `main`으로 전환해야 한다. 마지막 마이그레이션이 V520이라 다음 번호는 V530이다.

```sql
-- V530__ayaan_skill_permission_request.sql (공통 감사 컬럼 SRV_ID…MODIFIED_BY는 기존 표준대로)
CREATE TABLE SN_AYN_SKILL (SKILL_CD VARCHAR(50) PK, SKILL_NM VARCHAR(100) NOT NULL, TOOL_CD VARCHAR(20) NOT NULL,
  PHASE VARCHAR(10) NOT NULL /*PLAN|DEV|ANY*/, INVOKE_TEXT VARCHAR(500) NOT NULL, PLUGIN_REF VARCHAR(200),
  DESCRIPTION TEXT, ENABLED BOOLEAN NOT NULL DEFAULT TRUE, SORT_NO INTEGER NOT NULL DEFAULT 0, …);
ALTER TABLE SN_AYN_TOOL ADD COLUMN PLAN_SKILL_CD VARCHAR(50), ADD COLUMN DEV_SKILL_CD VARCHAR(50);
ALTER TABLE SN_AYN_TASK ADD COLUMN PHASE VARCHAR(10) NOT NULL DEFAULT 'DEV', ADD COLUMN SKILL_CD VARCHAR(50),
  ADD COLUMN PARENT_TASK_ID VARCHAR(100), ADD COLUMN PLAN_DOC_PATH VARCHAR(500);
  -- 기존 행은 DEV(지금까지의 동작). 새 작업의 기본값 PLAN은 ayaan이 정한다
CREATE TABLE SN_AYN_PERMISSION (OBJ_ID VARCHAR(100) PK, TOOL_CD VARCHAR(20) NOT NULL, CATEGORY VARCHAR(20) NOT NULL,
  MATCHER VARCHAR(300) NOT NULL, DECISION VARCHAR(10) NOT NULL, PRIORITY INTEGER NOT NULL DEFAULT 0, DESCRIPTION VARCHAR(500), …);
CREATE TABLE SN_AYN_REQUEST (OBJ_ID VARCHAR(100) PK, TASK_ID VARCHAR(100) NOT NULL REFERENCES SN_AYN_TASK, RUN_ID VARCHAR(100),
  KIND VARCHAR(20) NOT NULL /*QUESTION|PERMISSION|START*/, STATUS VARCHAR(20) NOT NULL /*OPEN|ANSWERED|CONSUMED|CANCELLED*/,
  QUESTION TEXT, OPTIONS TEXT /*JSON 배열*/, RECOMMEND INTEGER, CONTEXT TEXT, TOOL_NAME VARCHAR(100), INPUT_HASH VARCHAR(64),
  ANSWER TEXT, DECISION VARCHAR(10) /*ALLOW|DENY, PERMISSION·START*/, ANSWERED_BY VARCHAR(100), ANSWERED_AT TIMESTAMP,
  CONSUMED_AT TIMESTAMP, …);
CREATE UNIQUE INDEX ux_ayn_req_open_task ON SN_AYN_REQUEST (TASK_ID) WHERE STATUS = 'OPEN';
CREATE INDEX ix_ayn_req_task ON SN_AYN_REQUEST (TASK_ID, CREATED_AT);
DROP INDEX ux_ayn_task_active_issue;
CREATE UNIQUE INDEX ux_ayn_task_active_issue ON SN_AYN_TASK (ISSUE_KEY)
  WHERE STATUS IN ('QUEUED','RUNNING','PAUSED','WAITING') AND ISSUE_KEY IS NOT NULL;
-- 시드: §2.5 skill 3건, CLAUDE 기본 skill, §6.1 정책, CONFIG NOTIFY_EVENTS에 WAITING,PLAN_DONE 추가(UPDATE … WHERE 값이 시드 원값일 때만)
```
- ORM: `data/orm/ayaan/aynSkill`, `aynPermission`, `aynRequest`(Model/Repo/Access, CLAUDE.md 명명 규칙). `SnAynToolModel`·`SnAynTaskModel`에 컬럼 추가, `TaskDto`·`ToolDto` 필드 추가.
- 내부 API(`/api/v1/internal/ayaan`):
  - `GET/PUT/DELETE /skills[/{cd}]`, `GET /permissions`, `PUT /permissions`(전체 교체, 트랜잭션 1회), `DELETE /permissions/{id}`.
  - `POST /tasks/{id}/requests`(OPEN 중복이면 409 + 기존 요청), `GET /requests?status=&taskId=`, `GET /requests/{id}`.
  - `POST /requests/{id}/answer` {answer, decision, answeredBy} → CAS `OPEN→ANSWERED`, 불일치 409. `POST /requests/{id}/consume` → CAS `ANSWERED→CONSUMED`. `POST /requests/{id}/cancel`.
  - `createTask`는 `phase`, `skillCd`, `parentTaskId`, `status`(WAITING 허용) 필드를 받는다. `transition` 필드 화이트리스트에 `planDocPath`를 추가한다.
  - `getTask` 응답에 `requests`(최근 20건)를 포함한다.
- 상태 검증: `transition`의 next/expected 값에 `WAITING`이 허용되는지 확인한다(현재는 값을 검증하지 않음 → 변경 불필요, 테스트만 추가).

### 9.2 d94-app-ayaan
| 파일 | 변경 |
|---|---|
| `rules.py` | `NOTIFY`에 `WAITING`, `PLAN_DONE` 추가(상태 목록 상수는 없음 — 상태값은 query와 문자열로 주고받는다). `validate_skill`, `validate_permission`, `permission_decision`(순수), `parse_ask`, `plan_doc_path(result, workspace)` |
| `adapters.py` | `RunEvent.ask`. `_finish`에서 `AYAAN_ASK` 파싱. Claude: `--append-system-prompt`, `--disallowedTools AskUserQuestion`, `--settings`(hook). 각 Tool `permission_args(policies)` |
| `hook.py`(신규) | `ayaan-hook` 콘솔 스크립트(표준 라이브러리 `urllib`만 사용) |
| `scheduler.py` | `_STATUS_KO`(리포트 라벨)에 `("WAITING", "응답 대기")` 추가. 프롬프트 조립(skill + 프로토콜), 플러그인 확인(D6), `_settle` 판정 순서(§4.3), WAITING 전이와 알림, 답변 기반 재개 프롬프트, PLAN 완료 → 개발 작업 생성, 리포트에 WAITING 목록 추가 |
| `api.py` | `TaskReq.phase/skillCd`. `GET/PUT/DELETE /skills`, `GET/PUT /permissions`, `GET /requests`, `POST /requests/{id}/answer`(answer 또는 choice, answeredBy), `POST /tasks/{id}/dev-task`, `POST /hook/pre-tool`(hook 토큰 + localhost, `require_caller` 제외). `cancel`에 WAITING 추가 |
| `clients.py` | 위 query 엔드포인트용 메서드 |
| `setup_check.py` | 단계 7: 세션 보존 설정, skill 플러그인 설치 상태 |
| `fake_cli.py` + `fake_fixtures/` | 시나리오 `ask`(AYAAN_ASK 출력), `plan`(AYAAN_RESULT: docs/x.md, 파일 생성) |
| `tests/fakes.py` | `InMemoryQuery`에 skills/permissions/requests, WAITING |

알림 버튼 형식(ayaan → ECC): `{"label":"A안","action":"answer","requestId":"<12hex>","choice":0}`, 기존 `{"action":"resume|cancel","taskId"}`도 유지한다.

### 9.3 d94-app-ecc
- `NotifyButton`: `action: Literal["resume","cancel","answer"]`, `taskId: str | None`, `requestId: str | None`, `choice: int | None`(answer일 때 requestId·choice 필수, model_validator로 검사).
- `notify_keyboard`: answer는 `ayn:ans:<requestId>:<choice>`. 버튼이 3개를 넘으면 한 줄에 하나씩 둔다.
- `handle_callback`: `ayn:ans:` 분기 → `ayaan.answer(reqId, {"choice": n, "answeredBy": f"tg:{uid}"})`. 결과가 200이면 원 메시지에 "✔ <선택지> (응답됨)"을 덧붙여 수정하고 버튼을 제거한다. 409면 "이미 처리된 요청입니다"로 답한다.
- 명령: `/answer <reqId> <내용>`, 질문 메시지에 **답장**(`message.reply_to_message.text`에서 정규식 `요청 ID: ([0-9a-f]{12})`로 ID를 추출. 봇 자신의 메시지인지 `from_user.is_bot`과 bot id로 확인) → `{"answer": text}`. `/waiting`: 열린 요청 목록. `/run KEY [tool] [dev]`. `ACTIVE`에 WAITING 추가.
- `ayaan_client.py`: `answer`, `waiting`.
- 문서: `docs/features/telegram-approval.md`(입력·callback 목록), `ecc/telegram/AGENTS.md`, `docs/architecture.md`(흐름).
- 정기 리포트는 ayaan이 만들므로 ECC 변경은 없다(D12).

### 9.4 d94-app-web (`/ayaan`)
- `settings/page.tsx`: 탭 2개 추가.
  - **Skill**: 표(코드, 이름, Tool, 단계, 호출 문자열, 플러그인, 사용, 설명). 행 추가·수정 모달, 플러그인은 `CLAUDE_PLUGINS` 항목 드롭다운 + 직접 입력, 저장 경고 표시.
  - **권한**: Tool 선택(전체/CLAUDE/…) → 범주별 행(매처, 결정 ALLOW/ASK/DENY 세그먼트, 우선순위, 설명). 비Claude Tool에서 ASK를 고르면 경고를 표시한다.
  - Tool 탭: "기획 기본 skill", "개발 기본 skill" 드롭다운.
- `tasks/page.tsx` 새 작업 폼: 단계 라디오(기획[기본]/바로 개발) + skill 드롭다운(Tool과 단계로 필터링, 첫 항목 "Tool 기본값(<이름>)", 마지막 "사용 안 함"). `taskBody.ts`에 `phase`, `skillCd`를 추가한다.
- 목록·대시보드: `WAITING` 배지 "응답 대기"(PAUSED "한도 대기"와 색 구분), 대시보드에 "응답 대기" 섹션.
- `tasks/[taskId]/page.tsx`: 요청 패널(열린 요청: 질문, 선택지 버튼, 자유 답변 입력, 권한이면 명령과 [허용 1회][거부], START면 [실행][취소]), 요청 이력, 부모/자식 작업 링크, 기획서 경로.
- `constant/help.ts`: 새 항목 설명. `types/ayaan.ts`, `lib/api/ayaan.ts`, `lib/mock/ayaan.ts`, L3 `AGENTS.md`를 갱신하고 `npm run docs:check`를 실행한다.

---

## 10. 구현 단계와 테스트

각 단계는 독립 PR로 하고, 저장소 규칙의 검증 명령(ayaan·ecc `pytest -q` + `ruff check .`, query `mvn test`(IT 포함), web `npx tsc --noEmit` + `npx vitest run` + `npm run docs:check`)을 통과해야 한다.

| # | 단계 | 저장소 | 테스트 |
|---|---|---|---|
| 0 | **CLI 실측 스파이크**(코드 반영 없음): Claude 2.1.322에서 ① `-p --dangerously-skip-permissions --settings`의 PreToolUse deny 적용 여부 ② `--append-system-prompt`가 resume에서도 유지되는지 ③ `AYAAN_ASK` 준수 ④ `/superpowers:brainstorming` 슬래시 호출이 `-p`에서 동작하는지 ⑤ 세션 파일 위치. Codex/Gemini/Cursor는 세션 위치와 hook 실행 여부 | ayaan `docs/cli-probe.md` | 실측 로그를 기록한다. 결과에 따라 §6.2 대안을 확정한다 |
| 1 | V530 skill 부분 + skill API, TOOL 기본 skill 컬럼 | query | `AyaanSchemaIT`(테이블·시드), `AyaanInternalIT`(skills CRUD) |
| 2 | skill 해석·프롬프트 조립·플러그인 확인(D6), skills 프록시 API, `TaskReq.skillCd` | ayaan | `test_rules`(우선순위, NONE), `test_scheduler`(플러그인 없음 → FAILED, sync 1회 후 성공), `test_api` |
| 3 | Skill 탭, Tool 기본 skill, 새 작업 폼 드롭다운 | web | `taskBody.test.ts`, 검증 테스트, MSW로 탭 렌더 |
| 4 | WAITING 상태(인덱스 재생성), SN_AYN_REQUEST + 요청 API(CAS) | query | IT: 이슈당 활성 제약에 WAITING 포함, 같은 요청 answer 2회 → 두 번째 409, consume CAS, OPEN 중복 409, claim이 WAITING을 고르지 않음 |
| 5 | 질문 프로토콜: `AYAAN_ASK` 파싱, WAITING 전이, 답변 API, 답변 재개 프롬프트, 세션 유실 시 이력 첨부 | ayaan | `test_adapters`(마커 4종 + 깨진 JSON), `test_scheduler`: FAKE `ask` → WAITING + notify 버튼 → answer → QUEUED → 다음 run 프롬프트에 답 포함, 요청 CONSUMED. 재시작 시뮬레이션(Scheduler 재생성 후 answer) |
| 6 | ECC answer 버튼·답장·`/answer`·`/waiting` | ecc | `test_notify`(answer 버튼 callback_data ≤64B), `test_commands`(비허용 사용자 거부, 409 처리, 답장에서 ID 추출, 봇이 아닌 메시지에 답장하면 무시) |
| 7 | 웹 WAITING 배지·요청 패널 | web | 컴포넌트 테스트(선택지 클릭 → answer 호출), mock |
| 8 | 기획→개발: PHASE, PLAN_TEMPLATE, 기획서 경로 추출, 개발 작업 WAITING+START, `/dev-task`, `PLAN_DONE` 알림, ECC `/run … dev`, 웹 단계 라디오·[실행] | query·ayaan·ecc·web | ayaan: FAKE `plan` → 기획 DONE + 자식 작업 WAITING + START 요청, [실행] → QUEUED → DEV skill 프롬프트. 경로가 작업공간 밖이거나 없을 때 처리. 생성 실패 시 FAILED 알림 |
| 9 | 권한 정책 테이블·API·웹 권한 탭 | query·ayaan·web | `permission_decision` 표 기반 테스트(우선순위, Tool vs `*`, Bash 범주 분류), IT CRUD |
| 10 | Claude hook: `ayaan-hook`, `/hook/pre-tool`(토큰·localhost), 승인 1회 소비, `_settle` OPEN 요청 우선 규칙 | ayaan | 단위: hook 토큰 없음/다른 task → 401, localhost 아님 → 403, ASK → OPEN 생성 + deny, 승인 후 같은 해시 → allow + CONSUMED, 다른 해시 → 다시 ASK, ayaan 다운 → hook deny. **수동 실측**: 실제 Claude로 `git push` ASK → Telegram 승인 → 재개 후 push 1회 |
| 11 | 다른 CLI 정적 권한 변환(Codex sandbox, Gemini policy TOML, Cursor cli-config) | ayaan | `test_adapters`: 정책 → argv/파일 내용 스냅샷. ASK=DENY 경고 API |
| 12 | setup 단계 7, README·CLAUDE.md·ECC/web 문서 갱신, 운영 지침(자격증명 최소화) | 전체 | `docs:check`, 수동 점검 |

**전체 수동 시나리오(FAKE, 배포 후)**: `printf 'ask\nplan\nok\n' > ./data/fake-scenario` → 웹에서 기획 작업 등록 → Telegram 질문 수신 → 버튼 응답 → 기획 완료 알림 [실행] → 개발 작업 DONE. 중간에 `docker restart d94-app-ayaan` 후 응답해도 이어지는지 확인한다.

---

## 11. 접근 방식 비교 (기록)

| 방식 | 내용 | 장점 | 단점 | 판단 |
|---|---|---|---|---|
| A. 종료-재개 + 텍스트 마커 + Claude hook | 질문은 마커, 권한은 hook이 즉시 deny 후 승인되면 재개 | CLI 공통, 무기한·재시작에 강함, 새 서버 없음 | 모델 준수 의존, 승인 후 한 턴 더 소모 | **채택** |
| B. 블로킹 MCP | `ask`/`approve` MCP 도구가 사람 응답까지 블로킹, `--permission-prompt-tool` | 대화 끊김 없음, 구조화 입력 | 프로세스·리스를 무기한 유지, 재시작 시 유실, MCP 타임아웃(Codex 60s 기본), CLI별 MCP 설정 4종 | 기각(대기가 무기한이라는 요구와 충돌) |
| C. 하이브리드 | N분 블로킹 후 A로 전환 | 빠른 응답은 끊김 없음 | 두 경로를 모두 유지해야 함 | 필요해지면(응답이 대개 수 분 안에 온다는 데이터가 쌓이면) 추가 |

## 12. 빌더에게 남긴 것
- 0단계 실측 결과에 따른 Claude 권한 모드 선택(§6.2).
- Telegram 메시지 문구 세부, 버튼 배치.
- 웹 컴포넌트 구조(기존 `HelpCard`, settings 탭 패턴 재사용).
- `PLAN_TEMPLATE`/`DEV_TEMPLATE` 문구 미세 조정. 단, `AYAAN_RESULT` 규약은 유지한다.

> 검토 메모: brainstorming 절차의 "빌더 점검"은 무인 실행이라 별도 리뷰어 없이 작성자가 빌더 관점에서 다시 읽는 방식으로 대신했다. 그 과정에서 나온 질문 중 사용자만 답할 수 있는 것은 §0에 넣었고, 나머지는 본문에 "작성자 판단"으로 반영했다.
