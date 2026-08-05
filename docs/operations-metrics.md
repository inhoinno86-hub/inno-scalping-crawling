# 운영 지표와 확장 판단

이 문서는 하나의 `ObservationWindow`에서 계산하는 Phase 4 운영 지표와 그 결과의 보관·알림·확장 판단 규칙을 설명한다. 지표는 저장된 구조화 레코드를 읽어 계산하며, 계산 중 레코드나 운영 설정을 변경하지 않는다. 기술 identifier와 path는 코드의 원문 표기를 유지한다.

## 1. 공통 관측·결과 규칙

관측 창은 반개구간 `[start, end)`이다. `ObservationWindow`는 `start`, `end`, `timezone`과 결정적인 `window_id`를 가진다. 별도 지정이 없으면 `timezone`은 `Asia/Seoul`이다. 창 안에 들어오는 시각은 시간대에 맞춰 비교하며, 창의 끝 시각은 포함하지 않는다.

각 결과는 다음 필드를 가진다.

| 필드 | 의미 |
| --- | --- |
| `metric_id` | `M1`부터 `M6`까지의 지표 식별자 |
| `value` | 계산된 값. 표본이 없으면 `None` |
| `target` | 지표 목표값 |
| `verdict` | `meets_target`, `breached`, `insufficient_data` 중 하나 |
| `numerator`, `denominator` | 계산에 사용한 분자와 분모 |
| `sample_size` | 해당 지표가 관측한 표본 수 |
| `detail` | 리포트에 직접 노출하지 않는 제한된 계산 보조 정보 |

`sample_size == 0`이면 값은 `None`, `verdict`는 반드시 `insufficient_data`, `meets_target`는 `false`다. 0건을 성공률 100%나 실패율 0%로 바꾸지 않는다. `insufficient_data`는 목표 미달(`breached`)과 구별되는 관측 부족 판정이며, 확장 적격성을 통과시키지 않는다.

표본이 하나 이상이면 목표 비교를 적용한다. 비교 방향은 M1은 이상(`≥`), M2~M6은 이하(`≤`)다. 비교를 통과하면 `meets_target`, 통과하지 못하면 `breached`다. 모든 지표 계산은 읽기와 산술만 수행하며, 세션·전역 엔진·상태 변경을 소유하지 않는다.

## 2. 지표 정의·계산·목표

### M1 — 활성 출처 수집 성공률

- 정의: 활성 `Source`에 대한 창 안의 종결 수집 작업 중 성공한 비율이다.
- 표본: `Source.active is True`인 출처만 포함한다. `CollectionJob`의 `status == "success"` 또는 `terminal_error is True`인 작업만 종결 작업으로 센다. 재시도 중간 실패는 제외한다. 시각은 `completed_at`을 우선하고, 없으면 `scheduled_for`를 사용한다.
- 계산: 분모 = 창 안 종결 작업 수, 분자 = 그중 `status == "success"`인 작업 수, `value = numerator / denominator`.
- 목표: `M1_TARGET_SUCCESS_RATE = 0.95`, 즉 `≥ 95%`.
- 판정: 95% 이상이면 `meets_target`, 미만이면 `breached`, 종결 작업이 없으면 `insufficient_data`다.

### M2 — 브리핑 실행 → 초안 생성 지연

- 정의: 성공한 브리핑이 예약 시각에서 초안 생성까지 걸린 시간(분)이다.
- 표본: `Briefing.run_status == "success"`이고 `scheduled_for`가 창 안인 브리핑을 사용한다. 같은 `briefing_id`에 성공 재시도가 여러 개면 가장 높은 `run_attempt` 하나만 사용한다. `generated_at`이 없는 레코드는 계산할 수 없다.
- 계산: 각 표본에 대해 `generated_at - scheduled_for`를 분으로 계산하고, `value`는 창 안 지연의 최댓값이다. 분자에는 최댓값, 분모에는 1을 기록하며, 전체 표본 수는 `sample_size`로 기록한다. 중앙값은 `detail`의 `median`/`median_minutes`로 보조 기록한다.
- 목표: `M2_TARGET_DELAY_MINUTES = 30`, 즉 `≤ 30분`.
- 판정: 최댓값이 30분 이하이면 `meets_target`, 초과하면 `breached`, 계산 가능한 성공 표본이 없으면 `insufficient_data`다.

