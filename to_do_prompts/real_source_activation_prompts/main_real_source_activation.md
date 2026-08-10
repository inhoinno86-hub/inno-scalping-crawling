# 실 소스(real source) 크롤링 활성화 단계별 진행 프롬프트

> 생성일: 2026-08-09
> 대상: `intent-docs/scalping_real_source_activation_intent.md` — `config/source-policy.yaml`에
> 등록된 5개 real-source 후보(`real_arxiv_api`, `real_github_api`, `real_exchange_docs`,
> `real_research_blog`, `real_crossref_api`)를 robots/약관/rate-limit 검토를 거쳐 안전하게
> 활성화 가능한 상태로 만든다. 크롤링/분류/추출 로직 자체는 변경하지 않는다.
> 총 단계 수: 7단계 (STEP 1~5는 서로 독립적으로 병렬 진행 가능, STEP 6은 소스별 개별 진행,
> STEP 7은 마지막)

## 전체 단계 개요

| 단계 | 제목 | 대상 | 상세 파일 |
|------|------|------|---------|
| STEP 1 | robots.txt 실측 및 YAML 반영 | `config/source-policy.yaml` (3개 소스) | [step_01_robots_measurement.md](./step_01_robots_measurement.md) |
| STEP 2 | rate-limit 값 재확인 | `config/source-policy.yaml` (5개 소스) | [step_02_rate_limit_review.md](./step_02_rate_limit_review.md) |
| STEP 3 | 약관/재게시 라이선스 판단 자료 정리 | `config/source-policy.yaml`의 `license_notes` (5개 소스) | [step_03_license_notes.md](./step_03_license_notes.md) |
| STEP 4 | "최소 5개 비활성" 제약 처리 | `src/scalping_briefing/pipeline/source_policy.py` | [step_04_min_inactive_constraint.md](./step_04_min_inactive_constraint.md) |
| STEP 5 | GitHub 인증 토큰 주입 확인/구현 | `src/scalping_briefing/sources/connectors/github.py` | [step_05_github_auth_token.md](./step_05_github_auth_token.md) |
| STEP 6 | 검토 완료 소스 `active: true` 전환 | `config/source-policy.yaml` (소스 단위, 사용자 승인 필요) | [step_06_activate_sources.md](./step_06_activate_sources.md) |
| STEP 7 | `make test` 전량 재확인 | 전체 테스트 스위트 | [step_07_full_test_verify.md](./step_07_full_test_verify.md) |

## 의존성 그래프

```
STEP 1 (robots 실측)        ── 선행 없음, 소스별 병렬 가능
STEP 2 (rate-limit 재확인)  ── 선행 없음, STEP 1과 독립 병행 가능
STEP 3 (약관 판단 자료 정리) ── 선행 없음, STEP 1·2와 독립 병행 가능 (최종 판단은 사용자)
STEP 4 (최소 5개 비활성 처리) ── 선행 없음, 단 STEP 6 전 필수
STEP 5 (GitHub 인증 토큰)   ── 선행 없음, real_github_api STEP 6 전 필수
STEP 6 (소스별 active 전환) ── 해당 소스에 필요한 STEP 1~5 항목 전부 완료
                              + STEP 3 사용자 법적 판단 완료 + 사용자 승인 후, 소스 단위 개별 진행
STEP 7 (make test 전량 재확인) ── 마지막
```

## 공통 제약 (모든 단계 공통 — 재작업/재확인 금지 항목)

- 크롤링/분류/추출 로직(`classify.py`/`scoring.py`/`novelty.py`/`routing.py`), LLM 추출
  (`llm/local_ollama.py`), Telegram 전달(`delivery/connector.py`)에 diff 없음.
- `config/source-policy.yaml`의 5개 fixture 소스(`fixture_*`, `active: true`)는 건드리지 않음.
- `validate_source_policy()`의 "fixture 5개는 항상 active" 규칙은 어떤 STEP에서도 깨지 않음.
- rate-limit 집행 코드(`net/rate_limit.py`, `orchestration/collect.py:121-123,234-238`)는 이미
  완성돼 있음 — 새 집행 로직 작성 금지, YAML 값 조정만.
- robots 판정 코드(`net/robots.py`의 `evaluate_robots()`)는 이미 완성돼 있음 — 새 판정 로직
  작성 금지, 실측 후 YAML 반영만.
- 약관/재게시 범위의 최종 법적 판단과, `active: true` 실제 전환은 AI가 대신 결정하지 않고
  반드시 사용자 승인을 받는다(STEP 3, STEP 6).
