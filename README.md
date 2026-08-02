# scalping-briefing

Offline-first research collection foundation for a short-horizon scalping strategy briefing system.

## Scope

This repository implements Phase 0 + Phase 1 only:

- source policy, fixture collection, request safety boundaries, robots/access metadata, retry primitives, sanitization, URL normalization, and append-only document Versions;
- local review API scaffolding and publication/delivery safety guards;
- an offline `run-briefing` collection vertical slice that ends in `dry_run`.

Phase 2 strategy classification/extraction, Phase 3 briefing scheduling and delivery, and Phase 4 operational dashboards are deferred. There is no order execution, trading engine, backtest, portfolio optimization, live LLM path, live delivery path, or live source activation.

## Setup

Use Python 3.11+ and the repository virtual environment. Dependencies are declared in `pyproject.toml`; no runtime dependency is added by the fixture connectors.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

The standard test and run commands need no network, Docker, API key, or external service:

```bash
make test
make run-briefing
make review-api
```

`make run-briefing` reads the five active fixture sources, sanitizes and normalizes collected content, writes raw/normalized document bodies below `storage/`, and persists append-only `DocumentVersion` records in the configured SQLite database. It prints a JSON dry-run summary and does not generate a briefing or invoke delivery. Runtime artifacts are ignored by Git.

Postgres or real-network checks belong to tests marked `integration`; the default `make test` command excludes them.

## Source Policy

`config/source-policy.yaml` is the allowlist and source registry. The active fixture sources are `fixture_rss_blog`, `fixture_atom_research`, `fixture_github_repo`, `fixture_exchange_docs`, and `fixture_paper_meta`. Their responses live below `tests/fixtures/sources/`; fixture transport performs file reads only. GitHub uses release commit SHA cursors. HTML crosses `sanitize_html` before normalized storage. Real candidates remain `active: false` until a human approves terms, robots policy, license, rate limits, and activation.

The connector result contract is shared by fixture and live candidates. Inactive sources are rejected before a request. Fixture tests block sockets to make the offline boundary explicit.

## Configuration (Appendix A)

Values below are the fixed initial values from the intent. Environment variables may override existing keys using their uppercase names; no new configuration keys are supported.

| Key | Initial value |
| --- | --- |
| `PROJECT_SLUG` | `scalping-briefing` |
| `TIMEZONE` | `Asia/Seoul` |
| `WEEKLY_REPORT_SCHEDULE` | `TUE 08:00`, `FRI 08:00` |
| `initial_lookback_days` | `14` |
| `max_lookback_days` | `30` |
| `candidate_score_threshold` | `60` |
| `briefing_max_items` | `7` |
| `extraction_confidence_min` | `0.7` |
| `quote_max_chars` | `300` |
| `briefing_language` | `ko` |
| `publication_policy` | `manual_approval` |
| `DELIVERY_CHANNEL` | `telegram` |
| `DELIVERY_MODE` | `dry_run` |
| `LLM_MODE` | `fixture` |
| `LLM_MONTHLY_BUDGET_USD` | unset; required only for approved live LLM mode |
| `LLM_RUN_MAX_TOKENS` | unset; required only for approved live LLM mode |
| `DATABASE_URL` | `sqlite:///./data/app.sqlite3` |
| `REVIEW_API_BIND` | `127.0.0.1` |
| `REVIEW_API_TOKEN` | unset; environment only |
| `max_collect_retries` | `3` |
| `response_max_bytes` | `10485760` (10 MB) |
| `request_timeout_seconds` | `20` seconds |
| `max_redirects` | `3` |
| `raw_retention_days` | `365` |
| `normalized_retention_days` | `unlimited` |
| `llm_run_retention_days` | `365` |
| `alerts_dir` | `alerts/` |

Live LLM, live delivery, external review binding, real-source activation, and unlisted configuration keys fail closed or require explicit approval. Copy `.env.example` only for local placeholders; never put real secrets in tracked files.

## Notices and operating rules

- [Notices](docs/notices.md) covers safety, copyright, investment, and non-advice boundaries.
- [Operations](docs/operations.md) records alerts, retries, cursor behavior, approval gates, and deferred phases.
- [Protected requirement test map](docs/protected-requirements-tests.md) maps P1–P10 to collected pytest node IDs.
- External HTML, Markdown, fixture responses, and LLM fixture output are untrusted data. Scripts and collected code are never executed.
