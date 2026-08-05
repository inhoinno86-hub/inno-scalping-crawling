# Phase 4b orchestration cycle

`run_briefing_cycle()` is the public Phase 4b entrypoint. One invocation runs a
single, ordered cycle from fixture collection through dry-run delivery and the
operational outputs. The orchestration layer connects existing Phase 1–4
functions; it does not add a new state machine or automatically approve a
candidate.

## The 14 stages

`run_cycle()` visits these stages in order. Each per-document or per-candidate
call is wrapped by `run_stage()`, which records processed, succeeded, and failed
counters and isolates exceptions.

| # | Stage | Called function(s) | Contract |
| ---: | --- | --- | --- |
| 1 | collect | `collect_documents` | Reuses the fixture source registry and document repository to persist `Document`/`DocumentVersion` results. |
| 2 | classify | `classify_document` | Classifies each collected version with the fixture-safe, non-LLM path (`use_llm=False`). |
| 3 | extract | `extract_strategy_candidate` | Extracts a structured candidate and its accepted evidence from relevant documents. |
| 4 | validate | `validate_extracted_candidate` (or the state-aware reuse described below) | Confirms the extracted candidate is valid before downstream processing. |
| 5 | evidence | `link_evidence` | Links accepted quotes to the document version and candidate. |
| 6 | score | `score_candidate` | Calculates the existing value-score result without changing scoring rules. |
| 7 | novelty | `classify_novelty` | Compares the candidate with existing candidates using the existing novelty contract. |
| 8 | route | `route_candidate` | Routes the candidate to the existing review/approval queue; the cycle does not approve it. |
| 9 | briefing | `build_briefing` | Builds the scheduled briefing, including its cursor, rendered content, and archive. Only approved candidates enter the body. |
| 10 | gate | `gate_briefing` | Applies the publication and delivery safety gates. |
| 11 | delivery | `TelegramDryRunConnector`, `deliver_briefing` | Invokes the delivery service once in `DELIVERY_MODE=dry_run`; no live message is sent. |
| 12 | metrics | `compute_all_metrics` | Computes the six existing operational metrics for the observation window. |
| 13 | report | `render_report`, `archive_report` | Renders and archives the metrics report below `storage/ops-reports/` by default. |
| 14 | alerting | `emit_metric_alerts` | Writes deterministic local alert artifacts for breached or `insufficient_data` metrics. |

The cycle also uses `schedule_trigger` and `next_occurrence` to derive the
scheduled trigger and briefing identifier when callers do not inject a schedule.

## Collection boundary and the protected entrypoint

`orchestration/collect.py` reuses 15 private `scalping_briefing` helpers:
`_author_text`, `_body_hash`, `_collection_target`, `_content_hash`,
`_document_target`, `_evaluate_document_robots`, `_item_metadata`, `_item_url`,
`_license_text`, `_load_robots_text`, `_normalized_body`, `_parse_datetime`,
`_persist_source_cursor`, `_raw_body`, and `_sync_database_sources`. It still
executes the source loop and the per-item loop independently, so the
orchestration collector owns its `CollectionResult`, counters, and de-duplication
of returned versions.

That arrangement is allowed by the orchestration intent and is deliberate: it
preserves the protected `run_briefing()` observable contract instead of routing
the legacy entrypoint through the new cycle. `run_briefing()` therefore remains
the collection-only dry-run path, while `run_briefing_cycle()` can use the same
source policy and persistence helpers before continuing through the 14 stages.

## State-aware validation (PLAN_v2 §3.5)

The validate stage must read the `DocumentVersion` processing state after
extraction; it must not blindly call `validate_extracted_candidate` for every
successful extraction.

`extract_strategy_candidate` can complete its own schema validation and move the
document version directly to `validated`, returning a `validated_payload`.
`validate_extracted_candidate` accepts only the `extracted` state and raises an
invalid-transition error for another state. Therefore, unconditionally calling
it immediately after extraction would reject every candidate on that successful
path and would force an invalid backward transition.

The orchestration branch is:

1. Read the current processing state from the document version.
2. For `extracted`, call `validate_extracted_candidate(...)`.
3. For `validated`, count the validate stage as successful and use the
   extraction result's `validated_payload`; the already-completed validation is
   not repeated.
4. For any other state, record a validate-stage failure and isolate that item;
   do not claim validation succeeded.

This preserves the existing transitions and keeps the correction in
`orchestration/cycle.py`; `pipeline/extract.py`, `pipeline/validate.py`, and
`pipeline/state_machine.py` are not changed by the cycle entrypoint.

## Idempotency boundary and determinism scope

