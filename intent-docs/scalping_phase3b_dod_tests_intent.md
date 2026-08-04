# Phase 3 DoD 명명 테스트 마감 · 프로젝트 Intent

> 이 문서는 Loop Engine과 개발 에이전트가 **이번 run**의 목적, 범위, 품질 기준, 완료
> 조건을 동일하게 이해하도록 만드는 실행 기준 문서다.
>
> 상위 기준 문서: `intent-docs/scalping_strategy_briefing_intent.md` (이하 "원 intent")
> 직전 run 기준 문서: `intent-docs/scalping_phase3_briefing_delivery_intent.md`
> (이하 "Phase 3 intent")
>
> **이번 run의 범위는 직전 run이 backlog로 이월한 T10 하나다.** 새 기능을 만들지 않는다.
>
> Loop Engine project 이름: `scalping-briefing-p3b`
> (엔진의 run 식별용 이름이다. 제품 설정 키 `PROJECT_SLUG = scalping-briefing`은 바뀌지
> 않는다. 직전 run project `scalping-briefing-p3`는 `DEFERRED_BACKLOG`로 종료됐고 같은
> 이름으로는 새 run을 만들 수 없다.)

---

## 1. 한 줄 정의

Phase 3의 동작은 이미 전부 구현되어 있다. **그 동작이 원 intent §11 Phase 3 완료 기준을
충족한다는 것을 명명 테스트 6개와 protected 매핑으로 증명**해 Phase 3을 닫는다.

기능 코드를 새로 쓰는 run이 아니다. 증명이 빠진 자리를 메우는 run이다.

---

## 2. 전제 — 이미 완료된 것 (재작업 절대 금지)

직전 run `scalping-briefing-p3`(run_id `4d6c4d37-93c4-474b-8ad6-13e54451a413`)에서 T1~T9가
완료됐고 `make test`는 **272 passed / 0 failed**(네트워크·Docker·API 키 없이)다.
17개 요구사항 중 14개(R1~R11, R14, R16, R17)가 실현됐다.

| 완료 package | 산출물 |
| --- | --- |
| T1 검토 API 데이터 엔드포인트 | `src/scalping_briefing/review/api.py`, `tests/test_phase3_review_api_data.py` |
| T2 전 필드 conflicting 종결 경로 | `pipeline/validate.py`·`routing.py` 수정, `tests/test_phase3_validate_conflicting.py` |
| T3 주 2회 스케줄 계산 | `pipeline/schedule.py`, `tests/test_phase3_schedule.py` |
| T4 실행 커서 | `pipeline/briefing_cursor.py`, `tests/test_phase3_briefing_cursor.py` |
| T5 브리핑 렌더러 | `publishing/briefing_render.py`, `tests/test_phase3_briefing_render.py` |
| T6 브리핑 조립·아카이브 | `publishing/briefing_build.py`, `tests/test_phase3_briefing_build.py` |
| T7 브리핑 단위 발행 게이트 | `publishing/briefing_gate.py`, `tests/test_phase3_briefing_gate.py` |
| T8 dry-run 전달 커넥터 | `delivery/connector.py`, `tests/test_phase3_delivery_connector.py` |
| T9 전달 서비스·idempotency | `delivery/service.py`, `tests/test_phase3_delivery_service.py` |

**`make test` 272 passed / 0 failed가 이번 run의 하한선이다. 어떤 작업도 이 수를 줄이거나
failed를 만들면 안 된다.**

### 2.1 이 run이 호출할 실제 API (실측 확인됨 — 추측 금지)

