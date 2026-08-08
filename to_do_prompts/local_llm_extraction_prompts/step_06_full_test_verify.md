# STEP 6: `make test` 전량 재확인

> 메인: [main_local_llm_extraction.md](./main_local_llm_extraction.md)
> 선행 단계: STEP 1~5 전체 완료 후 (최종 단계)

## 목표

전체 테스트 스위트가 네트워크·Docker·API 키 없이 353 passed 이상 / 0 failed를 유지함을
확인한다. 로컬 LLM 호출이 필요한 새 테스트는 Ollama 없는 환경에서 스킵되거나 `integration`
마커로 분리되어 기본 `make test`를 막지 않아야 한다.

## 대상 파일/모듈

- 전체 테스트 스위트 (`tests/`)
- 특히 확인: `tests/test_llm_client.py`, `tests/test_phase2_extraction.py`, `tests/test_phase2_dod.py`

## 실행 프롬프트

```
intent-docs/scalping_local_llm_extraction_intent.md §6 DoD를 기준으로
STEP 1~5 작업 완료 후 make test를 실행해서 최종 검증해줘.

확인 항목:
1. make test가 네트워크·Docker·API 키 없이 353 passed 이상 / 0 failed인지.
2. STEP 1/2/3에서 추가한 로컬 LLM 관련 테스트가 Ollama가 없는 환경에서
   스킵되거나 integration 마커로 분리되어 기본 make test 실행을 막지 않는지
   (pytest -m "not integration" 등으로 확인).
3. git diff로 config.py, CONFIG_KEYS 관련 파일, schemas/, models/,
   migrations/ 아래에 diff가 없는지 최종 확인(§5 제약).
4. extract_strategy_candidate(..., llm_client=LocalLLMClient(...)) 호출이
   정상적인 ExtractionResult를 반환하고, 실패 시 기존 실패 경로(error_class,
   alerts/)를 그대로 타는지 테스트로 확인되어 있는지.

실패하는 테스트가 있으면 원인을 보고하고, "임시로 통과시키기 위한" 우회
(예: 테스트 스킵 남발, assert 완화)를 하지 말고 STEP 1~5 중 어느 단계의
문제인지 짚어서 되돌아가 고쳐라.

최종 결과(pass/fail 카운트, diff 없음 확인 결과)를 보고해줘.
```

## 완료 기준

- [ ] `make test` 결과 353 passed 이상 / 0 failed.
- [ ] 신규 LLM 통합 테스트가 Ollama 미가동 환경에서 스킵되거나 `integration` 마커로 분리됨.
- [ ] `config.py`·`CONFIG_KEYS`·`schemas/`·`models/`·`migrations/`에 diff 없음.
- [ ] `llm_client=LocalLLMClient(...)` 주입 시 정상 `ExtractionResult` 반환, 실패 시 기존
      실패 경로(`error_class`, `alerts/`) 유지가 테스트로 확인됨.

## 주의사항

- 이 단계는 검증 전용이다 — 실패 발견 시 임시 우회(스킵 남발, assert 완화)로 통과시키지 않고
  원인이 된 이전 단계(STEP 1~5)로 돌아가 수정한다.
- 이 단계 완료로 전체 계획(§6 DoD)이 종료된다. `LLM_MODE=live` 운영 전환은 여전히 범위 밖.
