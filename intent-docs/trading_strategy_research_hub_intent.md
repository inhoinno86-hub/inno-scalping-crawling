# 초단기 트레이딩 전략 리서치 허브 — 프로젝트 개발 개요 및 구현 의도

> 본 문서는 Codex 또는 기타 개발 에이전트에게 프로젝트의 목적, 범위, 설계 원칙, 권장 구조, 구현 우선순위 및 완료 기준을 전달하기 위한 기준 문서이다.  
> 이 프로젝트의 핵심은 공개 자료에서 주식·암호화폐·선물 등의 초단기 트레이딩 전략 관련 정보를 주기적으로 수집하고, 동일한 기준으로 구조화하여 검색·열람·공유·알림할 수 있는 웹앱을 구축하는 것이다.

---

## 1. 프로젝트 한 줄 정의

공개된 웹 자료, 논문, 오픈소스 저장소, 기술 문서 및 공식 게시물을 주기적으로 수집하여 초단기 트레이딩 전략 중심의 구조화된 `Strategy Registry`를 만들고, 이를 웹 대시보드·검색·정기 보고·알림 형태로 제공하는 리서치 허브를 구축한다.

---

## 2. 프로젝트 목적

본 프로젝트는 초단기 스캘핑 전략을 자동으로 실행하거나 수익성을 검증하는 시스템이 아니다.

주요 목적은 다음과 같다.

1. 공개된 전략 자료를 여러 출처에서 지속적으로 수집한다.
2. 수집 자료에서 전략의 핵심 개념, 적용 시장, 데이터 요구사항, 진입·청산 논리, 위험요소 등을 구조화한다.
3. 동일하거나 유사한 전략을 중복 제거하고 서로 연결한다.
4. 원문 출처와 요약·해석을 분리하여 추적 가능성을 확보한다.
5. 사용자가 전략 자료를 검색, 필터링, 비교, 즐겨찾기 및 검토할 수 있도록 한다.
6. 신규 자료, 기존 전략의 변경, 특정 키워드 또는 관심 전략과 관련된 업데이트를 주기적으로 보고한다.
7. 실제 백테스트나 추가 정량 검증을 수행할 별도 프로젝트가 활용할 수 있도록 구조화된 데이터를 제공한다.

---

## 3. 핵심 범위

### 3.1 포함 범위

- 공개 웹페이지, 공식 문서, RSS, GitHub 저장소, 논문 메타데이터 등의 수집
- 출처별 수집 정책 및 스케줄 관리
- 원문 또는 원문 스냅샷 저장
- 본문 추출 및 정제
- 중복 문서 및 유사 전략 식별
- 초단기 트레이딩 전략 중심의 자동 분류
- LLM 기반 전략 정보 추출 및 요약
- 사람이 검토할 수 있는 승인·수정 워크플로
- Strategy Registry 구축
- 웹 대시보드
- 전문 검색 및 필터
- 전략 상세 페이지
- 원문 근거 표시
- 신규 자료 및 변경사항 알림
- 일간·주간·월간 리포트
- 외부 프로젝트가 사용할 수 있는 API 또는 데이터 내보내기
- 데이터 수집 상태 및 오류 모니터링

### 3.2 제외 범위

다음 항목은 본 프로젝트에서 구현하지 않는다.

- 자동 매매
- 거래소 또는 증권사 주문 연동
- 실거래 계좌 연결
- 전략 수익성 보증
- 자동 백테스트
- 호가 재생
- 슬리피지 및 체결 시뮬레이션
- 포트폴리오 최적화
- 투자 추천
- 개인별 종목 추천
- 매수·매도 신호 제공
- 사용자 자금 운용
- 자동 생성 전략의 실행 가능성 보증

실제 백테스트, 틱·호가 검증, 페이퍼 트레이딩 및 실거래 검증은 별도 프로젝트에서 수행한다.

---

## 4. 프로젝트의 핵심 산출물

### 4.1 Source Registry

수집 대상과 정책을 관리하는 레지스트리이다.

각 출처는 최소한 다음 정보를 가진다.

- 출처 이름
- 출처 유형
- 기본 URL
- API 또는 RSS 사용 가능 여부
- 수집 방식
- 수집 주기
- 허용된 접근 범위
- robots.txt 확인 결과
- 이용약관 및 라이선스 참고 정보
- 요청 제한
- 마지막 수집 시각
- 다음 예정 수집 시각
- 수집 활성화 여부
- 오류 상태
- 신뢰도 등급
- 담당 커넥터

### 4.2 Document Registry

수집된 개별 원문을 관리한다.

- 문서 ID
- 출처 ID
- 원문 URL
- canonical URL
- 제목
- 저자 또는 조직
- 게시일
- 최초 수집일
- 최종 확인일
- 언어
- 문서 유형
- 라이선스
- 원문 해시
- 본문 해시
- 버전
- 변경 여부
- 원문 저장 위치
- 추출 본문 저장 위치
- 수집 상태
- 처리 상태
- 파싱 오류
- 원문 접근 가능 여부

