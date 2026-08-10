# STEP 7: `make test` 전량 재확인

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: STEP 1~6 (전부)

## 목표

전체 테스트 스위트가 네트워크 없이 기존 통과 개수 이상 / 0 failed를 유지함을 확인한다.
robots.txt 실측(STEP 1) 등 실제 네트워크가 필요한 작업은 테스트 스위트가 아니라 별도 조회로
이미 수행됐고, 그 결과값만 YAML/코드에 반영된 상태여야 한다.

## 대상 파일/모듈

- 전체 테스트 스위트 (`make test`)
- 확인 대상: `classify.py`/`scoring.py`/`novelty.py`/`routing.py`/`llm/local_ollama.py`/
  `delivery/connector.py`에 diff 없음(§5 최종 확인)

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §6(완료 기준), §7을
기준으로 make test를 전량 재실행해서 결과를 확인해줘.

절차:
1. `make test`(PYTHONPATH=src pytest -q)를 실행하고 passed/failed 개수를
   그대로 보고한다. 이번 작업 시작 전 기준선(baseline) 대비 통과 개수가
   같거나 늘었는지, failed가 0인지 확인한다.
2. STEP 1~6에서 config/source-policy.yaml, pipeline/source_policy.py,
   sources/connectors/github.py 외에 다른 파일에 diff가 생기지 않았는지
   git diff --stat로 확인한다. 특히 classify.py/scoring.py/novelty.py/
   routing.py/llm/local_ollama.py/delivery/connector.py에 diff가 없어야
   한다 — 있다면 왜 생겼는지 보고하고 되돌릴지 나에게 물어라(임의로
   되돌리지 마라).
3. STEP 5에서 추가한 GitHub 토큰 유무 테스트, STEP 4에서 추가/수정한
   source_policy 테스트가 실제로 이번 실행에 포함되어 통과했는지 개별
   확인한다.
4. STEP 6에서 실제로 active: true 전환이 있었다면, 그 실측 결과(사이클 통과
   여부)를 이 STEP의 최종 보고에 함께 요약한다.

실패가 있으면 추측으로 "아마 괜찮을 것"이라 말하지 말고 실패 로그 핵심 줄만
그대로 인용해서 보고해줘.
```

## 완료 기준

- [ ] `make test` 실행 결과 기존 통과 개수 이상 / 0 failed.
- [ ] `git diff --stat` 기준으로 diff 범위가 이번 계획의 대상 파일로 한정됨을 확인.
- [ ] `classify.py`/`scoring.py`/`novelty.py`/`routing.py`/`llm/local_ollama.py`/
      `delivery/connector.py`에 diff 없음.
- [ ] STEP 4/5에서 추가한 신규 테스트가 이번 실행에 포함되어 통과함이 확인됨.
- [ ] STEP 6에서 소스 전환이 있었다면 그 실측 결과가 함께 요약됨.

## 주의사항

- 실패를 발견해도 임의로 코드를 되돌리거나 스킵 처리하지 않는다 — 사용자에게 보고하고 판단을
  받는다.
- 이 STEP은 검증 전용이다. 새 기능/리팩터링을 추가하지 않는다.
