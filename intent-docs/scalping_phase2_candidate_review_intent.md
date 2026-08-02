# Phase 2 — 전략 후보 선별과 검토 · 프로젝트 Intent

> 이 문서는 Loop Engine과 개발 에이전트가 **이번 run**의 목적, 범위, 품질 기준, 완료
> 조건을 동일하게 이해하도록 만드는 실행 기준 문서다.
>
> 상위 기준 문서: `intent-docs/scalping_strategy_briefing_intent.md`
> (이하 "원 intent". 절 번호를 인용할 때는 그 문서를 가리킨다.)
>
> **이번 run의 구현 범위는 Phase 2로 한정한다.** Phase 0+1은 이미 완료됐고(§2),
> Phase 3~4는 각각 별도 run에서 수행한다(원 intent §11).
>
> Loop Engine project 이름: `scalping-briefing-p2`
> (엔진의 run 식별용 이름이다. 제품 설정 키 `PROJECT_SLUG = scalping-briefing`은
> 바뀌지 않는다. `loop-engine init`은 같은 project 이름에 run이 있으면
> `state already exists`로 거부하므로 새 이름을 쓴다.)

---

## 1. 한 줄 정의

Phase 1이 수집·보존한 문서 버전에서 **초단기 스캘핑 전략 후보를 근거와 함께 추출**하고,
가치 기준으로 점수화하며, 사람이 원문과 근거를 보고 승인·수정·거절할 수 있는
검토 경로를 만든다.

브리핑 생성과 전달은 이번 run 범위가 아니다(Phase 3).

---

## 2. 전제 — 이미 완료된 것 (재작업 금지)

커밋 `69e42d1` (`main`) 기준으로 Phase 0 + Phase 1이 완료됐고 독립 리뷰에서
`plan_conformance 6/6`, `intent_realization 26/26`으로 통과했다. `make test`는
**137 passed**(네트워크·Docker·API 키 없이)다.

아래는 그대로 쓴다. 고쳐야 할 이유가 생기면 구현을 멈추고 worker result에 기록한다.

| 영역 | 산출물 |
| --- | --- |
| 설정·안전 | `config/default.toml`, `.env.example`, `config.py`(fail-closed live 가드), `logging_setup.py`(시크릿 마스킹), `alerts.py` |
| 계약 | `schemas/` 8종, `pipeline/state_machine.py`(원 intent §9.1), `pipeline/source_policy.py`, `docs/notices.md` |
| 영속 | `models/` 11종, `alembic.ini`, `migrations/versions/0001_initial_persistence.py`(명시적 DDL), `delivery/guard.py` |
| 수집 | `net/`(transport·guards·rate_limit·robots·retry), `normalize/`(urls·sanitize), `storage/files.py`, `repository/documents.py`, `sources/`(registry·window·커넥터 4종) |
| 발행 경계 | `publishing/phrase_lint.py`, `publishing/gate.py` |
| LLM 경계 | `llm/fixture.py`(`FixtureLLMClient`), `llm/fixtures/response-map.json` |
| 테스트 | `tests/` 20개 파일, `tests/test_phase1_dod.py`(Phase 1 DoD 명명 테스트 5개), `docs/protected-requirements-tests.md` + `tests/test_protected_mapping.py` |

**`make test` 137 passed가 이번 run의 하한선이다. 어떤 작업도 이 수를 줄이면 안 된다.**

---

## 3. 승계되는 원칙과 안전 경계

원 intent §3.1·§3.2·§3.3이 그대로 적용된다. protected `P1`~`P10`은 이번 run에서도
**protected**이며, 이번 run의 변경이 그것을 퇴행시키면 안 된다.

이번 run에서 특히 중요한 승계 항목:

- `P3` 공개 브리핑의 모든 핵심 주장은 `document_version_id` 단위 Evidence로 역추적
- `P4` 확인 불가 값은 `unknown`, 추정 채우기 금지
- `P5` 외부 문서·**LLM 출력은 신뢰 경계 밖 데이터**, 검증 없이 저장·발행 금지
- `P6` 투자 권유·매매 신호·수익 보장 표현 금지
- `P9` 기본은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`. live 전환은 승인 필요
- `P10` 비밀값은 환경변수로만, 저장소·로그 노출 금지

### 3.1 이번 run에서 새로 부여하는 protected 요구사항

| ID | 요구사항 | 근거 절 |
| --- | --- | --- |
| P11 | LLM 구조화 출력은 JSON Schema 검증을 통과해야만 저장된다. 실패한 출력은 저장·발행하지 않고 실패로 기록한다 | 원 §9.2 |
| P12 | 원문에 없는 규칙·파라미터·성과·위험도를 생성하지 않는다. 근거를 찾을 수 없으면 `unknown` 또는 `needs_review`를 반환한다 | 원 §9.2 |
| P13 | 모든 LLM 호출은 `llm_run` 레코드로 남긴다: 모델명, 프롬프트 버전, 실행 시각, 입력 `document_version_id`, 토큰 사용량, 추정 비용 | 원 §9.2 |
| P14 | 가치 점수는 총점만이 아니라 기준별 세부 점수와 판단 근거를 함께 저장한다. 블랙박스 순위를 만들지 않는다 | 원 §5 |
| P15 | 아래 중 하나면 점수와 무관하게 `needs_review`로 보낸다: 총점이 `candidate_score_threshold ± 10` 구간, `extraction_confidence < extraction_confidence_min`, 핵심 필드(`entry_logic`/`exit_logic`/`required_data`) 중 하나 이상이 `conflicting` | 원 §5 |
| P16 | 검토 API는 `REVIEW_API_BIND`(기본 `127.0.0.1`) 로컬 바인딩 + 단일 정적 토큰(`REVIEW_API_TOKEN`, 환경변수)으로만 접근한다. 토큰 미설정이면 기동에 실패한다 | 원 §7.1 |

그 외 요구사항은 `normal`이며 근거를 남기면 후속 run으로 이월할 수 있다.

---

## 4. 이번 run의 기능 범위

### 4.1 관련성 분류 (`classified`)

수집·정규화된 `document_version`을 대상으로 원 intent §4.1 기준에 따라 판정한다.

- 결과는 상태 전이 `deduplicated → classified → {extracted | irrelevant | background_only}`로 기록한다. 전이는 반드시 기존 `pipeline/state_machine.py`를 통과시킨다.
- `irrelevant`: 초단기 스캘핑 후보 아님. 메타데이터와 판정 근거만 보존.
- `background_only`: 시장 미시구조 배경 자료. 보관하되 후보에 넣지 않는다.
- 판정 근거(어떤 신호로 그렇게 분류했는지)를 함께 저장한다.

### 4.2 구조화 추출 (`extracted`)

`schemas/strategy_candidate.schema.json`을 만족하는 후보를 생성한다. 필드는
원 intent §8.3을 따르며, Phase 0에서 이미 6개 핵심 필드에 대응 `*_status`가
required로 강제되어 있다(`core_hypothesis`, `signal_inputs`, `entry_logic`,
`exit_logic`, `required_data`, `risk_notes`).

- 값이 원문에 명시되어 있으면 `explicit`, 추론이면 `inferred`, 없으면 `unknown`,
  서로 충돌하면 `conflicting`, 해당 없으면 `not_applicable`.
- 빈 값을 기본값이나 추정으로 채우지 않는다 (`P4`, `P12`).

### 4.3 Evidence 연결과 검증 (`validated`)

- 각 핵심 필드는 최소 1개의 Evidence와 연결한다:
  `evidence_id`, `document_version_id`, `strategy_candidate_id`, `field_name`,
  `quote`, `section_or_locator`, `captured_at` (원 §8.4).
- `quote`는 항목당 최대 2개, 각 `quote_max_chars`(300) 이내 (`P2`).
- Evidence가 없는 핵심 주장은 외부 공유 대상에서 제외된다. 기존
  `publishing/gate.py`의 최소 1 Evidence 규칙을 재사용한다.
- 스키마 검증 실패는 `extracted → failed`로 종결하고 `error_class`를 남긴다.

### 4.4 가치 점수와 신규성

원 intent §5의 5개 기준과 가중치를 그대로 쓴다: 출처 신뢰도 30, 재현 가능성 25,
초단기 관련성 20, 최신성 15, 새로움 10. 총점 100점.

- `value_score`와 `value_score_breakdown`(기준별 점수 + 판단 근거)을 저장한다 (`P14`).
- `novelty_status`: 신규 / 기존 전략의 신규 근거 / 변경 / 변형 / 중복 후보.
  `related_strategy_ids`로 관계를 남긴다.
- 유사·중복 판정은 결정적 규칙(정규화된 이름, 전략군·자산군·보유 시간 범위,
  핵심 필드 유사도)으로 구현한다. **pgvector·임베딩 검색을 도입하지 않는다**
  (원 §7.2, §10.1).
- `validated → {needs_review | rejected}` 전이는 `P15`의 세 조건을 강제한다.

### 4.5 검토 인터페이스

원 intent §7.1 범위를 넘지 않는다.

- **FastAPI 로컬 바인딩 + CLI** 두 경로. `make review-api`가 진입점이다.
- 인증은 단일 정적 토큰(`REVIEW_API_TOKEN`) 하나뿐. 다중 사용자 계정, 역할 기반
  권한, 세션·비밀번호 관리, 공개 인터넷 노출은 범위 밖이다 (`P16`).
- 검토자는 후보와 함께 **원문 링크, 문서 버전, Evidence 인용**을 볼 수 있어야 한다.
- 결정은 `review` 레코드로 남긴다: `review_id`, `reviewer_id`, `decision`,
  `comment`, `reviewed_at` (원 §8.4). `reviewer_id`는 설정에 등록된 검토자 식별자
  문자열이다.
- `needs_review → {approved | rejected | archived}` 전이만 허용한다.
- 검토자가 필드를 수정하면 원문 기반 값을 덮어쓰지 않고 수정 이력으로 남긴다.

### 4.6 LLM 실행 전제와 fixture 녹화

- LLM 접근은 `LLMClient` 인터페이스 하나로만 이뤄진다. 기본 구현체는 기존
  `FixtureLLMClient`이며 `LLM_MODE` 기본값 `fixture`를 바꾸지 않는다.
- **모든 완료 기준과 DoD는 `LLM_MODE=fixture`에서 달성 가능해야 한다.** 실 API 키
  없이 전체 테스트와 end-to-end 흐름이 통과하지 않으면 미완료다 (원 §9.2).
- `src/scalping_briefing/llm/fixtures/response-map.json`에 Phase 2용
  프롬프트 해시 → 응답 매핑을 추가한다. 매핑이 없으면 조용히 넘어가지 않고 즉시
  실패하는 기존 동작을 유지한다.
- **fixture 녹화·갱신 스크립트를 만든다**(현재 없음). 녹화 시점과 입력
  `document_version_id`를 파일에 함께 기록한다 (원 §9.2).
- `LLM_MODE=live` 전환은 사용자 승인 사항이며 이번 run에서 하지 않는다 (`P9`).

---

## 5. 준비 항목 — robots fixture 보강

Phase 1 종료 시점 실측: `make run-briefing`이 fixture 5종에서 8건을 수집하는데
그중 **5건이 `access_denied`**다(`fixture_rss_blog` 2, `fixture_atom_research` 2,
`fixture_exchange_docs` 1). 본문이 남는 건 `fixture_github_repo` 2건과
`fixture_paper_meta` 1건뿐이다.

원인은 `tests/fixtures/sources/` 5종 중 `robots.txt`를 가진 것이
`fixture_exchange_docs`뿐이라, robots 평가가 나머지를 `unknown`으로 판정하고
`robots_allowed != true` 규칙에 걸리는 것이다. 동작 자체는 fail-closed로 원 intent
§3.1·§8.2에 부합하며 Phase 1 리뷰에서 `P1` 통과 판정을 받았다.

그러나 Phase 2는 **본문에서 후보를 추출**하는 단계라 이대로면 재료가 3건뿐이다.
따라서 이번 run의 첫 작업으로 다음을 수행한다.

- `fixture_rss_blog`, `fixture_atom_research`, `fixture_github_repo`,
  `fixture_paper_meta`에 대응하는 `robots.txt` fixture를 추가한다. 각 fixture는
  해당 출처의 실제 robots 응답을 녹화한 형태여야 하며, 피드·API 경로를 허용하되
  최소 하나의 `Disallow` 규칙을 포함해 평가기가 실제로 규칙을 파싱함을 증명한다.
- `fixture_exchange_docs`의 `/private` 거부는 **그대로 유지한다.** 이 케이스가
  `P1`과 Phase 1 DoD 3의 근거이므로 완화하면 안 된다.
- 보강 후 `make run-briefing`에서 `access_denied`가 `fixture_exchange_docs`의
  해당 문서로만 한정되고 나머지 출처의 본문이 저장됨을 테스트로 증명한다.

**이 방침을 바꾸려면(예: robots 부재를 허용으로 해석) 사용자 승인이 필요하다.**
`P1`을 완화하는 방향의 변경은 승인 없이 진행하지 않는다.

---

## 6. 이번 run에서 하지 않는 것

- 주 2회 스케줄러 실행, 브리핑 Markdown 생성, 브리핑 아카이브 (Phase 3)
- 전달 커넥터, 실제 메시지 전송 (Phase 3). 기존 `delivery/guard.py`의 금지 장치는
  그대로 두고 전달 경로를 만들지 않는다.
- 운영 지표 대시보드, 주기 리포트, 확장 판단 (Phase 4)
- 실 출처 `active: true` 전환, `LLM_MODE=live`, `DELIVERY_MODE=live`,
  검토 API 외부 노출 (원 §13.2 승인 대상)
- 일반 사용자용 검색·추천·즐겨찾기 UI, pgvector·OpenSearch, 다채널 전달 (원 §7.2)
- §14 부록 A에 없는 설정 키 신설

---

## 7. 완료 기준 (DoD)

원 intent §11 Phase 2 완료 기준: "하나의 수집 문서가 근거 있는 후보로 생성되고,
근거 없는 핵심 필드는 외부 공유되지 않으며, 검토 이력과 출처 Version을 확인할 수 있다."

이를 아래 **명명 테스트 5개**로 증명한다. 파일은 `tests/test_phase2_dod.py`다.

| 테스트 이름 | 증명 내용 |
| --- | --- |
| `test_phase2_dod1_collected_document_becomes_candidate_with_evidence` | 수집된 `document_version` 하나가 관련성 분류 → 추출 → 검증을 거쳐 Evidence가 붙은 후보로 생성된다 |
| `test_phase2_dod2_core_field_without_evidence_is_not_publishable` | Evidence 없는 핵심 필드가 발행 게이트에서 거부된다 |
| `test_phase2_dod3_review_decision_recorded_with_reviewer_and_source_version` | 검토 결정이 `reviewer_id`·`decision`·`reviewed_at`과 함께 저장되고 후보에서 원문 `document_version_id`까지 역추적된다 |
| `test_phase2_dod4_value_score_breakdown_persisted_with_reasons` | 총점과 기준별 세부 점수·판단 근거가 함께 저장된다 |
| `test_phase2_dod5_borderline_or_low_confidence_or_conflicting_goes_to_needs_review` | `P15`의 세 조건 각각이 `needs_review`를 강제한다 |

추가 완료 조건:

- `P11`~`P16` 각각에 최소 1개의 **실패 재현 테스트**가 대응하고,
  `docs/protected-requirements-tests.md`에 매핑을 추가한다. 기존
  `tests/test_protected_mapping.py`가 새 항목도 검사하도록 확장한다.
- `make test`가 네트워크·Docker·API 키 없이 통과하고 테스트 수가 **137을 넘는다**.
- `make review-api`가 `REVIEW_API_TOKEN` 미설정 시 기동에 실패하고, 설정 시
  `127.0.0.1`에만 바인딩된다.
- 모든 산출물이 git에 추적되어 리뷰 diff에 나타난다(staging은 오케스트레이터가 수행).
- Phase 3~4에 해당하는 실행 경로를 앞당겨 구현하지 않았다.

---

## 8. 실행 환경 전제 (Phase 0+1 run에서 확인된 사실)

이 절은 추측이 아니라 직전 run에서 실측된 제약이다. PLAN 작성 시 반드시 반영한다.

- **의존성은 워크스페이스 `.venv`에 이미 설치되어 있다**: `pytest`, `sqlalchemy`,
  `alembic`, `fastapi`, `uvicorn`, `httpx`, `jsonschema`, `pydantic`, `pyyaml`.
  `Makefile`이 `.venv/bin/python`을 자동 감지한다.
- **worker 샌드박스에는 네트워크가 없다.** `pip install`을 시도하지 않는다. 목록에
  없는 패키지가 필요하면 표준 라이브러리로 대체하거나 구현을 멈추고 보고한다.
- **worker 샌드박스에서 `.git`은 read-only다.** `git add`/`commit`/`stash`를
  실행하지 않는다. staging은 attempt 종료 후 오케스트레이터가 수행한다.
- **`worker_timeout_seconds=900`이다.** worker는 착수 후 약 600초를 지나면 새 파일을
  시작하지 않고, 진행 아티팩트를 최종 갱신한 뒤 worker result를 먼저 확정하고
  종료한다. 부분 완료는 실패가 아니며 다음 attempt가 이어받는다.
- **`max_work_orders=2`, `max_concurrent_workers=1`(순차)이다.** work package는
  "구현 5개 + 테스트 2개" 파일 상한을 넘지 않게 쪼갠다.
- **evidence 계약**: worker result의 `run_attempt`는 work order에 명시된 값을 그대로
  복사한다(재시도 횟수가 아니다). `output_hash`는 `sha256:` + 64자 소문자 hex.
  진행 아티팩트는 `task_id`/`updated_at`/`completed`/`pending`/`tests_run`(문자열)/
  `notes` 키만 쓴다.
- **coordinator는 `dependencies`에 이번 manifest에 포함된 `task_id`만 넣는다.**
  완료된 package를 참조하면 `unknown_dependency`로 dispatch가 거부된다.
- **`TMPDIR`을 디스크 기반 경로로 지정한다**(예: `/home/inno/.cache/loopeng-tmp`).
  기본 `/tmp`는 tmpfs 8.5G인데 codex가 세션 tape SQLite를 여기에 쌓아 고갈시키고,
  고갈되면 샌드박스가 `EDQUOT`로 실패한다. 직전 run에서 실제로 3회 발생했다.

---

## 9. Loop Engine 작업 지시

1. 이 문서를 이번 run의 최상위 범위로 사용한다. Loop Engine project 이름은
   `scalping-briefing-p2`다.
2. **Phase 3~4로 범위를 넓히지 않는다.** 필요성이 확인되면 후속 intent로 분리한다.
3. 운영값은 원 intent §14 부록 A에서 확정되어 있다. 표에 없는 새 설정 키가 필요하면
   임의로 정하지 말고 사용자 확인을 받는다.
4. LLM 출력과 외부 수집 콘텐츠는 신뢰 경계 밖 데이터다. 검증·정제·근거 연결 없이
   저장하지 않는다.
5. 외부 공유, 실제 API 키 사용, 비용 발생 서비스 활성화는 승인 없이 실행하지 않는다.
   기본값은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`이다.
