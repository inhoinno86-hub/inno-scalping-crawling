# Phase 2 잔여 — 검토 인터페이스와 DoD 마감 · 프로젝트 Intent

> 이 문서는 Loop Engine과 개발 에이전트가 **이번 run**의 목적, 범위, 품질 기준, 완료
> 조건을 동일하게 이해하도록 만드는 실행 기준 문서다.
>
> 상위 기준 문서: `intent-docs/scalping_strategy_briefing_intent.md` (이하 "원 intent")
> 직전 run 기준 문서: `intent-docs/scalping_phase2_candidate_review_intent.md`
> (이하 "Phase 2 intent". 절 번호를 인용할 때는 그 문서를 가리킨다.)
>
> **이번 run의 범위는 Phase 2의 잔여분으로 한정한다.** Phase 2의 수집·분류·추출·
> Evidence·점수·신규성은 이미 완료됐다(§2). Phase 3~4는 별도 run이다.
>
> Loop Engine project 이름: `scalping-briefing-p2b`
> (엔진의 run 식별용 이름이다. 제품 설정 키 `PROJECT_SLUG = scalping-briefing`은
> 바뀌지 않는다. 직전 run project `scalping-briefing-p2`는
> `DEFERRED_BACKLOG`로 종료됐고 같은 이름으로는 새 run을 만들 수 없다.)

---

## 1. 한 줄 정의

이미 생성되는 전략 후보를 **사람이 원문·Evidence·문서 버전과 함께 검토해 승인·수정·
거절할 수 있는 경로**를 만들고, Phase 2 DoD 5개 명명 테스트와 protected 요구사항
매핑으로 완료를 증명한다.

브리핑 생성과 전달은 이번 run 범위가 아니다(Phase 3).

---

## 2. 전제 — 이미 완료된 것 (재작업 금지)

직전 run(`scalping-briefing-p2`, run_id `d4c11e43-23f5-47d5-a902-64cf32b83e46`)에서
Phase 2의 파이프라인 본체가 완성됐다. 독립 review 판정 기준 protected 요구사항
20/27이 실현됐고, `make test`는 **182 passed / 0 failed**(네트워크·Docker·API 키 없이)다.

아래는 그대로 쓴다. 고쳐야 할 이유가 생기면 구현을 멈추고 worker result에 기록한다.

| 영역 | 산출물 |
| --- | --- |
| Phase 0+1 | `config/`, `schemas/` 8종, `models/` 11종, `net/`, `normalize/`, `storage/`, `sources/`, `repository/documents.py`, `pipeline/state_machine.py`, `pipeline/source_policy.py`, `publishing/gate.py`, `publishing/phrase_lint.py`, `llm/fixture.py`, `delivery/guard.py` |
| 수집 재료 | `tests/fixtures/sources/{fixture_rss_blog,fixture_atom_research,fixture_github_repo,fixture_paper_meta}/robots.txt`, `config/source-policy.yaml`의 `metadata.robots_file` |
| 분류·추출 | `pipeline/classify.py`, `pipeline/extract.py`, `llm/prompts.py`, `llm/schema_guard.py`(P11), `llm/audit.py`(P13), `llm/fixtures/response-map.json` Phase 2 매핑 |
| Evidence·검증 | `pipeline/evidence_link.py`, `pipeline/validate.py`, `publishing/candidate_view.py` |
| 점수·신규성·라우팅 | `pipeline/scoring.py`(P14), `pipeline/novelty.py`, `pipeline/routing.py`(P15) |
| 테스트 | `tests/test_phase2_collection_material.py`, `test_phase2_extraction.py`, `test_phase2_evidence.py`, `test_phase2_publication.py`, `test_phase2_scoring.py`, `test_phase2_routing.py`, `test_connectors_repo_html.py` 기대값 갱신 |

**`make test` 182 passed / 0 failed가 이번 run의 하한선이다. 어떤 작업도 이 수를 줄이거나
failed를 만들면 안 된다.**

### 2.1 직전 run에서 확정된 해석 (임의 변경 금지)

- `reviewer_id`는 호출자가 제공하는 필수 non-empty 문자열이며 그대로 기록한다.
  검토자 명부 파일이나 새 설정 키를 만들지 않는다. (원 intent §14 부록 A에 검토자
  레지스트리 키가 없기 때문이다. 명부 기반으로 바꾸려면 사용자 승인이 필요하다.)
- quote 상한 단위: **필드당 최대 2개**이면서 발행 항목 단위로도 기존
  `publishing/gate.py`의 `MAX_EVIDENCE_QUOTES` 계약을 통과해야 한다. 길이 상한은
  하드코딩하지 말고 설정 `quote_max_chars`(300)에서 읽는다.