### M3 — 검토 대기 후보 적체

- 정의: 관측 창 종료 시점에 검토가 필요한 `StrategyCandidate` 수다.
- 표본: 현재 행 상태를 `window.end`의 스냅샷으로 사용한다. `review_status == "needs_review"`만 대기 후보로 세며 `approved`, `rejected`, `archived`는 세지 않는다. 후보 행 전체 수가 `sample_size`다.
- 계산: 분자 = `needs_review` 후보 수, 분모 = 후보 행이 있으면 1, 없으면 0, `value = pending_reviews`다. 빈 후보 목록은 관측 표본 0으로 처리한다.
- 목표: `M3_TARGET_PENDING_REVIEWS = 20`, 즉 `≤ 20건`.
- 판정: 후보 표본이 있고 대기 수가 20건 이하이면 `meets_target`, 초과하면 `breached`, 후보 표본이 없으면 `insufficient_data`다.

### M4 — 전달 실패율(재시도 후)

- 정의: 브리핑과 채널 조합별 최종 전달 결과 중 성공하지 못한 조합의 비율이다.
- 표본: `Delivery.attempted_at`이 창 안인 전달만 사용한다. `(briefing_id, channel)`별로 가장 높은 `attempt_no`를 최종 시도로 선택하고, 같은 번호면 시각과 `delivery_id`로 결정적으로 선택한다.
- 계산: 분모 = 최종 결과가 있는 `(briefing_id, channel)` 쌍 수, 분자 = 최종 `status != "success"`인 쌍 수, `value = numerator / denominator`다. `DELIVERY_MODE == "dry_run"`의 성공도 성공으로 센다. 현재 `DELIVERY_MODE`는 리포트에 표시한다.
- 목표: `M4_TARGET_DELIVERY_FAILURE_RATE = 0.02`, 즉 `≤ 2%`.
- 판정: 실패율이 2% 이하이면 `meets_target`, 초과하면 `breached`, 최종 전달 쌍이 없으면 `insufficient_data`다.

### M5 — 문서 중복 생성률

- 정의: 같은 문서에서 이미 관측된 콘텐츠를 다시 저장한 `DocumentVersion`의 비율이다.
- 표본: `created_at`이 창 안인 `DocumentVersion`을 사용한다. 전체 `DocumentVersion`을 `document_id`와 생성 순서로 먼저 정렬해 같은 `document_id` 안의 이전 `content_hash`를 확인한다.
- 계산: 분모 = 창 안 `DocumentVersion` 수, 분자 = 같은 `document_id`에서 이미 보였던 `content_hash`를 다시 가진 창 안 Version 수, `value = numerator / denominator`다. 문서가 다르면 같은 hash여도 중복으로 세지 않는다.
- 목표: `M5_TARGET_DUPLICATE_RATE = 0.0`, 즉 `0%`.
- 판정: 중복률이 0% 이하이면 `meets_target`, 양수이면 `breached`, 창 안 Version이 없으면 `insufficient_data`다.

### M6 — 공개 항목 Evidence 누락률

- 정의: 공개 대상 브리핑의 핵심 주장 항목 중 연결된 `Evidence`가 없는 항목의 비율이다.
- 표본: `Briefing.publication_status`가 `approved` 또는 `published`이고 `scheduled_for`가 창 안인 브리핑의 `BriefingItem` 중 `core_claim is True`인 항목만 사용한다.
- 계산: 분모 = 위 조건의 핵심 주장 항목 수, 분자 = 연결된 `Evidence`가 0건인 항목 수, `value = numerator / denominator`다.
- 목표: `M6_TARGET_EVIDENCE_GAP_RATE = 0.0`, 즉 `0%`.
- 판정: 누락률이 0% 이하이면 `meets_target`, 양수이면 `breached`, 공개 대상 핵심 주장 표본이 없으면 `insufficient_data`다.

