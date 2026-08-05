# Phase 4b — end-to-end 실행 오케스트레이션 배선 · 프로젝트 Intent

> 이 문서는 Loop Engine과 개발 에이전트가 **이번 run**의 목적, 범위, 품질 기준, 완료
> 조건을 동일하게 이해하도록 만드는 실행 기준 문서다.
>
> 상위 기준 문서: `intent-docs/scalping_strategy_briefing_intent.md` (이하 "원 intent")
> 직전 run 기준 문서: `intent-docs/scalping_phase4_operations_intent.md`
> (이하 "Phase 4 intent")
>
> **이번 run의 범위는 Phase 4 intent §5가 별도 run으로 분리한 "end-to-end 실행
> 오케스트레이션 배선"이다.** 수집→분류→추출→검증→근거→점수→신규성→라우팅→브리핑→
> dry-run 전달→운영 지표·리포트·알림을 **하나의 실행**으로 잇는다.
>
> Loop Engine project 이름: `scalping-briefing-p4b`
> (엔진의 run 식별용 이름이다. 제품 설정 키 `PROJECT_SLUG = scalping-briefing`은 바뀌지
> 않는다. 직전 run project `scalping-briefing-p4`는 `COMPLETE`로 종료됐고 같은 이름으로는
> 새 run을 만들지 않는다.)

---

## 1. 한 줄 정의

이미 개별적으로 완성되어 테스트된 Phase 1~4의 단계 함수들을, **새 진입점
`run_briefing_cycle()` 하나가 순서대로 호출하는 결정적 실행 사이클**로 배선한다.

이 run은 **새 도메인 로직을 만들지 않는다.** 단계 함수의 계약은 이미 고정되어 있고, 이번
run은 그 함수들을 **호출·연결·실패 격리·요약 보고**하는 얇은 오케스트레이션 계층만 만든다.
단계 함수를 고쳐야 한다고 판단되면 그것은 **발견**이며, 고치지 말고 보고하고 멈춘다(§8.1).

---

## 2. 전제 — 이미 완료된 것 (재작업 금지)

Phase 0~4가 완료됐다. 직전 run(`scalping-briefing-p4`, run_id
`ec6ad732-8beb-4fae-924a-0da640679e1c`, outcome `COMPLETE`)에서 운영 지표 6종·주기 리포트·
지표 위반 알림·4주 연속 판정·확장 권고와 Phase 4 DoD 명명 테스트 6개가 마감됐다.

**실측값(2026-08-05, 네트워크·Docker·API 키 없이): `make test` = 303 passed / 0 failed,
약 29초.**

| 영역 | 산출물 |
| --- | --- |
| Phase 0+1 | `config/`, `schemas/`, `models/` 11종, `net/`, `normalize/`, `storage/`, `sources/`, `repository/documents.py`, `pipeline/state_machine.py`, `pipeline/source_policy.py`, `publishing/gate.py`, `publishing/phrase_lint.py`, `llm/fixture.py`, `delivery/guard.py`, `alerts.py`, `logging_setup.py` |
| Phase 2 | `pipeline/classify.py`, `extract.py`, `evidence_link.py`, `validate.py`, `scoring.py`, `novelty.py`, `routing.py`, `llm/prompts.py`, `llm/schema_guard.py`, `llm/audit.py`, `publishing/candidate_view.py`, `review/service.py`, `review/cli.py` |
| Phase 3 | `pipeline/schedule.py`, `pipeline/briefing_cursor.py`, `publishing/briefing_render.py`, `publishing/briefing_build.py`, `publishing/briefing_gate.py`, `delivery/connector.py`, `delivery/service.py`, `review/api.py` |
| Phase 4 | `ops/metrics.py`, `ops/report.py`, `ops/alerting.py`, `ops/expansion.py`, `docs/operations-metrics.md` |
| DoD·보호 | `tests/test_phase1_dod.py`, `test_phase2_dod.py`, `test_phase3_dod.py`, `test_phase4_dod.py`, `test_protected_mapping.py`, `test_protected_p11_p16.py`, `docs/protected-requirements-tests.md`의 `P1`~`P16` |

**`make test` 303 passed / 0 failed가 이번 run의 하한선이다. 어떤 작업도 이 수를 줄이거나
failed를 만들면 안 된다.**

### 2.1 배선할 단계 함수의 실제 서명 (실측 확인됨 — 추측 금지)

아래는 `inspect.signature`로 확인한 실제 서명이다. **이 함수들을 수정하지 않는다.**

