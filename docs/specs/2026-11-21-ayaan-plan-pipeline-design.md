# Ayaan 계획 파이프라인 · worktree 작업공간 설계

- 날짜: 2026-11-21
- 범위: d94-app-ayaan (스케줄러·git·API), d94-query (Ayaan 테이블·내부 API), d94-web (기준정보 작업공간 탭·Ayaan 작업 화면)
- 관련: `2026-11-12-ayaan-skills-approvals-design.md`, d94-note `docs/superpowers/specs/2026-11-21-cira-task-columns-design.md` (첫 적용 대상: CIRA 프로젝트 TASKS)

## 1. 의도

CIRA의 개발 Task들을 Ayaan에 맡겨 **브랜치 생성 → 개발 → push → 머지 → 배포 → 정리**까지 사람 손 없이 이어서 처리하게 한다. Ayaan이 이 단계들을 체인처럼 관리한다("LangChain처럼").

지금 Ayaan은
- 프로젝트 하나에 경로 하나를 매핑하고(`SN_AYN_WORKSPACE`), 미리 clone해 둔 공용 폴더에서 CLI를 실행한다.
- git(브랜치·커밋·push)은 CLI에 맡긴다.
- 같은 경로는 한 번에 하나만 실행한다(`ux_ayn_task_running_ws`).

그래서 여러 저장소에 걸친 계획(예: TASKS = d94-query + d94-web + d94-lib-pom)을 돌릴 수 없고, 작업 후 정리도 없다.

## 2. 목표 / 비목표 / 반목표

**목표**
- 작업공간 매핑을 "프로젝트 → 저장소 여러 개"로 바꾸고, 기본 clone은 처음 쓸 때 자동으로 만든다.
- 작업마다 저장소별 **git worktree**로 작업 폴더를 만든다.
- **계획 = CIRA 마일스톤.** 마일스톤의 Task를 키 순서로 실행하고, 다 끝나면 main 머지 → 브랜치 삭제 → deploy push.
- 끝난 계획의 worktree를 주기적으로 찾아 삭제한다.
- 매핑이 없는 프로젝트에서 바로 작업공간 설정으로 가는 버튼.

**비목표**
- 사람 승인 단계(CIRA 승인 버튼) — 이번엔 자동으로 진행. 나중에 단계 사이에 끼울 수 있게만 둔다.
- 병렬 Task 실행(한 계획 안에서는 순서대로 하나씩).
- LangGraph 등 워크플로 엔진 도입.
- 배포 결과(GHCR 빌드·서버 반영) 확인. deploy push까지만 책임진다.

**반목표**
- push되지 않은 커밋이 있는 worktree를 지움 (작업 유실).
- 충돌 상태나 CLI가 테스트 실패를 보고한 상태로 main이나 deploy에 반영됨.
- 기본 clone을 지움.
- 단계 도중 Ayaan이 재시작되면 계획이 처음부터 다시 돌거나 영원히 멈춤.

## 3. 결정된 사항 (사용자 결정)

| # | 결정 |
|---|---|
| P1 | 저장소 준비는 clone이 아니라 **worktree** (기본 clone 하나 + 작업별 worktree). |
| P2 | 개발 Task마다 브랜치를 만들고, 개발이 끝나면 그 브랜치로 push. |
| P3 | 계획의 Task가 모두 끝나면 main에 머지, 생성된 브랜치 삭제, **deploy 브랜치에 push해 배포**. |
| P4 | 이 단계들은 Ayaan이 관리한다. |
| P5 | 전체 개발이 끝난 worktree는 **주기적으로 찾아 삭제**(clone은 남김). |
| P6 | 계획 단위는 **CIRA 마일스톤**. |
| P7 | 관리 방식은 **Ayaan 안의 상태 기계**(아래 4절 A안). |
| P8 | **Ayaan은 작업을 관리·할당·모니터링하는 주체**다. 빌드·테스트는 CLI의 일이다. Ayaan은 테스트를 직접 돌리지 않고, CLI가 보고한 결과(Task COMPLETED/FAILED)만 보고 진행 여부를 정한다. |

## 4. 검토한 방식

