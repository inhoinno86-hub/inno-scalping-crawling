# Phase 2 잔여 간극 정리와 Phase 3 — 주 2회 브리핑·전달 · 프로젝트 Intent

> 이 문서는 Loop Engine과 개발 에이전트가 **이번 run**의 목적, 범위, 품질 기준, 완료
> 조건을 동일하게 이해하도록 만드는 실행 기준 문서다.
>
> 상위 기준 문서: `intent-docs/scalping_strategy_briefing_intent.md` (이하 "원 intent")
> 직전 run 기준 문서: `intent-docs/scalping_phase2_review_interface_intent.md`
> (이하 "Phase 2b intent")
>
> **이번 run의 범위는 Phase 2 검토 결과 남은 간극 2건 + Phase 3(원 intent §11)이다.**
> Phase 4는 별도 run이다.
>
> Loop Engine project 이름: `scalping-briefing-p3`
> (엔진의 run 식별용 이름이다. 제품 설정 키 `PROJECT_SLUG = scalping-briefing`은 바뀌지
> 않는다. 직전 run project `scalping-briefing-p2b`는 `DEFERRED_BACKLOG`로 종료됐고 같은
> 이름으로는 새 run을 만들 수 없다.)

---

## 1. 한 줄 정의

승인된 전략 후보를 **KST 주 2회 스케줄로 Markdown 브리핑으로 묶어 아카이브하고, 중복 없이
단일 채널로 전달(기본 `dry_run`)** 하는 경로를 만든다. 그 전에 Phase 2 검토에서 남은
간극 2건(검토 API 데이터 엔드포인트, 전 필드 `conflicting` 후보의 종결 경로)을 닫는다.

실제 외부 전송(`DELIVERY_MODE=live`), 실 봇 토큰, 운영 지표 대시보드는 이번 run 범위가
아니다.

---

## 2. 전제 — 이미 완료된 것 (재작업 금지)

Phase 0~2가 완료됐다. 직전 run(`scalping-briefing-p2b`, run_id
`2243d478-4302-4e09-a250-49e858f6e371`, commit `5d52a14`, `main` 병합 완료)에서 검토
인터페이스와 Phase 2 DoD가 마감됐고, **`make test`는 224 passed / 0 failed**(네트워크·
Docker·API 키 없이)다.

| 영역 | 산출물 |
| --- | --- |
| Phase 0+1 | `config/`, `schemas/` 8종, `models/` 11종, `net/`, `normalize/`, `storage/`, `sources/`, `repository/documents.py`, `pipeline/state_machine.py`, `pipeline/source_policy.py`, `publishing/gate.py`, `publishing/phrase_lint.py`, `llm/fixture.py`, `delivery/guard.py` |
| Phase 2 분류·추출·근거 | `pipeline/classify.py`, `pipeline/extract.py`, `pipeline/evidence_link.py`, `pipeline/validate.py`, `llm/prompts.py`, `llm/schema_guard.py`, `llm/audit.py`, `publishing/candidate_view.py` |
| Phase 2 점수·신규성·라우팅 | `pipeline/scoring.py`, `pipeline/novelty.py`, `pipeline/routing.py` |
| Phase 2b 검토 경로 | `review/service.py`(`list_candidates`, `get_candidate`, `record_decision`, `amend_field`), `review/cli.py`(`list`/`show`/`decide`), `__init__.py:create_review_app`의 토큰 fail-fast, `scripts/record_llm_fixtures.py` |
| Phase 2 DoD | `tests/test_phase2_dod.py` 5개 명명 테스트, `tests/test_protected_p11_p16.py`, `docs/protected-requirements-tests.md`의 `P1`~`P16` 매핑 |

**`make test` 224 passed / 0 failed가 이번 run의 하한선이다. 어떤 작업도 이 수를 줄이거나
failed를 만들면 안 된다.**

### 2.1 승계되는 확정 해석 (임의 변경 금지)

