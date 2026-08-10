# STEP 4: `validate_source_policy`의 "최소 5개 비활성" 제약 처리

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: 없음 (단, STEP 6 전 필수)

## 목표

`src/scalping_briefing/pipeline/source_policy.py`의 `validate_source_policy()`가 "real-source
후보 중 최소 5개는 `active: false`를 유지해야 한다"를 강제한다(58-59행,
`if len(inactive) < 5: raise SourcePolicyError(...)`). 지금 real 후보가 정확히 5개뿐이므로
하나라도 `active: true`로 켜면 이 검증이 실패한다. 방안 1(새 real 후보 추가 등록) 또는
방안 2(검증 규칙 자체 조정) 중 사용자와 함께 확정한 방안을 구현한다.

## 대상 파일/모듈

- `src/scalping_briefing/pipeline/source_policy.py` (55-59행, `validate_source_policy()`)
- 방안 1을 택할 경우: `config/source-policy.yaml` (새 real 후보 등록)
- 참고: `FIXTURE_SOURCE_IDS` 상수(13-21행) — fixture 5개 "항상 active" 규칙은 건드리지 않음

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §3.4를 기준으로,
src/scalping_briefing/pipeline/source_policy.py의 validate_source_policy()에
있는 "최소 5개 real 후보는 active: false 유지" 제약(55-59행)을 처리해줘.

먼저 나에게 두 방안을 정리해서 제시하고 선택을 물어라 — 임의로 결정하지 마라:

방안 1: 활성화하는 소스 수만큼 새 real 후보를 config/source-policy.yaml에
  추가로 등록해서 "비활성 5개 이상" 조건을 계속 만족시킨다. 검증 로직
  (pipeline/source_policy.py)은 변경 없음, 후보 목록만 늘어남.

방안 2: validate_source_policy()의 "최소 5개" 규칙 자체를 조정한다 — 예를 들어
  활성 소스 수와 무관하게 절대값 최소 N개를 유지하는 방식으로 바꾸거나, 규칙을
  완화한다. pipeline/source_policy.py에 코드 변경 발생.

내가 방안을 선택하면:
1. 방안 1이면 — 새 real 후보를 몇 개, 어떤 소스로 추가할지 제안하고 승인 받은 뒤
   config/source-policy.yaml에 access_policy/rate_limit 등 기존 real 후보와
   동일한 형태로 등록한다(active: false, schedule: approval_required).
   source_policy.py는 수정하지 않는다.
2. 방안 2면 — validate_source_policy()의 55-59행 규칙을 승인받은 형태로
   수정한다. 기존 5개 fixture 소스의 "항상 active 유지" 검증(52-56행,
   FIXTURE_SOURCE_IDS 관련)은 절대 건드리지 않는다.

어느 방안이든, 변경 후 real 후보 중 하나 이상이 active: true가 되어도
validate_source_policy()가 정상 통과하는지 실제로 확인해줘(단위 테스트 또는
직접 로드 검증).
```

## 완료 기준

- [ ] 방안 1 vs 2 선택이 사용자 승인을 거쳐 확정됨(임의 결정 없음).
- [ ] 선택된 방안이 구현되고, real 후보 일부가 `active: true`여도
      `validate_source_policy()`가 통과함을 실제로 확인함.
- [ ] `FIXTURE_SOURCE_IDS` 기반 "fixture 5개 항상 active" 검증에 diff 없음.
- [ ] diff 범위가 `pipeline/source_policy.py`(및 방안 1인 경우
      `config/source-policy.yaml`의 신규 후보 등록)로 한정됨.

## 주의사항

- 방안 선택은 사용자 결정 사항(§5) — 구현 전에 반드시 확인받는다.
- 방안 2를 택하더라도 fixture 관련 검증 규칙은 그대로 유지한다.