- `fixture_exchange_docs`의 `/private` 거부는 유지한다(P1, Phase 1 DoD 3의 근거).
- 유사·중복 판정은 결정적 규칙만 쓴다. pgvector·임베딩 도입 금지.

---

## 3. 승계되는 원칙과 안전 경계

원 intent §3.1·§3.2·§3.3과 Phase 2 intent §3이 그대로 적용된다. `P1`~`P16`은 이번
run에서도 **protected**이며 퇴행시키면 안 된다. 특히:

- `P3` 공개 브리핑의 모든 핵심 주장은 `document_version_id` 단위 Evidence로 역추적
- `P4` 확인 불가 값은 `unknown`, 추정 채우기 금지
- `P5` 외부 문서·LLM 출력은 신뢰 경계 밖 데이터
- `P6` 투자 권유·매매 신호·수익 보장 표현 금지
- `P9` 기본은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`
- `P10` 비밀값은 환경변수로만
- `P16` 검토 API는 `REVIEW_API_BIND`(기본 `127.0.0.1`) 로컬 바인딩 + 단일 정적 토큰
  (`REVIEW_API_TOKEN`)으로만 접근하며, 토큰 미설정이면 기동에 실패한다

---

## 4. 이번 run의 기능 범위

### 4.1 Evidence 강제 보강 (직전 run의 미해결 지적)

직전 review는 `pipeline/validate.py`가 **Evidence가 0건인 후보도 `validated`로
통과시킨다**고 지적했다. 발행 경로는 `publishing/candidate_view.py` + `gate.py`가
차단하지만, Phase 2 intent §4.3의 "각 핵심 필드는 최소 1개의 Evidence와 연결한다"는
검증 단계에서 강제되지 않는다.

- 핵심 필드에 연결된 Evidence가 하나도 없으면 `validated`로 보내지 않는다.
  `extracted → failed`(+`error_class`)로 종결하거나, 부분 근거만 있으면 해당 필드를
  발행 제외로 표시한 채 `needs_review` 경로로 보낸다. 어느 쪽인지 구현 전에 정하고
  worker result에 근거를 남긴다.
- 실패 재현 테스트로 증명한다: Evidence 0건 후보가 `validated`에 도달하지 못한다.

### 4.2 검토 service

- 후보 조회 시 **원문 링크, `document_version`, Evidence 인용**을 함께 제공한다.
- 결정은 `review` 레코드로 남긴다: `review_id`, `reviewer_id`, `decision`, `comment`,
  `reviewed_at` (원 intent §8.4). ORM(`models/review.py`)은 이미 있다.
- 상태 전이는 `needs_review → {approved | rejected | archived}`만 허용하며 반드시
  기존 `pipeline/state_machine.py`를 통과시킨다.
- 검토자가 필드를 수정하면 **원문 기반 값을 덮어쓰지 않고** append-only 수정 이력으로
  남긴다(후보 `metadata_json` 등 기존 컬럼 사용, 새 마이그레이션 금지).

### 4.3 검토 API (P16)

- FastAPI 로컬 바인딩. `make review-api`가 진입점이며 이미 존재한다.
- **`REVIEW_API_TOKEN`이 비어 있으면 앱 생성/기동 시점에 즉시 실패한다.** 현재
  `src/scalping_briefing/__init__.py:create_review_app`은 요청 시점 401만 낸다 —
  이것으로는 P16 미충족이다.
- `/health`를 제외한 모든 데이터 엔드포인트에 토큰 검사를 적용한다.
- 인증은 단일 정적 토큰 하나뿐. 다중 사용자 계정, 역할 기반 권한, 세션·비밀번호 관리,
  공개 인터넷 노출은 범위 밖이다.

### 4.4 검토 CLI

- API와 같은 service를 쓰는 CLI 경로(목록/상세/결정). 네트워크를 쓰지 않는다.

### 4.5 fixture 녹화 스크립트

- `scripts/record_llm_fixtures.py`(현재 없음)를 만든다. 녹화 시점(`recorded_at`)과 입력
  `document_version_id`를 매핑 파일에 함께 기록한다(원 intent §9.2).
- 오프라인 전용이며 live 호출 경로를 만들지 않는다. `llm/fixtures/response-map.json`의
  기존 구조(`recording_version`/`recorded_at`/`mappings`)를 유지한다.
- 매핑이 없으면 즉시 실패하는 기존 `FixtureLLMClient` 동작을 유지한다.

---

## 5. 이번 run에서 하지 않는 것

- Phase 3: 주 2회 스케줄러, 브리핑 Markdown 생성·아카이브, 전달 커넥터·실제 전송.
  기존 `delivery/guard.py`의 금지 장치는 그대로 두고 전달 경로를 만들지 않는다.
- Phase 4: 운영 지표 대시보드, 주기 리포트, 확장 판단
- 실 출처 `active: true` 전환, `LLM_MODE=live`, `DELIVERY_MODE=live`, 검토 API 외부 노출
- 일반 사용자용 검색·추천·즐겨찾기 UI, pgvector·OpenSearch, 다채널 전달
- 원 intent §14 부록 A에 없는 설정 키 신설
- 새 ORM 컬럼·새 alembic 마이그레이션

---

## 6. 완료 기준 (DoD)

원 intent §11 Phase 2 완료 기준: "하나의 수집 문서가 근거 있는 후보로 생성되고,
근거 없는 핵심 필드는 외부 공유되지 않으며, 검토 이력과 출처 Version을 확인할 수 있다."

이를 **명명 테스트 5개**로 증명한다. 파일은 `tests/test_phase2_dod.py`이며 이름은
정확히 아래와 같아야 한다.

| 테스트 이름 | 증명 내용 |
| --- | --- |
| `test_phase2_dod1_collected_document_becomes_candidate_with_evidence` | 수집된 `document_version` 하나가 분류 → 추출 → 검증을 거쳐 Evidence가 붙은 후보로 생성된다 |
| `test_phase2_dod2_core_field_without_evidence_is_not_publishable` | Evidence 없는 핵심 필드가 발행 게이트에서 거부된다 |
| `test_phase2_dod3_review_decision_recorded_with_reviewer_and_source_version` | 검토 결정이 `reviewer_id`·`decision`·`reviewed_at`과 함께 저장되고 후보에서 원문 `document_version_id`까지 역추적된다 |
| `test_phase2_dod4_value_score_breakdown_persisted_with_reasons` | 총점과 기준별 세부 점수·판단 근거가 함께 저장된다 |
| `test_phase2_dod5_borderline_or_low_confidence_or_conflicting_goes_to_needs_review` | `P15`의 세 조건 각각이 `needs_review`를 강제한다 |

추가 완료 조건:

- `P11`~`P16` 각각에 최소 1개의 **실패 재현 테스트**가 대응하고
  `docs/protected-requirements-tests.md`에 매핑을 추가한다(현재 P1~P10만 있다).
  `tests/test_protected_mapping.py`의 `MAPPING`과 범위 단언을 `P1..P16`으로 확장한다.
- `make test`가 네트워크·Docker·API 키 없이 **0 failed**로 통과하고 passed ≥ 182.
- `make review-api`가 `REVIEW_API_TOKEN` 미설정 시 기동에 실패하고, 설정 시
  `127.0.0.1`에만 바인딩된다.
- Evidence 0건 후보가 `validated`에 도달하지 못한다(§4.1).
- 모든 산출물이 git에 추적되어 리뷰 diff에 나타난다(staging은 오케스트레이터가 수행).
- Phase 3~4에 해당하는 실행 경로를 앞당겨 구현하지 않았다.

---

## 7. 실행 환경 전제 (직전 run에서 실측된 사실)

이 절은 추측이 아니라 `scalping-briefing-p2` run에서 측정된 제약이다. PLAN 작성 시
반드시 반영한다.

- **의존성은 워크스페이스 `.venv`에 이미 설치되어 있다**: `pytest`, `sqlalchemy`,
  `alembic`, `fastapi`, `uvicorn`, `httpx`, `jsonschema`, `pydantic`, `pyyaml`.
  `Makefile`이 `.venv/bin/python`을 자동 감지한다. **worker 샌드박스에는 네트워크가
  없다 — `pip install` 금지.**
- **worker 샌드박스에서 `.git`은 read-only다.** `git add`/`commit`/`stash` 금지.
  worker가 만든 신규 파일은 **untracked로 남는 것이 정상**이며, coordinator는 이를
  산출물 누락으로 판정하면 안 된다. `git diff`는 untracked 파일을 보지 못하므로
  `git status --porcelain`으로 열거하고 파일 해시를 기록한다. staging은 attempt 종료
  후 오케스트레이터가 수행한다.
- **`worker_timeout_seconds=900`이고, 이 값이 직전 run의 최대 실패 원인이었다.**
  "구현 3~5파일" 크기의 work order는 3회 연속 900초에서 강제 종료됐고
  (`provenance_unavailable`로 dispatch 루프 정지), **"신규 파일 1개 + 그 테스트 1개,
  300초 분량"으로 줄인 뒤 8개 worker 호출이 전부 정상 종료(164~893초)했다.**
  이번 run은 처음부터 이 크기를 쓴다.
- **worker 종료 규약**: 착수 후 300초가 지나면 새 코드 작성을 중단하고 그 시점 상태로
  worker result를 확정한 뒤 종료한다. 전체 스위트는 work order당 마지막 1회만 돌리고
  그 전에는 대상 테스트 파일만 돌린다. 부분 완료는 실패가 아니다.
- **evidence 계약**: worker result의 `run_id`/`task_id`/`work_order_hash`/`run_attempt`는
  work order·manifest 값을 **그대로 복사**한다(빈 문자열·`pending` 금지, 재계산 금지).
  `output_hash`는 `sha256:` + 64자 소문자 hex. `test_evidence[].output_hash`는 실행한
  명령의 stdout+stderr 텍스트에 대한 sha256이며 소스·테스트 파일 해시를 넣지 않는다.
  진행 아티팩트는 `task_id`/`updated_at`/`completed`/`pending`/`tests_run`/`notes` 키만
  쓰고 result 확정 직전 1회만 갱신한다.
- **`max_work_orders=2`, `max_concurrent_workers=1`(순차)**. 한 dispatch는 work order
  2개를 처리한다.
- **`TMPDIR`을 디스크 기반 경로로 지정한다**(예: `/home/inno/.cache/loopeng-tmp`).
  기본 `/tmp`는 tmpfs 8.5G인데 codex가 세션 tape SQLite를 여기에 쌓아 고갈시킨다.
- **review 역할 provenance 주의**: 직전 run 마지막 2회 review 호출이 stderr 배너에서
  model/effort 파싱에 실패해 `provenance_unavailable`로 기록됐다(verdict JSON 자체는
  스키마 통과, 내용도 일관). 재발하면 원인을 확인하고 `loop-engine resume`으로
  재개하되, verdict 내용을 근거로 그대로 신뢰하지 말고 최소 1회는 정상 provenance를
  확보하려 시도한다.
- **transition 예산**: `max_execution_retries=2`, `max_replans=3`. 직전 run은 이 예산을
  모두 소진해 잔여 범위를 backlog로 넘겼다. **이번 run은 work order 크기를 처음부터
  300초 규격으로 잡아 dispatch당 2 package를 확보하고, PLAN에 미착수 package의 분류
  기준(아직 dispatch되지 않은 package는 PLAN 결함이 아니라 `execution_nonconformance`)을
  명시해 replan 예산을 낭비하지 않는다.**

---

## 8. Loop Engine 작업 지시

1. 이 문서를 이번 run의 최상위 범위로 사용한다. project 이름은 `scalping-briefing-p2b`다.
2. **Phase 3~4로 범위를 넓히지 않는다.**
3. 운영값은 원 intent §14 부록 A에서 확정되어 있다. 표에 없는 새 설정 키가 필요하면
   임의로 정하지 말고 사용자 확인을 받는다.
4. LLM 출력과 외부 수집 콘텐츠는 신뢰 경계 밖 데이터다. 검증·정제·근거 연결 없이
   저장하지 않는다.
5. 외부 공유, 실제 API 키 사용, 비용 발생 서비스 활성화는 승인 없이 실행하지 않는다.
6. 반복 실행마다 범위, 미결정 운영값, 테스트 결과, 위험을 기록한다.
7. **리뷰 증거 규칙**: review packet은 워크스페이스 `git diff`를 증거로 사용한다.
   attempt 종료 후 오케스트레이터가 산출물을 stage하고 `make test` 로그를
   `--test-output-file`로 전달한다. `.env`·실 토큰·`storage/`·`data/` 산출물은
   stage하지 않는다. review 프롬프트가 argv 길이 상한(~128KB)을 넘으면 큰 입력은
   워크스페이스 상대 경로로 전달한다(review는 read-only 샌드박스에서 직접 읽는다).

### 8.1 승인이 필요한 지점 (사전 예고)

아래는 사용자 승인 전까지 BLOCKED로 두고 진행하지 않는다.

- `LLM_MODE=live` 전환 및 예산 한도 설정
- 검토 API의 로컬 바인딩 해제·외부 노출, 인증 방식 변경, 검토자 명부 도입
- 실 출처의 `active: true` 전환
- 원 intent §14 부록 A에 없는 설정 키 신설 또는 확정값 변경
- `P1`~`P16` 중 어느 것이든 완화하는 변경
- `.venv`에 없는 새 서드파티 의존성 도입
- 새 ORM 컬럼·새 alembic 마이그레이션

### 최종 성공 상태

검토자가 원문·Evidence·문서 버전을 보고 내린 결정이 이력으로 남고, 근거 없는 후보는
검증 단계에서 걸러지며, Phase 2 DoD 5개 명명 테스트와 `P11`~`P16` 매핑이 통과한다.
모든 것이 `LLM_MODE=fixture`에서 네트워크 없이 재현된다.