```python
# pipeline/classify.py
classify_document(document_version, *, session=None, llm_client=None, use_llm=False,
                  document_text=None, model_name='fixture',
                  prompt_version='phase2-classification-v1',
                  estimated_cost_usd=0.0) -> ClassificationResult

# pipeline/extract.py
extract_strategy_candidate(document_version, *positional, session=None, llm_client=None,
                           evidence=None, classification=None, document_text=None,
                           model_name='fixture', prompt_version='phase2-extraction-v1',
                           estimated_cost_usd=0.0, schema_path=None,
                           quote_max_chars=None) -> ExtractionResult

# pipeline/validate.py
validate_extracted_candidate(extracted, *positional, document_version=None, candidate=None,
                             evidence=None, schema_path=None, document_text=None,
                             quote_max_chars=None) -> ValidationResult

# pipeline/evidence_link.py
link_evidence(document_version, strategy_candidate_id, quotes=None, *,
              extraction_provenance=None, source_text=None, quote_max_chars=None,
              max_evidence_quotes=None, existing_evidence=None, evidence=None,
              accepted_quotes=None) -> list[Evidence]

# pipeline/scoring.py
score_candidate(candidate, document_version=None, existing_candidates=None, *,
                document=None, related_candidates=None, as_of=None, now=None,
                last_successful_briefing_at=None, persist=True) -> ValueScoreResult

# pipeline/novelty.py
classify_novelty(candidate, existing_candidates=None, *, persist=False) -> NoveltyResult

# pipeline/routing.py
route_candidate(candidate, document_version=None, *, settings=None, config=None,
                value_score=None, score=None, extraction_confidence=None,
                candidate_score_threshold=None,
                extraction_confidence_min=None) -> RoutingResult

# pipeline/schedule.py
schedule_trigger(scheduled_for: datetime, *, trigger_type: str) -> dict[str, Any]
next_occurrence(after, *, schedule, timezone) -> datetime
occurrences_between(start, end, *, schedule, timezone) -> list[datetime]

# publishing/briefing_build.py   — 내부에서 cursor 계산·렌더·아카이브까지 수행한다
build_briefing(session: Session, *, scheduled_for: datetime, trigger_type: str,
               settings, run_attempt: int = 1) -> Briefing

# publishing/briefing_gate.py
gate_briefing(briefing_payload, *, settings, delivery_history=None) -> object

# delivery/connector.py
TelegramDryRunConnector(storage_root=Path('storage'), *, settings=None, storage=None,
                        artifact_root=None, logger=None)

# delivery/service.py
deliver_briefing(session, briefing, *, connector, settings,
                 resend_reason=None, resend_approved_by=None) -> Delivery | None

# ops/metrics.py
compute_all_metrics(session: Session, window: ObservationWindow, *, settings=None,
                    delivery_mode: str | None = None) -> list[MetricResult]
ObservationWindow(start, end, timezone='Asia/Seoul')   # window_id는 자동 계산

# ops/report.py     render/archive (별칭 render_report / archive_report도 존재)
# ops/alerting.py
emit_metric_alerts(window, metrics, *, alerts_dir) -> list[Path]

# alerts.py
record_failure(event, message, *, details=None, severity='error', alerts_dir='alerts/') -> Path

# config.py
load_config() / load_settings() -> Settings
```

### 2.2 결정적 제약 1 — `run_briefing()`을 배선하면 protected 테스트가 깨진다 (실측)

`tests/test_protected_mapping.py::test_run_briefing_is_fixture_dry_run`(`P9` protected)은
`run_briefing()`의 출력 JSON에 대해 다음을 단언한다.

```python
assert payload["status"] == "dry_run"
assert payload["active_fixture_sources"] == 5
assert payload["persisted_versions"] >= 8
assert payload["briefing_generated"] is False      # <- 배선하면 깨진다
assert payload["delivery_invoked"] is False        # <- 배선하면 깨진다
assert payload["llm_mode"] == "fixture"
assert payload["delivery_mode"] == "dry_run"
```

**따라서 `run_briefing()`의 동작을 바꾸지 않는다.** 사용자 결정(2026-08-05):
`src/scalping_briefing/__init__.py`에 **새 진입점 `run_briefing_cycle()`을 추가(additive)**
하고, `Makefile`에 **새 타깃 `run-briefing-cycle`을 추가**한다. 기존 `run_briefing()` 함수
본문과 `run-briefing` 타깃은 **한 줄도 바꾸지 않는다.** 이로써 `P9` 완화 없이 배선이 가능하다.

### 2.3 결정적 제약 2 — 새 설정 키를 만들 수 없다 (실측)

`config.py`의 `Settings.__init__`은 `CONFIG_KEYS`에 없는 키를 받으면
`ConfigError: undefined configuration key(s): ...`로 즉시 실패한다. 설정 키 추가는 원 intent
§14 부록 A 변경이며 **사용자 승인 사항**이다.

배선에 필요한 값은 **전부 기존 키로 충당된다.** 사용 가능한 키 전량(실측):

```text
DATABASE_URL, DELIVERY_CHANNEL, DELIVERY_MODE, LLM_MODE, LLM_MONTHLY_BUDGET_USD,
LLM_RUN_MAX_TOKENS, PROJECT_SLUG, REVIEW_API_BIND, REVIEW_API_TOKEN, TIMEZONE,
WEEKLY_REPORT_SCHEDULE, alerts_dir, briefing_language, briefing_max_items,
candidate_score_threshold, extraction_confidence_min, initial_lookback_days,
llm_run_retention_days, max_collect_retries, max_lookback_days, max_redirects,
normalized_retention_days, publication_policy, quote_max_chars, raw_retention_days,
request_timeout_seconds, response_max_bytes
```

