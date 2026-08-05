# Phase 4 — 운영 지표·주기 리포트·실패 알림·확장 판단 · 프로젝트 Intent

> 이 문서는 Loop Engine과 개발 에이전트가 **이번 run**의 목적, 범위, 품질 기준, 완료
> 조건을 동일하게 이해하도록 만드는 실행 기준 문서다.
>
> 상위 기준 문서: `intent-docs/scalping_strategy_briefing_intent.md` (이하 "원 intent")
> 직전 run 기준 문서: `intent-docs/scalping_phase3b_dod_tests_intent.md`
> (이하 "Phase 3b intent")
>
> **이번 run의 범위는 원 intent §11 Phase 4 중 "측정·보고·알림·판단"이다.**
> 수집→브리핑→전달을 하나의 실행으로 잇는 **end-to-end 오케스트레이션 배선은 이번 run의
> 범위가 아니며 Phase 4b(별도 run)로 분리**한다(사용자 결정, 2026-08-05).
>
> Loop Engine project 이름: `scalping-briefing-p4`
> (엔진의 run 식별용 이름이다. 제품 설정 키 `PROJECT_SLUG = scalping-briefing`은 바뀌지
> 않는다. 직전 run project `scalping-briefing-p3b`는 `COMPLETE`로 종료됐고 같은 이름으로는
> 새 run을 만들지 않는다.)

---

## 1. 한 줄 정의

이미 저장되고 있는 레코드(`CollectionJob`, `Document`/`DocumentVersion`,
`StrategyCandidate`, `Review`, `Briefing`/`BriefingItem`, `Evidence`, `Delivery`)에서
**원 intent §11 Phase 4 지표 6종을 결정적으로 계산**하고, 그 결과를 **주기 Markdown 운영
리포트**로 조회 가능하게 만들며, **목표 위반 시 운영자에게 전달 채널과 분리된 알림**을 남기고,
**확장 결정(자동 발행·출처 확대·검색 UI)을 측정 결과와 Source Policy에 근거해 문서화**한다.

지표는 관측된 레코드에서만 나온다. 관측 표본이 없으면 `insufficient_data`이며 **목표 충족으로
판정하지 않는다.**

---

## 2. 전제 — 이미 완료된 것 (재작업 금지)

Phase 0~3이 완료됐다. 직전 run(`scalping-briefing-p3b`, run_id
`52bed4c4-3016-4678-bdc9-b3d1e11c8372`, outcome `COMPLETE`, commit `212694f`, `main` 병합
완료)에서 Phase 3 DoD 명명 테스트 6개와 protected 매핑이 마감됐다.

**실측값(2026-08-05, 네트워크·Docker·API 키 없이): `make test` = 278 passed / 0 failed,
29.16초.**

| 영역 | 산출물 |
| --- | --- |
| Phase 0+1 | `config/`, `schemas/`, `models/` 11종, `net/`, `normalize/`, `storage/`, `sources/`, `repository/documents.py`, `pipeline/state_machine.py`, `pipeline/source_policy.py`, `publishing/gate.py`, `publishing/phrase_lint.py`, `llm/fixture.py`, `delivery/guard.py`, `alerts.py`, `logging_setup.py` |
| Phase 2 | `pipeline/classify.py`, `extract.py`, `evidence_link.py`, `validate.py`, `scoring.py`, `novelty.py`, `routing.py`, `llm/prompts.py`, `llm/schema_guard.py`, `llm/audit.py`, `publishing/candidate_view.py`, `review/service.py`, `review/cli.py` |
| Phase 3 | `pipeline/schedule.py`, `pipeline/briefing_cursor.py`, `publishing/briefing_render.py`, `publishing/briefing_build.py`, `publishing/briefing_gate.py`, `delivery/connector.py`, `delivery/service.py`, `review/api.py` |
| DoD·보호 | `tests/test_phase1_dod.py`, `tests/test_phase2_dod.py`, `tests/test_phase3_dod.py`, `tests/test_protected_mapping.py`, `tests/test_protected_p11_p16.py`, `docs/protected-requirements-tests.md`의 `P1`~`P16` |

**`make test` 278 passed / 0 failed가 이번 run의 하한선이다. 어떤 작업도 이 수를 줄이거나
failed를 만들면 안 된다.**

### 2.1 이 run이 읽을 실제 레코드 필드 (실측 확인됨 — 추측 금지)

지표는 아래 컬럼에서만 계산한다. **새 ORM 컬럼을 만들지 않는다.**