- `pipeline/source_policy.py`에 코드 diff가 생기는 것은 STEP 4가 유일하다.

---

## STEP 1: robots.txt 실측 및 YAML 반영

**목표**: `real_arxiv_api`/`real_exchange_docs`/`real_research_blog` 3개 소스의 실제
robots.txt를 가져와 `evaluate_robots()`로 판정하고, YAML의 `robots_allowed`/
`robots_rule_matched`/`robots_evaluated_at` 세 필드를 실측값으로 채운다.

**대상**: `config/source-policy.yaml`

**프롬프트**: [step_01_robots_measurement.md](./step_01_robots_measurement.md) 참고

**완료 기준**:
- [ ] `real_arxiv_api`(`export.arxiv.org/robots.txt`, 대상 경로 `/api/query`),
      `real_exchange_docs`(`developers.binance.com/robots.txt`, 대상 경로 `/docs`),
      `real_research_blog`(`blog.quantinsti.com/robots.txt`, 대상 경로 `/feed/`) 3개 모두
      `robots_allowed`가 `"unknown"`이 아닌 실측값(`true`/`false`)으로 채워짐.
- [ ] `robots_rule_matched`, `robots_evaluated_at`도 `evaluate_robots()` 반환값 그대로 반영됨.
- [ ] `real_github_api`, `real_crossref_api`(`robots: not_applicable`)는 이 STEP에서 건드리지 않음.

**다음 단계**: STEP 6 (해당 소스 활성화 전 필수)

---

## STEP 2: rate-limit 값 재확인

**목표**: 5개 real 후보 각각의 `rate_limit` 블록 값을 제공자 공식 문서 기준으로 재확인하고,
차이가 있으면 YAML을 갱신한다.

**대상**: `config/source-policy.yaml`

**프롬프트**: [step_02_rate_limit_review.md](./step_02_rate_limit_review.md) 참고

**완료 기준**:
- [ ] 5개 소스(`real_arxiv_api`/`real_github_api`/`real_exchange_docs`/`real_research_blog`/
      `real_crossref_api`) 각각에 대해 공식 문서 근거와 함께 기존 값 유지 또는 갱신 여부가
      기록됨.
- [ ] `real_github_api`의 값이 STEP 5(인증 토큰) 결과와 정합적임(토큰 유무에 따른 rate limit
      차이 반영).
- [ ] `real_crossref_api`의 `user_agent`를 polite pool 형식(연락처 이메일 포함)으로 바꿀지
      결정하고 근거를 기록함.
- [ ] YAML 편집만으로 완료(집행 코드 변경 없음).

**다음 단계**: STEP 6 (해당 소스 활성화 전 필수)

---

## STEP 3: 약관/재게시 라이선스 판단 자료 정리

**목표**: 코드/YAML 변경이 아니라, 사용자가 약관/재게시 범위를 판단할 수 있도록 5개 소스별
자료를 정리하고 선택지를 제시한다. 최종 판단은 사용자가 내린다.

**대상**: `config/source-policy.yaml`의 `license_notes` 필드 (판단 완료 시)

**프롬프트**: [step_03_license_notes.md](./step_03_license_notes.md) 참고