- **A. Ayaan 상태 기계 (채택)** — 계획 상태를 d94-query에 저장하고 기존 스케줄러 루프가 단계를 진행. git은 Ayaan이 직접 실행. 새 의존성 없음, 재시작 시 저장된 단계부터 재개, 진행 상황을 웹에서 조회 가능. 단계 변경은 코드 수정.
- **B. LangGraph** — 그래프 선언·체크포인트 내장. 하지만 이번 흐름은 AI 판단이 아닌 고정 git/배포 순서라 이점이 작고, 의존성과 상태 저장소가 하나 더 생긴다.
- **C. GitHub Actions가 머지·배포** — 흐름과 상태가 GitHub로 흩어지고, "CIRA·Ayaan 안에서 관리"하는 방향과 맞지 않는다. 제외.

## 5. 설계

### 5.1 저장소 매핑

`SN_AYN_WORKSPACE`(프로젝트 단위 설정: 기본 Tool, 프롬프트 템플릿)는 유지하고, 저장소 목록을 새 테이블로 붙인다.

```
SN_AYN_WORKSPACE_REPO
  PROJECT_KEY  FK → SN_AYN_WORKSPACE
  REPO_NM      VARCHAR(100)   -- 폴더 이름. 예: d94-query
  GIT_URL      VARCHAR(500)   -- clone 원본
  BASE_BRANCH  VARCHAR(100) DEFAULT 'main'
  DEPLOY_BRANCH VARCHAR(100)  -- 비우면 배포 안 함. 예: deploy
  SORT_ORD     SMALLINT       -- 머지·배포 순서 (d94-query → d94-web)
  PK(PROJECT_KEY, REPO_NM)
```

기존 `WORKSPACE_PATH`는 이관 후 읽지 않는다. 이관: 기존 매핑마다, 경로의 저장소에서 `origin` URL을 읽어 `SN_AYN_WORKSPACE_REPO` 한 줄을 만든다(Ayaan 부팅 시 한 번, d94-query 마이그레이션으로는 경로를 읽을 수 없으므로).

### 5.2 폴더 구조와 git

```
/workspace/.repos/<REPO_NM>/            기본 clone (지우지 않음)
/workspace/.tasks/<issueKey>/<REPO_NM>/ 작업별 worktree
```

- **기본 clone 확보**: 없으면 `git clone <GIT_URL>`. 있으면 `git fetch --prune`.
- **worktree 생성** (Task 시작 직전, Ayaan이 실행):
  `git -C .repos/<repo> worktree add .tasks/<issueKey>/<repo> -b ayaan/<issueKey> <시작점>`
  - 계획 안의 Task: 시작점 = `origin/ayaan/plan-<마일스톤키>`
  - 계획 밖 단독 Task: 시작점 = `origin/<BASE_BRANCH>`
- CLI는 `.tasks/<issueKey>/`(모든 저장소의 worktree를 담은 폴더)에서 실행한다. 프롬프트 앞에 다음 안내를 덧붙인다(P8).
  - 이 폴더에 있는 저장소 목록과 각 브랜치
  - 바꾼 저장소의 빌드·테스트를 직접 돌려 통과시킨 뒤에만 완료할 것
  - 테스트가 통과하지 않으면 실패로 끝낼 것
  - 커밋만 하고 push는 하지 말 것
- **Task 완료 후** (CLI가 COMPLETED로 끝나면 Ayaan이 실행): 저장소마다 커밋이 있으면 `git push -u origin ayaan/<issueKey>`. 커밋되지 않은 변경이 남아 있으면 Task를 FAILED로 두고 알린다(자동 커밋하지 않음).
- `SN_AYN_TASK.WORKSPACE_PATH` = `/workspace/.tasks/<issueKey>`. 기존 `ux_ayn_task_running_ws`는 그대로 두면 Task별로 경로가 달라 의미가 없어지지만, 같은 이슈의 동시 실행 방지는 `ux_ayn_task_active_issue`가 맡는다.

### 5.3 계획(마일스톤) 상태 기계

```
SN_AYN_PLAN
  OBJ_ID, PROJECT_KEY, MILESTONE_ID, MILESTONE_KEY(브랜치 이름용 슬러그),
  STAGE, CURRENT_ISSUE_KEY, LAST_ERROR, STARTED_AT, ENDED_AT, (BaseModel)
  UNIQUE(PROJECT_KEY) WHERE STAGE NOT IN ('DONE','CANCELLED')   -- 프로젝트당 진행 중 계획 1개

SN_AYN_TASK
  + PLAN_ID (NULL = 계획 밖 단독 Task)
```