### 4.3 Strategy Registry

프로젝트의 최종 핵심 데이터 계층이다.

수집된 문서에서 식별된 전략 개념을 구조화하여 저장한다.

---

## 5. Strategy Registry 권장 스키마

### 5.1 식별 정보

- `strategy_id`
- `canonical_name`
- `aliases`
- `slug`
- `summary_short`
- `summary_detailed`
- `status`
- `created_at`
- `updated_at`
- `reviewed_at`
- `reviewed_by`

### 5.2 분류 정보

- `asset_classes`
  - equity
  - crypto
  - futures
  - options
  - forex
  - mixed
- `market_types`
  - spot
  - futures
  - perpetual
  - options
- `strategy_families`
  - momentum
  - mean_reversion
  - market_making
  - arbitrage
  - breakout
  - order_flow
  - liquidity
  - volatility
  - event_driven
  - statistical
  - machine_learning
  - hybrid
- `holding_horizon`
  - sub_second
  - seconds
  - under_1_minute
  - 1_to_5_minutes
  - 5_to_30_minutes
  - intraday
- `market_microstructure_level`
  - OHLCV
  - trades
  - L1
  - L2
  - L3
- `venues`
- `symbols`
- `regions`
- `languages`
- `tags`

### 5.3 전략 논리

- `core_hypothesis`
- `market_inefficiency`
- `signal_inputs`
- `entry_logic`
- `exit_logic`
- `position_direction`
- `order_types`
- `position_sizing_description`
- `risk_controls`
- `time_filters`
- `liquidity_conditions`
- `volatility_conditions`
- `market_regime_assumptions`
- `required_features`
- `required_data`
- `required_frequency`
- `required_latency`
- `parameters`
- `parameter_ranges`

### 5.4 증거 및 출처

- `source_documents`
- `primary_source`
- `source_quotes`
- `source_sections`
- `source_code_repositories`
- `papers`
- `authors`
- `organizations`
- `publication_dates`
- `evidence_level`
- `source_confidence`
- `extraction_confidence`

### 5.5 제한사항 및 검토 포인트

- `known_limitations`
- `unknowns`
- `ambiguities`
- `implementation_risks`
- `data_availability_risks`
- `execution_risks`
- `overfitting_risks`
- `regulatory_notes`
- `license_notes`
- `review_notes`

### 5.6 외부 검증 프로젝트 연계

본 프로젝트는 백테스트를 수행하지 않지만, 후속 프로젝트가 활용할 수 있도록 다음 필드를 제공한다.

- `validation_status`
  - not_reviewed
  - needs_clarification
  - ready_for_external_validation
  - sent_to_validation_project
  - externally_validated
  - externally_rejected
- `external_validation_reference`
- `export_schema_version`
- `machine_readable_spec`
- `strategy_spec_json`
- `strategy_spec_yaml`
- `recommended_validation_data`
- `recommended_validation_checks`

---

## 6. 처리 파이프라인

```text
Source Registry
    ↓
Scheduled Collection
    ↓
Raw Content Storage
    ↓
Content Extraction
    ↓
Normalization
    ↓
Document Deduplication
    ↓
Relevance Classification
    ↓
Strategy Extraction
    ↓
Strategy Similarity Matching
    ↓
Human Review
    ↓
Strategy Registry
    ↓
Dashboard / Search / Alerts / Reports / API Export
```

---

## 7. 수집 계층 설계

### 7.1 수집 우선순위

수집 방식은 다음 순서로 선택한다.

1. 공식 API
2. 공식 RSS 또는 Atom Feed
3. 공개 데이터 덤프
4. Git 저장소 동기화
5. 허용된 웹 크롤링
6. 브라우저 기반 렌더링이 필요한 페이지

API나 RSS가 있는 경우 HTML 크롤링을 우선하지 않는다.

### 7.2 초기 권장 출처

#### 연구 및 논문

- arXiv
- Crossref
- Semantic Scholar
- SSRN의 공개 접근 가능 자료
- 학회 또는 연구기관의 공식 공개 자료

#### 오픈소스 및 코드

- GitHub
- GitLab 공개 저장소
- 공개 문서 사이트
- 프로젝트 릴리스 노트
- 이슈 및 Discussion 중 전략 설명이 포함된 자료

#### 공식 시장 및 기술 문서

- 거래소 공식 연구 게시물
- 거래소 API 문서
- 브로커 공식 개발 문서
- 시장 미세구조 관련 공식 설명
- 데이터 공급자의 공개 기술 문서

#### 커뮤니티 및 블로그

초기에는 신뢰도와 권리 관계를 관리하기 어려우므로 제한적으로 포함한다.

- 공식 기업 블로그
- 작성자와 라이선스가 명확한 개인 기술 블로그
- 공개 전략 설명 페이지
- RSS가 제공되는 사이트