```python
# src/scalping_briefing/publishing/briefing_build.py
build_briefing(session, *, scheduled_for: datetime, trigger_type: str,
               settings, run_attempt: int = 1) -> Briefing
#   schedule_trigger가 idempotency 경계다. 같은 (scheduled_for, trigger_type)으로 다시
#   부르면 새 Briefing을 만들지 않고 그 행의 run_attempt를 올린다.
#   반환된 Briefing에 items(BriefingItem, carried_over 포함), markdown_location,
#   source_summary, run_status, publication_status, window_start/window_end,
#   window_truncated, candidate_count, approved_count, items_truncated가 채워져 있다.

# src/scalping_briefing/delivery/service.py
deliver_briefing(session, briefing, *, connector, settings,
                 resend_reason: str | None = None,
                 resend_approved_by: str | None = None) -> Delivery | None
#   순서: briefing_gate.gate_briefing -> connector.render -> guard.make_idempotency_key
#         -> guard.can_resend/resend_is_approved -> connector.send(dry_run=True)
#         -> Delivery 레코드 기록
#   중복은 guard가 거부하고, 재전송은 resend_reason과 resend_approved_by가 둘 다
#   채워졌을 때만 새 attempt_no로 허용된다.

# src/scalping_briefing/pipeline/schedule.py
schedule_trigger(scheduled_for: datetime, *, trigger_type: str) -> dict
#   -> {"briefing_id", "scheduled_for", "trigger_type", "counts_toward_weekly_schedule"}
next_occurrence(after, *, schedule, timezone) -> datetime
occurrences_between(start, end, *, schedule, timezone) -> list[datetime]

# src/scalping_briefing/pipeline/briefing_cursor.py
advance_cursor(previous_runs, *, scheduled_for, run_status,
               initial_lookback_days=14, max_lookback_days=30) -> CursorAdvance
#   run_status == "success"일 때만 커서가 전진한다.
#   .window_start .window_end .window_truncated .truncated_from .truncation .cursor .advanced
```

### 2.2 재사용할 기존 테스트 자산 (새로 만들지 말 것)

- `tests/test_phase3_briefing_build.py`: `CORE_FIELDS`, `SETTINGS`,
  `_candidate(...)`, `_database(tmp_path) -> (engine, Session, DocumentVersion)`
- `tests/test_phase3_delivery_service.py`: `SETTINGS`, `ATTEMPTED_AT`, `SpyConnector`,
  `_session()`, `_briefing(review_status=...)`, `_add(session, briefing)`, `_close(session)`

**새 픽스처·새 헬퍼·새 conftest를 만들지 않는다.** 위 자산을 import하거나 같은 패턴으로
최소한만 확장한다. 직전 run에서 T10이 실패한 주 원인은 시간이었고, 픽스처를 새로 만드는 것이
가장 큰 시간 낭비다.

---

## 3. 승계되는 원칙과 안전 경계

원 intent §3.1·§3.2·§3.3, Phase 3 intent §3이 그대로 적용된다. `P1`~`P16`은 이번 run에서도
**protected**이며 퇴행시키면 안 된다. 특히:

- `P3` 공개 브리핑의 모든 핵심 주장은 `document_version_id` 단위 Evidence로 역추적
- `P6` 투자 권유·매매 신호·수익 보장 표현 금지
- `P8` 동일 `briefing_id`+채널 중복 전달 금지(idempotent delivery)
- `P9` 기본은 `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`
- `P10` 비밀값은 환경변수로만
- `P16` 검토 API는 로컬 바인딩 + 단일 정적 토큰

---

## 4. 이번 run의 기능 범위

### 4.1 명명 테스트 6개 — `tests/test_phase3_dod.py`