| STAGE | 하는 일 | 다음 |
|---|---|---|
| `PREPARING` | 저장소마다 기본 clone 확보, `origin/<BASE>`에서 `ayaan/plan-<키>` 생성·push | `RUNNING` |
| `RUNNING` | 마일스톤 이슈 중 아직 안 끝난 첫 이슈(키 순서)로 Ayaan Task 등록 → 끝나면 Task 브랜치를 계획 브랜치에 머지·push → Task 브랜치(로컬·원격) 삭제 → 다음 이슈 | 이슈가 다 끝나면 `MERGING` |
| `MERGING` | 저장소마다(`SORT_ORD` 순) 계획 브랜치를 최신 `origin/<BASE>` 위로 rebase → `<BASE>`에 fast-forward 머지·push | `DEPLOYING` |
| `DEPLOYING` | 계획에서 바뀐 저장소만(`SORT_ORD` 순) `<BASE>`를 `DEPLOY_BRANCH`에 머지·push | `CLEANING` |
| `CLEANING` | 계획 브랜치(로컬·원격) 삭제, 계획의 worktree 제거 | `DONE` |
| `HALTED` | 어느 단계든 실패(충돌, push 거부, Task FAILED). `LAST_ERROR` 기록, Telegram 알림, worktree 보존 | 사람이 [재개] → 실패한 단계부터, [취소] → `CANCELLED` |

- **진행**: 기존 스케줄러 `tick()`에 `advance_plans()` 한 줄을 추가. 단계마다 한 번에 한 단계씩만 진행하고, 단계 시작·끝에 STAGE를 저장한다. 각 단계는 다시 실행해도 안전하게 만든다(이미 있는 브랜치는 재사용, 이미 머지된 커밋은 건너뜀). 그래서 재시작하면 저장된 단계부터 이어간다.
- **대상 이슈** (작성자 판단):
  - 마일스톤 이슈 중 Epic 유형과 CIRA 상태가 이미 완료(category DONE)인 이슈는 건너뛴다.
  - 계획 도중 사람이 CIRA에서 완료 처리한 이슈도 건너뛴다.
  - 순서는 키의 번호 순서다(문자열 순서가 아님). 예: TASKS-2가 TASKS-10보다 먼저.
  - 진행 표시의 분모는 대상 이슈 수다.
- **실패한 Task와 재개** (작성자 판단):
  - 계획 Task가 FAILED이면 계획은 HALTED가 된다.
  - [재개]를 누르면 새 Task를 만들지 않고 **같은 Ayaan Task를 다시 대기열에 넣는다.** 남아 있는 worktree와 CLI 세션을 그대로 이어 쓴다.
  - Task 알림의 [재시도] 버튼도 같은 Task를 다시 대기열에 넣는다. 그 Task가 DONE이 되면 계획은 RUNNING으로 돌아가 이어서 진행한다.
- **push 위치**: Task의 `_settle`은 지금처럼 DONE 전이까지만 한다. 브랜치 push와 계획 브랜치 머지는 `advance_plans()`가 계획의 RUNNING 단계에서 한다. 계획 밖 단독 Task의 push는 DONE 직후 같은 함수가 처리한다.
- **완료 판단**: 마일스톤의 모든 이슈에 대해 이 계획에서 만든 Ayaan Task가 DONE이고 계획 브랜치에 머지되었으면 `RUNNING` 종료. 실행 중에 마일스톤에 이슈가 추가되면 같은 계획에서 이어서 처리한다.
- **CIRA 반영**: Task가 DONE이 되면 CIRA 이슈 상태를 완료로 전이(전이 불가하면 건너뛰고 기록만).
- **알림**: 계획 시작, 단계 실패(HALTED), 배포 완료를 ECC Telegram으로. 승인 동작은 넣지 않는다.

### 5.4 worktree 정리

- **즉시**: `CLEANING` 단계, 그리고 계획 밖 단독 Task가 DONE이 되어 브랜치 push를 확인한 뒤.
- **주기**: 설정 `WORKTREE_GC_CRON`(기본 매일 04:00 KST). `/workspace/.tasks/*` 중
  - 연결된 계획이 `DONE`·`CANCELLED`이거나, 연결된 Task가 없거나 DONE/CANCELLED인 폴더만 대상.
  - 지우기 전에 저장소마다 `git status --porcelain`이 비어 있고 `git log origin/<branch>..HEAD`가 비어 있는지 확인. 하나라도 남았으면 지우지 않고 리포트에 "정리 보류"로 표시.
  - `git worktree remove` 후 `git worktree prune`. 기본 clone은 건드리지 않는다.
- **수동**: Ayaan 작업 상세에 [작업공간 정리] 버튼. 같은 안전 확인을 거친다.

### 5.5 API

