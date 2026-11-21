# Ayaan 계획 파이프라인 · worktree 작업공간 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CIRA 마일스톤 단위로 Ayaan이 Task별 worktree·브랜치 생성 → CLI 개발 → push → 계획 브랜치 머지 → main 머지 → deploy push → 정리까지 이어서 관리하게 한다.

**Architecture:** 상태는 d94-query(`SN_AYN_PLAN`, `SN_AYN_WORKSPACE_REPO`, `SN_AYN_TASK.PLAN_ID`)에 두고, Ayaan의 기존 `Scheduler.tick()`이 `advance_plans()`로 단계를 한 번에 하나씩 진행한다. git은 새 모듈 `ayaan/git.py`가 subprocess로 실행하고, 빌드·테스트는 CLI의 일이다(Ayaan은 결과만 본다). 웹은 기준정보 작업공간 탭과 Ayaan 작업 화면만 바꾼다.

**Tech Stack:** Python 3.12 asyncio·FastAPI·httpx·croniter·pytest (d94-app-ayaan), Spring Boot·JPA·Flyway·PostgreSQL (d94-query), Next.js·React Query·Vitest (d94-web), git CLI.

**Spec:** `d94-app-ayaan/docs/specs/2026-11-21-ayaan-plan-pipeline-design.md`

## Global Constraints

- 폴더: 기본 clone `/workspace/.repos/<REPO_NM>/`, 작업 worktree `/workspace/.tasks/<issueKey>/<REPO_NM>/`. 루트는 `Settings.workspace_roots`의 첫 값(기본 `/workspace`).
- 브랜치: 계획 `ayaan/plan-<milestoneKey>`, Task `ayaan/<issueKey>`. milestoneKey = 마일스톤 이름을 `[a-z0-9-]` 슬러그로(소문자, 나머지 문자는 `-`, 연속 `-` 축약, 양끝 `-` 제거). 결과가 비면 마일스톤 ID 앞 8자.
- STAGE 값: `PREPARING`, `RUNNING`, `MERGING`, `DEPLOYING`, `CLEANING`, `DONE`, `HALTED`, `CANCELLED`.
- 프로젝트당 진행 중 계획 1개(`STAGE NOT IN ('DONE','CANCELLED')`). 위반 시 `POST /plans` 409.
- Ayaan은 테스트·빌드를 실행하지 않는다. CLI 프롬프트 앞 안내로 맡긴다(P8).
- push·머지·브랜치 삭제는 Ayaan만 한다. CLI에는 "커밋만, push 금지"를 안내한다.
- push 안 된 커밋이나 커밋 안 된 변경이 있는 worktree는 절대 지우지 않는다. 기본 clone은 지우지 않는다.
- Task 브랜치 → 계획 브랜치: `merge --no-ff`. 계획 브랜치 → BASE: `origin/<BASE>` 위로 rebase 후 fast-forward.
- 배포: 계획에서 바뀐 저장소만, `SORT_ORD` 순으로 `<BASE>`를 `DEPLOY_BRANCH`에 머지·push. `DEPLOY_BRANCH`가 비면 건너뜀.
- 주기 정리 설정 키 `WORKTREE_GC_CRON`, 기본 `0 4 * * *`(KST).
- d94-query Flyway 번호: **V580** (V570은 CIRA 컬럼 설계가 사용).
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- 대상 이슈가 0개인 마일스톤으로 계획 실행 → 머지·배포 없이 바로 `CLEANING`→`DONE`, 브랜치만 정리. (Task 5 테스트)
- Ayaan이 `MERGING` 도중 재시작 → 같은 단계를 다시 돌려도 이중 머지·push 오류 없이 이어감(이미 반영된 커밋은 건너뜀). (Task 3·5 테스트)
- 주기 정리 시 push 안 된 커밋이 남은 worktree → 지우지 않고 리포트에 "정리 보류". (Task 6 테스트)
- 계획 브랜치 rebase 충돌 → `HALTED`, `LAST_ERROR`에 저장소 이름, worktree 보존, rebase 중단 상태가 남지 않음(`rebase --abort`). (Task 3·5 테스트)
- 한글만 있는 마일스톤 이름("사용자 정의 컬럼") → 브랜치 이름이 ID 앞 8자로 정상 생성. (Task 3 테스트)

---

### Task 1: d94-query — 저장소 매핑·계획 테이블과 내부 API

