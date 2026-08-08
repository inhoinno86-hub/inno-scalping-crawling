# 로컬 LLM 기반 Phase 2 추출 연동 단계별 진행 프롬프트

> 생성일: 2026-08-09
> 대상: `intent-docs/scalping_local_llm_extraction_intent.md` — Phase 2 `extract_strategy_candidate()`용
> 로컬 LLM(`LocalLLMClient`, Ollama/Qwen2.5-7B) 구현·재시도 로직·배선. API 비용 없이 추출 단계를
> 실제로 돌릴 수 있는 상태를 만드는 것이 목표이며, 도메인 계산 로직(classify/scoring/novelty/routing)은
> 건드리지 않는다.
> 총 단계 수: 6단계 (STEP 4는 STEP 1~3과 병렬 진행 가능, 나머지는 순차)

## 전체 단계 개요

| 단계 | 제목 | 대상 | 상세 파일 |
|------|------|------|---------|
| STEP 1 | LocalLLMClient 구현 | `src/scalping_briefing/llm/local_ollama.py` (신규) | [step_01_local_llm_client.md](./step_01_local_llm_client.md) |
| STEP 2 | 스키마 미준수 재시도 로직 | `local_ollama.py`의 `complete()` | [step_02_retry_logic.md](./step_02_retry_logic.md) |
| STEP 3 | 5개 fixture 재측정 | `tests/fixtures/sources/*` 5종 | [step_03_fixture_remeasure.md](./step_03_fixture_remeasure.md) |
| STEP 4 | config 로컬 모드 값 정리 | `.env.example`, 문서 주석 (병렬 가능) | [step_04_config_values.md](./step_04_config_values.md) |
| STEP 5 | 실행 사이클 배선 | `src/scalping_briefing/__init__.py`, `orchestration/cycle.py` | [step_05_cycle_wiring.md](./step_05_cycle_wiring.md) |
| STEP 6 | `make test` 전량 재확인 | 전체 테스트 스위트 (353 passed 이상 유지) | [step_06_full_test_verify.md](./step_06_full_test_verify.md) |

## 의존성 그래프

```
STEP 1 (LocalLLMClient) ── 선행 없음
   └─▶ STEP 2 (재시도 로직, 같은 클라이언트에 얹음)
          └─▶ STEP 3 (재측정, STEP 2 완료 기준 확인)
   └─▶ STEP 5 (사이클 배선, STEP 1~2 완료 후)
STEP 4 (config 값 정리) ── STEP 1과 독립적으로 병행 가능
STEP 6 (make test 전량 재확인) ── 마지막, 모든 단계 완료 후
```

## 공통 제약 (모든 단계 공통 — 재작업/재확인 금지 항목)

- `config.py`의 `CONFIG_KEYS`·`_validate()` 검증 로직 변경 금지, 새 설정 키 신설 금지.
- `Settings`(`config.py`)에 `llm_client` 속성 추가 금지 — 별도 래퍼/인자로만 주입.
- `pipeline/extract.py`의 기존 계약(`ExtractionResult`, 실패 처리, `EvidenceContractError`) 변경 금지.
- Phase 2 계산 로직(`classify.py`/`scoring.py`/`novelty.py`/`routing.py`) 수정 금지.
- `LLM_MODE=live` 실제 전환(운영값 적용)은 이번 범위 밖 — 클라이언트·배선까지만.
- Ollama 헬스체크·자동 기동 로직을 사이클 코드에 넣지 않는다 — 사전 기동 전제, 실패는 기존
  `orchestration/cycle.py`의 `run_stage`/`alerts/` 격리 경로가 처리.

---

## STEP 1: LocalLLMClient 구현

**목표**: Ollama REST API(`/api/generate`)를 호출하는 `LLMClient` Protocol 구현체를 신규 파일로 추가한다.

**대상**: `src/scalping_briefing/llm/local_ollama.py` (신규)

**프롬프트**: [step_01_local_llm_client.md](./step_01_local_llm_client.md) 참고

**완료 기준**:
- [ ] `local_ollama.py`가 존재하고 `complete(prompt, **kwargs) -> Any`, `metadata(prompt) -> Mapping[str, Any] | None`을 구현한다.
- [ ] `complete()`는 `format: "json"`, `options.temperature=0.1`(고정 낮은 값)로 POST하고 `response` 필드를 `json.loads`한다.
- [ ] `metadata()`는 `eval_count`/`prompt_eval_count`를 `usage`로 매핑, `estimated_cost_usd: 0.0`, `model_name: "local:<model>"` 채움.
- [ ] 생성자 인자: `model="qwen2.5:7b-instruct-q4_K_M"`, `base_url="http://127.0.0.1:11434"` 기본값, 새 설정 키 없음.
- [ ] 네트워크 실패/JSON 파싱 실패는 예외로 그대로 올림(자체 방어 로직 없음).

**다음 단계**: STEP 2 (동일 클라이언트), STEP 5 (배선, STEP 2 이후)

---

## STEP 2: 스키마 미준수 재시도 로직

**목표**: `field_status: {}` 등 스키마 미준수 응답에 대해 동일 프롬프트로 1회만 재호출하는 재시도 정책을 `LocalLLMClient` 레벨에 추가한다.

**대상**: `src/scalping_briefing/llm/local_ollama.py` (STEP 1 확장)

**프롬프트**: [step_02_retry_logic.md](./step_02_retry_logic.md) 참고