```text
# models/pipeline.py :: CollectionJob        -> M1
collection_job_id, source_id, job_type, status, scheduled_for, started_at,
completed_at, attempt_no, error_class, retry_count, next_retry_at,
last_error_at, terminal_error, error

# models/source.py :: Source
source_id, active, last_success_at, cursor, error_state, trust_tier

# models/briefing.py :: Briefing             -> M2, M4, M6
briefing_id, scheduled_for, trigger_type, run_attempt, window_start, window_end,
window_truncated, run_status, publication_status, generated_at, shared_at,
timezone, markdown_location, source_summary, candidate_count, approved_count,
items_truncated

# models/briefing.py :: BriefingItem         -> M6
briefing_item_id, briefing_id, strategy_candidate_id, strategy_id,
reason_included, rank, carried_over, core_claim, canonical_name, summary,
asset_classes, strategy_families, holding_horizon, value_score

# models/delivery.py :: Delivery             -> M4
delivery_id, briefing_id, channel, idempotency_key, content_hash, attempt_no,
resend_reason, resend_approved_by, attempted_at, status, provider_reference, error

# models/document.py :: Document / DocumentVersion  -> M5
document_id, source_id, canonical_url, collection_status, processing_status,
access_status, content_hash / document_version_id, document_id, version_no,
retrieved_at, content_hash, body_hash, change_summary, collection_status

# models/strategy.py :: StrategyCandidate    -> M3
candidate_id, relevance_status, review_status
  review_status IN ('pending','needs_review','approved','rejected','archived')
extraction_confidence, value_score, value_score_breakdown, field_status, novelty_status

# models/review.py :: Review                 -> M3
review_id, strategy_candidate_id, reviewer_id, decision, reviewed_at

# models/evidence.py :: Evidence             -> M6
evidence_id, document_version_id, strategy_candidate_id, field_name, quote,
section_or_locator, captured_at, source_url
```

### 2.2 재사용할 기존 API (새로 만들지 말 것)

```python
# src/scalping_briefing/alerts.py  — 실패 알림은 이미 있다. 새 알림 모듈을 만들지 않는다.
write_alert(event, message, *, severity="error", details=None,
            alerts_dir="alerts/", alert_id=None) -> Path
record_failure(event, message, *, details=None, severity="error",
               alerts_dir="alerts/") -> Path
#   payload = {alert_id, created_at(UTC ISO), event, severity, message, details}
#   mask_secrets()가 이미 적용된다. 별도 마스킹을 다시 구현하지 않는다.

# src/scalping_briefing/logging_setup.py
mask_secrets(value, *, key=None, secret_values=None)
is_secret_key(key) -> bool

# src/scalping_briefing/pipeline/schedule.py  — 주간 창 경계 계산에 재사용
next_occurrence(after, *, schedule, timezone) -> datetime
occurrences_between(start, end, *, schedule, timezone) -> list[datetime]

# src/scalping_briefing/config.py
load_config() / load_settings() -> Settings
```

### 2.3 결정적 제약 — 새 설정 키를 만들 수 없다 (실측)

`config.py`의 `Settings.__init__`은 `CONFIG_KEYS`에 없는 키를 받으면
`ConfigError: undefined configuration key(s): ...`로 즉시 실패하고, `__getattr__`/`get`도
미정의 키 접근을 거부한다. 즉 **설정 키 추가는 `CONFIG_KEYS` 수정 = 원 intent §14 부록 A
변경이며 사용자 승인 사항**이다.

따라서 이번 run의 **지표 목표값(§4.1 표)은 설정 키가 아니라 지표 모듈의 모듈 수준 상수**로
둔다. 리포트에는 그 상수를 목표값으로 함께 출력한다. 목표값을 설정으로 빼고 싶어지면
구현하지 말고 사용자 확인을 받는다.

### 2.4 재사용할 기존 테스트 자산

`tests/test_phase3_briefing_build.py`(`CORE_FIELDS`, `SETTINGS`, `_candidate(...)`,
`_database(tmp_path)`)와 `tests/test_phase3_delivery_service.py`(`SETTINGS`, `ATTEMPTED_AT`,
`SpyConnector`, `_session()`, `_briefing(...)`, `_add(...)`, `_close(...)`)를 import하거나
같은 패턴으로 최소한만 확장한다. **새 conftest·새 공용 픽스처 모듈을 만들지 않는다.**

---

## 3. 승계되는 원칙과 안전 경계

원 intent §3.1·§3.2·§3.3과 Phase 3 intent §3이 그대로 적용된다. `P1`~`P16`은 이번 run에서도
**protected**이며 퇴행시키면 안 된다. 이번 범위와 직접 맞닿는 것:

- `P3` 공개 브리핑의 모든 핵심 주장은 `document_version_id` 단위 Evidence로 역추적
  (지표 M6이 이 규칙의 위반률을 측정한다 — 측정이 규칙을 대체하지 않는다)
- `P4` 확인 불가 값은 `unknown`, 추정 채우기 금지 → **표본이 없는 지표를 100%·0%로 채우지
  않는다.** `insufficient_data`가 정답이다
- `P6` 투자 권유·매매 신호·수익 보장 표현 금지 (운영 리포트 본문도 예외 아님)
- `P8` 동일 `briefing_id`+채널 중복 전달 금지
- `P9` 기본은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`. 실제 전송·실 키 사용은 승인 전 금지
- `P10` 비밀값은 환경변수로만. **리포트·알림 아티팩트에 토큰·chat_id·원문 전문이 들어가면 안
  된다** (`mask_secrets` 경유 필수)
- `P16` 검토 API는 `REVIEW_API_BIND`(기본 `127.0.0.1`) 로컬 바인딩 + 단일 정적 토큰

원 intent §7.1의 확정 사항도 승계한다: **실패 알림은 브리핑 전달 채널과 분리한다.** 지표 위반
알림을 Telegram 커넥터나 `delivery/service.py`로 보내지 않는다. 구조화 로그 + `alerts/`
아티팩트가 이번 run의 유일한 알림 경로다.

---

## 4. 이번 run의 기능 범위

### 4.1 지표 6종 — `src/scalping_briefing/ops/metrics.py`

원 intent §11 Phase 4 표의 6개 지표를 **주간 관측 창(observation window) 단위**로 계산한다.
창의 기본 경계는 `TIMEZONE=Asia/Seoul` 기준 주(week)이며, 경계 계산은 `pipeline/schedule.py`를
재사용한다. 각 지표는 값·목표·판정(`meets_target` / `breached` / `insufficient_data`)·분자·
분모·표본 수를 함께 반환한다. **숫자만 반환하는 블랙박스 지표를 만들지 않는다**(§5 원칙과 동일).

| ID | 지표 | 목표 | 확정 계산 규칙 |
| --- | --- | --- | --- |
| M1 | 활성 출처 수집 성공률 | ≥ 95% (주간) | 분모 = 창 안에서 **종결된** `CollectionJob`(`status` 성공 또는 `terminal_error is True`) 수, 분자 = 성공 종결 수. 재시도 중간 실패는 분모에 넣지 않는다. `Source.active is False`인 출처는 제외한다 |
| M2 | 브리핑 실행 → 초안 생성 지연 | ≤ 30분 | `run_status`가 성공인 `Briefing`에 대해 `generated_at - scheduled_for`. 같은 `briefing_id`의 재시도는 **최신 `run_attempt` 1건만** 센다. 판정은 창 내 **최댓값**으로 하고 리포트에는 최댓값·중앙값·표본 수를 함께 싣는다 |
| M3 | 검토 대기 후보 적체 | ≤ 20건 | 창 종료 시각 스냅샷에서 `StrategyCandidate.review_status == "needs_review"`인 후보 수. `approved`/`rejected`/`archived`는 제외한다 |
| M4 | 전달 실패율 (재시도 후) | ≤ 2% | `(briefing_id, channel)` 쌍 단위로 창 내 **최대 `attempt_no`** 레코드의 `status`를 그 쌍의 최종 결과로 본다. 분모 = 쌍의 수, 분자 = 최종 결과가 성공이 아닌 쌍의 수. `DELIVERY_MODE=dry_run`의 성공도 성공으로 세되 **리포트에 현재 모드를 반드시 표기**한다 |
| M5 | 문서 중복 생성률 | 0% | 분모 = 창 내 생성된 `DocumentVersion` 수, 분자 = 같은 `document_id` 안에서 이미 존재하는 `content_hash`를 다시 만든 Version 수 |
| M6 | 공개 항목 Evidence 누락률 | 0% | 분모 = 창 내 발행 대상 브리핑의 `BriefingItem` 중 `core_claim is True`인 항목 수, 분자 = 연결된 `Evidence`가 0건인 항목 수 |

공통 규칙:

- **표본이 0이면 `insufficient_data`다.** `meets_target: true`를 반환하지 않는다(`P4`).
- 계산은 순수 조회 + 산술이다. 지표 계산이 레코드를 쓰거나 상태를 전이시키지 않는다.
- 세션은 인자로 받는다. 모듈 전역 세션·전역 엔진을 만들지 않는다.
- 부동소수 비교는 목표값 상수와 비교하며, 목표값 상수는 이 표에서 온다(§2.3).

### 4.2 주기 운영 리포트 — `src/scalping_briefing/ops/report.py`

- 하나의 관측 창에 대한 **Markdown 운영 리포트**를 렌더링하고 파일로 아카이브한다. 출력
  디렉터리는 함수 인자(`output_dir`)로 받으며 기본값은 `storage/ops-reports/`다. **새 설정 키를
  만들지 않는다**(§2.3).
- 본문 최소 항목: 리포트 ID, 생성 시각, 시간대, 관측 창 `start`/`end`, `LLM_MODE`·
  `DELIVERY_MODE` 현재 값, 지표 6종의 값·목표·판정·분자/분모/표본 수, 위반 지표 목록,
  `insufficient_data` 지표 목록, 4주 연속 관찰 상태(§4.4), 확장 권고(§4.5), 고지 문구.
- 서술 언어는 한국어(`briefing_language: ko`). 기술 용어·식별자는 원문 표기를 유지한다.
- **`publishing/phrase_lint.py`의 금지 표현 검사를 리포트 본문에도 적용한다**(`P6`). 원문 인용·
  원문 전문·토큰·chat_id를 넣지 않는다(`P2`, `P10`).
- 이 리포트는 브리핑이 아니다. `Briefing`/`BriefingItem` 레코드를 만들지 않고 전달 커넥터를
  호출하지 않는다.
- **원 intent §11의 "대시보드 또는 주기 리포트" 중 주기 리포트를 택한다.** 웹 대시보드·HTTP
  지표 엔드포인트는 이번 run 범위가 아니다.

### 4.3 지표 위반 알림 — `src/scalping_briefing/ops/alerting.py`

- 지표 판정이 `breached`인 항목마다 기존 `alerts.write_alert`로 운영자 알림 아티팩트를 남긴다.
  `event`는 지표를 식별할 수 있어야 하고(`metric_breach` + 지표 ID), `details`에 값·목표·창·
  분자/분모를 담는다.
- `insufficient_data`는 위반이 아니라 **별도 severity(경고)** 로 기록한다. 침묵하지 않는다.
- **전달 채널과 분리한다.** `delivery/` 아래 어떤 함수도 호출하지 않으며 네트워크를 쓰지 않는다.
- 같은 창·같은 지표에 대해 반복 실행해도 알림이 무한 증식하지 않도록 `alert_id`를
  `{window_id}:{metric_id}` 기반의 결정적 값으로 만든다.

### 4.4 4주 연속 관찰 판정 — `src/scalping_briefing/ops/expansion.py`

원 intent §11 Phase 4: "4주 연속 관찰값이 목표를 만족할 때만 확장을 검토한다."

- 연속한 주간 창 관측 결과 목록을 받아 `expansion_eligible`을 판정한다. **가장 최근 4개 창이
  모두 존재하고, 그 4개 창의 지표 6종이 모두 `meets_target`일 때만 `true`**다.
- 창이 4개 미만이거나 어느 창에 `insufficient_data`가 하나라도 있으면
  `expansion_eligible: false` + 사유 `insufficient_data`다. **비어 있는 관측을 충족으로 보지
  않는다.**
- 판정 결과에는 항상 사유(어느 창의 어느 지표가 막았는지)를 담는다.

### 4.5 확장 결정과 임계값 재조정 — 권고까지만

- 확장 후보는 원 intent §11이 지정한 3가지다: **자동 발행(`publication_policy: auto_publish`),
  출처 확대(실 출처 `active: true`), 검색 UI**. 각각에 대해 판정 결과와 Source Policy를 근거로
  한 권고(`recommend` / `hold`)와 사유를 만든다.
- 원 intent §14 부록 A에서 "변경 조건: Phase 4 측정"인 값(`initial_lookback_days`,
  `max_lookback_days`, `candidate_score_threshold`, `briefing_max_items`,
  `extraction_confidence_min`, `max_collect_retries`)에 대해 **재조정 권고안**을 만든다.
- **이번 run은 어떤 확정값도 실제로 바꾸지 않는다.** `config/default.toml`,
  `.env.example`, `CONFIG_KEYS`를 수정하지 않는다. 값 변경은 사용자 승인 사항이다(§8.1).
- 결과는 `docs/operations-metrics.md`에 문서화한다. 현재 관측 표본이 없으므로 이번 run의
  문서화된 결정은 **"측정 데이터 부족 — 확장하지 않음"** 이 정상이며, 그것이 근거와 함께
  기록되면 완료다.

### 4.6 문서 갱신

- **신규** `docs/operations-metrics.md`: 지표 6종의 정의·계산 규칙·목표·판정 규칙,
  `insufficient_data` 원칙, 리포트 위치와 읽는 법, 알림 경로, 4주 연속 판정 규칙, 확장 결정
  기록(현재 상태 포함), 임계값 재조정 권고안.
- **갱신** `docs/operations.md`: "Deferred phases"의 Phase 4 항목을 이번 run에서 구현된 범위와
  Phase 4b(오케스트레이션 배선)로 갱신한다.
- **갱신** `docs/protected-requirements-tests.md`: Phase 4 코드가 `P4`/`P6`/`P9`/`P10`에 닿는
  지점마다 **node ID를 추가**한다. `MAPPING` 범위는 `P1`~`P16` 그대로이며 **새 P 항목을 만들지
  않고 기존 node ID를 지우지 않는다.** `tests/test_protected_mapping.py`가
  `pytest --collect-only`와 대조하므로 추가한 node ID는 실제로 수집 가능해야 한다.

---

## 5. 이번 run에서 하지 않는 것

- **end-to-end 실행 오케스트레이션 배선.** `run_briefing()`을 수집→분류→추출→검증→점수→라우팅
  →브리핑→dry-run 전달로 잇는 작업은 **Phase 4b(별도 run)** 다. `src/scalping_briefing/__init__.py`의
  `run_briefing()`은 현재의 Phase 0+1 수집 전용 동작 그대로 둔다.
- 웹 대시보드, HTTP 지표 엔드포인트, 검토 API에 지표 라우트 추가
- Prometheus·OpenTelemetry·Grafana 등 새 서드파티 의존성 도입
- `DELIVERY_MODE=live`, 실 봇 토큰·chat_id, 실제 외부 전송, 알림의 외부 채널 연동
- `LLM_MODE=live` 전환, 실 출처 `active: true` 전환, `publication_policy: auto_publish` 전환
- 확정 설정값의 실제 변경(권고안 문서화까지만), 새 설정 키 신설
- 새 ORM 컬럼·새 alembic 마이그레이션
- Phase 1~3 기존 동작 수정. 지표 계산을 위해 기존 코드를 고쳐야 한다고 판단되면 그것은 발견이며
  worker result의 `blockers`에 사유를 남기고 멈춘다
- 실 운영 데이터 생성·시드 스크립트. 지표 테스트는 테스트 안에서 레코드를 직접 구성한다

---

## 6. 완료 기준 (DoD)

원 intent §11 Phase 4 완료 기준: "위 지표가 대시보드 또는 주기 리포트로 조회 가능하고, 실패
알림이 운영자에게 도달하며, 확장 결정(자동 발행, 출처 확대, 검색 UI)이 측정 결과와 Source
Policy에 근거해 문서화된다."

이를 **명명 테스트 6개**로 증명한다. 파일은 `tests/test_phase4_dod.py`이며 이름은 정확히 아래와
같아야 한다.

| 테스트 이름 | 증명 내용 |
| --- | --- |
| `test_phase4_dod1_six_operational_metrics_are_computed_from_records` | 지표 6종이 §4.1 규칙대로 레코드에서 계산되고 값·목표·판정·분자/분모/표본 수를 함께 반환한다 |
| `test_phase4_dod2_periodic_report_renders_all_metrics_with_window_and_targets` | 주기 Markdown 리포트가 관측 창·현재 모드·지표 6종의 값과 목표·위반 목록을 담아 아카이브되고, 금지 표현 검사를 통과한다 |
| `test_phase4_dod3_metric_breach_emits_operator_alert_separate_from_delivery_channel` | 목표 위반이 `alerts/` 아티팩트로 운영자에게 도달하고, 그 경로가 전달 커넥터·네트워크를 쓰지 않는다 |
| `test_phase4_dod4_missing_observations_are_insufficient_data_not_passing` | 표본이 없는 지표가 `insufficient_data`로 판정되고 목표 충족으로 계산되지 않는다 |
| `test_phase4_dod5_expansion_requires_four_consecutive_weeks_meeting_targets` | 4주 연속 충족일 때만 `expansion_eligible`이며, 3주 또는 `insufficient_data` 포함 시 사유와 함께 거부된다 |
| `test_phase4_dod6_threshold_recalibration_is_recommendation_only_and_config_unchanged` | 임계값 재조정이 권고안으로만 산출되고 `config/default.toml`·`CONFIG_KEYS`가 바뀌지 않는다 |

추가 완료 조건:

- `docs/operations-metrics.md`가 존재하고 지표 정의·판정 규칙·확장 결정(현재 "데이터 부족 —
  확장하지 않음")·임계값 재조정 권고안을 담는다.
- `docs/operations.md`의 Phase 4 항목이 이번 run 범위와 Phase 4b 분리를 반영한다.
- `docs/protected-requirements-tests.md`의 `P4`/`P6`/`P9`/`P10` 행에 Phase 4 node ID가 추가되고
  `tests/test_protected_mapping.py`, `tests/test_protected_p11_p16.py`가 계속 통과한다.
- `make test`가 네트워크·Docker·API 키 없이 **0 failed**로 통과하고 **passed ≥ 284**
  (278 + DoD 6개, 지표·리포트·알림 단위 테스트는 그 위에 더해진다).
- 네트워크 호출 없이 전 경로가 재현된다(`LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`).
- `src/scalping_briefing/__init__.py`의 `run_briefing()` 동작이 바뀌지 않았다.
- 새 설정 키·새 ORM 컬럼·새 마이그레이션·새 서드파티 의존성이 없다.
- 모든 산출물이 git에 추적되어 리뷰 diff에 나타난다(staging은 오케스트레이터가 수행).
- Phase 4b(오케스트레이션 배선)를 앞당겨 구현하지 않았다.

---

## 7. 실행 환경 전제 (직전 run들에서 실측된 사실 — PLAN에 반드시 반영)

이 절은 추측이 아니라 `scalping-briefing-p2b`/`p3`/`p3b` run에서 측정된 제약이다.

### 7.1 워크스페이스

- **의존성은 워크스페이스 `.venv`에 이미 설치되어 있다**(`pytest`, `sqlalchemy`, `alembic`,
  `fastapi`, `uvicorn`, `httpx`, `jsonschema`, `pydantic`, `pyyaml`). `Makefile`이
  `.venv/bin/python`을 자동 감지한다. **worker 샌드박스에 네트워크가 없다 — `pip install`
  금지.** 새 서드파티 의존성은 사용자 승인 사항이다.
- **worker 샌드박스에서 `.git`은 read-only다.** `git add`/`commit`/`stash` 금지. worker가 만든
  신규 파일은 untracked로 남는 것이 정상이며 coordinator는 이를 산출물 누락으로 판정하면 안 된다.
  staging은 attempt 종료 후 오케스트레이터가 수행한다.
- **`TMPDIR`을 디스크 기반 경로로 지정한다**(`/home/inno/.cache/loopeng-tmp`). 기본 `/tmp`는
  tmpfs 8.5G인데 codex가 세션 tape SQLite를 여기에 쌓아 고갈시킨다.
- `make test`는 29초, 278 passed다. 전체 스위트는 work order당 마지막 1회만 돌린다.

### 7.2 900초 kill이 `p3` run을 끝냈다 — PLAN의 최우선 제약

`scalping-briefing-p3`에서 worker가 `worker_timeout_seconds=900`에 걸려 강제 종료된 것이 **3회**
(T1, T6, T9)다. 매번 stderr 배너가 사라져 `provenance_unavailable`로 dispatch 루프가 멈췄고
예산이 소진됐다. **세 번 다 코드는 완성돼 있었고 worker가 result JSON만 못 남긴 경우였다.**

효과가 확인된 유일한 지시는 PLAN을 절차로 쓰는 것이다. PLAN은 각 work order의 `objective` 끝에
아래를 그대로 싣는다.

> HARD TIME BOX — follow this as a procedure, not as a target. Write the source file first
> and the test file second. Run your narrow target test at most TWICE; after the second run
> stop editing whether or not it passes. At 600 seconds elapsed stop all work unconditionally
> and spend the remaining time only on writing your worker_result JSON, which must be on disk
> before 900 seconds. Emitting a worker_result whose tests still fail is a SUCCESS and costs
> nothing. Being killed at the 900s timeout is a run-stopping FAILURE: it produces
> provenance_unavailable and blocks every remaining package.

**work order 크기는 "신규 파일 1개 + 그 테스트 1개, 250초 분량"으로 잡는다.**

### 7.3 dispatch는 attempt당 1회다 — PLAN 크기의 결정적 제약

`loop-engine dispatch`를 같은 `run_attempt`에서 다시 부르면 새 work order가 실행되지 않는다
(기존 worker result가 재사용된다). 새 package를 태우려면
`complete-run → project-review → retry-run|replan`으로 **새 attempt**를 만들어야 한다.

- `max_work_orders=2`, `max_concurrent_workers=1`(순차) → **attempt 1회 = package 2개**.
- attempt 생성권은 `max_execution_retries=2` + `max_replans=3` ≈ **총 6회**.
- 이번 run의 package는 **7개**다(§7.4). attempt 4회면 끝나고 2회 여유가 남는다. `p3`가 예산을
  전부 소진한 이유는 package 10개를 한 run에 넣었기 때문이다.

### 7.4 권장 package 분할

| package | 내용 | 배치 |
| --- | --- | --- |
| A | `src/scalping_briefing/ops/metrics.py`에 M1·M2·M5 + `tests/test_phase4_metrics_collection.py` | attempt 1 |
| B | 같은 모듈에 M3·M4·M6 + `tests/test_phase4_metrics_review_delivery.py` | attempt 1 |
| C | `src/scalping_briefing/ops/report.py` + `tests/test_phase4_report.py` | attempt 2 |
| D | `src/scalping_briefing/ops/alerting.py` + `tests/test_phase4_alerting.py` | attempt 2 |
| E | `src/scalping_briefing/ops/expansion.py`(4주 판정 + 확장·재조정 권고) + `tests/test_phase4_expansion.py` | attempt 3 |
| F | `docs/operations-metrics.md` 신규, `docs/operations.md` Phase 4 절 갱신 | attempt 3 |
| G | `tests/test_phase4_dod.py` 명명 테스트 6개 + `docs/protected-requirements-tests.md` node ID 추가 | attempt 4 |

B는 A가 만든 모듈에 이어 쓰므로 A 다음이다. G는 A~F의 산출물과 node ID를 참조하므로 마지막이다.

### 7.5 coordinator가 반복해서 틀린 것 — PLAN이 미리 막아야 한다

1. **완료 package를 `dependencies`에 넣어 dispatch가 `unknown_dependency`로 거부**됐다(3회).
   PLAN은 "work order의 `dependencies`에는 같은 manifest 안의 task_id만 넣는다. 이미 완료된
   package는 `dependencies`가 아니라 `input_artifact_hashes`의 `source_file`로만 참조한다.
   순차 실행이므로 `dependencies: []`가 기본이다"를 명시해야 한다.
2. **`state.execution_remediation_packet`은 replan으로 지워지지 않는다.** dispatch가 그 내용을
   coordinator 프롬프트에 계속 주입하므로 오래된 지시가 새 PLAN을 누른다. **새 PLAN 최상단에
   "이전 remediation packet의 package 지정은 무효다. 유효한 manifest는 이 PLAN의 work order
   목록뿐이다"를 선언**하고 완료 task_id를 열거한다.
3. **`final_diff`는 `git diff HEAD`로 만든다.** bare `git diff`는 staged 변경도 untracked 파일도
   보지 못한다(`p3`에서 4회 연속 오류). PLAN은 `git diff HEAD` 강제와 함께 **"빈 diff나
   untracked 파일을 사유로 status fail을 내지 않는다"**를 명시한다. untracked 신규 파일은
   `git status --porcelain` 열거와 파일별 sha256으로 함께 보고한다.
4. package DoD에 "전체 스위트 0 failed"를 걸면 선행 package가 남긴 불일치가 후속 package를 전부
   실패로 만든다. **package DoD는 "대상 테스트 통과 + 이 package가 새로 깨뜨린 기존 테스트
   없음"으로 쓰고, 전체 스위트 0 failed는 run 종료 조건으로 분리**한다.
5. **PLAN이 기존 파일 수정을 금지하면서 그 파일의 테스트가 새 계약과 충돌하면 work order는
   구조적으로 완료 불가능하다.** 계약을 바꾸는 작업은 그 계약을 고정한 기존 테스트 파일을 반드시
   `allowed_paths`에 포함한다. 이번 run은 신규 파일 위주이므로 이 위험이 낮다.
6. **evidence 계약**: worker result의 `run_id`/`task_id`/`work_order_hash`/`run_attempt`는 work
   order·manifest 값을 **그대로 복사**한다(빈 문자열·`pending` 금지, 재계산 금지). `output_hash`는
   `sha256:` + 64자 소문자 hex. `test_evidence[].output_hash`는 실행한 명령의 stdout+stderr
   텍스트에 대한 sha256이며 소스·테스트 파일 해시가 아니다.

### 7.6 review 호출

- `loop-engine`에는 review를 실행하는 CLI 명령이 없다. `loop_engine.adapters.codex_cli`의
  `execute_review`/`review_prompt`를 쓰는 얇은 드라이버로 호출하며, provenance와
  execution_evidence는 엔진이 기록한다.
- **review 프롬프트가 argv 상한(~128KB)을 넘으면 `OSError: Argument list too long`으로
  실패한다**(실제 발생 3회). PLAN·diff·test 로그는 워크스페이스 상대 경로로 넘기고
  `state.execution_evidence`는 provenance 필드만 추려서 싣는다.

---

## 8. Loop Engine 작업 지시

1. 이 문서를 이번 run의 최상위 범위로 사용한다. project 이름은 `scalping-briefing-p4`다.
2. **범위를 넓히지 않는다.** end-to-end 오케스트레이션 배선이 필요해 보이면 그것은 Phase 4b이며
   이번 run의 범위 밖이다. 지표는 테스트가 구성한 레코드로 증명한다.
3. 운영값은 원 intent §14 부록 A에서 확정되어 있다. 표에 없는 새 설정 키가 필요하면 임의로
   정하지 말고 사용자 확인을 받는다. 지표 목표값은 설정 키가 아니라 모듈 상수다(§2.3).
4. LLM 출력과 외부 수집 콘텐츠는 신뢰 경계 밖 데이터다. 지표·리포트에 원문 전문이나 미검증
   콘텐츠를 넣지 않는다.
5. 외부 공유, 실제 API 키·봇 토큰 사용, 비용 발생 서비스 활성화는 승인 없이 실행하지 않는다.
6. PLAN은 §7.3의 attempt 예산 안에 들어와야 한다. package는 7개(§7.4), work order 크기는 §7.2
   절차 규격, dispatch당 2개다. PLAN에 미착수 package의 분류 기준(아직 dispatch되지 않은 package는
   PLAN 결함이 아니라 `execution_nonconformance`)을 명시해 replan 예산을 낭비하지 않는다.
7. 반복 실행마다 범위, 미결정 운영값, 테스트 결과, 위험을 기록한다.
8. **리뷰 증거 규칙**: review packet은 워크스페이스 diff를 증거로 사용한다. attempt 종료 후
   오케스트레이터가 산출물을 stage하고 `make test` 로그를 `--test-output-file`로 전달한다.
   `.env`·실 토큰·`storage/`·`data/`·`alerts/`·`__pycache__`는 stage하지 않는다.

### 8.1 승인이 필요한 지점 (사전 예고)

아래는 사용자 승인 전까지 BLOCKED로 두고 진행하지 않는다.

- 원 intent §14 부록 A 확정값의 **실제 변경**(임계값 재조정 권고는 문서화까지만)
- 부록 A에 없는 설정 키 신설(= `CONFIG_KEYS` 수정)
- `publication_policy`를 `auto_publish`로 변경
- 실 출처의 `active: true` 전환
- `DELIVERY_MODE=live` 전환, 실 봇 토큰·chat_id 사용, 실제 외부 전송
- `LLM_MODE=live` 전환 및 예산 한도 설정
- 알림의 외부 채널 연동(현재는 `alerts/` 아티팩트 + 구조화 로그만)
- 검토 API의 로컬 바인딩 해제·외부 노출, 지표 라우트 추가
- `P1`~`P16` 중 어느 것이든 완화하는 변경
- `.venv`에 없는 새 서드파티 의존성 도입
- 새 ORM 컬럼·새 alembic 마이그레이션
- Phase 1~3 기존 기능 코드 수정(테스트가 실제 결함을 찾은 경우 포함 — 발견을 보고하고 멈춘다)

### 최종 성공 상태

운영자가 하나의 주기 Markdown 리포트에서 지표 6종의 값·목표·판정·표본 수를 관측 창과 함께 읽을
수 있고, 목표 위반과 표본 부족이 전달 채널과 분리된 `alerts/` 아티팩트로 도달하며, 확장 결정이
4주 연속 관찰 규칙과 Source Policy에 근거해 `docs/operations-metrics.md`에 기록된다. 표본이 없는
지표는 충족이 아니라 `insufficient_data`로 남는다. `make test`는 284 이상 passed / 0 failed이며
네트워크·Docker·API 키 없이 재현된다. 이로써 Phase 4의 측정·보고·판단이 닫히고, 남은
end-to-end 오케스트레이션 배선을 Phase 4b 별도 run으로 시작할 수 있다.