**Files:**
- Create: `d94-query/src/main/resources/db/migration/V580__ayaan_plan_pipeline.sql`
- Create: `d94-query/src/main/java/tsh/d94/query/data/orm/ayaan/aynWorkspaceRepo/SnAynWorkspaceRepoModel.java`, `...Access.java`, `...Repo.java`
- Create: `d94-query/src/main/java/tsh/d94/query/data/orm/ayaan/aynPlan/SnAynPlanModel.java`, `...Access.java`, `...Repo.java`
- Modify: `d94-query/src/main/java/tsh/d94/query/constant/ApTableName.java` (`SN_AYN_WORKSPACE_REPO`, `SN_AYN_PLAN`)
- Modify: `d94-query/src/main/java/tsh/d94/query/data/orm/ayaan/aynTask/SnAynTaskModel.java` (`planId`)
- Modify: `d94-query/src/main/java/tsh/d94/query/data/orm/ayaan/aynWorkspace/SnAynWorkspaceModel.java` (`workspacePath` nullable)
- Modify: `d94-query/src/main/java/tsh/d94/query/apService/ayaan/dto/AyaanDtos.java`
- Modify: `d94-query/src/main/java/tsh/d94/query/apService/ayaan/AyaanService.java`
- Modify: `d94-query/src/main/java/tsh/d94/query/interfaces/controller/ayaan/AyaanInternalController.java`
- Test: `d94-query/src/test/java/tsh/d94/app/server/apService/ayaan/AyaanInternalIT.java`
- Modify: `lib/d94-lib-pom/ddl/` 의 Ayaan DDL 파일(있으면 같은 DDL 추가)

**Interfaces:**
- Produces (모두 `/api/v1/internal/ayaan` 아래, 응답은 기존 `ApiResponse` 감싸기):
  - `record RepoDto(String repoNm, String gitUrl, String baseBranch, String deployBranch, Integer sortOrd)`
  - `WorkspaceDto`에 `List<RepoDto> repos` 추가(마지막 필드). `PUT /workspaces/{key}`는 repos 전체 교체, `GET /workspaces`는 `sortOrd` 순 repos 포함.
  - `record PlanDto(String id, String projectKey, String milestoneId, String milestoneKey, String stage, String currentIssueKey, String lastError, LocalDateTime startedAt, LocalDateTime endedAt)`
  - `POST /plans` body `{projectKey, milestoneId, milestoneKey}` → 201 PlanDto(stage=`PREPARING`), 진행 중 계획 있으면 409(기존 `AyaanConflictException`).
  - `GET /plans?projectKey=&active=true|false` → `List<PlanDto>` 최신순.
  - `GET /plans/{id}` → PlanDto.
  - `POST /plans/{id}/stage` body `{expectedStage, nextStage, currentIssueKey?, lastError?, endedAt?}` → 200 PlanDto, 현재 stage가 다르면 409.
  - `TaskDto`에 `String planId` 추가. `POST /tasks` body에 `planId` 허용.
  - `record MilestoneIssueDto(String issueKey, int issueNo, String issueTypeNm, String statusCategory)` / `GET /milestones/{id}/issues` → `issueNo` 오름차순.
  - `record MilestoneDto(String id, String name)` / `GET /projects/{projectKey}/milestones` (웹의 계획 실행 선택용).
  - `POST /issues/by-key/{key}/done` → 현재 상태에서 갈 수 있는 첫 `category=DONE` 상태로 `IssueService.changeStatus` 호출. 갈 수 없으면 200 `{"changed": false}`.

- [ ] **Step 1: V580 마이그레이션 작성**

`SN_AYN_WORKSPACE_REPO(PROJECT_KEY FK→SN_AYN_WORKSPACE, REPO_NM VARCHAR(100), GIT_URL VARCHAR(500) NOT NULL, BASE_BRANCH VARCHAR(100) NOT NULL DEFAULT 'main', DEPLOY_BRANCH VARCHAR(100), SORT_ORD SMALLINT NOT NULL DEFAULT 0, BaseModel 칼럼, PK(PROJECT_KEY, REPO_NM))`.
`SN_AYN_PLAN(OBJ_ID PK, PROJECT_KEY, MILESTONE_ID, MILESTONE_KEY VARCHAR(60), STAGE VARCHAR(20), CURRENT_ISSUE_KEY, LAST_ERROR TEXT, STARTED_AT, ENDED_AT, BaseModel)` + `CREATE UNIQUE INDEX ux_ayn_plan_active ON SN_AYN_PLAN(PROJECT_KEY) WHERE STAGE NOT IN ('DONE','CANCELLED')`.
`ALTER TABLE SN_AYN_TASK ADD COLUMN PLAN_ID VARCHAR(100)`; `ALTER TABLE SN_AYN_WORKSPACE ALTER COLUMN WORKSPACE_PATH DROP NOT NULL`. BaseModel 칼럼 목록은 V500의 `SN_AYN_WORKSPACE`와 동일하게.

