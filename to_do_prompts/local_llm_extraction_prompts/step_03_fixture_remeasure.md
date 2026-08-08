# STEP 3: 5개 fixture 재측정

> 메인: [main_local_llm_extraction.md](./main_local_llm_extraction.md)
> 선행 단계: STEP 2 (재시도 로직 포함 상태여야 재측정 의미가 있음)

## 목표

재시도 로직 포함 상태의 `LocalLLMClient`로 5개 fixture 문서에 대한 추출 통과율을
재측정한다. 목표는 5/5 통과이나, 실패가 남으면 추측 없이 사실대로 보고한다.

## 대상 파일/모듈

- `tests/fixtures/sources/` 아래 5개 소스: `fixture_rss_blog`, `fixture_atom_research`,
  `fixture_github_repo`, `fixture_exchange_docs`, `fixture_paper_meta`
  (`tests/fixtures/sources/fixture-manifest.json` 참고)
- Ollama 로컬 서버가 실제로 기동 중이어야 한다(`ollama list`에
  `qwen2.5:7b-instruct-q4_K_M` 확인 — 이미 완료된 전제).

## 실행 프롬프트

```
intent-docs/scalping_local_llm_extraction_intent.md §3.3의 완료 기준에 따라,
STEP 1~2에서 완성한 LocalLLMClient(재시도 포함)로 tests/fixtures/sources/의
5개 문서(fixture_rss_blog, fixture_atom_research, fixture_github_repo,
fixture_exchange_docs, fixture_paper_meta)에 대해 extract_strategy_candidate를
실행하고 스키마 통과 여부를 재측정해줘.

- 로컬 Ollama 서버가 기동 중인지 먼저 확인(ollama list에
  qwen2.5:7b-instruct-q4_K_M 존재 확인). 기동돼 있지 않으면 사전 기동 전제이므로
  중단하고 사용자에게 보고한다 — 이 단계에서 자동 기동 로직을 만들지 않는다.
- 측정은 반복 재실행 가능한 스크립트나 pytest integration 케이스로 만들어라
  (STEP 6에서 이 테스트가 Ollama 없는 환경에서 스킵/마커 분리되어야 하므로,
  여기서부터 integration 마커를 붙이는 걸 고려해라).
- 5개 문서 각각의 통과/실패 여부, 실패 시 검증 오류 메시지를 기록해라.
- 5/5 통과가 목표이나 실패가 남으면 "고쳐졌다"고 추측하지 말고 실패 사례와
  사유를 있는 그대로 보고해라(원 intent P4 — 추측 금지 원칙).

결과를 커밋 메시지 또는 별도 산출물 리포트로 남겨줘.
```

## 완료 기준

- [ ] 5개 fixture 문서 각각에 대한 통과/실패 결과가 기록됨.
- [ ] 재측정에 사용한 스크립트/테스트가 재실행 가능한 형태로 저장소에 남음.
- [ ] 5/5 통과 또는 (미달 시) 실패 사례+사유가 추측 없이 명시됨.
- [ ] Ollama 서버 자동 기동 로직이 이 단계 산출물에 포함되지 않음(사전 기동 전제 유지).

## 주의사항

- 이 측정 결과가 5/5에 미달해도 STEP 5(배선)로 진행할 수 있다 — DoD(§6)는 "5/5 또는 실패
  사례와 사유 명시"를 요구하지, 100% 통과를 강제하지 않는다. 단, 실패가 남으면 사유를
  명확히 남겨야 한다.
