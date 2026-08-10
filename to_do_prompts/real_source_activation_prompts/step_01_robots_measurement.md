# STEP 1: robots.txt 실측 및 YAML 반영

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: 없음

## 목표

`real_arxiv_api`/`real_exchange_docs`/`real_research_blog` 3개 소스의 실제 robots.txt를 각
도메인에서 가져와 `evaluate_robots()`로 판정하고, `config/source-policy.yaml`의
`robots_allowed`/`robots_rule_matched`/`robots_evaluated_at` 세 필드를 그 결과로 채운다.

## 대상 파일/모듈

- `config/source-policy.yaml` (수정 대상, `real_arxiv_api`/`real_exchange_docs`/
  `real_research_blog` 3개 레코드)
- 참고(수정 금지): `src/scalping_briefing/net/robots.py`의 `evaluate_robots(robots_text, url,
  user_agent=...)` — 판정 로직은 이미 완성돼 있다.
- 참고: `tests/test_phase2_collection_material.py`의 `ROBOTS_FIXTURES`/robots 판정 테스트
  패턴(11-63행 부근) — 동일한 호출 형태를 그대로 따른다.

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §3.1을 기준으로
config/source-policy.yaml의 robots 판정 필드를 실측값으로 채워줘.

대상 소스 3개:
1. real_arxiv_api — https://export.arxiv.org/robots.txt 를 가져와서 대상 경로
   /api/query 에 대해 판정.
2. real_exchange_docs — https://developers.binance.com/robots.txt 를 가져와서
   대상 경로 /docs 에 대해 판정.
3. real_research_blog — https://blog.quantinsti.com/robots.txt 를 가져와서
   대상 경로 /feed/ 에 대해 판정.

절차:
1. 각 도메인의 robots.txt를 1회 GET으로 가져온다(WebFetch 또는 동등 도구 사용,
   실제 외부 네트워크 요청임을 인지하고 진행).
2. src/scalping_briefing/net/robots.py의 evaluate_robots(robots_text, url,
   user_agent=...)를 그대로 호출해서 판정한다 — 새 판정 로직을 작성하지 않는다.
   user_agent는 각 소스의 access_policy.user_agent 값
   ("scalping-briefing/0.1 (approval required)")을 그대로 쓴다.
3. 판정 결과(RobotsDecision)의 robots_allowed, robots_rule_matched,
   robots_evaluated_at 세 값을 config/source-policy.yaml의 해당 소스 레코드에
   그대로 반영한다. access_decision_reason도 판정 결과의 reason으로 갱신한다.
4. real_github_api, real_crossref_api는 access_policy.robots: not_applicable로
   이미 표시돼 있으므로 이 STEP에서 건드리지 않는다.
5. YAML 외 파일(코드/스키마)은 수정하지 않는다.

작업 후 각 소스에 대해 "가져온 robots.txt 원문 중 판정에 쓰인 부분 + 판정 결과"를
간단히 요약해서 보고해줘 — 실측값이지 추측이 아님을 근거와 함께 보여줘야 한다.
```

## 완료 기준

- [ ] 3개 소스 모두 `robots_allowed`가 `"unknown"`이 아닌 `true`/`false` 실측값.
- [ ] `robots_rule_matched`, `robots_evaluated_at`이 `evaluate_robots()` 반환값과 일치.
- [ ] `access_decision_reason`이 판정 근거를 담고 있음(추측 문구 아님).
- [ ] `real_github_api`, `real_crossref_api` 레코드에는 diff 없음.
- [ ] 판정에 쓰인 robots.txt 원문 근거가 보고에 포함됨.

## 주의사항

- `evaluate_robots()` 자체는 수정하지 않는다 — 이미 완성된 판정 로직.
- robots.txt를 가져오는 것 자체는 읽기 전용 조회이지 크롤링 활성화가 아니다. 다만 각 도메인에
  실제 네트워크 요청을 보낸다는 점은 인지하고 진행한다(1회씩, 대량 요청 금지).
- 이 STEP만으로 소스가 `active: true`가 되지 않는다 — 활성화는 STEP 6에서 별도 승인 후.