사용자 생성 콘텐츠를 수집할 때는 원문 전체 재게시를 피하고, 요약과 출처 링크 중심으로 저장한다.

### 7.3 수집 스케줄

기본 스케줄 예시:

- RSS: 1~6시간
- GitHub release: 6시간
- GitHub repository metadata: 12~24시간
- arXiv: 24시간
- 일반 웹페이지: 24시간~7일
- 변경 가능성이 낮은 문서: 30일
- 삭제 또는 접근불가 확인: 7일~30일

출처별로 독립적인 스케줄을 설정할 수 있어야 한다.

### 7.4 증분 수집

매번 전체를 다시 수집하지 않는다.

다음 값을 이용한다.

- ETag
- Last-Modified
- commit SHA
- release ID
- publication ID
- DOI
- canonical URL
- content hash
- body hash
- source-specific cursor
- last seen timestamp

---

## 8. 원문 저장 원칙

### 8.1 저장 계층

- 메타데이터: PostgreSQL
- 원문 HTML 또는 JSON: 객체 스토리지
- 정제된 Markdown 또는 텍스트: 객체 스토리지
- 첨부 PDF: 객체 스토리지
- 이미지: 필요 최소 범위
- 코드 스냅샷: 원본 저장소 링크 및 commit hash 우선
- 검색용 색인: PostgreSQL Full Text Search 또는 OpenSearch
- 임베딩: pgvector

### 8.2 원문 추적성

모든 요약과 전략 필드는 원문 근거로 역추적할 수 있어야 한다.

최소 요건:

- 문서 ID
- 원문 URL
- 문서 버전
- 본문 위치 또는 섹션
- 근거 문장
- 추출 시각
- 사용 모델
- 프롬프트 버전
- 추출 결과 버전

### 8.3 변경 이력

동일 URL의 내용이 변경되면 기존 데이터를 덮어쓰지 않는다.

- 이전 버전 유지
- 새 버전 생성
- 변경된 문단 식별
- Strategy Registry 영향 여부 판단
- 변경 알림 생성

---

## 9. 문서 처리 및 정규화

### 9.1 기본 처리

- HTML 본문 추출
- 메뉴, 광고, 댓글, 반복 푸터 제거
- Markdown 변환
- 언어 감지
- 제목·저자·게시일 추출
- 코드 블록 보존
- 표 구조 보존
- 링크 정규화
- 추적 파라미터 제거
- canonical URL 결정
- 문서 해시 생성

### 9.2 중복 제거

세 가지 수준에서 수행한다.

1. 정확 중복
   - 동일 URL
   - 동일 DOI
   - 동일 commit SHA
   - 동일 content hash

2. 근접 중복
   - 제목 유사도
   - 본문 MinHash 또는 SimHash
   - 동일 문서의 번역본 또는 재게시본

3. 전략 중복
   - 서로 다른 문서가 동일 전략을 설명하는 경우
   - 신규 Strategy를 생성하지 않고 기존 Strategy에 근거 문서를 추가
   - 핵심 논리 또는 조건이 다르면 Variant로 관리

---

## 10. 전략 관련성 분류

모든 수집 문서를 Strategy Registry 후보로 넣지 않는다.

### 10.1 관련성 분류 결과

- `relevant`
- `partially_relevant`
- `background_only`
- `not_relevant`
- `uncertain`

### 10.2 주요 관련성 기준

- 실제 진입 또는 청산 규칙이 있는가
- 신호 계산 방법이 있는가
- 적용 자산 또는 시장이 명시되어 있는가
- 초단기 또는 장중 전략과 관련되는가
- 주문 흐름, 호가, 거래량, 변동성 또는 시장 미세구조를 다루는가
- 재현 가능한 데이터 요구사항이 설명되어 있는가
- 단순 시장 전망 또는 홍보성 콘텐츠가 아닌가

### 10.3 초단기 전략 판정

다음 중 하나 이상을 만족하면 초단기 후보로 분류할 수 있다.

- 보유시간이 수초에서 수십 분
- 틱, 거래 체결, L1, L2 또는 L3 데이터 사용
- 호가 불균형, 주문흐름, 스프레드, 큐, 체결강도 등을 사용
- 시장조성, 단기 차익거래, 뉴스 반응, 짧은 평균회귀
- 장중 고빈도 진입과 청산
- 짧은 시간 프레임의 브레이크아웃 또는 모멘텀

---

## 11. LLM 기반 추출 설계

### 11.1 LLM의 역할

LLM은 다음 업무에 사용한다.

- 문서 관련성 판정
- 전략명 후보 생성
- 전략 요약
- 전략 구성요소 추출
- 근거 문장 연결
- 불명확한 항목 식별
- 유사 전략 후보 검색
- 태그 및 분류 생성
- 변경 문서의 영향 요약
- 일간·주간 보고서 작성

### 11.2 LLM이 하지 않아야 할 것

