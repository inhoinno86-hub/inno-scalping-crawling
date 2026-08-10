# STEP 5: GitHub 커넥터 인증 토큰 주입 지점 확인/구현

> 메인: [main_real_source_activation.md](./main_real_source_activation.md)
> 선행 단계: 없음 (단, `real_github_api` 활성화 전 필수)

## 목표

`real_github_api`를 켜기 전에 GitHub API 인증 토큰을 커넥터가 어떻게 받는지 확정한다.
`src/scalping_briefing/sources/connectors/github.py`를 확인한 결과, 현재 `GitHubConnector`는
요청 헤더로 `Accept`/`X-GitHub-Api-Version`만 보내고(`collect()`의 220-223행)
`Authorization` 헤더 주입 지점이 없다 — 이번 STEP에서 추가한다.

## 대상 파일/모듈

- `src/scalping_briefing/sources/connectors/github.py` (수정 대상, `GitHubConnector.collect()`
  의 헤더 구성부, 220-223행 부근)
- 참고(패턴만, 새 CONFIG_KEYS 추가 금지): 로컬 LLM 클라이언트에서 썼던 환경변수 직접 읽기
  패턴(`os.environ.get(...)`)
- 참고: `tests/` 아래 기존 `GitHubConnector` 테스트 — mock transport로 호출되는 패턴을 따른다.

## 실행 프롬프트

```
intent-docs/scalping_real_source_activation_intent.md §3.5를 기준으로
src/scalping_briefing/sources/connectors/github.py의 GitHubConnector에
GitHub API 인증 토큰 주입을 추가해줘.

요구사항:
1. GitHubConnector.collect()가 요청을 보내기 전에 os.environ.get("GITHUB_API_TOKEN")
   로 토큰을 읽는다. config.py에 새 CONFIG_KEYS를 신설하지 않는다(원 intent §5와
   동일 제약) — 환경변수 직접 읽기만 사용한다.
2. 토큰이 있으면 현재 헤더 구성(220-223행, Accept/X-GitHub-Api-Version)에
   "Authorization": f"Bearer {token}" 을 추가해서 releases 요청과 README 요청
   양쪽 모두에 사용한다.
3. 토큰이 없으면: 인증 없이 호출 자체는 계속 진행하되(낮은 rate limit로 동작),
   자격증명 누락을 조용히 넘기지 않고 명확한 경고를 남긴다 — 예외를 던져서 수집을
   막지는 않는다(실행 단계 판단에 맡긴다고 했으니, warnings 모듈 로그 또는 동등한
   가시적 경고 방식을 선택해서 구현하고 어떤 방식을 택했는지 이유와 함께 보고해줘).
4. 기존 collect()의 cursor/response 처리 로직, ConnectorResult 계약은 건드리지 않는다
   — 헤더 구성 지점만 확장한다.

작업 후 GITHUB_API_TOKEN이 설정된 경우/설정되지 않은 경우 두 시나리오에 대한 단위
테스트를 추가해줘(실제 네트워크 호출 없이 mock transport로 헤더 내용을 검증,
make test가 통과해야 함).
```

## 완료 기준

- [ ] `GitHubConnector`가 `os.environ.get("GITHUB_API_TOKEN")`로 토큰을 읽어
      `Authorization: Bearer <token>` 헤더를 releases/README 요청 양쪽에 포함.
- [ ] `config.py`에 새 `CONFIG_KEYS` 신설 없음.
- [ ] 토큰 없을 때도 호출은 계속되되 자격증명 누락에 대한 명확한 경고가 남음.
- [ ] 기존 `collect()` cursor/ConnectorResult 계약에 diff 없음.
- [ ] 토큰 있음/없음 두 시나리오에 대한 단위 테스트가 추가되고 실제 네트워크 호출 없이 통과.

## 주의사항

- 토큰 값을 로그나 예외 메시지에 그대로 노출하지 않는다(있음/없음 여부만 알린다).
- STEP 2에서 결정한 `real_github_api`의 `rate_limit` 값이 이 STEP의 토큰 유무 전제와
  맞물려야 한다 — 서로 다른 프롬프트에서 작업했다면 마지막에 정합성을 재확인한다.