**완료 기준**:
- [ ] `complete()` 내부에서 1차 응답을 `schema_guard.validate_strategy_candidate`로 즉시 검증.
- [ ] 검증 실패 시 동일 프롬프트로 정확히 1회 재호출(총 최대 2회 호출), 무한 재시도 없음.
- [ ] 재시도 후에도 실패하면 마지막 응답을 그대로 반환(자체적으로 예외를 던지지 않음 — `extract.py`의 기존 실패 경로가 처리).
- [ ] `metadata()`가 재시도로 소비된 토큰·호출 횟수를 정직하게 합산해서 보고.

**다음 단계**: STEP 3

---

## STEP 3: 5개 fixture 재측정

**목표**: `tests/fixtures/sources/` 5개 소스(fixture_rss_blog, fixture_atom_research,
fixture_github_repo, fixture_exchange_docs, fixture_paper_meta)로 재시도 로직 포함 상태의 추출
통과율을 재측정한다.

**대상**: `tests/fixtures/sources/*` 5종, 측정 스크립트/기록

**프롬프트**: [step_03_fixture_remeasure.md](./step_03_fixture_remeasure.md) 참고

**완료 기준**:
- [ ] 5개 fixture 문서 각각에 대해 `extract_strategy_candidate(..., llm_client=LocalLLMClient(...))` 실행 결과 기록.
- [ ] 5/5 통과를 목표로 하되, 실패가 남으면 실패 사례와 사유를 추측 없이 있는 그대로 보고.
- [ ] 측정 결과가 문서(커밋 메시지 또는 산출물 리포트)로 남아 있음.

**다음 단계**: STEP 5 (STEP 2 완료 후 아무 때나 가능하지만, 재측정 결과 확인 후 진행 권장)

---

## STEP 4: config 로컬 모드 값 정리 (STEP 1~3과 병렬 가능)

**목표**: `LLM_MODE=live`를 로컬 모델로 전환할 때 채워야 할 `LLM_MONTHLY_BUDGET_USD`,
`LLM_RUN_MAX_TOKENS` 값의 의미를 확정하고 `.env.example`에 설명 주석을 추가한다. `config.py`
자체는 수정하지 않는다.

**대상**: `.env.example` (또는 동등 문서), 설명 주석만

**프롬프트**: [step_04_config_values.md](./step_04_config_values.md) 참고

**완료 기준**:
- [ ] `.env.example`에 `LLM_MONTHLY_BUDGET_USD=0`(로컬 추론 무과금) 의미 주석 추가.
- [ ] `LLM_RUN_MAX_TOKENS` 값(예: 2000)이 STEP 1 클라이언트의 `options.num_predict`와 일치한다는 설명 주석 추가.
- [ ] `config.py`, `CONFIG_KEYS`, 검증 로직에 diff 없음.
- [ ] 실제 `LLM_MODE=live` 운영값 적용은 수행하지 않음(승인 필요 항목으로 남김).

**다음 단계**: 없음 (독립 완료, STEP 6에 합류)

---

## STEP 5: 실행 사이클 배선

**목표**: `run_briefing_cycle()`(또는 진입점)이 `llm_client` 인자를 옵트인으로 받을 수 있게
하고, `LLM_MODE` 값에 따라 `src/scalping_briefing/__init__.py` 레벨의 얇은 팩토리가
`LocalLLMClient`를 조립해 넘기게 한다. 인자가 없으면 기존 `FixtureLLMClient()` 기본 동작 유지.

**대상**: `src/scalping_briefing/__init__.py`, `src/scalping_briefing/orchestration/cycle.py`

**프롬프트**: [step_05_cycle_wiring.md](./step_05_cycle_wiring.md) 참고

**완료 기준**:
- [ ] `run_briefing_cycle()`(또는 상위 진입점)이 `llm_client` 인자를 받고, 없으면 `None` → 기존
      `FixtureLLMClient()` 기본값 유지(기본 동작 불변).
- [ ] `LLM_MODE`에 따른 조립 지점이 `__init__.py`(또는 동등 팩토리)에 있고 `Settings`에 `llm_client`
      속성을 추가하지 않음.
- [ ] Ollama 헬스체크/자동 기동 로직 없음 — 사전 기동 전제만 문서화.
- [ ] `llm_client` 인자 없을 때 기존 동작 불변임을 확인하는 테스트 존재.

**다음 단계**: STEP 6

---

## STEP 6: `make test` 전량 재확인

**목표**: 전체 테스트 스위트가 네트워크·Docker·API 키 없이 353 passed 이상 / 0 failed를
유지함을 확인한다. 로컬 LLM 호출이 필요한 새 테스트는 Ollama 없는 환경에서 스킵되거나
`integration` 마커로 분리되어 기본 `make test`를 막지 않아야 한다.

**대상**: 전체 테스트 스위트

**프롬프트**: [step_06_full_test_verify.md](./step_06_full_test_verify.md) 참고

**완료 기준**:
- [ ] `make test` 실행 결과 353 passed 이상 / 0 failed.
- [ ] 신규 LLM 통합 테스트가 Ollama 미가동 환경에서 스킵되거나 별도 마커로 분리됨.
- [ ] `config.py`·`CONFIG_KEYS`·`schemas/`·`models/`·`migrations/`에 diff 없음(§5 제약 최종 확인).

**다음 단계**: 없음 (최종 단계)