스케줄·시간대는 `WEEKLY_REPORT_SCHEDULE`·`TIMEZONE`, 알림 경로는 `alerts_dir`, 임계값은
`candidate_score_threshold`·`extraction_confidence_min`을 쓴다. 그 외에 값이 필요하면
**설정 키를 만들지 말고 함수 인자로 받는다**(Phase 4의 `output_dir`/`alerts_dir` 방식과 동일).

### 2.4 결정적 제약 3 — 사이클은 후보를 자동 승인할 수 없다 (실측)

`build_briefing`은 `review_status == "approved"`인 후보만 브리핑 본문에 싣고, 나머지는 큐
상태로 남긴다. 검토는 사람의 행위이며 `publication_policy` 기본값은 `manual_approval`이다.

따라서 **fixture 사이클의 정상 결과는 "승인 항목 0건 브리핑"이다.** 이것은 결함이 아니라
원 intent §11 Phase 3이 정의한 "빈 결과 브리핑" 경로다. **사이클이 후보를 자동으로
`approved`로 바꾸는 코드를 만들면 안 된다**(§8.1 승인 사항이자 `P15` 위반).

### 2.5 재사용할 기존 테스트 자산

`tests/test_phase3_briefing_build.py`(`CORE_FIELDS`, `SETTINGS`, `_candidate(...)`,
`_database(tmp_path)`), `tests/test_phase3_delivery_service.py`(`SETTINGS`, `ATTEMPTED_AT`,
`SpyConnector`, `_session()`, `_briefing(...)`), `tests/test_protected_mapping.py`의
fixture 수집 경로를 import하거나 같은 패턴으로 최소한만 확장한다.
**새 conftest·새 공용 픽스처 모듈을 만들지 않는다.**

---

## 3. 승계되는 원칙과 안전 경계

원 intent §3.1·§3.2·§3.3, Phase 3 intent §3, Phase 4 intent §3이 그대로 적용된다.
`P1`~`P16`은 이번 run에서도 **protected**이며 퇴행시키면 안 된다. 이번 범위와 직접 맞닿는 것:

- `P3` 공개 브리핑의 모든 핵심 주장은 `document_version_id` 단위 Evidence로 역추적
- `P4` 확인 불가 값은 `unknown`, 추정 채우기 금지. 사이클이 빈 단계 결과를 성공으로 위장하지
  않는다
- `P6` 투자 권유·매매 신호·수익 보장 표현 금지 (사이클 요약 출력도 예외 아님)
- `P8` 동일 `briefing_id`+채널 중복 전달 금지. **사이클을 두 번 돌려도 전달이 두 번 일어나지
  않는다**