**완료 기준**:
- [ ] 5개 소스 각각 약관/정책 링크(`access_policy.terms`)와 재게시 범위 선택지("요약+링크만
      허용" / "발췌 인용 허용" / "재게시 금지")가 정리되어 사용자에게 제시됨.
- [ ] `real_exchange_docs`(Binance), `real_research_blog`(QuantInsti)를 우선 정리.
- [ ] 사용자 판단이 내려진 소스는 `license_notes`에 판단 결과를 기록, 아직 판단 전인 소스는
      "판단 대기 중"임을 명시(추측으로 "괜찮다"고 기록하지 않음).

**다음 단계**: STEP 6 (사용자 법적 판단 완료가 활성화 전제조건)

---

## STEP 4: `validate_source_policy`의 "최소 5개 비활성" 제약 처리

**목표**: real 후보를 하나라도 활성화할 수 있으려면 이 구조적 블로커부터 풀어야 한다. 방안
1(새 real 후보 추가 등록) 또는 방안 2(검증 규칙 자체 조정) 중 하나를 사용자와 함께 확정해
구현한다.

**대상**: `src/scalping_briefing/pipeline/source_policy.py`

**프롬프트**: [step_04_min_inactive_constraint.md](./step_04_min_inactive_constraint.md) 참고

**완료 기준**:
- [ ] 방안 1 vs 2 중 어느 것을 택할지 사용자 확인을 받음(§3.4, 임의로 결정하지 않음).
- [ ] 선택한 방안이 구현되어 real 후보 중 일부가 `active: true`가 되어도
      `validate_source_policy()`가 통과함.
- [ ] 기존 5개 fixture 소스의 "항상 active 유지" 검증(`FIXTURE_SOURCE_IDS` 관련 로직)은
      변경 없이 유지됨.
- [ ] 이 STEP의 diff는 `pipeline/source_policy.py`(및 방안 1을 택한 경우
      `config/source-policy.yaml`)로 한정됨.

**다음 단계**: STEP 6 (활성화 전 필수 선행)

---

## STEP 5: GitHub 커넥터 인증 토큰 주입 지점 확인/구현

**목표**: `real_github_api`를 켜기 전에 GitHub API 인증 토큰을 커넥터가 받는 지점을 확정한다.
확인 결과 현재 `GitHubConnector`(`src/scalping_briefing/sources/connectors/github.py`)에는
인증 헤더 주입 지점이 없다 — 이번 STEP에서 구현한다.

**대상**: `src/scalping_briefing/sources/connectors/github.py`

**프롬프트**: [step_05_github_auth_token.md](./step_05_github_auth_token.md) 참고

**완료 기준**:
- [ ] `GitHubConnector`가 `os.environ.get("GITHUB_API_TOKEN")`(또는 동등 환경변수 직접 조회)
      로 토큰을 읽어 `Authorization: Bearer <token>` 헤더를 요청에 포함함.
- [ ] `config.py`에 새 `CONFIG_KEYS` 신설 없음.
- [ ] 토큰이 없을 때 인증 없이도 호출은 가능하되, 자격증명 누락을 조용히 넘기지 않고 명확한
      경고/로그를 남김(예외로 요청 자체를 막지는 않음 — 낮은 rate limit로 계속 동작).
- [ ] 기존 `GitHubConnector` 테스트가 깨지지 않고, 토큰 있음/없음 두 경로에 대한 새 단위
      테스트가 추가됨(실제 네트워크 호출 없이 mock transport 사용).

**다음 단계**: STEP 6 (`real_github_api` 활성화 전 필수)

---

## STEP 6: 검토 완료된 소스부터 `active: true` 전환 (사용자 승인 필요)

**목표**: STEP 1~5 중 해당 소스에 필요한 항목이 전부 끝나고 STEP 3의 법적 판단이 사용자 승인을
받은 소스에 한해, 소스 단위로 개별 `active: true` 전환한다. 5개를 한 번에 켜지 않는다.

**대상**: `config/source-policy.yaml` (소스 단위)

**프롬프트**: [step_06_activate_sources.md](./step_06_activate_sources.md) 참고

**완료 기준**:
- [ ] 전환 대상 소스 목록과 각 소스의 STEP 1~5 완료 여부, STEP 3 사용자 승인 여부가 표로
      정리되어 사용자에게 제시됨.
- [ ] 사용자가 명시적으로 승인한 소스만 `active: true`로 전환됨(임의 판단으로 켜지 않음).
- [ ] 전환된 소스는 실제로 크롤링 사이클을 1회 통과시켜 실측 확인함(추측 금지).
- [ ] 아직 전환된 소스가 없다면 그 이유(판단 대기 등)가 명확히 기록됨.

**다음 단계**: STEP 7

---

## STEP 7: `make test` 전량 재확인

**목표**: 네트워크 없이 기존 통과 개수 이상 / 0 failed를 유지함을 확인한다. robots.txt 실측
등 실제 네트워크가 필요한 작업은 테스트 스위트가 아니라 STEP 1에서 별도 조회로 이미 수행됐고,
결과값만 YAML/코드에 반영된 상태여야 한다.

**대상**: 전체 테스트 스위트

**프롬프트**: [step_07_full_test_verify.md](./step_07_full_test_verify.md) 참고

**완료 기준**:
- [ ] `make test` 실행 결과가 기존 통과 개수 이상 / 0 failed.
- [ ] `classify.py`/`scoring.py`/`novelty.py`/`routing.py`/`llm/local_ollama.py`/
      `delivery/connector.py`에 diff 없음(최종 확인).
- [ ] STEP 6에서 전환한 소스가 있다면 관련 통합 테스트(또는 별도 조회 기록)가 근거로 남아 있음.
