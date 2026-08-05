# Operations

Phase 0 + Phase 1 operation is offline-first. `make run-briefing` runs one collection pass over the five active fixture sources, stores document metadata and append-only Versions, and exits with a dry-run result. It does not create a briefing, call an LLM, or send a message.

## Alerts and failure records

Collection failures use the existing structured logger and write a JSON alert below `alerts/`. The two records are separate from delivery output. A terminal failure records at least:

- `event: collection_failure`;
- `source_id`, `error_class`, and the safe error message;
- `retry_count`, `next_retry_at`, `last_error_at`, and `terminal_error`;
- UTC timestamps and the configured alert location.

Alert files are runtime artifacts and are ignored by Git. Do not put tokens, cookies, authorization headers, or original full documents into logs or alerts.

## Retry and access rules

Each source uses `max_collect_retries=3`. Retry delay is exponential and capped at 60 seconds. A transient error remains retryable until the cap; only the capped attempt ends the collection job as `failed` and emits the terminal alert. Rate limits, request timeout, response-size, MIME, redirect, allowlist, and SSRF guards remain active on every attempt.

`robots_allowed` is fail-closed. `true` permits body persistence; `false` or `unknown` retains metadata and the access decision but no raw or normalized body. No login, CAPTCHA, paywall, robots, or rate-limit bypass exists.

## Cursor and Version rules

- RSS/Atom cursors preserve `ETag` and `Last-Modified`; the next request sends `If-None-Match` and `If-Modified-Since`. `304` means no new Version.
- GitHub cursors preserve the latest release commit SHA, release ID, and README SHA. A changed release or new release appends a Version; an unchanged item is deduplicated.
- Paper metadata has the same connector result contract and preserves DOI, authors, and license metadata.
- HTML is sanitized at collection time. `normalized_location` is written only from the sanitized body.
- The repository canonicalizes URLs and deduplicates by document identity plus content/body hashes. Changed content creates a new `DocumentVersion`; old Versions are never overwritten.
- A missing cursor uses `initial_lookback_days=14`. Recovery is bounded by `max_lookback_days=30`; truncation is recorded when the requested window is older.
- Collection success advances source state. Approval or delivery is not a cursor advancement condition.

## Approval gates

The following actions stay blocked until explicit user approval:

1. changing a real Source Policy candidate from `active: false` to `active: true`;
2. enabling `LLM_MODE=live` and setting its monthly budget/token limits;
3. enabling `DELIVERY_MODE=live` or registering a Telegram bot token/chat ID;
4. adding a configuration key or changing a fixed Appendix A value;
5. binding the review API outside `127.0.0.1`.

The default is `LLM_MODE=fixture`, `DELIVERY_MODE=dry_run`, SQLite, and local review binding. `REVIEW_API_TOKEN` is environment-only.

## Test and integration separation

```bash
make test
PYTHONPATH=src .venv/bin/python -m pytest -q -m integration
```

The first command is the required offline gate and excludes integration tests through `pyproject.toml`. Integration tests are the only place for Postgres, real HTTP, Docker, or other external services. Fixture tests block sockets and read only approved files below `tests/fixtures/sources/`.

## Deferred phases

- Phase 2: relevance classification, structured strategy extraction, scoring, review queue, and Evidence-backed candidate generation. Deferred because this run only proves collection and Version persistence.
- Phase 3: scheduled twice-weekly briefing generation, archive rendering, and Telegram delivery. Deferred to keep this entrypoint dry-run and prevent external delivery.
- Phase 4: six operational metrics, recurring Markdown reports, local metric alerts, four-week expansion eligibility, and Appendix A recalibration recommendations are implemented. Reports are archived below `storage/ops-reports/`; alert artifacts remain below `alerts/` and separate from the delivery channel. This run records recommendations only and does not change source activation or configuration.
- Phase 4b: end-to-end orchestration wiring is implemented as a separate entrypoint. `run_briefing()` remains the existing collection-only entrypoint; `run_briefing_cycle()` connects collection→classification→extraction→validation→evidence→scoring→novelty→routing→briefing→gate→dry-run delivery→metrics→report→local alerting. With default fixture data, the gate stops the cycle before delivery until approved records exist.

### Phase 4b cycle procedure

Run one offline-safe orchestration cycle with:

```bash
make run-briefing-cycle
```

The command prints deterministic summary JSON, writes operational reports below
`storage/ops-reports/`, and writes local failure or metric alert artifacts below
`alerts/`. It uses the configured fixture/`dry_run` defaults, does not auto-approve
candidates, and does not send a live message. Exit code `0` means the cycle had
no isolated stage failures; a nonzero exit code means the summary contains a
non-success status and should be inspected before retrying the same scheduled
trigger.

No deferred phase, including Phase 4b, is activated by `make run-briefing`.
