# STEP 1: LocalLLMClient 구현

> 메인: [main_local_llm_extraction.md](./main_local_llm_extraction.md)
> 선행 단계: 없음

## 목표

Ollama REST API(`/api/generate`)를 호출하는 `LLMClient` Protocol 구현체를
`src/scalping_briefing/llm/` 아래 신규 파일로 추가한다. 이번 단계는 재시도 로직(STEP 2) 없이
1회 호출 기본 동작만 구현한다.

## 대상 파일/모듈

- `src/scalping_briefing/llm/local_ollama.py` (신규)
- 참고: `src/scalping_briefing/llm/fixture.py` (`LLMClient` Protocol, `FixtureLLMClient` 구현 패턴)
- 참고: `src/scalping_briefing/llm/audit.py` (`audited_complete`, `_usage_value`/`_cost_value` — `metadata()` 형식 확인용)
- 참고(로직만, 복사 금지): `/tmp/.../scratchpad/bench_local_llm.py` — 세션 1회성 실측 스크립트, 테스트·감사 계약 미준수 상태이므로 그대로 옮기지 않는다.

## 실행 프롬프트

```
intent-docs/scalping_local_llm_extraction_intent.md §3.1을 기준으로
src/scalping_briefing/llm/local_ollama.py를 신규 작성해줘.

요구사항:
1. LLMClient(src/scalping_briefing/llm/fixture.py의 Protocol)를 만족하는
   LocalLLMClient 클래스를 만든다.
2. 생성자: model="qwen2.5:7b-instruct-q4_K_M", base_url="http://127.0.0.1:11434",
   timeout을 인자로 받는다(합리적 기본값). 새 config.py 설정 키는 만들지 않는다.
3. complete(self, prompt: str, **kwargs) -> Any:
   - Ollama /api/generate에 POST. body에 format: "json",
     options.temperature=0.1(낮은 고정값) 포함.
   - 응답 JSON의 "response" 필드 문자열을 json.loads()해서 반환.
   - JSON 파싱 실패, 네트워크 실패(연결 거부/타임아웃)는 그대로 예외를 올린다 —
     자체 방어/재시도 로직 넣지 않는다(재시도는 STEP 2에서 별도로 다룬다).
4. metadata(self, prompt: str) -> Mapping[str, Any] | None:
   - 직전 complete() 호출의 Ollama 응답에서 eval_count(출력 토큰),
     prompt_eval_count(입력 토큰)를 읽어 usage로 매핑.
   - estimated_cost_usd: 0.0, model_name: f"local:{self.model}" 포함.
   - src/scalping_briefing/llm/audit.py의 audited_complete가 기대하는
     형태(_usage_value, _cost_value 참고)와 맞춰라.
5. pipeline/extract.py의 기존 계약(ExtractionResult 등)은 건드리지 않는다 —
   이 파일은 신규 클라이언트 구현에 한정한다.

작업 후 tests/test_llm_client.py의 기존 LLMClient 계약 테스트 패턴을 참고해서
LocalLLMClient에 대한 최소 단위 테스트도 추가해줘(Ollama 서버는 mock/stub 처리 —
실제 네트워크 호출 없이 make test가 통과해야 함).
```

## 완료 기준

- [ ] `local_ollama.py`가 존재하고 `LLMClient` Protocol(`complete`, `metadata`)을 만족한다.
- [ ] `complete()`가 `format: "json"`, `options.temperature=0.1`로 POST하고 `response` 필드를 파싱한다.
- [ ] `metadata()`가 `usage`(`eval_count`/`prompt_eval_count`), `estimated_cost_usd: 0.0`,
      `model_name: "local:<model>"`을 채운다.
- [ ] 네트워크/JSON 실패 시 예외가 그대로 전파된다(방어 로직 없음).
- [ ] 새 단위 테스트가 실제 네트워크 호출 없이 `make test`를 통과한다.

## 주의사항

- `config.py`의 `CONFIG_KEYS`나 검증 로직을 건드리지 않는다.
- Ollama 헬스체크·자동 기동 로직을 이 클라이언트에 넣지 않는다(§3.4, STEP 5에서도 넣지 않음).
- 재시도 로직은 이번 단계 범위 밖 — STEP 2에서 같은 파일을 확장한다.