The pair `(scheduled_for, trigger_type)` is the cycle's idempotency boundary.
`schedule_trigger` derives the deterministic `briefing_id` from that pair.
Repeating a cycle with the same pair reuses the same briefing identity, and the
existing delivery guard prevents a second delivery for the same briefing,
channel, and content. This boundary covers briefing identity and delivery; it
does not make an already-processed `DocumentVersion` valid for another
classification transition.

The summary contract has a fixed field set (the fields listed below), uses
sorted JSON keys (`sort_keys=True`), and produces reproducible stage aggregates
and metric verdicts for identical inputs, including the same persisted state and
observation-window inputs. That is structural/semantic determinism, not a
promise that two complete JSON outputs are byte-for-byte identical. UUID alert
filenames from `alerts.write_alert`, observation-window IDs, and timestamps can
make two JSON outputs differ byte-for-byte. Metric alert IDs remain deterministic
for the same window and metric, but failure-alert filenames and their
`created_at` values are intentionally run-specific.

## Failure isolation and exit codes

`run_stage()` catches an exception for one item or stage, masks and bounds its
identifier and reason, writes a local `alerts/` failure artifact through
`alerts.record_failure`, appends a structured entry to `failures`, and continues
with the next item. Failure artifacts are separate from delivery and do not call
the delivery connector or use a network channel.

Failures in the cycle-level briefing, gate, or delivery stage skip the dependent
downstream delivery work. The cycle still runs metrics, report, and alerting so
that an operational failure does not remove observation evidence. A cycle with
no failures has `status: "success"` and exit code `0`. Any isolated failure
changes the status to a non-success value (normally `"partial_success"`) and
returns exit code `1`; the summary never presents a failed stage as success.

## Summary JSON

The entrypoint prints `CycleSummary.to_json()`. It has the fixed field set below
and sorted keys (`sort_keys=True`); identical inputs reproduce the same stage
aggregates and metric verdicts even though run-specific alert filenames,
window IDs, or timestamps can change the raw JSON bytes.

| Field | Meaning |
| --- | --- |
| `phase` | Phase identifier, `"4b"`. |
| `status` | Overall `success` or non-success cycle status. |
| `llm_mode` | Effective LLM mode, fixture by default. |
| `delivery_mode` | Effective delivery mode, `dry_run` by default. |
| `scheduled_for` | ISO timestamp of the scheduled occurrence. |
| `trigger_type` | Trigger classification, normally `scheduled`. |
| `briefing_id` | Deterministic briefing identity for the trigger pair. |
| `stages` | All 14 stage names, each with `processed`, `succeeded`, and `failed` counts. |
| `briefing_generated` | Whether `build_briefing` returned a briefing. |
| `delivery_invoked` | Whether the delivery service was called. |
| `delivery_status` | Delivery result status, or `null` when no result exists. |
| `metrics` | Metric ID to verdict mapping, including `M1`–`M6`. |
| `report_path` | Archived report path, or `null` if report creation failed. |
| `alerts_written` | Local alert artifact paths written by the cycle. |
| `failures` | Bounded entries with `stage`, `identifier`, and `reason`. |

## Entrypoints and operating commands

`make run-briefing` remains the Phase 0+1 collection-only entrypoint. It calls
`run_briefing()`, prints its existing dry-run collection JSON, creates no
briefing, and does not invoke delivery.

`make run-briefing-cycle` calls `run_briefing_cycle()`. That wrapper loads the
configuration, creates the configured database engine and session, imports
`run_cycle` inside the function body, prints the cycle summary JSON, and returns
the summary exit code. It is additive; it does not alter `run_briefing()` or the
existing `run-briefing` target.

Both commands use the repository's offline-safe defaults. The cycle command is
the one-shot procedure for collection → candidate processing → briefing →
dry-run delivery → metrics → report → local alerts; repeated scheduling is
owned by the external operator or scheduler, not by a daemon in this entrypoint.

## Normal fixture outcomes

The fixture run normally produces a briefing with zero approved items. The
default publication policy is `manual_approval`, and routing leaves candidates
in `needs_review` rather than auto-promoting them to `approved`. An empty
approved set is the normal zero-approved fixture outcome: it is a normal,
successful briefing path, not evidence that the cycle failed.

The same fixture run normally has too few observations for the operational
metrics. Those metrics report `insufficient_data`, and the cycle may write the
corresponding local alert artifacts. `insufficient_data` is an honest sample
verdict, not a passing verdict and not a cycle exception; it remains visible in
the summary, report, and alerting output.

## 기본 fixture 실행에서 무엇이 일어나는가

The preceding fixture outcome describes briefing construction: the default
fixture has zero approved items. The full cycle then applies the existing
publication contracts. `src/scalping_briefing/publishing/briefing_build.py:541,568`
sets every built briefing's `publication_status` to `pending_approval`.
`src/scalping_briefing/publishing/briefing_gate.py:562-578` accepts only
`approved` or `published`, unless the briefing is explicitly marked as an
`internal draft`; any other status raises the approval error.