- 원문에 없는 전략 규칙 생성
- 누락된 파라미터 임의 보완
- 수익성을 추정
- 실제 성과를 단정
- 투자 추천
- 백테스트 결과 생성
- 근거 없는 위험도 판정
- 코드 실행 가능성을 보증

### 11.3 추출 원칙

각 추출 필드는 다음 상태 중 하나를 가져야 한다.

- `explicit`: 원문에 명확히 존재
- `inferred`: 여러 문장을 종합해 제한적으로 추론
- `unknown`: 확인할 수 없음
- `conflicting`: 출처 간 충돌
- `not_applicable`: 해당 없음

`unknown` 값을 허용하고 임의로 채우지 않는다.

### 11.4 구조화 출력

LLM 결과는 반드시 JSON Schema 검증을 통과해야 한다.

권장 흐름:

```text
LLM Raw Output
    ↓
JSON Schema Validation
    ↓
Normalization
    ↓
Evidence Validation
    ↓
Confidence Scoring
    ↓
Human Review Queue
```

---

## 12. 사람 검토 워크플로

초기에는 완전 자동 공개를 허용하지 않는다.

### 12.1 검토 상태

- `new`
- `auto_extracted`
- `needs_review`
- `reviewing`
- `approved`
- `rejected`
- `needs_update`
- `archived`

### 12.2 검토 화면 기능

- 원문과 추출 결과 나란히 보기
- 필드별 근거 문장 표시
- 잘못된 추출 수정
- 기존 전략과 병합
- 새로운 Variant 생성
- 태그 수정
- 신뢰도 수정
- 공개 여부 결정
- 검토 코멘트 기록
- 변경 이력 확인

### 12.3 공개 기준

다음 조건을 만족한 전략만 기본적으로 공개한다.

- 출처가 확인됨
- 원문 링크가 존재함
- 최소 1개 이상의 근거 문장이 있음
- 핵심 전략 설명이 원문과 일치함
- 불명확한 항목이 명시됨
- 라이선스 또는 재사용 관련 위험이 기록됨
- 투자 추천처럼 표현되지 않음
- 검토 상태가 `approved`

---

## 13. 웹 대시보드

### 13.1 메인 대시보드

- 최근 수집 자료
- 신규 Strategy
- 업데이트된 Strategy
- 관심 키워드 동향
- 출처별 신규 문서 수
- 자산군별 신규 전략 수
- 전략 유형별 분포
- 검토 대기 항목
- 수집 오류
- 최근 주간 리포트

### 13.2 전략 목록

지원 필터:

- 자산군
- 시장 유형
- 전략군
- 보유시간
- 데이터 수준
- 거래소
- 언어
- 출처
- 게시일
- 최근 업데이트일
- 검토 상태
- 증거 수준
- 태그
- 키워드

정렬:

- 최신 등록
- 최신 변경
- 출처 수
- 검토 신뢰도
- 사용자 즐겨찾기 수
- 관련 문서 수

### 13.3 전략 상세 페이지

- 전략명
- 한 줄 요약
- 상세 요약
- 핵심 가설
- 진입·청산 논리
- 필요한 데이터
- 적용 시장
- 조건 및 파라미터
- 한계와 불명확한 부분
- 연관 전략
- 변형 전략
- 관련 문서
- 원문 근거
- 변경 이력
- 외부 검증 상태
- JSON/YAML 내보내기
- 즐겨찾기
- 알림 설정

### 13.4 문서 상세 페이지

- 원문 메타데이터
- 원문 링크
- 추출된 본문
- 요약
- 관련 Strategy
- 문서 버전
- 변경 내용
- 인용 정보
- 라이선스 정보
- 수집 기록

---

## 14. 검색 설계

### 14.1 검색 방식

- 키워드 검색
- 필드 검색
- 태그 검색
- 전문 검색
- 의미 기반 검색
- 관련 전략 추천
- 유사 문서 검색

### 14.2 검색 결과의 기본 원칙

- Strategy 결과와 Document 결과를 구분
- 원문 출처 표시
- 검색 결과가 생성형 답변보다 우선
- 요약문만으로 결과를 구성하지 않음
- 최신성과 원문 신뢰도를 함께 표시
- 의미 검색 결과에는 유사도와 선택 이유를 내부적으로 기록

### 14.3 초기 구현 권장

MVP에서는 다음을 우선한다.

1. PostgreSQL Full Text Search
2. 필드 필터
3. 태그 검색
4. pgvector 의미 검색

OpenSearch는 데이터량과 검색 요구가 증가한 후 도입한다.

---

## 15. 알림 및 정기 보고

### 15.1 알림 대상

- 신규 전략 등록
- 관심 전략 업데이트
- 관심 키워드 신규 문서
- 특정 출처 신규 게시물
- 관련 GitHub release
- 기존 전략의 핵심 조건 변경
- 새로운 관련 논문
- 문서 삭제 또는 접근 불가
- 수집 장애
- 검토 요청

### 15.2 알림 채널

초기:

- 웹앱 알림 센터
- 이메일
- Telegram 또는 Slack Webhook 중 하나

확장:

- Discord
- Microsoft Teams
- 모바일 Push

### 15.3 정기 보고서

- 일간 신규 자료 요약
- 주간 전략 리서치 브리핑
- 월간 전략 분류 동향
- 출처별 주요 업데이트
- 관심 태그별 신규 자료
- 기존 전략 변경 요약
- 검토 대기 목록
- 수집 품질 리포트

### 15.4 보고서 생성 원칙

- 원문 링크 포함
- 신규와 업데이트를 구분
- 중복 내용 제거
- 투자 추천 표현 금지
- 확인되지 않은 내용 표시
- 중요 변경의 근거 문장 표시
- 생성 시각과 데이터 기준 시각 표시

---

## 16. 외부 프로젝트 연계

본 프로젝트는 별도 백테스트 또는 검증 프로젝트와 느슨하게 연결한다.

### 16.1 연계 방법

- REST API
- JSON export
- YAML export
- NDJSON batch export
- S3 호환 객체 스토리지
- 이벤트 메시지
- 검증 요청 큐

### 16.2 권장 API

```text
GET  /api/v1/strategies
GET  /api/v1/strategies/{strategy_id}
GET  /api/v1/strategies/{strategy_id}/sources
GET  /api/v1/strategies/{strategy_id}/export
GET  /api/v1/documents
GET  /api/v1/documents/{document_id}
GET  /api/v1/reports
POST /api/v1/validation-requests
GET  /api/v1/changes
```

### 16.3 설계 원칙

- 검증 프로젝트의 데이터 모델을 본 프로젝트에 강하게 결합하지 않는다.
- `schema_version`을 명시한다.
- 필드 추가는 하위 호환성을 유지한다.
- Strategy Registry는 사실 및 출처 중심으로 유지한다.
- 백테스트 성과는 외부 참조로 저장한다.
- 외부 검증 결과는 원본 전략 정의를 덮어쓰지 않는다.

---

## 17. 권장 기술 스택

### 17.1 백엔드

- Python 3.12 이상
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- Celery 또는 Dramatiq
- Redis

### 17.2 수집

- Scrapy
- httpx
- feedparser
- BeautifulSoup 또는 selectolax
- Playwright는 필요한 출처에 한정
- GitHub API
- arXiv API
- Crossref API
- 기타 출처별 커넥터

### 17.3 워크플로

초기 선택지:

- Celery Beat 또는 APScheduler
- Docker Compose

확장 선택지:

- Apache Airflow
- Temporal

MVP에서는 복잡한 Airflow 구축보다 작업 큐와 스케줄러로 시작할 수 있다. 다만 출처 수, 재시도 정책, 의존성 및 배치 관찰성이 커지면 Airflow로 전환한다.

### 17.4 데이터

- PostgreSQL
- pgvector
- Redis
- MinIO 또는 S3
- PostgreSQL Full Text Search

### 17.5 프론트엔드

- Next.js
- TypeScript
- React
- TanStack Query
- Tailwind CSS
- shadcn/ui 또는 동급 컴포넌트
- ECharts 또는 Recharts

### 17.6 인증

초기:

- 단일 관리자 또는 소수 사용자
- 이메일 기반 로그인
- 관리자/검토자/일반 사용자 역할

확장:

- OAuth
- 조직 단위 권한
- SSO

### 17.7 배포

초기:

- Docker Compose
- 단일 VM
- Managed PostgreSQL 또는 로컬 PostgreSQL
- S3 호환 스토리지
- Nginx 또는 Caddy

확장:

- Kubernetes는 실제 운영 규모가 필요한 경우만 도입
- CI/CD는 GitHub Actions
- IaC는 Terraform

---

## 18. 권장 서비스 구성

```text
apps/
  web/                 # Next.js 웹앱
  api/                 # FastAPI API
  worker/              # 비동기 작업 및 처리
  scheduler/           # 수집 스케줄 관리

packages/
  schemas/             # 공통 JSON Schema / Pydantic 모델
  source-connectors/   # 출처별 커넥터
  content-extractors/  # HTML/PDF/Markdown 추출
  strategy-extractor/  # LLM 기반 추출
  search/              # 전문 검색 및 의미 검색
  reporting/           # 정기 보고 생성
  notifications/       # 이메일/메신저 알림
  common/              # 로깅, 설정, 유틸리티

infra/
  docker/
  migrations/
  monitoring/
  scripts/

docs/
  architecture/
  schemas/
  source-policies/
  prompts/
  adr/
```

---

## 19. 데이터베이스 핵심 테이블

초기 핵심 테이블:

- `sources`
- `source_collection_runs`
- `documents`
- `document_versions`
- `document_contents`
- `document_links`
- `strategies`
- `strategy_versions`
- `strategy_sources`
- `strategy_tags`
- `strategy_variants`
- `strategy_evidence`
- `strategy_reviews`
- `strategy_exports`
- `watchlists`
- `subscriptions`
- `notifications`
- `reports`
- `jobs`
- `llm_runs`
- `prompt_versions`
- `users`
- `roles`
- `audit_logs`

