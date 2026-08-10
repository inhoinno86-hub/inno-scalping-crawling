# STEP 2: rate-limit 값 재확인

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: 없음 (STEP 1과 독립적으로 병행 가능)

## 목표

5개 real 후보(`real_arxiv_api`/`real_github_api`/`real_exchange_docs`/`real_research_blog`/
`real_crossref_api`) 각각의 `rate_limit` 블록 값을 제공자 공식 문서 기준으로 재확인하고,
차이가 있으면 `config/source-policy.yaml`을 갱신한다.

## 대상 파일/모듈

- `config/source-policy.yaml` (수정 대상, `rate_limit` 블록 5개)
- 참고(수정 금지): `src/scalping_briefing/net/rate_limit.py`의 `SourceRateLimiter`,
  `src/scalping_briefing/orchestration/collect.py:121-123,234-238` — 집행 코드는 이미
  완성돼 있고 YAML 값을 그대로 읽어 쓴다. 새 집행 로직을 작성하지 않는다.

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §3.2를 기준으로
config/source-policy.yaml의 rate_limit 값을 재확인·갱신해줘.

대상과 현재 후보값:
1. real_arxiv_api — requests_per_minute: 1. arXiv API 공식 rate-limit 정책
   (https://info.arxiv.org/help/api/tou.html 등)을 확인해서 값이 맞는지 검증.
2. real_github_api — requests_per_minute: 60. GitHub REST rate-limit 공식 문서
   (https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)를
   기준으로, 인증 토큰 유무에 따라 값이 크게 달라짐을 확인. STEP 5(인증 토큰 주입)
   결과와 이 값이 맞물려야 하므로, "토큰 있을 때"와 "토큰 없을 때" 두 시나리오의
   공식 rate limit을 모두 조사해서 보고하고, YAML에는 실제 운영 시나리오(토큰 사용
   전제)에 맞는 값을 반영한다.
3. real_exchange_docs — requests_per_minute: 30. Binance 개발자 문서 페이지
   (developers.binance.com/docs)의 명시된 접근 정책을 확인. 거래 API의 rate-limit
   정책과 혼동하지 않도록 주의 — 문서 페이지 자체에 대한 정책만 본다.
4. real_research_blog — requests_per_minute: 10. 공식 rate-limit 문서가 없는
   일반 블로그이므로, RSS 피드 갱신 주기(예: 일 1회 발행) 기준으로 이 값이
   "충분히 보수적인가"를 판단 근거와 함께 기록.
5. real_crossref_api — Crossref polite pool 정책(User-Agent에 연락처 이메일 포함 시
   더 높은 rate limit 허용)을 확인해서, access_policy.user_agent를 polite pool
   형식으로 바꿀지 여부와 근거를 기록. 바꾸기로 결정했다면 실제 연락처 이메일이
   필요한데, 이는 사용자 소유 정보이므로 임의로 채우지 말고 "결정 필요" 항목으로
   남겨 보고한다.

각 소스마다 "기존 값 유지" 또는 "OO로 갱신" 중 하나를 근거(공식 문서 링크/인용)와
함께 결정하고, YAML을 그 결정대로 갱신한다. 집행 코드(net/rate_limit.py,
orchestration/collect.py)는 수정하지 않는다.
```

## 완료 기준

- [ ] 5개 소스 각각에 대해 "유지/갱신" 결정과 근거가 보고에 포함됨.
- [ ] `real_github_api` 값이 STEP 5 결과(토큰 사용 여부)와 정합적임.
- [ ] `real_crossref_api`의 `user_agent` polite pool 전환 여부는 이메일 등 사용자 정보가
      필요하면 임의로 채우지 않고 "결정 필요"로 명시.
- [ ] `net/rate_limit.py`, `orchestration/collect.py`에 diff 없음.

## 주의사항

- 값을 바꾸는 것 자체는 YAML 편집만으로 끝난다 — 집행 코드는 건드리지 않는다.
- Binance 문서 페이지 정책과 거래(trading) API rate-limit 정책을 혼동하지 않는다.