- `reviewer_id`는 호출자가 제공하는 필수 non-empty 문자열이며 그대로 기록한다. 검토자
  명부 파일이나 새 설정 키를 만들지 않는다.
- 인용 상한: **필드당 최대 2개**이면서 발행 항목 단위로도 `publishing/gate.py`의
  `MAX_EVIDENCE_QUOTES` 계약을 통과해야 한다. 길이 상한은 하드코딩하지 않고 설정
  `quote_max_chars`(300)에서 읽는다.
- 검토자 수정은 원문 기반 값을 덮어쓰지 않고 `metadata_json["review_amendments"]`에
  append-only로 남긴다. 새 ORM 컬럼·새 alembic 마이그레이션을 만들지 않는다.
- 유사·중복 판정은 결정적 규칙만 쓴다. pgvector·임베딩 도입 금지.
- 상태 전이는 항상 `pipeline/state_machine.py`를 통과한다.

---

## 3. 승계되는 원칙과 안전 경계

원 intent §3.1·§3.2·§3.3과 Phase 2b intent §3이 그대로 적용된다. `P1`~`P16`은 이번
run에서도 **protected**이며 퇴행시키면 안 된다. 특히 이번 범위와 직접 맞닿는 것:

- `P3` 공개 브리핑의 모든 핵심 주장은 `document_version_id` 단위 Evidence로 역추적
- `P6` 투자 권유·매매 신호·수익 보장 표현 금지
- `P8` 동일 `briefing_id`+채널 중복 전달 금지(idempotent delivery)
- `P9` 기본은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`. 실제 전송·실 키 사용은 승인 전 금지
- `P10` 비밀값(봇 토큰, chat_id)은 환경변수로만
- `P15` 총점 경계·낮은 confidence·핵심 필드 `conflicting` 중 하나면 `needs_review`
- `P16` 검토 API는 `REVIEW_API_BIND`(기본 `127.0.0.1`) 로컬 바인딩 + 단일 정적 토큰

---

## 4. 이번 run의 기능 범위

### 4.1 간극 1 — 검토 API에 데이터 엔드포인트가 없다

현재 `src/scalping_briefing/__init__.py:create_review_app`은 `/health`와 고정
`{"reviews": []}`를 반환하는 `/reviews`뿐이다. `ReviewService`는 CLI에만 연결돼 있어,
HTTP로 검토하려는 사람은 후보를 볼 수 없다. `P16`의 "모든 데이터 엔드포인트 토큰 검사"도
보호 대상이 스텁 1개뿐이라 사실상 공허하게 통과한다.

- `ReviewService`를 API에 연결한다. 최소 경로: 후보 목록, 후보 상세(원문 링크,
  `document_version_id`, Evidence 인용 포함), 검토 결정 기록.
- 결정 엔드포인트는 `record_decision`을 그대로 쓰고 상태 전이는 `state_machine`을 통과한다.
  API가 서비스 계약을 우회해 직접 ORM을 쓰지 않는다.
- `/health`를 제외한 **모든** 데이터 엔드포인트에 기존 토큰 의존성을 적용한다. 인증은 단일
  정적 토큰 하나뿐이며 다중 계정·역할 권한·세션·공개 노출은 범위 밖이다(`P16`).
- 세션 수명은 요청 단위로 관리하고 전역 공유 세션을 만들지 않는다.

### 4.2 간극 2 — 전 필드 `conflicting` 후보가 `needs_review` 대신 `failed`가 된다

`pipeline/validate.py`의 Evidence 강제(직전 run에서 추가)는 `evidence_rows`가 비었거나
**`publishable_fields`가 비었을 때** `extracted → failed`로 종결한다.
`_publishable_fields`는 `field_status`가 `conflicting`/`unknown`인 필드를 제외하므로,
Evidence는 붙어 있는데 핵심 필드가 전부 `conflicting`인 후보는 `failed`가 된다.
`P15`는 `conflicting`이면 `needs_review`를 요구한다. 두 규칙이 이 극단 케이스에서 충돌한다.

- 판정 기준을 정한다: **Evidence가 하나도 없으면 `failed`, Evidence는 있으나 발행 가능한
  필드가 없으면 `P15`에 따라 `validated → needs_review`로 보내되 발행 가능한 필드는 0개로
  표시**한다. 다른 해석을 택하려면 구현 전에 근거를 worker result에 남긴다.
- 실패 재현 테스트로 증명한다: 핵심 필드가 전부 `conflicting`이고 Evidence가 있는 후보가
  `failed`가 아니라 `needs_review`에 도달하며, 발행 게이트는 여전히 그 후보를 거부한다.
- 기존 `tests/test_phase2_dod.py::test_phase2_dod5_...`는 `route_candidate`를 직접
  호출해 validate를 우회한다. validate → routing을 관통하는 경로로 한 번 더 증명한다.

### 4.3 스케줄러와 실행 커서 (원 intent §6.1)

- `WEEKLY_REPORT_SCHEDULE`(확정값 `TUE 08:00`, `FRI 08:00`, `Asia/Seoul`)를 읽어 실행
  시각을 계산한다. 실제 데몬·cron 등록은 범위 밖이며, **스케줄 계산과 트리거 진입점**을
  테스트 가능한 순수 함수로 만든다.
- 하나의 스케줄 발생 = 하나의 `briefing_id`. 실패 후 재시도는 같은 `briefing_id`의 새
  `run_attempt`이며 새 브리핑을 만들지 않는다.
- `trigger_type: manual` 실행은 주 2회 카운트에서 제외한다.
- 커서는 **전달 성공이 아니라 실행 성공으로 전진**한다: `window_start` = 직전
  `run_status: success` 실행의 `window_end`, `window_end` = 해당 실행의 스케줄 기준 시각.
- 커서 부재 시 `initial_lookback_days`(14). 어떤 경우에도 `max_lookback_days`(30)를 넘지
  않으며 초과분은 절단하고 `window_truncated`와 절단된 시작 시각을 기록한다.
- 미승인 후보는 큐에 남고 이후 브리핑에 `carried_over`로 다시 포함될 수 있다. 구간
  재수집으로 처리하지 않는다.

### 4.4 브리핑 생성과 아카이브 (원 intent §6.3)

- 승인(`approved`) 항목 중심으로 Markdown 브리핑을 생성해 `markdown_location`에 저장하고
  `Briefing`/`BriefingItem` 레코드를 남긴다. 모델은 이미 있다(`models/briefing.py`).
- 본문에 최소한 다음을 담는다: `briefing_id`, 생성 시각, 시간대, 실제 사용된
  `window_start`/`window_end`(절단 시 그 사실), 발행 상태, 수집 출처 수와 성공/실패/미실행,
  후보 수와 승인 수, 항목별 한 줄 요약·전략군·자산군·보유 시간 범위·가치 점수와 근거,
  원문 URL·게시일 또는 버전·근거 문장·문서/전략 ID, 한계와 라이선스·실행 위험 메모,
  기존 전략과의 관계, 고지 문구.
- 서술 언어는 한국어(`briefing_language: ko`). 전략명·기술 용어·`quote`·원문 제목은 원문
  표기를 유지한다. 항목 수 상한은 `briefing_max_items`(7).
- **적격 항목이 없거나 일부 출처가 실패해도 실행을 실패로 만들지 않는다.** "승인 대기",
  "적격 신규 자료 없음", "일부 출처 수집 실패"를 명시한 브리핑을 생성한다.
- 원문 전문·과도한 인용을 포함하지 않는다(`P2`). 인용은 항목당 최대 2개, `quote_max_chars`.

### 4.5 발행 게이트 연결 (원 intent §9.3)

브리핑을 외부로 내보내기 전에 기존 `publishing/gate.py`를 **브리핑 단위로** 통과시킨다.
게이트 7조건(허용 출처·원문 링크, Evidence 역추적, 문서 버전·데이터 구간 기록, 중복 제거
또는 관계 표시, `approved` 또는 내부 초안 명시, 금지 표현·원문 전문·민감정보 없음, 중복
전달 이력 없음)을 브리핑 페이로드에 적용한다. 게이트를 우회하는 별도 렌더링 경로를 만들지
않는다. `publication_policy`는 `manual_approval`이며 승인되지 않은 항목은 전달 대상이
아니다.

### 4.6 전달 커넥터와 idempotency (원 intent §6.2, §8.4)

- 단일 채널 커넥터(`DELIVERY_CHANNEL: telegram`)를 **인터페이스와 dry-run 구현으로만**
  만든다. `DELIVERY_MODE=dry_run`에서는 렌더링된 메시지를 로컬 아티팩트와 로그로만 남기고
  네트워크를 쓰지 않는다. live 전송 코드 경로를 만들지 않는다(`P9`).
- `Delivery` 레코드를 남긴다: `delivery_id`, `briefing_id`, `channel`, `idempotency_key`,
  `attempt_no`, `resend_reason`, `resend_approved_by`, `attempted_at`, `status`,
  `provider_reference`, `error`.
- `idempotency_key = {briefing_id}:{channel}:{content_hash}`. 같은 키로 `status: success`
  이력이 있으면 재전송을 거부하고, 예외 재전송은 `resend_reason`과 `resend_approved_by`가
  **모두** 채워졌을 때만 새 `attempt_no`로 허용한다. 판정은 기존 `delivery/guard.py`를
  그대로 쓴다 — 그 안의 금지 장치를 완화하지 않는다(`P8`).

---

## 5. 이번 run에서 하지 않는 것

- `DELIVERY_MODE=live`, 실 봇 토큰·chat_id 사용, 실제 외부 전송
- `LLM_MODE=live` 전환, 실 출처 `active: true` 전환
- 실제 cron/systemd/데몬 등록, 외부 스케줄러 연동
- Phase 4: 운영 지표 대시보드, 주기 리포트, 임계값 재조정, 확장 판단
- 검토 API 외부 노출, 다중 사용자 계정·역할 권한·세션 관리
- 일반 사용자용 검색·추천 UI, pgvector·OpenSearch, 다채널 전달
- 원 intent §14 부록 A에 없는 설정 키 신설
- 새 ORM 컬럼·새 alembic 마이그레이션 (`Briefing`/`BriefingItem`/`Delivery` 모델은 이미 있다)

---

## 6. 완료 기준 (DoD)

원 intent §11 Phase 3 완료 기준: "테스트 환경에서 2회의 서로 다른 주간 실행이 중복 전달
없이 재현되고, 각 보고서 항목이 원문·근거·검토 상태·데이터 기준 구간으로 역추적된다."

이를 **명명 테스트 6개**로 증명한다. 파일은 `tests/test_phase3_dod.py`이며 이름은 정확히
아래와 같아야 한다.

| 테스트 이름 | 증명 내용 |
| --- | --- |
| `test_phase3_dod1_two_scheduled_runs_produce_distinct_briefings` | 서로 다른 두 스케줄 발생이 서로 다른 `briefing_id`와 인접한 `window_start`/`window_end`를 갖는다 |
| `test_phase3_dod2_briefing_item_traces_to_source_evidence_and_review` | 브리핑 항목이 원문 URL·`document_version_id`·Evidence 인용·검토 상태·데이터 구간으로 역추적된다 |
| `test_phase3_dod3_duplicate_delivery_is_rejected_by_idempotency_key` | 같은 `briefing_id`+채널+내용 해시의 재전송이 거부되고, 승인 2요소가 모두 있을 때만 새 `attempt_no`가 허용된다 |
| `test_phase3_dod4_empty_or_failed_window_still_produces_reported_briefing` | 적격 항목이 없거나 일부 출처가 실패해도 실행이 실패하지 않고 그 사실이 브리핑에 명시된다 |
| `test_phase3_dod5_unapproved_candidate_is_carried_over_not_delivered` | 미승인 후보는 전달되지 않고 큐에 남아 다음 브리핑에 `carried_over`로 포함된다 |
| `test_phase3_dod6_retry_reuses_same_briefing_id_and_advances_cursor_on_success` | 실패 후 재시도가 같은 `briefing_id`의 새 `run_attempt`이며, 커서는 전달 성공이 아니라 실행 성공으로 전진한다 |

추가 완료 조건:

- 간극 1: 검토 API의 후보 목록·상세·결정 엔드포인트가 동작하고 `/health` 외 모든 데이터
  엔드포인트가 토큰 없이는 401이다. 상세 응답에 원문 링크·`document_version_id`·Evidence
  인용이 포함된다.
- 간극 2: 핵심 필드가 전부 `conflicting`이고 Evidence가 있는 후보가 `failed`가 아니라
  `needs_review`에 도달하며 발행 게이트는 여전히 거부한다.
- `docs/protected-requirements-tests.md`의 `P1`~`P16` 매핑이 계속 통과하고, Phase 3에서
  새로 만든 코드가 `P3`/`P6`/`P8`/`P9`에 걸리는 지점마다 해당 행의 테스트가 실제로 그 경로를
  덮는다. 필요하면 기존 행에 node ID를 추가한다(`MAPPING` 범위는 `P1..P16` 그대로).
- `make test`가 네트워크·Docker·API 키 없이 **0 failed**로 통과하고 passed ≥ 224.
- 네트워크 호출 없이 전 경로가 재현된다(`LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`).
- 모든 산출물이 git에 추적되어 리뷰 diff에 나타난다(staging은 오케스트레이터가 수행).
- Phase 4에 해당하는 실행 경로를 앞당겨 구현하지 않았다.

---

## 7. 실행 환경 전제 (직전 run에서 실측된 사실 — PLAN에 반드시 반영)

이 절은 추측이 아니라 `scalping-briefing-p2b` run에서 측정된 제약이다.

### 7.1 워크스페이스

- **의존성은 워크스페이스 `.venv`에 이미 설치되어 있다**: `pytest`, `sqlalchemy`,
  `alembic`, `fastapi`, `uvicorn`, `httpx`, `jsonschema`, `pydantic`, `pyyaml`.
  `Makefile`이 `.venv/bin/python`을 자동 감지한다. **worker 샌드박스에는 네트워크가 없다 —
  `pip install` 금지.** 새 서드파티 의존성은 사용자 승인 사항이다.
- **worker 샌드박스에서 `.git`은 read-only다.** `git add`/`commit`/`stash` 금지. worker가
  만든 신규 파일은 untracked로 남는 것이 정상이며 coordinator는 이를 산출물 누락으로
  판정하면 안 된다. staging은 attempt 종료 후 오케스트레이터가 수행한다.
- **`TMPDIR`을 디스크 기반 경로로 지정한다**(예: `/home/inno/.cache/loopeng-tmp`).
  기본 `/tmp`는 tmpfs 8.5G인데 codex가 세션 tape SQLite를 여기에 쌓아 고갈시킨다.
- `make test`는 27초, 224 passed다. 전체 스위트는 work order당 마지막 1회만 돌린다.

### 7.2 dispatch는 attempt당 1회다 — PLAN 크기의 결정적 제약

**직전 run에서 확인된 가장 중요한 사실이다.** `loop-engine dispatch`를 같은
`run_attempt`에서 다시 부르면 새 work order가 실행되지 않는다(기존 worker result가
재사용되고 같은 run report가 반환된다). 새 package를 태우려면 반드시
`complete-run → project-review → retry-run|replan`으로 **새 attempt**를 만들어야 한다.

- `max_work_orders=2`, `max_concurrent_workers=1`(순차) → **attempt 1회 = package 2개**.
- attempt 생성권은 `max_execution_retries=2` + `max_replans=3` ≈ **총 6회**다.
- 따라서 PLAN의 package 총량은 **12개를 넘으면 안 되고**, 실패 여유를 감안하면 **8~10개**로
  잡는 것이 안전하다. 직전 run은 package 11개로 시작해 예산을 전부 소진했다.

### 7.3 worker 시간과 provenance

- `worker_timeout_seconds=900`이고 `lease_ttl_seconds=900`이다. **worker가 900초에 걸려
  강제 종료되면 stderr 배너가 없어 `provenance_unavailable`로 run이 BLOCKED되고 lease도
  만료된다.** 직전 run에서 실제로 1회 발생했다(`loop-engine resume`으로 해제).
- **work order 크기는 "신규 파일 1개 + 그 테스트 1개, 250초 분량"으로 잡는다.** 착수 후
  250초가 지나면 새 코드 작성을 멈추고 그 시점 상태로 worker result를 확정한 뒤 종료한다.
  부분 완료는 실패가 아니다. 이 규격에서 실제 worker 소요는 240~510초였다.
- **evidence 계약**: worker result의 `run_id`/`task_id`/`work_order_hash`/`run_attempt`는
  work order·manifest 값을 **그대로 복사**한다(빈 문자열·`pending` 금지, 재계산 금지).
  `output_hash`는 `sha256:` + 64자 소문자 hex. `test_evidence[].output_hash`는 실행한
  명령의 stdout+stderr 텍스트에 대한 sha256이며 소스·테스트 파일 해시가 아니다.
  이 항목은 직전 run에서 4회 연속 review 지적을 받았고 backlog로 이월됐다.

### 7.4 coordinator가 반복해서 틀린 것 — PLAN이 미리 막아야 한다

1. **완료 package를 `dependencies`에 넣어 dispatch가 `unknown_dependency`로 거부**됐다
   (3회). PLAN은 "work order의 `dependencies`에는 같은 manifest 안의 task_id만 넣는다.
   이미 완료된 package는 `dependencies`가 아니라 `input_artifact_hashes`의 `source_file`로만
   참조한다. 순차 실행이므로 `dependencies: []`가 기본이다"를 명시해야 한다.
2. **`state.execution_remediation_packet`은 replan으로 지워지지 않는다.** dispatch가 그
   내용을 coordinator 프롬프트에 계속 주입하므로, 오래된 "이번 manifest는 X, Y만" 지시가
   새 PLAN을 눌러 완료된 package를 다시 계획하게 만든다(1회 발생, attempt 1회 낭비).
   **새 PLAN 최상단에 "이전 remediation packet의 package 지정은 무효다. 유효한 manifest는
   이 PLAN의 work order 목록뿐이다"를 선언**하고 완료 task_id를 열거한다.
3. **`final_diff`는 `git diff HEAD`로 만든다.** `git diff`는 staged 변경도 untracked 파일도
   보지 못해 빈 diff가 보고됐다. untracked 신규 파일은 `git status --porcelain` 열거와
   파일별 sha256으로 함께 보고한다.
4. PLAN의 package DoD에 "전체 스위트 0 failed"를 걸면 선행 package가 남긴 불일치가 후속
   package를 전부 실패로 만든다. **package DoD는 "대상 테스트 통과 + 이 package가 새로
   깨뜨린 기존 테스트 없음"으로 쓰고, 전체 스위트 0 failed는 run 종료 조건으로 분리**한다.
5. **PLAN이 기존 파일 수정을 금지하면서 그 파일의 테스트가 새 계약과 충돌하면 work order는
   구조적으로 완료 불가능하다.** 직전 run에서 이 이유로 replan 1회를 소비했다. 계약을
   바꾸는 작업은 그 계약을 고정한 기존 테스트 파일을 반드시 allowed_paths에 포함한다.

### 7.5 review 호출

- `loop-engine`에는 review를 실행하는 CLI 명령이 없다. `loop_engine.adapters.codex_cli`의
  `execute_review`/`review_prompt`를 쓰는 얇은 드라이버로 호출하며, 이때 provenance와
  execution_evidence는 엔진이 기록한다.
- **review 프롬프트가 argv 상한(~128KB)을 넘으면 `OSError: Argument list too long`으로
  실패한다.** 직전 run에서 실제로 발생했다. diff와 test 로그처럼 큰 입력은 워크스페이스
  상대 경로로 넘기고 read-only 샌드박스가 직접 읽게 한다.

---

## 8. Loop Engine 작업 지시

1. 이 문서를 이번 run의 최상위 범위로 사용한다. project 이름은 `scalping-briefing-p3`다.
2. **Phase 4로 범위를 넓히지 않는다.** §4.1·§4.2를 먼저 닫고 Phase 3으로 넘어간다.
3. 운영값은 원 intent §14 부록 A에서 확정되어 있다. 표에 없는 새 설정 키가 필요하면
   임의로 정하지 말고 사용자 확인을 받는다.
4. LLM 출력과 외부 수집 콘텐츠는 신뢰 경계 밖 데이터다. 검증·정제·근거 연결 없이 저장하거나
   브리핑에 넣지 않는다.
5. 외부 공유, 실제 API 키·봇 토큰 사용, 비용 발생 서비스 활성화는 승인 없이 실행하지 않는다.
6. PLAN은 §7.2의 attempt 예산 안에 들어와야 한다. package는 8~10개, work order 크기는
   250초 규격, dispatch당 2개다. PLAN에 미착수 package의 분류 기준(아직 dispatch되지 않은
   package는 PLAN 결함이 아니라 `execution_nonconformance`)을 명시해 replan 예산을 낭비하지
   않는다.
7. 반복 실행마다 범위, 미결정 운영값, 테스트 결과, 위험을 기록한다.
8. **리뷰 증거 규칙**: review packet은 워크스페이스 diff를 증거로 사용한다. attempt 종료 후
   오케스트레이터가 산출물을 stage하고 `make test` 로그를 `--test-output-file`로 전달한다.
   `.env`·실 토큰·`storage/`·`data/`·`__pycache__`는 stage하지 않는다.

### 8.1 승인이 필요한 지점 (사전 예고)

아래는 사용자 승인 전까지 BLOCKED로 두고 진행하지 않는다.

- `DELIVERY_MODE=live` 전환, 실 봇 토큰·chat_id 사용, 실제 외부 전송
- `LLM_MODE=live` 전환 및 예산 한도 설정
- 검토 API의 로컬 바인딩 해제·외부 노출, 인증 방식 변경, 검토자 명부 도입
- 실 출처의 `active: true` 전환
- `publication_policy`를 `auto_publish`로 변경
- 원 intent §14 부록 A에 없는 설정 키 신설 또는 확정값 변경
- `P1`~`P16` 중 어느 것이든 완화하는 변경
- `.venv`에 없는 새 서드파티 의존성 도입
- 새 ORM 컬럼·새 alembic 마이그레이션

### 최종 성공 상태

검토자가 API와 CLI 양쪽에서 원문·Evidence·문서 버전을 보고 결정할 수 있고, 승인된 항목이
KST 주 2회 스케줄로 Markdown 브리핑에 묶여 아카이브되며, 같은 브리핑이 같은 채널로 두 번
전달되지 않고, 각 항목이 원문·근거·검토 상태·데이터 구간으로 역추적된다. 모든 것이
`LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`에서 네트워크 없이 재현된다.
