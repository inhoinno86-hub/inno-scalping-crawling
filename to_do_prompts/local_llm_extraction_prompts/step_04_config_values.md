# STEP 4: config 로컬 모드 값 정리 (STEP 1~3과 병렬 가능)

> 메인: [main_local_llm_extraction.md](./main_local_llm_extraction.md)
> 선행 단계: 없음 (STEP 1과 독립적으로 병행 가능)

## 목표

`LLM_MODE=live`를 로컬 모델로 전환할 때 채워야 하는 `LLM_MONTHLY_BUDGET_USD`,
`LLM_RUN_MAX_TOKENS` 값의 **의미**를 확정하고 `.env.example`에 설명 주석을 추가한다.
`config.py`의 검증 로직·`CONFIG_KEYS`는 절대 바꾸지 않는다.

## 대상 파일/모듈

- `.env.example` (또는 동등 운영값 문서) — 주석 추가만
- 참고(읽기 전용, 수정 금지): `src/scalping_briefing/config.py`의 `_validate()`(약 224~264행)

## 실행 프롬프트

```
intent-docs/scalping_local_llm_extraction_intent.md §3.2를 기준으로
.env.example(없으면 저장소에서 동등한 운영값 예시 파일을 찾아라)에
로컬 LLM 모드 전환용 설명 주석을 추가해줘.

- LLM_MONTHLY_BUDGET_USD=0 : 로컬 추론은 API 과금이 없으므로 명목값 0으로
  채우면 기존 config.py의 _validate() 통과 조건(음수 아님, None 아님)을
  만족한다는 것을 주석으로 설명.
- LLM_RUN_MAX_TOKENS=2000(예시) : 실질적 폭주 방지 상한이며, 이 숫자가
  STEP 1의 LocalLLMClient가 Ollama 호출 시 options.num_predict로 전달할
  값과 일치해야 한다는 것을 주석으로 설명.
- LLM_MODE=live로 전환 시 필요한 승인 별칭(llm_live/live_llm/llm_mode_live/
  llm/live) 중 하나가 필요하다는 것도 언급해라.

절대 하지 말 것:
- config.py의 CONFIG_KEYS나 _validate() 로직 수정 — 이번 범위에서 완전히
  제외된 항목이다.
- 실제로 LLM_MODE=live로 전환하는 운영값 적용 — 스위치를 켜는 결정은
  사용자 승인 사항으로 남긴다. 주석/문서화만 한다.
- 새 설정 키 신설.

작업 후 config.py, CONFIG_KEYS 관련 파일에 diff가 없는지 git diff로
확인해서 보고해줘.
```

## 완료 기준

- [ ] `.env.example`에 `LLM_MONTHLY_BUDGET_USD=0` 의미 설명 주석 추가.
- [ ] `.env.example`에 `LLM_RUN_MAX_TOKENS`와 `num_predict` 일치 요구사항 설명 주석 추가.
- [ ] `config.py`, `CONFIG_KEYS`에 diff 없음(git diff로 확인).
- [ ] `LLM_MODE=live` 실제 전환(운영값 적용)이 수행되지 않음.

## 주의사항

- 이 단계는 문서화/주석 작업에 한정된다 — 코드 로직 변경은 STEP 1/2/5에서만 일어난다.