---

## 20. 신뢰도 및 품질 관리

### 20.1 출처 신뢰도

예시 등급:

- A: 공식 거래소, 공식 문서, peer-reviewed 논문
- B: 기관 연구, 저자 식별 논문, 유지관리되는 오픈소스
- C: 작성자 식별 기술 블로그, 공개 발표 자료
- D: 커뮤니티 글, 출처 불명확 자료
- X: 수집 제외

출처 신뢰도는 전략 수익성 평가가 아니라 정보 출처의 추적 가능성과 신뢰성을 의미한다.

### 20.2 추출 신뢰도

- 원문 내 명시 여부
- 근거 문장 존재 여부
- 여러 출처 간 일치 여부
- 수식 및 파라미터 완전성
- 전략 논리의 일관성
- 사람 검토 여부

### 20.3 품질 지표

- 수집 성공률
- 중복률
- 파싱 실패율
- 관련성 분류 정확도
- 전략 필드 누락률
- 근거 없는 추출 비율
- 사람 수정 비율
- 알림 오탐률
- 보고서 중복률
- 평균 처리 지연
- 출처별 오류율

---

## 21. 저작권·라이선스·운영 원칙

### 21.1 기본 원칙

- 공개되어 있다는 이유만으로 전체 본문을 재배포하지 않는다.
- 원문 링크와 출처를 명확히 표시한다.
- API 약관과 robots.txt를 준수한다.
- 사이트별 이용약관을 기록한다.
- 코드의 라이선스를 확인한다.
- 원문 전체 대신 요약과 제한된 인용을 사용한다.
- 삭제 요청 및 수집 제외 절차를 제공한다.
- 유료 데이터와 재배포 제한 데이터는 별도로 관리한다.
- 접근 제한을 우회하지 않는다.
- CAPTCHA 또는 로그인 우회를 구현하지 않는다.
- 민감정보나 개인정보를 불필요하게 저장하지 않는다.

### 21.2 표시 원칙

웹앱에는 다음을 명확히 표시한다.

- 본 서비스는 투자자문 또는 투자추천 서비스가 아님
- 수집·요약 정보는 오류가 있을 수 있음
- 원문 확인이 필요함
- 전략의 수익성은 검증되지 않음
- 과거 자료가 미래 결과를 보장하지 않음
- 실제 적용 전 별도 검증이 필요함

---

## 22. 보안 설계

- 외부 콘텐츠를 신뢰하지 않는다.
- HTML과 Markdown을 sanitize한다.
- 수집 콘텐츠의 스크립트를 실행하지 않는다.
- GitHub 저장소 코드를 자동 실행하지 않는다.
- PDF, 압축 파일, 코드 첨부는 격리 저장한다.
- LLM Prompt Injection을 외부 문서의 지시로 간주하고 무시한다.
- 수집 문서와 시스템 프롬프트를 분리한다.
- API 키를 비밀 저장소에 보관한다.
- 작업별 네트워크 접근을 제한한다.
- 관리자 작업은 감사 로그를 남긴다.
- 파일 크기, 요청 횟수 및 처리 시간을 제한한다.
- SSRF 방어를 적용한다.
- 허용 도메인 및 차단 도메인 정책을 제공한다.

---

## 23. 관측성과 운영

### 23.1 필수 로그

- 수집 시작 및 종료
- 요청 URL
- 응답 상태
- 재시도 횟수
- 수집 데이터 크기
- 파싱 결과
- 중복 판정
- LLM 호출
- 검토 상태 변경
- 알림 전송
- 보고서 생성
- 외부 API export
- 오류 stack trace

### 23.2 필수 메트릭

- 출처별 수집 성공률
- 시간당 신규 문서 수
- 처리 대기열 길이
- 평균 처리 시간
- LLM 비용
- 토큰 사용량
- 추출 실패율
- 알림 실패율
- API 응답 시간
- DB 및 객체 스토리지 사용량

### 23.3 권장 도구

- OpenTelemetry
- Prometheus
- Grafana
- Sentry
- 구조화 JSON 로그

---

## 24. 구현 단계

### Phase 0. 프로젝트 기준 확정

목표:

- 범위 고정
- 초기 출처 선정
- Strategy Schema 확정
- 법적·운영 원칙 정의

산출물:

- 프로젝트 README
- 아키텍처 문서
- Source Policy
- Strategy JSON Schema
- ADR 문서
- 데이터 보존 정책

### Phase 1. 수집 및 문서 레지스트리 MVP

구현:

- Source Registry
- RSS 커넥터
- GitHub 커넥터
- arXiv 커넥터
- 일반 HTML 커넥터
- raw 저장
- 본문 추출
- 문서 중복 제거
- 수집 관리 화면

완료 기준:

- 최소 5개 출처를 안정적으로 정기 수집
- 중복 문서 방지
- 수집 실패 재시도
- 원문과 정제 본문 추적 가능

### Phase 2. Strategy Registry MVP

구현:

- 관련성 분류
- 전략 추출
- 근거 문장 추출
- Strategy 생성
- 유사 Strategy 후보
- 사람 검토 화면
- 승인 및 수정 이력

완료 기준:

- 하나의 문서에서 Strategy 후보를 JSON Schema에 맞게 생성
- 근거 문장 없는 필드는 공개하지 않음
- 기존 Strategy에 새로운 문서를 연결 가능
- Strategy Version 관리 가능

### Phase 3. 웹 검색 및 대시보드

구현:

- 전략 목록
- 전략 상세
- 문서 상세
- 필터
- 전문 검색
- 의미 검색
- 즐겨찾기
- 최근 변경 대시보드

완료 기준:

- 자산군, 전략군, 보유시간, 데이터 수준별 검색 가능
- 원문 근거까지 이동 가능
- 신규와 업데이트 구분 가능

### Phase 4. 알림 및 정기 보고

구현:

- 관심 키워드
- Watchlist
- 이메일 또는 Telegram
- 일간 및 주간 보고
- 변경 기반 알림
- 전송 이력

완료 기준:

- 사용자가 관심 조건을 저장 가능
- 중복 알림 방지
- 보고서에서 모든 항목의 원문 확인 가능

### Phase 5. 외부 검증 프로젝트 연계

구현:

- Strategy export API
- JSON/YAML export
- schema version
- validation request
- 외부 검증 상태 참조

완료 기준:

- 외부 프로젝트가 Strategy 정의와 출처를 기계적으로 읽을 수 있음
- 검증 결과가 원본 Strategy를 변경하지 않음

---

## 25. MVP의 현실적인 최소 범위

초기 MVP에서는 범위를 다음과 같이 제한한다.

### 출처

- GitHub
- arXiv
- RSS
- 허용된 일반 웹사이트 2~3개

### 자산군

- crypto
- futures
- equity

### 전략군

- momentum
- mean reversion
- market making
- arbitrage
- order flow
- breakout

### 사용자 기능

- 관리자
- 검토자
- 일반 열람자

### 알림

- 이메일 또는 Telegram 하나

### 배포

- Docker Compose
- 단일 서버
- PostgreSQL
- MinIO
- Redis

### 제외

- 멀티테넌시
- 모바일 앱
- 실시간 틱 수집
- 자동 코드 실행
- 자동 백테스트
- 거래소 주문
- 사용자 과금

---

## 26. 개발 원칙

1. 원문 추적성을 기능보다 우선한다.
2. Strategy와 Document를 분리한다.
3. 자동 추출과 사람 검토를 분리한다.
4. `unknown`을 허용한다.
5. 원문에 없는 내용을 생성하지 않는다.
6. 중복 Strategy 생성을 최소화한다.
7. 모든 변경은 버전으로 관리한다.
8. 백테스트나 수익성 평가를 본 프로젝트에 혼합하지 않는다.
9. 외부 검증 프로젝트와 느슨하게 결합한다.
10. 초기에는 단순한 모놀리식 구조를 선호한다.
11. 실제 필요가 확인되기 전 마이크로서비스를 도입하지 않는다.
12. 데이터 출처별 정책을 코드와 문서에 함께 남긴다.
13. 외부 콘텐츠는 보안상 신뢰하지 않는다.
14. UI는 전략을 추천하는 방식이 아니라 정보를 탐색하는 방식으로 설계한다.
15. 최신 문서와 과거 문서를 구분한다.
16. 삭제 또는 변경된 원문도 이력으로 보존하되 공개 정책은 별도로 적용한다.

---

## 27. Codex 구현 지침

Codex는 다음 순서를 우선한다.

1. 현재 저장소 구조를 분석한다.
2. 이 문서를 기준으로 범위 밖 기능을 식별한다.
3. 구현 전에 데이터 모델과 JSON Schema를 제안한다.
4. 최소 단위의 vertical slice를 우선 구현한다.
5. 한 번에 여러 인프라를 도입하지 않는다.
6. 모든 외부 커넥터에 timeout, retry, rate limit을 적용한다.
7. 모든 LLM 출력은 schema validation을 거친다.
8. 추출 결과에는 evidence를 필수로 연결한다.
9. 데이터베이스 마이그레이션을 포함한다.
10. 테스트 없이 핵심 파이프라인을 완료 처리하지 않는다.
11. 단위 테스트, 통합 테스트, fixture를 함께 작성한다.
12. 실제 API 키가 없어도 mock 또는 fixture로 테스트 가능하게 한다.
13. 개발 환경은 Docker Compose 한 번으로 실행 가능하게 한다.
14. README에 실행, 테스트, 마이그레이션, 수집 실행 방법을 기록한다.
15. 주요 설계 결정은 `docs/adr`에 기록한다.
16. Scope 밖 기능은 구현하지 않고 별도 TODO 또는 후속 프로젝트로 남긴다.

