# STEP 6: 검토 완료된 소스부터 `active: true` 전환 (사용자 승인 필요)

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: STEP 1~5 (해당 소스에 필요한 항목 전부) + STEP 3 사용자 법적 판단

## 목표

STEP 1~5 중 해당 소스에 필요한 항목이 전부 끝나고 STEP 3의 법적 판단이 사용자 승인을 받은
소스에 한해, 소스 단위로 개별 `active: true` 전환한다. 5개를 한 번에 켜지 않는다 — 판단이
가장 먼저 끝나는 소스(예: `real_crossref_api`나 `real_arxiv_api`)부터 순차 진행을 권장한다.

## 대상 파일/모듈

- `config/source-policy.yaml` (소스 단위 `active` 필드)
- 확인 필요: 크롤링 사이클 실행 진입점(`src/scalping_briefing/__init__.py` 또는
  `orchestration/cycle.py`) — 전환 후 실제 사이클 1회 통과 확인용

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §4 STEP 6, §5를 기준으로
real-source 후보를 active: true로 전환할 준비 상태를 점검해줘. 내 명시적 승인
없이는 어떤 소스도 active: true로 바꾸지 마라.

절차:
1. config/source-policy.yaml의 5개 real 후보 각각에 대해 표로 정리해줘:
   - robots 판정 완료 여부(STEP 1, 해당 없는 소스는 not_applicable로 표시)
   - rate-limit 재확인 완료 여부(STEP 2)
   - license_notes 판단 완료 여부(STEP 3) — "판단 대기 중"이면 그대로 표시
   - GitHub 인증 토큰(STEP 5, real_github_api만 해당)
   - pipeline/source_policy.py 최소 5개 비활성 제약 처리 여부(STEP 4)
2. 이 표를 근거로 "지금 바로 active: true 전환 가능한 소스" 목록을 제시하고
   내 승인을 기다려라.
3. 내가 특정 소스를 승인하면:
   a. config/source-policy.yaml에서 해당 소스의 active를 true로, schedule을
      approval_required가 아닌 실제 운영 스케줄로 바꾼다(다른 real 후보와
      일관된 스케줄 형식 확인 후 적용).
   b. validate_source_policy()가 여전히 통과하는지 확인한다(STEP 4 처리 결과
      검증).
   c. 실제로 크롤링 사이클을 1회 실행해서(네트워크 필요 — 진행 전 인지시켜라)
      해당 소스가 정상적으로 통과하는지 실측 확인하고 결과를 그대로 보고한다
      (성공/실패 모두 추측 없이 있는 그대로).
4. 아직 어떤 소스도 활성화 조건을 못 채웠다면, 어떤 항목이 남았는지(예: STEP 3
   판단 대기)를 명확히 보고하고 이 STEP을 "전환 없음 + 사유 기록"으로 종료한다.
```

## 완료 기준

- [ ] 5개 소스의 준비 상태 표가 사용자에게 제시됨.
- [ ] 사용자가 명시적으로 승인한 소스만 `active: true`로 전환됨.
- [ ] 전환된 소스는 크롤링 사이클 1회 통과가 실측으로 확인됨(성공/실패 그대로 기록).
- [ ] 전환된 소스가 없다면 그 이유가 명확히 기록됨.
- [ ] `validate_source_policy()`가 전환 후에도 통과함.

## 주의사항

- 실제 크롤링 사이클 실행은 외부 네트워크에 실제 요청을 보내는 비가역적 성격의 활동이다 —
  진행 전 사용자에게 다시 한번 인지시킨다.
- 5개를 한 번에 전환하지 않는다 — 소스 단위로 개별 승인·개별 실측.