- [ ] **Step 2: 실패하는 IT 작성** (`AyaanInternalIT`)

```java
@Test void putWorkspace_replacesRepos_inSortOrder()      // repos 2개 PUT → GET 시 sortOrd 순, 다시 1개 PUT → 1개만 남음
@Test void createPlan_secondActiveInSameProject_409()     // 같은 projectKey로 두 번 → 두 번째 409
@Test void createPlan_afterDone_allowed()                 // stage DONE으로 바꾼 뒤 새 계획 201
@Test void stage_wrongExpected_409()                      // expectedStage=RUNNING 인데 실제 PREPARING → 409
@Test void milestoneIssues_orderedByNumber()              // TASKS-10, TASKS-2 연결 → [2, 10] 순서
@Test void issueDone_movesToDoneCategory()                // 이슈 상태 category가 DONE으로 바뀜
@Test void createTask_keepsPlanId()                       // planId 넣어 생성 → GET 시 같은 planId
```

- [ ] **Step 3: 실패 확인**

Run: `cd d94-query && ./mvnw -q -Dit.test=AyaanInternalIT verify`
Expected: 위 7개 FAIL(엔드포인트 없음/404).

- [ ] **Step 4: 모델·Access·DTO·서비스·컨트롤러 구현**

기존 `SnAynWorkspace*` 패턴 그대로. `issueNo`는 `issueKey`의 `-` 뒤 숫자. `statusCategory`는 이슈 상태의 `category`. 마일스톤 이슈는 `SnCiraMilestoneIssue` 연결로 조회.

- [ ] **Step 5: 통과 확인**

Run: `cd d94-query && ./mvnw -q -Dit.test=AyaanInternalIT,AyaanSchemaIT verify`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C d94-query add -A && git -C d94-query commit -m "feat(ayaan): 저장소 매핑·계획 테이블과 내부 API"
```

---

### Task 2: Ayaan — QueryClient·InMemoryQuery 확장

**Files:**
- Modify: `d94-app-ayaan/ayaan/clients.py`
- Modify: `d94-app-ayaan/tests/fakes.py`
- Test: `d94-app-ayaan/tests/test_clients.py`

**Interfaces:**
- Consumes: Task 1 엔드포인트.
- Produces (`QueryClient`와 `InMemoryQuery` 같은 시그니처):
  - `create_plan(project_key: str, milestone_id: str, milestone_key: str) -> tuple[int, dict]` (201 또는 409)
  - `list_plans(project_key: str | None = None, active: bool | None = None) -> list[dict]`
  - `get_plan(plan_id: str) -> dict`
  - `plan_stage(plan_id: str, expected: str, next: str, **fields) -> dict | None` (409면 None)
  - `milestone_issues(milestone_id: str) -> list[dict]`
  - `project_milestones(project_key: str) -> list[dict]`
  - `issue_done(issue_key: str) -> bool`
  - 기존 `get_workspaces()` 결과의 각 항목에 `repos: list[dict]`.
  - `InMemoryQuery`: `add_plan(**fields) -> str`, `plan(pid) -> dict`, `milestones: dict[str, list[dict]]`(id → 이슈 목록), `done_issues: list[str]` 기록.

- [ ] **Step 1: 실패하는 테스트** (`test_clients.py`, 기존 httpx MockTransport 패턴)

```python
async def test_plan_stage_409_returns_none(): ...   # transport가 409 → None
async def test_create_plan_409_returns_status(): ... # (409, data)
async def test_milestone_issues_path(): ...          # GET /milestones/m1/issues 호출 확인
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest -q tests/test_clients.py` / Expected: 3 FAIL

- [ ] **Step 3: 구현** — 기존 `transition`/`create_task` 패턴 그대로. `InMemoryQuery.plan_stage`는 stage가 `expected`가 아니면 None, `create_plan`은 같은 프로젝트에 진행 중 계획이 있으면 `(409, existing)`.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -q tests/test_clients.py && .venv/bin/ruff check .` / Expected: PASS

