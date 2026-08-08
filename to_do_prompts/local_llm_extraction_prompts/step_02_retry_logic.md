# STEP 2: 스키마 미준수 재시도 로직

> 메인: [main_local_llm_extraction.md](./main_local_llm_extraction.md)
> 선행 단계: STEP 1 (LocalLLMClient 구현 완료 필요 — 같은 클라이언트에 얹는 확장)

## 목표

실측 스키마 통과율 4/5(80%)의 유일한 반복 실패 패턴(`field_status` 빈 값 `{}` —
`minProperties: 1` 위반)에 대응해, `LocalLLMClient` 레벨에서 스키마 검증 후 최대 1회
재시도하는 정책을 추가한다.

## 대상 파일/모듈

- `src/scalping_briefing/llm/local_ollama.py` (STEP 1에서 만든 파일 확장)
- 참고: `src/scalping_briefing/llm/schema_guard.py` — `validate_strategy_candidate`, 스키마 로딩
- 참고: `schemas/strategy_candidate.schema.json` — 실패 조건(`minProperties: 1`) 확인용

## 실행 프롬프트

```
intent-docs/scalping_local_llm_extraction_intent.md §3.3을 기준으로
STEP 1에서 만든 src/scalping_briefing/llm/local_ollama.py의 LocalLLMClient.complete()에
재시도 로직을 추가해줘.

정책:
1. complete() 내부에서 1차 응답을 받은 직후
   schema_guard.validate_strategy_candidate로 즉시 검증한다.
2. 검증 실패 시 동일 프롬프트로 정확히 1회만 재호출한다(총 최대 2회 API 호출).
   무한 재시도 금지 — 이 상한은 하드코딩해도 된다.
3. 재시도 후에도 검증 실패면 마지막(2차) 응답을 그대로 반환한다 —
   여기서 자체적으로 예외를 던지지 않는다. extract.py의 기존 실패 경로가
   반환값을 보고 실패 처리를 하도록 그대로 둔다(이 클라이언트는 검증 통과 여부를
   스스로 판단해 예외를 던지지 않는다).
4. metadata()가 재시도로 소비된 토큰(1차+2차 usage 합산)과 실제 호출 횟수를
   정직하게 보고하도록 갱신한다. 로컬은 비용이 0이라 무해하지만 토큰 합산은
   정확해야 한다(향후 API 모델 전환 시 재사용).
5. pipeline/extract.py의 기존 계약(ExtractionResult, 실패 처리, EvidenceContractError)은
   건드리지 않는다 — 재시도 로직은 클라이언트 레벨에만 존재한다.

STEP 1에서 추가한 단위 테스트를 확장해서:
- 1차 응답이 스키마를 통과하면 재호출이 발생하지 않는 케이스
- 1차 실패 → 2차 성공 케이스
- 1차·2차 모두 실패 → 2차 응답 그대로 반환(예외 없음) 케이스
- metadata()의 토큰 합산이 두 케이스 모두에서 정확한지
를 검증해줘(모두 mock/stub, 실제 네트워크 호출 없음).
```

## 완료 기준

- [ ] `complete()`가 1차 응답을 `validate_strategy_candidate`로 즉시 검증한다.
- [ ] 검증 실패 시 동일 프롬프트로 정확히 1회만 재호출(최대 2회 호출 고정).
- [ ] 재시도 후에도 실패하면 마지막 응답을 예외 없이 그대로 반환한다.
- [ ] `metadata()`가 재시도 포함 토큰·호출 횟수를 정직하게 합산해서 보고한다.
- [ ] 신규/확장 단위 테스트가 3가지 케이스(1차 통과/2차 통과/모두 실패)를 mock으로 검증한다.

## 주의사항

- 재시도는 `extract.py` 파이프라인이 아니라 `LocalLLMClient` 레벨에 둔다(파이프라인 계약 불변).
- "고쳐졌다"고 추측으로 기록하지 않는다 — 실측은 STEP 3에서 별도로 확인한다.