```
# Ayaan 공개 API (/tsh/api/v1/ayaan)
PUT    /workspaces/{projectKey}                 기존 + repos: [{repoNm, gitUrl, baseBranch, deployBranch, sortOrd}]
POST   /plans                                   {projectKey, milestoneId} → PREPARING 으로 생성
GET    /plans?projectKey=                       목록 (STAGE, 현재 이슈, 오류)
POST   /plans/{id}/resume | /plans/{id}/cancel
DELETE /tasks/{id}/workspace                    수동 worktree 정리
```
d94-query 내부 API에 `SN_AYN_PLAN`, `SN_AYN_WORKSPACE_REPO` CRUD와 마일스톤 이슈 목록 조회를 추가한다.

### 5.6 화면

- **기준정보 › Ayaan › 작업공간**: 매핑 폼에 저장소 목록(이름·URL·기준 브랜치·배포 브랜치, 순서 위/아래). URL `?tab=workspace&projectKey=X`면 해당 탭과 키가 채워진 추가 폼으로 연다.
- **Ayaan 작업 화면**:
  - 매핑 없는 프로젝트 옆 **[작업공간 설정]** 버튼 → 위 URL.
  - **[계획 실행]**: 프로젝트 → 마일스톤 선택 → 실행.
  - 계획 목록: 마일스톤, 단계 진행 표시(`준비 › 실행 3/11 › 머지 › 배포 › 정리`), 오류, [재개]/[취소].
- 셋업 점검 3단계: 매핑 경로 대신 저장소별 `git ls-remote <GIT_URL>`로 확인.

## 6. 제약

- git 명령은 Ayaan 컨테이너 안에서 실행. 인증은 셋업 3단계의 `gh auth setup-git` 그대로.
- 브랜치 이름: `ayaan/plan-<마일스톤키>`, `ayaan/<issueKey>`. 마일스톤키는 마일스톤 이름의 슬러그(영숫자·하이픈), 비면 ID 앞 8자.
- **실행 환경**: CLI는 Ayaan 컨테이너 안에서 실행된다. 그래서 CLI가 테스트하려면 각 저장소의 빌드 도구가 이 컨테이너에 있어야 한다. 이건 Ayaan 로직이 아니라 실행 환경 준비다. TASKS 프로젝트의 경우 d94-query와 d94-lib-pom에 JDK 21과 Maven(또는 저장소의 wrapper)을 Dockerfile에 추가해야 한다. 셋업 점검에서 매핑된 저장소에 필요한 도구가 있는지 확인한다(작성자 판단).
- CLI 권한 정책(`GIT_PUSH ASK` 등)과 충돌하지 않도록 push·머지는 CLI가 아니라 Ayaan이 한다.
- d94-query Flyway 다음 번호 사용. 테스트는 기존 `tests/fakes.py` 패턴(InMemoryQuery, fake_runner)에 git 대체물(로컬 bare 저장소)을 더해 실제 git으로 단계 테스트.

## 7. 구현자에게 맡기는 것

- 상태 기계 코드 배치(새 모듈 `ayaan/plans.py` 권장), git 호출 래퍼.
- 단계별 재시도 횟수·백오프.
- 화면 컴포넌트 구성과 진행 표시 모양.

## 8. 작성자 판단 (사용자 확인 전 기본값)

- 프로젝트당 진행 중인 계획은 1개다. 그래서 같은 저장소의 머지와 배포가 서로 경쟁하지 않는다. 이미 진행 중인 계획이 있으면 `POST /plans`는 409를 돌려준다.
- Epic과 이미 완료된 이슈는 건너뛰고, 실행 순서는 키 번호 순서다.
- [재개]와 [재시도]는 모두 같은 Task를 다시 실행한다.
- 계획 안 Task는 키 순서로 **하나씩** 실행. 다음 Task는 이전 Task가 계획 브랜치에 머지된 뒤 시작.
- 계획 밖 단독 Task(`/task`, 이슈 단건 등록)도 worktree에서 실행하고 브랜치 push까지만. 머지·배포는 계획에서만.
- Task 브랜치는 계획 브랜치에 머지할 때 merge commit(`--no-ff`), 계획 브랜치 → main은 rebase 후 fast-forward.
- CLI가 커밋하지 않고 남긴 변경은 자동 커밋하지 않고 실패 처리.
- 실패 시 자동 롤백 없음. HALTED로 멈추고 알림, worktree 보존.
- 주기 정리 기본값 매일 04:00 KST.
- 배포는 계획에서 바뀐 저장소만.