- [ ] **Step 5: Commit** — `git add ayaan/clients.py tests/fakes.py tests/test_clients.py && git commit -m "feat: 계획·마일스톤 query 클라이언트"`

---

### Task 3: Ayaan — git 작업 모듈

**Files:**
- Create: `d94-app-ayaan/ayaan/git.py`
- Test: `d94-app-ayaan/tests/test_git.py`

**Interfaces:**
- Produces (모두 `async`, 실패 시 `GitError(repo: str, cmd: str, stderr: str)` 발생):
  - `slug(name: str, fallback_id: str) -> str` (동기)
  - `repo_dir(root: Path, repo_nm: str) -> Path` / `task_dir(root: Path, issue_key: str) -> Path` (동기)
  - `ensure_clone(root: Path, repo: dict) -> Path` — 없으면 clone, 있으면 `fetch --prune`.
  - `ensure_branch(root, repo, branch: str, start: str) -> None` — 원격에 없을 때만 `start`에서 만들어 push. 있으면 아무것도 안 함.
  - `add_worktree(root, repo, issue_key: str, start: str) -> Path` — 이미 있으면 그대로 반환.
  - `dirty(path: Path) -> bool` — `status --porcelain` 비어 있지 않음.
  - `has_commits(path: Path, base: str) -> bool` — `base..HEAD` 커밋 존재.
  - `push_branch(path: Path, branch: str) -> None`
  - `unpushed(path: Path) -> bool` — upstream이 없거나 `@{u}..HEAD`가 비어 있지 않음.
  - `merge_no_ff(root, repo, source: str, target: str) -> bool` — target에 source 머지·push. 이미 포함돼 있으면 False(아무것도 안 함).
  - `rebase_ff(root, repo, branch: str) -> bool` — branch를 `origin/<base>` 위로 rebase 후 base를 fast-forward·push. 이미 같으면 False. 충돌 시 `rebase --abort` 후 GitError.
  - `changed_since(root, repo, ref: str) -> bool` — `origin/<base>`가 ref 이후 바뀌었는지(배포 대상 판단).
  - `delete_branch(root, repo, branch: str) -> None` — 로컬·원격 삭제, 없으면 무시.
  - `remove_worktree(path: Path, repo_root: Path) -> None` — `worktree remove` + `worktree prune`.

- [ ] **Step 1: 실패하는 테스트** — `tmp_path`에 `git init --bare origin.git` + 초기 커밋을 만드는 fixture `origin`, `repo = {"repoNm": "r1", "gitUrl": str(origin), "baseBranch": "main", "deployBranch": "deploy"}`.

```python
def test_slug_korean_only_falls_back(): assert slug("사용자 정의 컬럼", "80ae64d2-xxxx") == "80ae64d2"
def test_slug_ascii(): assert slug("Task Columns v2!", "x") == "task-columns-v2"
async def test_ensure_clone_twice_is_fetch(tmp_path, origin): ...        # 두 번째 호출이 예외 없이 끝남
async def test_worktree_commit_push_then_clean(tmp_path, origin): ...    # 커밋 후 unpushed True → push 후 False
async def test_merge_no_ff_idempotent(tmp_path, origin): ...             # 두 번째 호출 False, 원격 커밋 수 그대로
async def test_rebase_ff_conflict_aborts(tmp_path, origin): ...          # GitError, 이후 `git status`에 rebase 진행 중 아님
async def test_rebase_ff_idempotent(tmp_path, origin): ...               # 두 번째 False
async def test_delete_branch_missing_ok(tmp_path, origin): ...
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest -q tests/test_git.py` / Expected: FAIL(import error)