## 3. 주기 리포트와 읽는 법

`compute_all_metrics()`는 `M1`, `M2`, `M3`, `M4`, `M5`, `M6` 순서로 결과를 만든다. `render_operational_report()` 또는 `render_report()`가 한 관측 창의 한국어 Markdown을 만들고, `archive_operational_report()` 또는 `archive_report()`가 파일로 보관한다.

기본 저장 위치는 `storage/ops-reports/`다. `output_dir`를 명시하면 그 위치를 사용하지만 새로운 설정 키는 만들지 않는다. 파일명은 안전한 `report_id`를 사용한 `<report_id>.md`다.

리포트를 읽을 때는 다음 순서로 확인한다.

1. `storage/ops-reports/`에서 해당 `<report_id>.md`를 찾는다.
2. 머리말의 `generated_at`, `timezone`, `window_start`, `window_end`, `LLM_MODE`, `DELIVERY_MODE`로 대상과 실행 모드를 확인한다.
3. 지표 표에서 각 ID의 값, 목표, `verdict`, 분자, 분모, 표본 수를 함께 읽는다. M2는 최댓값이 판정값이며 중앙값과 전체 표본 수도 보조 정보다.
4. `목표 위반 목록`과 `insufficient_data 목록`을 확인한다. 후자는 위반 목록에 합치지 않는다.
5. `4주 연속 관찰 상태`와 `확장 권고`에서 확장 여부, 차단 사유, 후보별 `recommend`/`hold`를 확인한다.

리포트 본문에는 계산에 필요한 제한된 지표 필드만 렌더링한다. `detail`의 원천 내용이나 출처 본문은 렌더링하지 않으며, 본문은 `publishing/phrase_lint.py` 검사와 비밀값 마스킹 경계를 통과해야 한다. 이 파일은 운영 측정 리포트이며 브리핑 생성, 전달 호출, 외부 공유를 수행하지 않는다.

## 4. 알림 경로: 전달 채널과 분리

`emit_metric_alerts()`는 기존 `alerts.write_alert()`를 통해 로컬 JSON 아티팩트를 `alerts/`에 기록한다. 전달 커넥터나 `delivery/` 경로를 호출하지 않고 네트워크도 사용하지 않는다.

| 조건 | `event` | `severity` | 기록 내용 |
| --- | --- | --- | --- |
| `verdict == "breached"` | `metric_breach:<metric_id>` | `error` | `value`, `target`, `window`, `numerator`, `denominator` |
| `verdict == "insufficient_data"` | `metric_insufficient_data:<metric_id>` | `warning` | 같은 제한 필드. 관측 부족을 침묵시키지 않음 |
| `verdict == "meets_target"` | 기록하지 않음 | — | 정상 판정은 아티팩트를 만들지 않음 |

아티팩트 경로는 `alerts/<window_id>:<metric_id>.json`이며 `alert_id`도 `{window_id}:{metric_id}`다. 따라서 같은 창과 지표를 반복 처리해도 같은 파일을 갱신하고 알림이 무한 증식하지 않는다. 알림에는 계산 보조 `detail` 전체가 아니라 위 표의 제한된 필드만 넣는다.

구조화 로그는 별도의 파일 저장 규칙이 아니라 `src/scalping_briefing/logging_setup.py`의 `configure_logging()`이 설치한 JSON stream handler 경로를 따른다. 기본 출력은 `sys.stderr`이며, 각 레코드에는 UTC `timestamp`, `level`, `logger`, `message`와 안전한 추가 필드가 들어간다. 수집 실패 경로에서는 `RetryPolicy.record_failure()`가 `collection_failure` 구조화 로그를 먼저 남기고 같은 사건의 JSON 아티팩트를 `alerts/`에 기록한다. 두 경로 모두 민감값 마스킹을 거친다.

## 5. 4주 연속 관찰 판정