파일명과 테스트 이름이 **정확히** 아래와 같아야 한다. 원 intent §11 Phase 3 완료 기준
("테스트 환경에서 2회의 서로 다른 주간 실행이 중복 전달 없이 재현되고, 각 보고서 항목이
원문·근거·검토 상태·데이터 기준 구간으로 역추적된다")을 이 6개로 증명한다.

| 테스트 이름 | 증명 내용 | 주로 쓰는 API |
| --- | --- | --- |
| `test_phase3_dod1_two_scheduled_runs_produce_distinct_briefings` | 서로 다른 두 스케줄 발생이 서로 다른 `briefing_id`와 인접한 `window_start`/`window_end`를 갖는다 | `build_briefing` ×2 |
| `test_phase3_dod2_briefing_item_traces_to_source_evidence_and_review` | 브리핑 항목이 원문 URL·`document_version_id`·Evidence 인용·검토 상태·데이터 구간으로 역추적된다 | `build_briefing` 반환 `Briefing.items[*]` |
| `test_phase3_dod3_duplicate_delivery_is_rejected_by_idempotency_key` | 같은 `briefing_id`+채널+내용 해시의 재전송이 거부되고, 승인 2요소가 모두 있을 때만 새 `attempt_no`가 허용된다 | `deliver_briefing` |
| `test_phase3_dod4_empty_or_failed_window_still_produces_reported_briefing` | 적격 항목이 없거나 일부 출처가 실패해도 실행이 실패하지 않고 그 사실이 브리핑에 명시된다 | `build_briefing` |
| `test_phase3_dod5_unapproved_candidate_is_carried_over_not_delivered` | 미승인 후보는 전달되지 않고 큐에 남아 다음 브리핑에 `carried_over`로 포함된다 | `build_briefing` ×2 + `deliver_briefing` |
| `test_phase3_dod6_retry_reuses_same_briefing_id_and_advances_cursor_on_success` | 실패 후 재시도가 같은 `briefing_id`의 새 `run_attempt`이며, 커서는 전달 성공이 아니라 실행 성공으로 전진한다 | `build_briefing` + `advance_cursor` |

**DoD5는 통합 증명이어야 한다.** 직전 run 리뷰가 지적한 간극이 정확히 이것이다 —
"carried_over 포함"과 "미승인 후보 미전달"이 서로 다른 테스트에 흩어져 있으면 안 되고,
**같은 미승인 후보가 1회차에 전달되지 않고 2회차 브리핑에 `carried_over`로 나타나는
것을 하나의 테스트가 관통해서** 보여야 한다.

각 테스트는 실제 동작을 호출해 assert한다. 구현을 모킹해 통과시키지 않는다. `SpyConnector`
같은 기존 테스트 더블은 네트워크를 쓰지 않기 위한 것이므로 그대로 쓴다.

### 4.2 protected 매핑 갱신 — `docs/protected-requirements-tests.md`

- Phase 3 신규 코드가 `P3`/`P6`/`P8`/`P9`에 닿는 지점마다 해당 행에 **새 node ID를 추가**한다.
- `MAPPING` 범위는 `P1`~`P16` 그대로다. 새 P 항목을 만들지 않는다.
- **기존 node ID를 지우지 않는다.** 추가만 한다.
- `tests/test_protected_mapping.py`가 `pytest --collect-only` 결과와 대조하므로 추가한
  node ID는 실제로 수집 가능해야 한다.

---

## 5. 이번 run에서 하지 않는 것

- **기능 코드 수정.** `src/` 아래 파일을 고치지 않는다. 테스트가 실패하면 그것은 발견이며,
  구현을 고치는 대신 worker result의 `blockers`에 사유를 남긴다.
- 새 픽스처·새 conftest·새 테스트 헬퍼 모듈 신설
- T1~T9의 기존 테스트 재작성 (node ID가 바뀌면 매핑이 깨진다)
- `DELIVERY_MODE=live`, 실 봇 토큰·chat_id, 실제 외부 전송
- `LLM_MODE=live` 전환, 실 출처 `active: true` 전환
- Phase 4: 운영 지표 대시보드, 주기 리포트, 임계값 재조정, 확장 판단
- 새 ORM 컬럼·새 alembic 마이그레이션·새 설정 키·새 서드파티 의존성

---

## 6. 완료 기준 (DoD)

- `tests/test_phase3_dod.py`가 존재하고 §4.1의 6개 이름이 정확히 일치하며
  `pytest tests/test_phase3_dod.py -v`가 6 passed다.
- DoD5가 §4.1에 쓴 대로 통합 증명이다.
- `docs/protected-requirements-tests.md`의 `P3`/`P6`/`P8`/`P9` 행에 Phase 3 node ID가
  추가되고 `tests/test_protected_mapping.py`가 통과한다.
- `tests/test_protected_p11_p16.py`가 계속 통과한다.
- `make test`가 네트워크·Docker·API 키 없이 **0 failed**로 통과하고 **passed ≥ 278**
  (272 + 신규 6개).
- `src/` 아래 파일이 수정되지 않았다(`git diff HEAD -- src/`가 비어 있다).
- 모든 산출물이 git에 추적되어 리뷰 diff에 나타난다(staging은 오케스트레이터가 수행).

---

## 7. 실행 환경 전제 (직전 run에서 실측된 사실 — PLAN에 반드시 반영)

### 7.1 워크스페이스

- 의존성은 워크스페이스 `.venv`에 이미 설치되어 있다(`pytest`, `sqlalchemy`, `alembic`,
  `fastapi`, `uvicorn`, `httpx`, `jsonschema`, `pydantic`, `pyyaml`). `Makefile`이
  `.venv/bin/python`을 자동 감지한다. **worker 샌드박스에 네트워크가 없다 — `pip install`
  금지.**
- **worker 샌드박스에서 `.git`은 read-only다.** `git add`/`commit`/`stash` 금지. worker가
  만든 신규 파일은 untracked로 남는 것이 정상이며 coordinator는 이를 산출물 누락으로
  판정하면 안 된다. staging은 attempt 종료 후 오케스트레이터가 수행한다.
- **`TMPDIR`을 디스크 기반 경로로 지정한다**(`/home/inno/.cache/loopeng-tmp`). 기본 `/tmp`는
  tmpfs 8.5G인데 codex가 세션 tape SQLite를 여기에 쌓아 고갈시킨다.
- `make test`는 29초, 272 passed다. 전체 스위트는 work order당 마지막 1회만 돌린다.

### 7.2 900초 kill이 직전 run을 끝냈다 — 이번 PLAN의 최우선 제약

직전 run에서 worker가 `worker_timeout_seconds=900`에 걸려 강제 종료된 것이 **3회**다
(T1, T6, T9). 매번 stderr 배너가 사라져 `provenance_unavailable`로 dispatch 루프 전체가
멈췄고, 세 번째 발생 시점에 예산이 소진돼 T10이 아예 dispatch되지 못했다.
**세 번 다 코드는 완성돼 있었고 worker가 result JSON만 못 남긴 경우였다.**

효과가 확인된 유일한 지시는 PLAN을 절차로 쓰는 것이다. T7(878.9초)·T9 이전의 T8(582.1초)이
이 문구에서 kill 없이 통과했다. PLAN은 각 work order의 `objective` 끝에 아래를 그대로
싣는다.

> HARD TIME BOX — follow this as a procedure, not as a target. Write the source file first
> and the test file second. Run your narrow target test at most TWICE; after the second run
> stop editing whether or not it passes. At 600 seconds elapsed stop all work unconditionally
> and spend the remaining time only on writing your worker_result JSON, which must be on disk
> before 900 seconds. Emitting a worker_result whose tests still fail is a SUCCESS and costs
> nothing. Being killed at the 900s timeout is a run-stopping FAILURE: it produces
> provenance_unavailable and blocks every remaining package.

이번 run은 작업량이 작으므로 **명명 테스트 6개를 한 work order에 몰지 않는다.** 아래 §7.4의
package 분할을 따른다.

### 7.3 dispatch는 attempt당 1회다

`loop-engine dispatch`를 같은 `run_attempt`에서 다시 부르면 새 work order가 실행되지 않는다.
새 package를 태우려면 `complete-run → project-review → retry-run|replan`으로 새 attempt를
만들어야 한다.

- `max_work_orders=2`, `max_concurrent_workers=1`(순차) → **attempt 1회 = package 2개**.
- attempt 생성권은 `max_execution_retries=2` + `max_replans=3` ≈ **총 6회**.
- 이번 run의 package는 **3개면 충분하다**(§7.4). attempt 2회면 끝나고 4회의 여유가 남는다.
  직전 run이 예산을 전부 소진한 이유는 package 10개를 한 run에 넣었기 때문이다.

### 7.4 권장 package 분할

| package | 내용 |
| --- | --- |
| A | `tests/test_phase3_dod.py`에 DoD1·DoD2·DoD4·DoD6 (`build_briefing`과 `advance_cursor`만 쓰는 4개) |
| B | 같은 파일에 DoD3·DoD5 (`deliver_briefing`이 필요한 2개. DoD5는 통합 증명) |
| C | `docs/protected-requirements-tests.md`의 P3/P6/P8/P9 node ID 추가 + 매핑 테스트 통과 |

B는 A가 만든 파일에 이어 쓰므로 A 다음이다. C는 A·B가 만든 node ID를 참조하므로 마지막이다.
attempt 1에 A·B, attempt 2에 C를 배치한다.

### 7.5 coordinator가 반복해서 틀린 것 — PLAN이 미리 막아야 한다

1. **완료 package를 `dependencies`에 넣어 dispatch가 `unknown_dependency`로 거부**됐다.
   PLAN은 "work order의 `dependencies`에는 같은 manifest 안의 task_id만 넣는다. 이미 완료된
   package는 `input_artifact_hashes`의 `source_file`로만 참조한다"를 명시해야 한다.
2. **`state.execution_remediation_packet`은 replan으로 지워지지 않는다.** dispatch가 그
   내용을 coordinator 프롬프트에 계속 주입한다. **새 PLAN 최상단에 "이전 remediation
   packet의 package 지정은 무효다"를 선언**한다.
3. **`final_diff`는 `git diff HEAD`로 만든다.** 직전 run에서 coordinator가 이 항목을 **4회
   연속** 틀렸다(bare `git diff`를 써서 빈 diff를 보고하고 그것만으로 `status: fail`).
   PLAN은 `git diff HEAD` 강제와 함께 **"빈 diff나 untracked 파일을 사유로 status fail을
   내지 않는다"**를 명시한다. 그래도 재발할 수 있으니 오케스트레이터가 stage 후 권위 diff를
   공급한다.
4. package DoD에 "전체 스위트 0 failed"를 걸면 선행 package가 남긴 불일치가 후속 package를
   전부 실패로 만든다. **package DoD는 "대상 테스트 통과 + 이 package가 새로 깨뜨린 기존
   테스트 없음"으로 쓰고, 전체 스위트 0 failed는 run 종료 조건으로 분리**한다.
5. **evidence 계약**: worker result의 `run_id`/`task_id`/`work_order_hash`/`run_attempt`는
   work order·manifest 값을 **그대로 복사**한다. `output_hash`는 `sha256:` + 64자 소문자
   hex이며, `test_evidence[].output_hash`는 실행한 명령의 stdout+stderr 텍스트에 대한
   sha256이다(소스·테스트 파일 해시가 아니다).

### 7.6 review 호출

- `loop-engine`에는 review를 실행하는 CLI 명령이 없다. `loop_engine.adapters.codex_cli`의
  `execute_review`/`review_prompt`를 쓰는 얇은 드라이버로 호출한다.
- **review 프롬프트가 argv 상한(~128KB)을 넘으면 `OSError: Argument list too long`으로
  실패한다.** 직전 run에서 2회 발생했다. PLAN·diff는 워크스페이스 상대 경로로 넘기고,
  `state.execution_evidence`는 provenance 필드만 추려서 싣는다.

---

## 8. Loop Engine 작업 지시

1. 이 문서를 이번 run의 최상위 범위로 사용한다. project 이름은 `scalping-briefing-p3b`다.
2. **범위를 넓히지 않는다.** 기능 코드를 고치고 싶어지면 그것은 이 run의 범위 밖이다.
3. 운영값은 원 intent §14 부록 A에서 확정되어 있다. 표에 없는 새 설정 키가 필요하면 임의로
   정하지 말고 사용자 확인을 받는다.
4. PLAN은 §7.3의 attempt 예산 안에 들어와야 한다. package는 3개(§7.4), work order 크기는
   §7.2 절차 규격, dispatch당 2개다.
5. 반복 실행마다 범위, 미결정 운영값, 테스트 결과, 위험을 기록한다.
6. **리뷰 증거 규칙**: review packet은 워크스페이스 diff를 증거로 사용한다. attempt 종료 후
   오케스트레이터가 산출물을 stage하고 `make test` 로그를 `--test-output-file`로 전달한다.
   `.env`·실 토큰·`storage/`·`data/`·`__pycache__`는 stage하지 않는다.

### 8.1 승인이 필요한 지점 (사전 예고)

아래는 사용자 승인 전까지 BLOCKED로 두고 진행하지 않는다.

- `src/` 아래 기능 코드 수정 (테스트가 실제 결함을 찾은 경우 포함 — 발견을 보고하고 멈춘다)
- `DELIVERY_MODE=live` 전환, 실 봇 토큰·chat_id 사용, 실제 외부 전송
- `LLM_MODE=live` 전환, 실 출처의 `active: true` 전환
- 원 intent §14 부록 A에 없는 설정 키 신설 또는 확정값 변경
- `P1`~`P16` 중 어느 것이든 완화하는 변경
- `.venv`에 없는 새 서드파티 의존성 도입
- 새 ORM 컬럼·새 alembic 마이그레이션

### 최종 성공 상태

`tests/test_phase3_dod.py`의 6개 명명 테스트가 이름 그대로 존재하고 통과하며, DoD5가 같은
미승인 후보의 미전달과 다음 브리핑 `carried_over` 포함을 하나의 테스트로 관통해 증명한다.
`docs/protected-requirements-tests.md`의 P3/P6/P8/P9에 Phase 3 경로를 덮는 node ID가
추가되고 매핑 테스트가 통과한다. `make test`는 278 이상 passed / 0 failed이며 `src/` 아래는
한 줄도 바뀌지 않았다. 이로써 Phase 3이 닫히고 Phase 4를 별도 run으로 시작할 수 있다.