- [ ] **Step 3: 구현** — `asyncio.create_subprocess_exec("git", ...)`, `GIT_TERMINAL_PROMPT=0`. 머지·rebase는 기본 clone(`.repos/<repo>`)에서 실행한다. 작업 브랜치는 detached 체크아웃(`checkout --detach origin/<target>`)으로 처리해서 worktree가 쓰는 브랜치와 부딪히지 않게 한다. 커밋 작성자는 저장소 설정을 따른다.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -q tests/test_git.py && .venv/bin/ruff check .` / Expected: PASS

- [ ] **Step 5: Commit** — `git add ayaan/git.py tests/test_git.py && git commit -m "feat: worktree·브랜치 git 작업 모듈"`

---

### Task 4: Ayaan — Task를 worktree에서 실행

**Files:**
- Modify: `d94-app-ayaan/ayaan/api.py` (`create_task`, `_workspace` 검증)
- Modify: `d94-app-ayaan/ayaan/scheduler.py` (`_execute` 앞 준비, `_settle` 후 단독 Task push)
- Modify: `d94-app-ayaan/ayaan/rules.py` (`validate_workspace`, `workspace_preamble`)
- Test: `d94-app-ayaan/tests/test_scheduler.py`, `tests/test_api.py`, `tests/test_rules.py`

**Interfaces:**
- Consumes: Task 2 (`get_workspaces()[].repos`), Task 3 (`ensure_clone`, `add_worktree`, `dirty`, `has_commits`, `push_branch`, `task_dir`).
- Produces:
  - `rules.workspace_preamble(repos: list[dict], issue_key: str) -> str` — 아래 고정 문구.
  - `rules.validate_workspace(body, roots)`: `workspacePath` 검사 제거, `repos` 필수(1개 이상), 각 항목 `repoNm` `[A-Za-z0-9._-]+`, `gitUrl` 비어 있지 않음, `repoNm` 중복 금지.
  - `Scheduler._prepare(task: dict) -> None` — 계획 Task면 시작점 `origin/ayaan/plan-<milestoneKey>`, 단독이면 `origin/<baseBranch>`.
  - 생성되는 Task의 `workspacePath` = `str(task_dir(root, issueKey))`. 이슈 없는 `/task` 프롬프트 작업은 `issueKey` 대신 `adhoc-<YYYYMMDDHHMMSS>`.

`workspace_preamble` 고정 문구(저장소 줄은 반복):
```
[작업 환경]
이 폴더에는 아래 저장소가 있고, 각 저장소는 브랜치 ayaan/{issueKey} 에 체크아웃되어 있습니다.
- {repoNm}/
[규칙]
- 바꾼 저장소의 빌드와 테스트를 직접 실행해 통과시킨 뒤에만 완료하세요. 통과하지 못하면 실패로 끝내세요.
- 변경은 커밋까지만 하고 push 하지 마세요.

```

- [ ] **Step 1: 실패하는 테스트**

```python
def test_validate_workspace_requires_repos(): assert "repos" in validate_workspace({"defaultTool": "CLAUDE"}, roots)
def test_validate_workspace_duplicate_repo(): ...
async def test_create_task_sets_task_dir(client): ...          # workspacePath == "/workspace/.tasks/TASKS-1"
async def test_execute_prepares_worktrees_then_runs(tmp_path): ...  # runner 호출 시 cwd/<repo> 디렉터리가 존재
async def test_prompt_has_preamble(): ...                      # runner에 넘긴 prompt가 "[작업 환경]"으로 시작
async def test_standalone_done_pushes_branch(tmp_path): ...    # DONE 후 origin에 ayaan/TASKS-1 존재
async def test_done_with_dirty_worktree_fails(tmp_path): ...   # 커밋 안 된 변경 → FAILED, failReason에 "uncommitted"
```
(scheduler 테스트는 Task 3의 `origin` fixture를 `tests/conftest.py`로 옮겨 공유하고, `fake_runner`가 cwd에 파일을 쓰고 커밋하는 스크립트를 받도록 확장.)

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest -q tests/test_rules.py tests/test_api.py tests/test_scheduler.py` / Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현** — `_execute`에서 첫 실행 전에 `_prepare` 호출, 실패하면 `_fail(..., "prepare: <GitError>")`. 새 실행 프롬프트 앞에 `workspace_preamble` 붙임(재개 프롬프트에는 안 붙임). COMPLETED 시: 저장소 중 하나라도 `dirty`면 DONE 대신 `_fail(reason="uncommitted changes in <repo>")`. 단독 Task(planId 없음)는 DONE 전이 후 `has_commits`인 저장소만 `push_branch`. 계획 Task의 push는 Task 5가 한다.

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -q && .venv/bin/ruff check .` / Expected: PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: Task를 저장소별 worktree에서 실행"`

---

### Task 5: Ayaan — 계획 상태 기계와 API