`evaluate_four_week_expansion()`은 최신 주간 관측 창을 시간 필드로 정렬하고, 시간 필드가 없으면 입력 순서를 유지한다. 가장 최근의 4개 창을 선택한다.

`expansion_eligible`은 다음 조건을 모두 만족할 때만 `true`다.

- 주간 창이 4개 모두 존재한다.
- 선택된 창들이 연속한 주간 창이다.
- 각 창에 `M1`~`M6`가 모두 존재한다.
- 4개 창의 6개 지표가 전부 `meets_target`이다.

창이 4개보다 적거나 지표가 빠졌거나 연속성이 확인되지 않으면 `insufficient_data`로 차단한다. 어떤 지표라도 `breached`면 해당 창·지표를 차단 근거로 남긴다. `insufficient_data` 차단이 하나라도 있으면 최종 사유는 `insufficient_data`를 우선한다. 결과에는 `window_ids`, `blocked_windows`, `blocked_metrics`와 각 `window_id`, `metric_id`, `reason`, `detail`이 포함되므로 결정을 다시 읽을 수 있다.

## 6. 확장 결정 기록

4주 게이트가 통과한 뒤에도 아래 후보는 자동 실행이 아니라 근거가 붙은 권고다.

| 후보 identifier | 검토 대상 | 현재 결정 원칙 |
| --- | --- | --- |
| `auto_publish` | `publication_policy: auto_publish` 검토 | 게이트와 근거가 없으면 `hold` |
| `real_source_activation` | Source Policy의 비활성 실 출처 후보 검토 | Source Policy, 이용 조건, 접근 규칙 검토가 없으면 `hold` |
| `search_ui` | 검색 UI 검토 | 게이트가 없으면 `hold` |

결과는 후보별 `recommendation`, `decision`, `action`, `reason`, `expansion_eligible`, 차단 창과 차단 지표를 기록한다. 현재 관측 표본과 4주 결과 목록이 없으므로 4주 게이트는 통과할 수 없다. 이번 run의 결정은 다음과 같이 기록한다.

> 측정 데이터 부족 — 확장하지 않음

근거는 (1) 주간 관측 결과 4개가 제공되지 않았고, (2) M1~M6의 유효한 관측 표본을 확장 판정에 사용할 수 없으며, (3) 따라서 `expansion_eligible`을 `true`로 만들 수 없다는 것이다. 현재 세 후보의 권고는 모두 `hold`다.

## 7. Appendix A 임계값 재조정 권고

Phase 4 측정으로 변경을 검토할 수 있는 Appendix A 값은 아래 6개뿐이다. 현재 값은 기준값이며, 권고 함수는 값과 사유를 반환할 뿐 실제 설정을 바꾸지 않는다.

| Appendix A identifier | 현재 값 | 이번 run 권고 | `changed` | 사유 |
| --- | ---: | --- | --- | --- |
| `initial_lookback_days` | `14` | `hold` | `false` | 4주 측정 게이트와 변경 공식이 없음 |
| `max_lookback_days` | `30` | `hold` | `false` | 4주 측정 게이트와 변경 공식이 없음 |
| `candidate_score_threshold` | `60` | `hold` | `false` | 4주 측정 게이트와 변경 공식이 없음 |
| `briefing_max_items` | `7` | `hold` | `false` | 4주 측정 게이트와 변경 공식이 없음 |
| `extraction_confidence_min` | `0.7` | `hold` | `false` | 4주 측정 게이트와 변경 공식이 없음 |
| `max_collect_retries` | `3` | `hold` | `false` | 4주 측정 게이트와 변경 공식이 없음 |

향후 4주 게이트가 통과하고 검토용 `proposed_values`가 명시적으로 제공되면 각 값에 대해 `recommend` 또는 `hold`와 현재값·제안값·사유를 만들 수 있다. 그래도 권고는 사용자 검토용 기록일 뿐이며, `config/default.toml`, `.env.example`, `CONFIG_KEYS` 또는 런타임 상태를 자동으로 변경하지 않는다.

