# 남은 작업 · 결정 대기 항목

마지막 갱신: 2026-08-06 · `make test` 346 passed / 0 failed (오프라인)

이 문서는 "지금 코드에 없는 것"과 "사람이 결정해야 하는 것"만 담는다. 완료된 범위는
`docs/operations.md`와 `docs/orchestration-cycle.md`가 기술한다.

---

## 1. 승인이 필요한 결정 (원 intent §14 부록 A · Phase 4b intent §8.1)

아래는 전부 **사용자 승인 전까지 진행 금지**다. 승인 시 함께 확정해야 할 값을 같이 적었다.

| 항목 | 함께 확정할 값 | 현재 상태 |
| --- | --- | --- |
| 실 출처 `active: true` 전환 | 어떤 출처부터, robots·라이선스 재확인 | 전 출처 fixture |
| `LLM_MODE=live` | `LLM_MONTHLY_BUDGET_USD`, `LLM_RUN_MAX_TOKENS`, provider·모델 | `fixture` 고정 |
| `DELIVERY_MODE=live` | 실 봇 토큰·chat_id 보관 방식(환경변수만), 전송 대상 채널 | `dry_run` 고정 |
| 알림의 외부 채널 연동 | 전달 채널과 분리 유지 여부 | `alerts/` 아티팩트 + 구조화 로그만 |
| 검토 API 외부 노출 | 인증·바인딩 정책 | 로컬 바인딩(`P16`) |
| 상시 실행 프로세스(cron·데몬) | 실행 주체, 실패 시 알림 경로 | 없음. 사이클은 1회 호출 = 1회 실행 |

한도 미설정 상태에서 `live` 실행은 금지다(원 intent §9.2).

---

## 2. 코드에 남아 있는 갭

### 2.1 `alerts/` 보존 정책 없음

`raw_retention_days`·`normalized_retention_days`·`llm_run_retention_days`는 있지만 알림
아티팩트에는 없다. 실패 알림은 무기한 누적된다.

- 영향: 장기 운영 시 디렉터리 증가. 재실행 스팸은 제거됐으므로(`d084822`) 증가 속도는
  실제 실패 건수 수준이다.
- 결정 필요: 보존 키를 신설할지(= `CONFIG_KEYS` 변경, 승인 사항) 운영 스크립트로 정리할지.

### 2.2 실제 승인 흐름이 운영에서 검증되지 않음

승인된 후보 → 게이트 통과 → dry-run 전달 경로는 **테스트에서만** 증명됐다. 기본 fixture
실행은 `pending_approval` 브리핑이 게이트에서 멈추는 것이 정상이다
(`docs/orchestration-cycle.md`의 "게이트 정지" 절).

- 남은 일: 검토 API로 사람이 실제 승인한 뒤 `make run-briefing-cycle`이 전달까지 가는지
  한 번 관측하고 그 결과를 문서화.

### 2.3 4주 연속 판정·확장 권고에 쓸 실데이터 없음

`ops/expansion.py`의 4주 연속 충족 판정은 구현돼 있으나, 관측 창이 쌓여야 의미가 생긴다.
현재는 대부분의 지표가 `insufficient_data`다(표본 없음 = 충족 아님, `P4`).

---

## 3. 문서·저장소 정리

### 3.1 문서가 git에 없는 파일을 인용한다

`.gitignore:22`가 `.loop-engine/`을 제외하므로 추적 파일이 0개인데,
`docs/orchestration-cycle.md`가 근거로
`.loop-engine/runs/02ecc401-.../artifacts/run-briefing-cycle.clean-state.txt`,
`...run-briefing-cycle.repeat-run.txt`를 가리킨다. 다른 사람의 클론에는 존재하지 않는다.

- 선택지: 캡처를 `docs/` 아래로 옮겨 추적하거나, 인용을 제거하고 표의 수치만 남긴다.

### 3.2 테스트 스위트 시간 증가

`tests/test_phase4b_offline_fixture_cycle.py`가 실제 수집 사이클을 2회 돌려 전체 스위트가
약 38초 → 64초가 됐다. 유지할지, 마커로 분리할지 판단 필요.

### 3.3 fixture 코퍼스의 의미 변화

`d084822`에서 `tests/fixtures/sources/**`의 문서 본문에 6개 핵심 필드 문장을 추가했다.
Phase 1~3 테스트는 모두 통과하지만, fixture가 대표하는 문서상이 "한 줄 스텁"에서
"전략 후보를 구성할 수 있는 문서"로 바뀌었다.

---

## 4. 리뷰가 남은 변경

`d084822`는 loop engine의 dual-axis review를 거치지 않은 커밋이다. 특히 다음이 포함된다.

- `src/scalping_briefing/llm/fixture.py` — Phase 0/1 코드. content-addressed 폴백 키와
  `{{document_version_id}}` 치환 추가. 기존 protected 테스트 2개는 그대로 통과
- `tests/fixtures/sources/**` 문서 6개 본문 보강
- `src/scalping_briefing/orchestration/cycle.py` — `skipped` 집계와 분류 가능 상태 필터

이어지는 커밋에서 `src/scalping_briefing/publishing/gate.py`의 Evidence 요구 경계도
바뀌었다(주장 없는 필드는 Evidence 불필요). protected 요구사항 `P3`에 직접 닿는 변경이므로
같이 검토 대상이다.

권장: `/code-review` 또는 별도 loop run으로 확인.

---

## 5. 완료된 것 (재작업 금지 · 참고용)

- Phase 0~4: 수집·분류·추출·검증·근거·점수·신규성·라우팅·브리핑·게이트·dry-run 전달,
  운영 지표 6종·주기 리포트·지표 위반 알림·4주 판정·확장 권고
- Phase 4b: `run_briefing_cycle()` 진입점과 `make run-briefing-cycle`, 14단계 배선,
  단계별 실패 격리, `(scheduled_for, trigger_type)` 멱등 경계, 운영 지표 연동
- `d084822`: 이미 처리된 문서 버전 skip 처리, content-addressed fixture 재생
- Phase 3 게이트 경계 교정: 주장이 없는(`unknown`) 필드는 Evidence를 요구하지 않는다. 부분 근거 후보 하나가 브리핑 생성 전체를 실패시키던 문제 해소 (`docs/orchestration-cycle.md`의 "Partially supported candidates stay publishable")
- protected 요구사항 `P1`~`P16` 전부 유지, 매핑은 `docs/protected-requirements-tests.md`
