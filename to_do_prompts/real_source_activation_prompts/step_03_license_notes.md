# STEP 3: 약관/재게시 라이선스 판단 자료 정리

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: 없음 (STEP 1·2와 독립적으로 병행 가능)

## 목표

코드/YAML 작업이 아니라, 사용자가 법적 판단을 내리는 데 필요한 자료를 정리한다. 5개 소스
각각의 약관/정책 링크를 재확인하고, 재게시/인용 범위에 대해 사용자가 고를 수 있는 선택지를
정리해 제시한다. **최종 법적 판단은 이 STEP의 산출물이 아니다** — 판단이 내려지면 그 결과를
`license_notes`에 기록하는 것까지가 완료 기준.

## 대상 파일/모듈

- `config/source-policy.yaml`의 `license_notes` 필드 (사용자 판단 완료 시에만 수정)
- 참고: 각 소스의 `access_policy.terms` (이미 등록된 약관 링크)

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §3.3을 기준으로,
config/source-policy.yaml의 5개 real 후보에 대한 약관/재게시 판단 자료를
정리해서 나에게 제시해줘. 너 스스로 최종 판단을 내리지 마라.

각 소스마다:
1. access_policy.terms에 이미 등록된 약관/정책 링크를 다시 확인한다.
2. 재게시/인용 범위 선택지를 정리한다 — 예: "요약+링크만 허용" / "발췌 인용 허용" /
   "재게시 금지". 소스 성격(공식 API 메타데이터 vs 블로그 전문 vs 거래소 문서)에
   따라 어떤 선택지가 더 안전한 기본값인지 참고 의견은 제시해도 되지만, 결정은
   내가 한다.
3. real_exchange_docs(Binance)와 real_research_blog(QuantInsti)가 재게시 범위
   쟁점이 가장 크다고 이미 파악돼 있다 — 이 두 소스를 우선 정리해서 먼저 보여줘.
4. real_arxiv_api, real_crossref_api(메타데이터 API), real_github_api(저장소별
   라이선스)에 대해서도 각각 참고할 만한 특이사항(예: Crossref 메타데이터는
   전문 재게시 권한을 주지 않음)을 함께 정리한다.

5개 소스 전부에 대한 표(소스 / 약관 링크 / 선택지 / 특이사항)로 정리해서 보여주고,
내 판단을 기다려라. 내가 판단을 내리면 그 결과를 config/source-policy.yaml의 해당
소스 license_notes 필드에 정확히 반영해줘. 아직 내가 판단하지 않은 소스는
license_notes를 임의로 "괜찮음"류로 채우지 말고 "판단 대기 중"임을 명시한 채로
둬라.
```

## 완료 기준

- [ ] 5개 소스 전부에 대해 약관 링크 + 재게시 범위 선택지 + 특이사항이 표로 정리되어
      제시됨.
- [ ] `real_exchange_docs`, `real_research_blog`가 우선 정리됨.
- [ ] 사용자 판단이 내려진 소스는 `license_notes`에 판단 결과가 정확히 기록됨.
- [ ] 아직 판단 전인 소스는 `license_notes`에 "판단 대기 중"임이 명시되어 있고, 추측으로
      "괜찮다"고 기록되지 않음.

## 주의사항

- 최종 법적 판단은 AI가 대신 내리지 않는다 — 선택지 제시와 참고 의견까지만.
- `license_notes` 갱신은 사용자가 실제로 판단을 내린 소스에 한해서만 수행한다.