---

## 28. 첫 번째 Vertical Slice

Codex가 가장 먼저 구현할 권장 단위는 다음과 같다.

```text
GitHub Repository Source
    ↓
Repository Metadata Collection
    ↓
README Extraction
    ↓
Document Registry 저장
    ↓
관련성 분류
    ↓
Strategy 후보 추출
    ↓
사람 검토
    ↓
Strategy Registry 승인
    ↓
웹 상세 페이지 표시
```

### 첫 번째 Slice 완료 조건

- 관리자가 GitHub 저장소 URL을 등록할 수 있음
- 스케줄 또는 수동 실행으로 README와 메타데이터를 수집함
- 수집 결과를 Document로 저장함
- README 변경 시 새 버전을 생성함
- LLM이 Strategy 후보를 구조화된 JSON으로 생성함
- 필드별 근거 문장을 함께 저장함
- 검토자가 수정 및 승인할 수 있음
- 승인된 Strategy가 웹 상세 페이지에 표시됨
- 원문 GitHub URL과 commit SHA를 확인할 수 있음
- 같은 저장소를 반복 수집해도 불필요한 중복이 생성되지 않음

---

## 29. 테스트 전략

### 29.1 단위 테스트

- URL 정규화
- 해시 생성
- 문서 중복 판정
- Strategy schema validation
- 출처별 parser
- 태그 정규화
- 상태 전이
- 알림 중복 방지

### 29.2 통합 테스트

- 수집 → 저장 → 추출 → 검토 → 공개
- 변경된 문서 버전 생성
- 기존 Strategy에 신규 문서 연결
- 외부 API export
- 보고서 생성
- 장애 재시도

### 29.3 안전성 테스트

- 악성 HTML
- Prompt Injection 포함 문서
- 대용량 문서
- 무한 redirect
- SSRF 후보 URL
- 잘못된 MIME
- 손상 PDF
- rate limit 응답
- API timeout

### 29.4 회귀 테스트

대표 문서 fixture를 보존하고 다음을 비교한다.

- 관련성 판정
- Strategy 필드
- 근거 문장
- 태그
- 기존 Strategy 매칭
- 추출 confidence

---

## 30. Definition of Done

기능은 다음 조건을 모두 만족해야 완료로 간주한다.

- 요구사항 충족
- Scope 밖 기능 미포함
- 데이터 모델 및 마이그레이션 포함
- 입력 검증 포함
- 오류 처리 포함
- 구조화 로그 포함
- 테스트 포함
- 문서화 포함
- 원문 추적 가능
- 권한 검토 완료
- 보안 기본 검토 완료
- 운영자가 상태를 확인할 수 있음
- 재실행 시 중복 또는 불일치가 발생하지 않음
- LLM 출력이 schema validation을 통과함
- 근거 없는 자동 생성 필드가 공개되지 않음

---

## 31. 성공 판단 기준

프로젝트의 성공은 전략 수익률로 평가하지 않는다.

다음 지표로 평가한다.

- 신뢰할 수 있는 출처를 안정적으로 수집하는가
- 전략 자료를 일관된 구조로 변환하는가
- 모든 핵심 정보가 원문으로 추적 가능한가
- 동일 전략의 중복 생성을 억제하는가
- 신규 및 변경 자료를 빠르게 파악할 수 있는가
- 사용자가 필요한 전략을 검색하고 비교할 수 있는가
- 정기 보고가 중복 없이 유용한가
- 외부 검증 프로젝트가 데이터를 재사용할 수 있는가
- 운영자가 수집 실패와 추출 오류를 확인할 수 있는가
- 시스템이 투자 추천으로 오인되지 않는가

---

## 32. 최종 프로젝트 방향

본 프로젝트는 트레이딩 시스템이 아니라 `Strategy Intelligence and Knowledge Management System`으로 설계한다.

핵심 흐름은 다음과 같다.

```text
수집
→ 정제
→ 출처 검증
→ 전략 구조화
→ 중복 및 관계 분석
→ 사람 검토
→ Registry 등록
→ 검색·열람·정기 보고
→ 외부 검증 프로젝트로 전달
```

가장 중요한 설계 기준은 다음 세 가지이다.

1. **모든 전략 정보는 원문 근거와 연결되어야 한다.**
2. **확인할 수 없는 정보는 임의로 채우지 않고 `unknown`으로 남겨야 한다.**
3. **백테스트와 투자 판단은 본 프로젝트의 책임 범위 밖에 있어야 한다.**

이 기준을 유지하면 본 프로젝트는 시간이 지날수록 전략 자료가 축적되고, 새로운 자료와 기존 전략의 관계를 지속적으로 정리하며, 별도 검증 프로젝트가 재사용할 수 있는 고품질 Strategy Registry로 발전할 수 있다.