**Files:**
- Create: `d94-app-ayaan/ayaan/plans.py`
- Modify: `d94-app-ayaan/ayaan/scheduler.py` (`tick()`에서 `await advance_plans(self)` 호출, 알림 문구)
- Modify: `d94-app-ayaan/ayaan/api.py` (계획 엔드포인트)
- Test: `d94-app-ayaan/tests/test_plans.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: Task 2 계획 메서드, Task 3 git 함수, Task 4 `_prepare`.
- Produces:
  - `plans.advance_plans(sched: Scheduler) -> None` — 진행 중 계획마다 `step(sched, plan)` 한 번.
  - `plans.step(sched, plan: dict) -> str` — 이번에 끝낸 뒤의 stage 반환. 예외는 `HALTED` + `lastError` + 알림 `PLAN_HALTED`로 바꾼다.
  - `plans.targets(issues: list[dict]) -> list[dict]` — `issueTypeNm != "Epic"`이고 `statusCategory != "DONE"`, `issueNo` 순.
  - 공개 API (`/tsh/api/v1/ayaan`): `POST /plans {projectKey, milestoneId}`(milestoneKey는 서버가 `slug`로 계산, 마일스톤 이름은 `project_milestones`에서), `GET /plans?projectKey=`(각 계획에 `done`=이 계획의 DONE Task 수, `total`=`len(targets())` + `done` — 이 계획이 끝낸 이슈는 CIRA에서 DONE이 되어 `targets()`에서 빠지므로 더한다), `POST /plans/{id}/resume`, `POST /plans/{id}/cancel`, `GET /projects/{projectKey}/milestones`.
  - 알림 이벤트: `PLAN_STARTED`, `PLAN_HALTED`, `PLAN_DEPLOYED`.

단계 규칙(스펙 5.3 그대로):
- `PREPARING`: 모든 저장소 `ensure_clone` + `ensure_branch(ayaan/plan-<key>, origin/<base>)` → `RUNNING`.
- `RUNNING`: 계획의 Task 중 RUNNING/QUEUED/PAUSED가 있으면 대기. 막 DONE된 Task가 있으면 → 저장소별 `has_commits`인 곳만 `push_branch` → `merge_no_ff(ayaan/<issueKey>, ayaan/plan-<key>)` → `delete_branch(ayaan/<issueKey>)` → `remove_worktree` → `issue_done(issueKey)`. FAILED Task가 있으면 `HALTED`. 아니면 `targets()`의 다음 이슈(이 계획에 DONE Task가 없는 첫 이슈)로 `create_task(planId=…)`, `currentIssueKey` 갱신. 남은 이슈가 없으면 `MERGING`.
- `MERGING`: `sortOrd` 순으로 `rebase_ff(ayaan/plan-<key>)` → `DEPLOYING`.
- `DEPLOYING`: 계획 시작 이후 바뀐 저장소만 `merge_no_ff(<base>, <deployBranch>)` → 알림 `PLAN_DEPLOYED` → `CLEANING`.
- `CLEANING`: `delete_branch(ayaan/plan-<key>)`, 남은 계획 worktree 제거(안전 확인 통과한 것만) → `DONE`(`endedAt`).
- resume: `HALTED` → 실패한 Task가 있으면 그 Task를 `transition(FAILED→QUEUED)` 하고 `RUNNING`으로, 아니면 `lastError`에 기록된 단계로. cancel: 아무 진행 중 stage → `CANCELLED`(worktree는 GC가 안전 확인 후 정리).

- [ ] **Step 1: 실패하는 테스트** (`test_plans.py`, InMemoryQuery + 실제 git origin fixture + 커밋하는 fake_runner)

```python
async def test_full_plan_two_issues_merges_and_deploys(): ...
    # TASKS-2, TASKS-10 → 실행 순서 [2,10], 끝나면 origin/main과 origin/deploy에 두 커밋, ayaan/* 브랜치 없음, stage DONE, done_issues == ["TASKS-2","TASKS-10"]
