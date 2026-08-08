# STEP 5: 실행 사이클 배선

> 메인: [main_local_llm_extraction.md](./main_local_llm_extraction.md)
> 선행 단계: STEP 1, STEP 2 (클라이언트가 있어야 배선 가능)

## 목표

`run_briefing_cycle()`(또는 그 위 진입점)이 실제로 `LocalLLMClient`를 쓸 수 있게 배선하되,
기존 fixture 기본 동작과 `run_briefing()`의 계약을 깨지 않는다. 로컬 LLM은 옵트인이며
기본 동작은 절대 바뀌지 않는다.

## 대상 파일/모듈

- `src/scalping_briefing/__init__.py` (조립 지점 — 얇은 팩토리 함수)
- `src/scalping_briefing/orchestration/cycle.py` (`_setting_kwargs`, 단계 배선, 실패 격리 — 참고/최소 수정)
- 참고: `src/scalping_briefing/config.py` (`Settings`에 `llm_client` 속성 추가 금지)

## 실행 프롬프트

```
intent-docs/scalping_local_llm_extraction_intent.md §3.4를 기준으로,
STEP 1~2에서 완성한 LocalLLMClient를 실행 사이클에 옵트인으로 배선해줘.

요구사항:
1. run_briefing_cycle()(또는 그 위 진입점)이 llm_client 인자를 받을 수 있게 한다.
   인자가 없으면 기존과 동일하게 None → 각 단계 함수의 기본값인
   FixtureLLMClient()가 그대로 쓰인다. 즉 기본 동작은 절대 안 바뀐다.
2. LLM_MODE 값에 따라 LocalLLMClient 인스턴스를 조립해서 넘기는 조립 지점을
   src/scalping_briefing/__init__.py의 진입점 레벨(또는 그에 준하는 얇은
   팩토리 함수)에 둔다.
3. config.py의 Settings에는 llm_client 속성을 추가하지 않는다 — orchestration/
   cycle.py:150의 _setting_kwargs(settings, "llm_client", "quote_max_chars")가
   Settings에서 llm_client를 읽으려 하면 CONFIG_KEYS에 없어 조용히 빠지므로,
   이 경로에 의존하지 말고 함수 인자/래퍼로 명시적으로 주입해라.
4. 사이클 코드(orchestration/cycle.py)에 Ollama 헬스체크·자동 기동 로직을
   추가하지 않는다. 서버가 죽어 있으면 LocalLLMClient.complete()가 예외를
   던지고, 기존 run_stage/alerts/ 격리 경로가 그대로 처리하게 둔다.
5. 분류 단계(classify.py)는 건드리지 않는다 — 실 사이클은 분류를
   항상 use_llm=False(규칙 기반)로 호출하므로 로컬 LLM이 필요한 지점은
   추출 단계뿐이다.
6. Makefile 타깃 신설 여부/기존 run-briefing-cycle에 환경변수 스위치로
   얹을지는 네 판단에 맡긴다 — "새 설정 키를 안 만든다"는 제약만 지켜라.

작업 후 아래를 검증하는 테스트를 추가/확장해줘:
- llm_client 인자 없이 run_briefing_cycle()을 호출하면 기존 동작
  (FixtureLLMClient 사용, run_briefing() 계약 불변)이 그대로 유지되는지.
- LLM_MODE 값에 따라 팩토리가 LocalLLMClient를 조립해 넘기는지(Ollama 서버는
  mock/stub 처리).
```

## 완료 기준

- [ ] `run_briefing_cycle()`(또는 진입점)이 `llm_client` 인자를 받고, 미지정 시 기존 동작 불변.
- [ ] `LLM_MODE`에 따른 조립 지점이 `__init__.py`(또는 동등 팩토리)에 존재.
- [ ] `Settings`에 `llm_client` 속성이 추가되지 않음.
- [ ] Ollama 헬스체크/자동 기동 로직이 사이클 코드에 없음.
- [ ] `classify.py`, `scoring.py`, `novelty.py`, `routing.py`에 diff 없음.
- [ ] 기본 동작 불변을 확인하는 테스트와 옵트인 배선을 확인하는 테스트가 모두 통과.

## 주의사항

- `pipeline/extract.py`의 계약을 변경하지 않는다.
- 이 단계에서 `LLM_MODE=live` 운영값을 실제로 적용하지 않는다(STEP 4에서 정리한 값의
  의미만 참고, 스위치를 켜는 결정은 사용자 승인 필요).