- `P9` 기본은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`. **사이클은 실제 전송·실 키를
  쓰지 않는다.** `run_briefing()`의 기존 계약은 불변(§2.2)
- `P10` 비밀값은 환경변수로만. 사이클 요약 출력·알림 아티팩트에 토큰·chat_id·원문 전문이 들어가면
  안 된다 (`mask_secrets` 경유 필수)
- `P15` 경계 점수·낮은 신뢰도·충돌 필드는 `needs_review`로 남는다. 사이클이 이를 우회하지 않는다
- `P16` 검토 API는 로컬 바인딩 유지. 사이클이 API를 띄우거나 라우트를 추가하지 않는다

원 intent §7.1의 확정 사항도 승계한다: **실패 알림은 브리핑 전달 채널과 분리한다.** 사이클의
단계 실패 알림을 Telegram 커넥터나 `delivery/service.py`로 보내지 않는다.

---

## 4. 이번 run의 기능 범위

### 4.1 새 진입점 — `run_briefing_cycle()`

- `src/scalping_briefing/__init__.py`에 **함수 추가**. `run_briefing()` 본문은 불변(§2.2).
- 반환은 종료 코드 `int`. 사이클 요약을 결정적 JSON으로 stdout에 출력한다(`run_briefing()`과
  같은 방식). 요약 최소 필드: `phase`, `status`, `llm_mode`, `delivery_mode`,
  `scheduled_for`, `trigger_type`, `briefing_id`, 단계별 `processed`/`succeeded`/`failed` 수,
  `briefing_generated`, `delivery_invoked`, `delivery_status`, `metrics`(지표 ID→판정),
  `report_path`, `alerts_written`, `failures`(단계·식별자·사유 요약).
- `Makefile`에 타깃 `run-briefing-cycle` 추가. 기존 `run-briefing`·`review-api` 타깃은 불변.
- 오케스트레이션 본체는 **`src/scalping_briefing/orchestration/cycle.py`(신규)** 에 두고,
  `__init__.py`의 진입점은 그것을 호출하는 얇은 래퍼로 만든다.
- **세션·엔진은 사이클 함수가 인자로 받을 수 있어야 한다.** 진입점만 `load_config()`로
  엔진을 만든다. 모듈 전역 세션·전역 엔진을 만들지 않는다(테스트가 인메모리 SQLite를 주입한다).

### 4.2 단계 배선 — `orchestration/cycle.py`

한 실행은 아래 순서다. 각 단계는 §2.1의 기존 함수를 호출할 뿐이다.

```text
1. 수집        기존 run_briefing()의 수집 경로와 동일한 결과(Document/DocumentVersion)
2. 분류        classify_document        (use_llm=False, LLM_MODE=fixture 기본)
3. 추출        extract_strategy_candidate
4. 검증        validate_extracted_candidate
5. 근거 연결   link_evidence
6. 점수        score_candidate
7. 신규성      classify_novelty
8. 라우팅      route_candidate          (needs_review / approved 대기 큐로 분기)
9. 브리핑      build_briefing           (승인 항목만 본문, 내부에서 렌더·아카이브)
10. 게이트     gate_briefing
11. 전달       deliver_briefing         (TelegramDryRunConnector, DELIVERY_MODE=dry_run)
12. 지표       compute_all_metrics      (§4.4)
13. 리포트     render/archive           (§4.4)
14. 알림       emit_metric_alerts       (§4.4, breach·insufficient_data 시)
```

- **수집 경로 재사용**: 1단계는 기존 수집 동작과 동일한 결과를 만들어야 한다. `run_briefing()`
  본문을 복사·분기시키지 말고, 공통 수집 로직을 `orchestration/` 아래 함수로 **추출**하되
  `run_briefing()`의 관찰 가능한 출력(§2.2의 7개 단언)은 바뀌지 않아야 한다. 이 리팩터링이
  `run_briefing()` 출력을 바꾼다고 판단되면 리팩터링하지 말고 **수집 단계를 사이클 쪽에서
  독립 호출**하는 방식으로 우회한다.
- 스케줄 경계는 `WEEKLY_REPORT_SCHEDULE`·`TIMEZONE` 기준으로 `pipeline/schedule.py`를 재사용해
  계산한다. `scheduled_for`/`trigger_type`은 함수 인자로 주입 가능해야 한다(테스트 결정성).
- **`(scheduled_for, trigger_type)`이 멱등 경계다.** 같은 값으로 사이클을 두 번 돌려도
  `Briefing`이 하나이고 전달이 한 번이다(`P8`).
- 사이클은 **상태 전이를 새로 정의하지 않는다.** `pipeline/state_machine.py`의 기존 전이만 쓴다.

### 4.3 실패 격리와 종료 신호

사용자 결정(2026-08-05): **단계별 격리 + 계속 진행.**

- 한 문서·한 후보의 단계 실패가 **사이클 전체를 멈추지 않는다.** 실패를 잡아
  `alerts.record_failure(...)`로 `alerts/` 아티팩트에 남기고, 요약의 `failures`에 집계한 뒤
  **다음 항목으로 계속 진행**한다.
- 실패 알림은 **전달 채널과 분리**한다(`delivery/` 호출 금지, 네트워크 금지). 시크릿·원문
  전문을 details에 넣지 않는다(`P10`).
- **성공을 위장하지 않는다.** 실패가 하나라도 있으면 요약 `status`가 성공이 아니고 종료 코드가
  0이 아니다. 부분 성공은 "부분 성공"으로 보고한다.
- 단, **`build_briefing`/`gate_briefing`/`deliver_briefing` 같은 사이클 수준 단계의 실패**는
  그 지점 이후 단계를 건너뛰되(전달 없이 브리핑을 만들 수 없다) 사이클 자체는 §4.4의 지표·
  리포트·알림까지 수행하고 종료한다. 관측이 실패 때문에 사라지면 안 된다.

### 4.4 운영 지표·리포트·알림 연동

사용자 결정(2026-08-05): **사이클 끝에 연결한다.**

- 전달 단계 이후 `compute_all_metrics(session, window, settings=..., delivery_mode=...)`로
  지표 6종을 계산한다. 관측 창은 이번 실행의 브리핑 창 또는 인자로 주입된 창을 쓴다.
- 지표 결과로 `ops/report.py`의 렌더·아카이브를 호출한다. 출력 디렉터리는 **함수 인자**이며
  기본값은 기존 Phase 4 기본값(`storage/ops-reports/`)이다. 새 설정 키를 만들지 않는다.
- `breached`·`insufficient_data` 항목에 대해 `ops/alerting.py`의 `emit_metric_alerts`를 호출한다.
  `alert_id`는 이미 `{window_id}:{metric_id}` 결정적 값이므로 **사이클을 두 번 돌려도 알림이
  증식하지 않는다.**
- **표본이 없으면 `insufficient_data`다**(`P4`). fixture 1회 실행에서 대부분의 지표가
  `insufficient_data`인 것이 정상이며, 이를 충족으로 바꾸지 않는다.

### 4.5 문서 갱신

- **신규** `docs/orchestration-cycle.md`: 사이클 단계 순서와 각 단계가 호출하는 함수, 멱등
  경계, 실패 격리 정책과 종료 코드 의미, 요약 JSON 필드 사전, `run-briefing`과
  `run-briefing-cycle`의 차이, fixture 실행에서 "승인 0건 브리핑"과 `insufficient_data`가
  정상인 이유.
- **갱신** `docs/operations.md`: Phase 4b 항목을 "deferred"에서 "구현됨"으로 바꾸고 새 make
  타깃을 운영 절차에 반영한다. 다른 절은 건드리지 않는다.
- **갱신** `docs/protected-requirements-tests.md`: Phase 4b 코드가 `P8`/`P9`/`P10`/`P15`에 닿는
  지점마다 **node ID를 추가**한다. `MAPPING` 범위는 `P1`~`P16` 그대로이며 **새 P 항목을 만들지
  않고 기존 node ID를 지우지 않는다.** `tests/test_protected_mapping.py`가
  `pytest --collect-only`와 대조하므로 추가한 node ID는 실제로 수집 가능해야 한다.

---

## 5. 이번 run에서 하지 않는 것

- **새 도메인 로직.** 분류·추출·검증·점수·라우팅·브리핑·전달·지표의 **계산 규칙을 바꾸지
  않는다.** 이번 run은 호출 순서와 실패 처리만 만든다
- `run_briefing()` 동작 변경, `run-briefing` 타깃 변경, `P9` 테스트 단언 완화
- 후보 자동 승인, `publication_policy: auto_publish` 전환
- `DELIVERY_MODE=live`, 실 봇 토큰·chat_id, 실제 외부 전송
- `LLM_MODE=live` 전환, 실 출처 `active: true` 전환
- 새 설정 키·새 ORM 컬럼·새 alembic 마이그레이션·새 서드파티 의존성
- 스케줄러 데몬·cron 설치·백그라운드 프로세스·큐 시스템. 사이클은 **한 번 호출되면 한 번
  실행되고 끝나는 함수**다. 반복 실행은 외부(운영자·cron)의 몫이며 이번 범위가 아니다
- 웹 대시보드, HTTP 지표 엔드포인트, 검토 API 라우트 추가
- 병렬 실행·비동기 전환. 사이클은 순차·결정적이다
- Phase 1~4 기존 기능 코드 수정. 고쳐야 한다고 판단되면 그것은 발견이며 worker result의
  `blockers`에 사유를 남기고 멈춘다
- 실 운영 데이터 생성·시드 스크립트. 테스트는 fixture와 테스트 안에서 구성한 레코드만 쓴다

---

## 6. 완료 기준 (DoD)

**명명 테스트 6개**로 증명한다. 파일은 `tests/test_phase4b_dod.py`이며 이름은 정확히 아래와
같아야 한다.

| 테스트 이름 | 증명 내용 |
| --- | --- |
| `test_phase4b_dod1_cycle_runs_collection_through_dry_run_delivery_in_one_execution` | 한 번의 `run_briefing_cycle()` 호출이 수집부터 dry-run 전달까지 §4.2 순서대로 수행하고 결정적 요약 JSON을 낸다 |
| `test_phase4b_dod2_stage_failure_is_isolated_and_cycle_continues_with_alert` | 한 항목의 단계 실패가 사이클을 멈추지 않고 `alerts/` 아티팩트와 요약 `failures`에 남으며 나머지 항목이 계속 처리된다 |
| `test_phase4b_dod3_repeated_cycle_for_same_trigger_does_not_duplicate_briefing_or_delivery` | 같은 `(scheduled_for, trigger_type)`으로 두 번 실행해도 `Briefing` 1건·전달 1건이다(`P8`) |
| `test_phase4b_dod4_cycle_never_auto_approves_candidates_and_never_sends_live` | 사이클이 후보를 자동 승인하지 않고 `needs_review`를 유지하며, `DELIVERY_MODE=dry_run`·`LLM_MODE=fixture`에서 네트워크를 쓰지 않는다(`P9`, `P15`) |
| `test_phase4b_dod5_cycle_emits_metrics_report_and_alerts_after_delivery` | 전달 이후 지표 6종이 계산되고 리포트가 아카이브되며 위반·표본부족이 알림으로 남는다. 표본 없는 지표는 `insufficient_data`다(`P4`) |
| `test_phase4b_dod6_run_briefing_entrypoint_contract_is_unchanged` | 기존 `run_briefing()`의 출력 계약 7개 단언이 그대로 성립한다(`P9` 불변) |

추가 완료 조건:

- `run_briefing_cycle()`이 `src/scalping_briefing/__init__.py`에서 import 가능하고
  `make run-briefing-cycle`이 동작한다.
- `docs/orchestration-cycle.md`가 존재하고 §4.5 항목을 담는다.
- `docs/operations.md`의 Phase 4b 항목이 구현됨으로 갱신된다.
- `docs/protected-requirements-tests.md`의 `P8`/`P9`/`P10`/`P15` 행에 Phase 4b node ID가
  추가되고 `tests/test_protected_mapping.py`, `tests/test_protected_p11_p16.py`가 계속 통과한다.
- `make test`가 네트워크·Docker·API 키 없이 **0 failed**로 통과하고 **passed ≥ 309**
  (303 + DoD 6개, 단위 테스트는 그 위에 더해진다).
- `git diff HEAD`에 `config/default.toml`, `.env.example`, `CONFIG_KEYS`, `models/`,
  `migrations/`, 의존성 선언 파일 변경이 없다.
- `run_briefing()` 함수 본문과 `Makefile`의 `run-briefing` 타깃이 바뀌지 않았다.
- 모든 산출물이 git에 추적되어 리뷰 diff에 나타난다(staging은 오케스트레이터가 수행).

---

## 7. 실행 환경 전제 (직전 run들에서 실측된 사실 — PLAN에 반드시 반영)

이 절은 추측이 아니라 `scalping-briefing-p3`/`p3b`/`p4` run에서 측정된 제약이다.

### 7.1 워크스페이스

- **의존성은 워크스페이스 `.venv`에 이미 설치되어 있다.** `Makefile`이 `.venv/bin/python`을
  자동 감지한다. **worker 샌드박스에 네트워크가 없다 — `pip install` 금지.**
- **worker 샌드박스에서 `.git`은 read-only다.** `git add`/`commit`/`stash` 금지. worker가 만든
  신규 파일은 untracked로 남는 것이 정상이며 coordinator는 이를 산출물 누락으로 판정하면 안 된다.
  staging은 attempt 종료 후 오케스트레이터가 수행한다.
- **`TMPDIR`을 디스크 기반 경로로 지정한다**(`/home/inno/.cache/loopeng-tmp`). 기본 `/tmp`는
  tmpfs이며 codex 세션 tape SQLite가 고갈시킨다.
- `make test`는 약 29초, 303 passed다. 전체 스위트는 work order당 마지막 1회만 돌린다.

### 7.2 worker 900초 kill — PLAN의 최우선 제약

`p4` run 실측: worker 3회 중 2회가 `worker_timeout_seconds=900`에 걸려 강제 종료됐고
`provenance_unavailable`로 dispatch 루프가 멈췄다(`outcome: BLOCKED`). **두 번 다 코드는
완성돼 있었고 worker가 result JSON만 못 남긴 경우였다.** 관측된 worker 소요는 272~900초이며,
이미 완료된 작업을 재확인하는 실행조차 870초를 썼다 — **Luna는 작업량과 무관하게 시간 예산을
거의 전부 소진한다.**

PLAN은 각 work order의 `objective` 끝에 아래를 그대로 싣는다.

> HARD TIME BOX — follow this as a procedure, not as a target. Write the source file first
> and the test file second. Run your narrow target test at most TWICE; after the second run
> stop editing whether or not it passes. At 600 seconds elapsed stop all work unconditionally
> and spend the remaining time only on writing your worker_result JSON, which must be on disk
> before 900 seconds. Emitting a worker_result whose tests still fail is a SUCCESS and costs
> nothing. Being killed at the 900s timeout is a run-stopping FAILURE: it produces
> provenance_unavailable and blocks every remaining package.

**work order 크기는 "신규 파일 1개 + 그 테스트 1개, 250초 분량"으로 잡는다.**

`provenance_unavailable`로 BLOCKED가 되면 **예산을 쓰지 않고 복구할 수 있다**: 산출물을 직접
검증한 뒤 `loop-engine resume --project {P} --evidence "..."`, 그다음 같은 attempt로 dispatch를
다시 호출하면 남은 worker와 coordinator integrate가 진행된다(`p4`에서 실측 확인). `resume`은
`execution_retry_count`를 소모하지 않는다.

### 7.3 dispatch는 attempt당 1회다 — PLAN 크기의 결정적 제약

`loop-engine dispatch`를 같은 `run_attempt`에서 다시 부르면 이미 result가 있는 work order는
재실행되지 않는다. 새 package를 태우려면 `complete-run → project-review → retry-run`으로
**새 attempt**를 만들어야 한다.

- `max_work_orders=2`, `max_concurrent_workers=1`(순차) → **attempt 1회 = package 2개**.
- `max_execution_retries=2`. **한도 도달 후 `retry-run`을 부르면 CLI가 packet을 `plan_defect`로
  자동 승격해 `replan` 경로를 탄다**(`p4`에서 실측). 그때 Claude가 축소판 PLAN_v2를 쓴다.
  `max_replans=3`이므로 총 attempt 발급권은 약 6회다.
- 최악 예산 검사: `coordinator_plan(600) + max_work_orders(2) × worker(900) × (1+retries 1)
  + integrate(900) = 4500 ≤ run_timeout_seconds(5400)`. 출하 기본값은 이 검사를 통과한다.
- 이번 run의 package는 **6개**다(§7.4). attempt 3회면 끝나고 여유가 크다.

### 7.4 권장 package 분할

| package | 내용 | 배치 |
| --- | --- | --- |
| A | `src/scalping_briefing/orchestration/__init__.py`, `cycle.py`에 사이클 골격(단계 실행기·실패 격리·요약 자료구조) + `tests/test_phase4b_cycle_skeleton.py` | attempt 1 |
| B | 같은 모듈에 2~8단계(분류→추출→검증→근거→점수→신규성→라우팅) 배선 + `tests/test_phase4b_candidate_stages.py` | attempt 1 |
| C | 1단계 수집 연결과 9~11단계(브리핑 빌드→게이트→dry-run 전달) 배선, 멱등 경계 + `tests/test_phase4b_briefing_delivery.py` | attempt 2 |
| D | 12~14단계 ops 연동(지표→리포트→알림)과 요약 JSON 확정 + `tests/test_phase4b_ops_hookup.py` | attempt 2 |
| E | `__init__.py`에 `run_briefing_cycle()` 추가, `Makefile`에 `run-briefing-cycle` 타깃 추가, `docs/orchestration-cycle.md` 신규, `docs/operations.md` 갱신 + `tests/test_phase4b_entrypoint.py` | attempt 3 |
| F | `tests/test_phase4b_dod.py` 명명 테스트 6개 + `docs/protected-requirements-tests.md` node ID 추가 | attempt 3 |

B는 A가 만든 모듈에 이어 쓰므로 A 다음이다. F는 A~E의 산출물과 node ID를 참조하므로 마지막이다.

### 7.5 coordinator가 반복해서 틀린 것 — PLAN이 미리 막아야 한다

1. **완료 package를 `dependencies`에 넣어 dispatch가 `unknown_dependency`로 거부**됐다.
   PLAN은 "work order의 `dependencies`에는 같은 manifest 안의 task_id만 넣는다. 이미 완료된
   package는 `dependencies`가 아니라 `input_artifact_hashes`의 `source_file`로만 참조한다.
   순차 실행이므로 `dependencies: []`가 기본이다"를 명시해야 한다.
2. **`state.execution_remediation_packet`은 replan으로 지워지지 않는다.** dispatch가 그 내용을
   coordinator 프롬프트에 계속 주입하므로 오래된 지시가 새 PLAN을 누른다. **새 PLAN 최상단에
   "이전 remediation packet의 package 지정은 무효다. 유효한 manifest는 이 PLAN의 work order
   목록뿐이다"를 선언**하고 완료 task_id를 표로 열거한다.
3. **`final_diff`는 `git diff HEAD`로 만든다.** `p4` run에서 coordinator가 attempt 3회 중 2회
   빈 diff(`sha256:e3b0c442...` = 빈 문자열 해시)를 제출하고 그것을 사유로 run report를
   `fail`로 만들었다. PLAN은 `git diff HEAD` 강제와 함께 **"빈 diff나 untracked 파일을 사유로
   status fail을 내지 않는다"**를 명시한다. untracked 신규 파일은 `git status --porcelain`
   열거와 파일별 sha256으로 함께 보고한다.
4. package DoD에 "전체 스위트 0 failed"를 걸면 선행 package가 남긴 불일치가 후속 package를 전부
   실패로 만든다. **package DoD는 "대상 테스트 통과 + 이 package가 새로 깨뜨린 기존 테스트
   없음"으로 쓰고, 전체 스위트 0 failed는 run 종료 조건으로 분리**한다.
5. **PLAN이 기존 파일 수정을 금지하면서 그 파일의 테스트가 새 계약과 충돌하면 work order는
   구조적으로 완료 불가능하다.** 이번 run은 §2.2에서 이 충돌을 이미 제거했다(진입점 추가 방식).
   package E는 `__init__.py`와 `Makefile`을 **추가만** 하므로 `allowed_paths`에 두 파일을
   포함하되 `run_briefing()` 본문과 `run-briefing` 타깃 불변을 acceptance_criteria로 건다.
6. **evidence 계약**: worker result의 `run_id`/`task_id`/`work_order_hash`/`run_attempt`는 work
   order·manifest 값을 **그대로 복사**한다(빈 문자열·`pending`·재계산 금지 — `p4`에서 2회 위반).
   worker result는 엔진이 지정한 출력 경로에 쓴다(`/tmp` 경로 금지). `output_hash`는
   `sha256:` + 64자 소문자 hex. `test_evidence[].output_hash`는 실행한 명령의 stdout+stderr
   텍스트에 대한 sha256이며 소스·테스트 파일 해시가 아니다.

### 7.6 review 호출

- `loop-engine`에는 review를 실행하는 CLI 명령이 없다. `loop_engine.adapters.codex_cli`의
  `execute_review`/`review_prompt`를 쓰는 얇은 드라이버로 호출한다. verdict는
  `loop-engine verdict set --project {P} --file {verdict.json}`으로 state에 반영해야
  `complete`/`defer` 전환이 통과한다(`p4`에서 실측).
- **review 프롬프트가 argv 상한(~128KB)을 넘으면 `OSError: Argument list too long`으로
  실패한다.** PLAN·diff·test 로그는 **워크스페이스 상대 경로로 넘기고** read-only review
  세션이 직접 열게 한다. `state.execution_evidence`는 provenance 필드만 추려서 싣는다.
  `p4`에서 이 방식으로 프롬프트를 약 52KB로 유지했다.

---

## 8. Loop Engine 작업 지시

1. 이 문서를 이번 run의 최상위 범위로 사용한다. project 이름은 `scalping-briefing-p4b`다.
2. **범위를 넓히지 않는다.** 단계 함수의 계산 규칙을 고쳐야 할 것 같으면 그것은 발견이며
   이번 run의 범위 밖이다. 배선은 기존 계약 위에서만 이뤄진다.
3. 운영값은 원 intent §14 부록 A에서 확정되어 있다. 표에 없는 새 설정 키가 필요하면 임의로
   정하지 말고 함수 인자로 받거나 사용자 확인을 받는다(§2.3).
4. LLM 출력과 외부 수집 콘텐츠는 신뢰 경계 밖 데이터다. 사이클 요약·알림에 원문 전문이나
   미검증 콘텐츠를 넣지 않는다.
5. 외부 공유, 실제 API 키·봇 토큰 사용, 비용 발생 서비스 활성화는 승인 없이 실행하지 않는다.
6. PLAN은 §7.3의 attempt 예산 안에 들어와야 한다. package는 6개(§7.4), work order 크기는 §7.2
   절차 규격, dispatch당 2개다. PLAN에 미착수 package의 분류 기준(아직 dispatch되지 않은 package는
   PLAN 결함이 아니라 `execution_nonconformance`)을 명시해 replan 예산을 낭비하지 않는다.
7. 반복 실행마다 범위, 미결정 운영값, 테스트 결과, 위험을 기록한다.
8. **리뷰 증거 규칙**: review packet은 워크스페이스 diff를 증거로 사용한다. attempt 종료 후
   오케스트레이터가 산출물을 stage하고 `make test` 로그를 `--test-output-file`로 전달한다.
   `.env`·실 토큰·`storage/`·`data/`·`alerts/`·`__pycache__`는 stage하지 않는다.

### 8.1 승인이 필요한 지점 (사전 예고)

아래는 사용자 승인 전까지 BLOCKED로 두고 진행하지 않는다.

- `run_briefing()` 동작 변경 또는 `P9` 테스트 단언 완화(§2.2)
- 사이클의 후보 자동 승인, `publication_policy`를 `auto_publish`로 변경(§2.4)
- 원 intent §14 부록 A 확정값의 실제 변경, 부록 A에 없는 설정 키 신설(= `CONFIG_KEYS` 수정)
- 실 출처의 `active: true` 전환
- `DELIVERY_MODE=live` 전환, 실 봇 토큰·chat_id 사용, 실제 외부 전송
- `LLM_MODE=live` 전환 및 예산 한도 설정
- 알림의 외부 채널 연동(현재는 `alerts/` 아티팩트 + 구조화 로그만)
- 검토 API의 로컬 바인딩 해제·외부 노출, 라우트 추가
- `P1`~`P16` 중 어느 것이든 완화하는 변경
- `.venv`에 없는 새 서드파티 의존성 도입
- 새 ORM 컬럼·새 alembic 마이그레이션
- 스케줄러 데몬·cron 설치 등 상시 실행 프로세스 도입
- Phase 1~4 기존 기능 코드 수정(테스트가 실제 결함을 찾은 경우 포함 — 발견을 보고하고 멈춘다)

### 최종 성공 상태

운영자가 `make run-briefing-cycle` 한 번으로 수집부터 dry-run 전달까지를 실행하고, 그 실행이
어떤 단계에서 몇 건을 처리하고 몇 건이 실패했는지를 결정적 요약 JSON과 `alerts/` 아티팩트로
읽을 수 있다. 같은 스케줄 트리거로 다시 실행해도 브리핑과 전달이 중복되지 않는다. 사이클은
후보를 자동 승인하지 않고 실제 전송을 하지 않으며, 실행 직후의 운영 지표·리포트·알림이 같은
사이클 안에서 남는다. 기존 `run_briefing()`의 계약과 `P1`~`P16`은 하나도 퇴행하지 않았고
`make test`는 309 이상 passed / 0 failed로 네트워크·Docker·API 키 없이 재현된다. 이로써
수집→브리핑→전달의 end-to-end 경로가 닫히고, 남은 것은 실 출처·실 전달 활성화라는 별도의
승인 결정뿐이다.