6. 반복 실행마다 범위, 미결정 운영값, 테스트 결과, 위험을 기록한다. 핵심 intent를
   바꿀 필요가 생기면 구현을 멈추고 재계획한다.
7. **리뷰 증거 규칙**: `project-review`의 review packet은 워크스페이스 `git diff`를
   증거로 사용한다. attempt 종료 후 오케스트레이터가 산출물을 stage하고,
   `make test` 로그를 `--test-output-file`로 전달한다. `.env`·실 토큰·`storage/`
   산출물은 stage하지 않는다.

### 9.1 승인이 필요한 지점 (사전 예고)

아래는 사용자 승인 전까지 BLOCKED로 두고 진행하지 않는다.

- `LLM_MODE=live` 전환 및 예산 한도(`LLM_MONTHLY_BUDGET_USD`, `LLM_RUN_MAX_TOKENS`) 설정
- 검토 API의 로컬 바인딩 해제·외부 노출, 인증 방식 변경
- 실 출처의 `active: true` 전환
- 원 intent §14 표에 없는 설정 키 신설 또는 확정값 변경
- `P1`~`P16` 중 어느 것이든 완화하는 변경 (§5의 robots 방침 변경 포함)
- `.venv`에 없는 새 서드파티 의존성 도입

### 최종 성공 상태

Phase 1이 보존한 문서 버전에서 근거 있는 전략 후보가 생성되고, 근거 없는 핵심
필드는 외부 공유 경로에서 차단되며, 검토자가 원문·Evidence·문서 버전을 보고 내린
결정이 이력으로 남는다. 모든 것이 `LLM_MODE=fixture`에서 네트워크 없이 재현된다.