async def test_epic_and_done_issues_skipped(): ...
async def test_zero_targets_goes_to_done_without_merge(): ...      # main 커밋 수 그대로, plan 브랜치 삭제됨
async def test_task_failed_halts_then_resume_requeues_same_task(): ...  # create_task 호출 수 증가 없음
async def test_rebase_conflict_halts_keeps_worktree(): ...          # lastError에 "r1", .tasks 폴더 남음
async def test_restart_during_merging_is_idempotent(): ...          # MERGING에서 step 두 번 → 오류 없음, main 커밋 중복 없음
async def test_unchanged_repo_not_deployed(): ...                   # r2는 deploy 브랜치 그대로
async def test_second_plan_same_project_409(client): ...
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest -q tests/test_plans.py` / Expected: FAIL

- [ ] **Step 3: `plans.py` 구현과 `tick()` 연결, API 추가**

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -q && .venv/bin/ruff check .` / Expected: PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: 마일스톤 계획 상태 기계(머지·배포·정리)"`

---

### Task 6: Ayaan — worktree 정리·기존 매핑 이관·셋업 점검·이미지

**Files:**
- Modify: `d94-app-ayaan/ayaan/scheduler.py` (`_gc_loop`, `boot()`에 이관)
- Modify: `d94-app-ayaan/ayaan/plans.py` (`gc_worktrees`)
- Modify: `d94-app-ayaan/ayaan/rules.py` (`WORKTREE_GC_CRON` 검증, 기본값)
- Modify: `d94-app-ayaan/ayaan/api.py` (`DELETE /tasks/{id}/workspace`)
- Modify: `d94-app-ayaan/ayaan/setup_check.py` (3단계)
- Modify: `d94-app-ayaan/Dockerfile` (JDK 21, Maven)
- Modify: `d94-app-ayaan/CLAUDE.md` (모듈 표에 `git.py`, `plans.py`)
- Test: `tests/test_plans.py`, `tests/test_rules.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: Task 3 `dirty`, `unpushed`, `remove_worktree`; Task 2 `list_plans`, `list_tasks`.
- Produces:
  - `plans.gc_worktrees(sched) -> list[str]` — 보류된 폴더 경로 목록 반환(리포트에 "정리 보류: <경로>"로 추가).
  - `plans.safe_remove(root: Path, issue_key: str, repos: list[dict]) -> bool` — 하나라도 `dirty` 또는 `unpushed`면 False(아무것도 안 지움).
  - `Scheduler.migrate_workspaces() -> None` — `repos`가 비어 있고 `workspacePath`가 있는 매핑마다 `git -C <path> remote get-url origin`으로 repo 1줄 생성(`repoNm`=폴더 이름).

- [ ] **Step 1: 실패하는 테스트**

```python
async def test_gc_skips_unpushed_and_reports(): ...      # 반환값에 경로, 폴더 존재
async def test_gc_removes_done_plan_worktrees(): ...     # 폴더 없음, .repos 존재
async def test_gc_keeps_running_task_dir(): ...
def test_worktree_gc_cron_validation(): ...              # "0 4 * * *" OK, "bad" 오류
async def test_delete_task_workspace_unsafe_409(client): ...
async def test_migrate_workspace_creates_repo_row(tmp_path, origin): ...
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest -q` / Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현** — `_gc_loop`는 `_report_loop`와 같은 30초 폴링 + `croniter` 방식. 대상: `.tasks/*` 중 연결된 Task가 없거나 DONE/CANCELLED이고, 그 Task의 계획이 없거나 DONE/CANCELLED. 셋업 3단계는 매핑마다 저장소별 `git ls-remote <gitUrl>`, 그리고 `java -version`·`mvn -v` 존재 확인(없으면 경고 항목, 실패 아님). Dockerfile에 `openjdk-21-jdk-headless maven` 설치(apt 패키지가 slim 베이스에 없으면 Temurin apt 저장소 추가).

- [ ] **Step 4: 통과 확인** — Run: `.venv/bin/pytest -q && .venv/bin/ruff check . && docker build -t ayaan-test .` / Expected: PASS, 이미지 빌드 성공, `docker run --rm ayaan-test java -version`이 21 출력

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: worktree 주기 정리·매핑 이관·셋업 점검·JDK"`

---

### Task 7: d94-web — 작업공간 저장소 폼과 계획 화면

**Files:**
- Modify: `d94-web/src/types/ayaan.ts` (`AyaanRepo`, `AyaanWorkspace.repos`, `AyaanPlan`)
- Modify: `d94-web/src/lib/api/ayaan.ts`
- Modify: `d94-web/src/app/(main)/ayaan/settings/toolBody.ts` (`toWorkspaceBody`에 repos)
- Modify: `d94-web/src/components/settings/AyaanSettingsPanel.tsx` (작업공간 탭 저장소 목록, URL `tab`·`projectKey`)
- Modify: `d94-web/src/app/(main)/ayaan/tasks/page.tsx` ([작업공간 설정], [계획 실행], 계획 목록)
- Modify: `d94-web/src/app/(main)/ayaan/tasks/[id]/page.tsx` ([작업공간 정리])
- Modify: `d94-web/src/app/(main)/settings/AGENTS.md`, `d94-web/src/app/(main)/ayaan/` 의 AGENTS.md
- Test: `d94-web/src/app/(main)/ayaan/settings/toolBody.test.ts`, `d94-web/src/app/(main)/ayaan/tasks/planStage.test.ts`

**Interfaces:**
- Consumes: Task 5·6 공개 API.
- Produces:
  - `interface AyaanRepo { repoNm: string; gitUrl: string; baseBranch: string; deployBranch: string | null; sortOrd: number }`
  - `interface AyaanPlan { id: string; projectKey: string; milestoneId: string; milestoneKey: string; stage: PlanStage; currentIssueKey: string | null; lastError: string | null; startedAt: string; endedAt: string | null; done: number; total: number }` (`done/total`은 Task 5의 `GET /plans`가 계산해 넣는다)
  - API: `getPlans(projectKey?)`, `createPlan(projectKey, milestoneId)`, `resumePlan(id)`, `cancelPlan(id)`, `getMilestones(projectKey)`, `cleanTaskWorkspace(taskId)`.
  - `planStageLabel(p: AyaanPlan): string` → `"준비 › 실행 3/11 › 머지 › 배포 › 정리"`에서 현재 단계를 `[ ]`로 감싼 문자열. 예: RUNNING, 3/11 → `"준비 › [실행 3/11] › 머지 › 배포 › 정리"`, HALTED → 앞에 `"중단: "`.

- [ ] **Step 1: 실패하는 테스트**

```ts
it("toWorkspaceBody는 빈 저장소 행을 버리고 sortOrd를 순서대로 매긴다", ...)
it("planStageLabel RUNNING 3/11", () => expect(planStageLabel(p({stage:"RUNNING",done:3,total:11}))).toBe("준비 › [실행 3/11] › 머지 › 배포 › 정리"))
it("planStageLabel HALTED는 '중단: ' 접두", ...)
```

- [ ] **Step 2: 실패 확인** — Run: `cd d94-web && npx vitest run src/app/\(main\)/ayaan` / Expected: FAIL

- [ ] **Step 3: 구현**
  - 작업공간 탭: 저장소 행(이름·URL·기준 브랜치 기본 `main`·배포 브랜치·위/아래·삭제) + [저장소 추가]. `WORKSPACE_PATH` 입력 제거. `useSearchParams`로 `tab=workspace`면 탭 선택, `projectKey`가 있고 매핑이 없으면 그 키로 새 폼을 연다.
  - 작업 화면: 매핑 없는 프로젝트 option은 그대로 disabled로 두고, 선택 아래 안내 문장 대신 매핑 없는 프로젝트마다 `[작업공간 설정]` 링크 버튼(`/settings?group=ayaan&tab=workspace&projectKey=<key>`). `[계획 실행]` 폼: 매핑된 프로젝트 → 마일스톤 select → 실행(409면 "이미 진행 중인 계획이 있습니다." 토스트). 계획 목록: `planStageLabel`, `lastError`, HALTED면 [재개], 진행 중이면 [취소](`window.confirm`).
  - 작업 상세: DONE/CANCELLED/FAILED일 때 [작업공간 정리], 409면 "push되지 않은 변경이 있어 정리하지 않았습니다." 토스트.

- [ ] **Step 4: 통과 확인** — Run: `cd d94-web && npx vitest run && npx tsc --noEmit && npm run lint` / Expected: PASS

- [ ] **Step 5: 브라우저 확인** — dev 서버에서 `/settings?group=ayaan&tab=workspace&projectKey=TASKS`가 작업공간 탭과 TASKS 새 폼으로 열리는지, `/ayaan/tasks`에 [작업공간 설정]·[계획 실행]이 보이는지 확인.

- [ ] **Step 6: Commit** — `git -C d94-web add -A && git -C d94-web commit -m "feat(ayaan): 작업공간 저장소 폼과 계획 실행 화면"`