Therefore, a default fixture run with no approved item normally stops at the
gate and does not enter delivery. This is an expected safety stop, not a
delivery success. Delivery runs when approved records exist and the resulting
briefing satisfies the gate; the cycle never auto-approves candidates or
briefings under `P15`. An explicit approval or internal-draft marker is an
operator/test precondition, not something the cycle creates.

`PLAN_v3` §1.2 measured `make run-briefing-cycle` on 2026-08-05 in offline
fixture mode. The two captured artifacts are documented separately below; the
stage counts are not interchangeable. A previously recorded shorthand said
`processed 7 / succeeded 7` and `processed 6 / **failed 6**`; those values are
not the clean-state artifact's complete stage counts, so the tables below are
the authoritative transcription.

### Clean-state first run

Captured in
`.loop-engine/runs/02ecc401-5010-45a5-801f-f3c0317b2849/artifacts/run-briefing-cycle.clean-state.txt`.
The summary uses `scheduled_for: "2026-08-07T08:00:00+09:00"`,
`trigger_type: "scheduled"`, and
`briefing_id: "briefing-ca09f3cd0240ffb3262ceb39e1bf72a94afb877a91ea8a747104a0544a11f639"`.

| Stage | Captured result |
| --- | --- |
| collect | processed 1 / succeeded 1 / failed 0 |
| classify | processed 8 / succeeded 7 / **failed 1** — `classification requires deduplicated state, got 'access_denied'` |
| extract | processed 6 / succeeded 0 / **failed 6** — no matching prompt-hash entries in `llm/fixtures/response-map.json` |
| validate · evidence · score · novelty · route | each processed 0 / succeeded 0 / failed 0 — no candidate arrived from upstream |
| briefing | processed 1 / succeeded 1 / failed 0 (`pending_approval`) |
| gate | processed 1 / succeeded 0 / **failed 1** — `briefing must be approved or explicitly marked as an internal draft` |
| delivery | processed 0 / succeeded 0 / failed 0 — skipped after the gate stop |
| metrics · report · alerting | each processed 1 / succeeded 1 / failed 0 |

There are eight bounded failures in this artifact: one `classify`, six
`extract`, and one `gate`. The summary is `status: "partial_success"` with exit
code `1`, `briefing_generated: true`, and `delivery_invoked: false`. Metrics
are `M5: meets_target`; `M1`, `M2`, `M3`, `M4`, and `M6` are
`insufficient_data`. M5 has a measurable zero-duplicate sample, while the
other five metrics have no qualifying observations in this one fixture window;
under `P4`, a zero sample remains `insufficient_data` rather than becoming a
passing or breached result. The report is archived below
`storage/ops-reports/`.

The six clean-state extract failures are fixture-data gaps: the required prompt
hashes have no entries in `llm/fixtures/response-map.json`. Reporting those
items as failures, instead of disguising missing responses as success, keeps
the incomplete evidence visible in the cycle summary.

### Already-processed-database rerun

Captured in
`.loop-engine/runs/02ecc401-5010-45a5-801f-f3c0317b2849/artifacts/run-briefing-cycle.attempt-3.txt`.
It uses the same scheduled occurrence and therefore the same
`briefing_id`, but it runs against a database containing already-processed
versions.

| Stage | Captured result |
| --- | --- |
| collect | processed 1 / succeeded 1 / failed 0 |
| classify | processed 7 / succeeded 0 / **failed 7** — `classification requires deduplicated state, got 'failed'` or `classification requires deduplicated state, got 'background_only'` |
| extract · validate · evidence · score · novelty · route | each processed 0 / succeeded 0 / failed 0 — classification produced no downstream items |
| briefing | processed 1 / succeeded 1 / failed 0 (`pending_approval`) |
| gate | processed 1 / succeeded 0 / **failed 1** — `briefing must be approved or explicitly marked as an internal draft` |
| delivery | processed 0 / succeeded 0 / failed 0 — skipped after the gate stop |
| metrics · report · alerting | each processed 1 / succeeded 1 / failed 0 |

This artifact has eight bounded failures: seven `classify` failures caused by
the existing processing states and one `gate` failure. Its summary is also
`status: "partial_success"` with exit code `1`, `briefing_generated: true`,
`delivery_invoked: false`, `M5: meets_target`, and
`M1`/`M2`/`M3`/`M4`/`M6: insufficient_data`.

The rerun's `classification requires deduplicated state, got ...` reason is a
classification state-machine failure, not a briefing or delivery idempotency
failure. The same `briefing_id` is reused and the gate still stops delivery, so
the briefing/delivery idempotency boundary remains separate from the upstream
classification state. Reusing a briefing identity does not make `failed` or
`background_only` document versions eligible for the `deduplicated` →
classification transition.
