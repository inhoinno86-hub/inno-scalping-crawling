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
| `stages` | All 14 stage names, each with `processed`, `succeeded`, `failed`, and `skipped` counts. `skipped` counts inputs the stage was never asked to handle because an earlier run already carried them past it; a skip is not a failure and writes no alert. |
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

`make run-briefing-cycle` was re-measured offline on 2026-08-05, after the
fixture recordings were rebuilt and the classify stage learned to skip
already-processed versions. Two captured artifacts document the two cases; the
stage counts are not interchangeable.

### Clean-state first run

Captured in
`.loop-engine/runs/02ecc401-5010-45a5-801f-f3c0317b2849/artifacts/run-briefing-cycle.clean-state.txt`.

| Stage | Captured result |
| --- | --- |
| collect | processed 1 / succeeded 1 / failed 0 |
| classify | processed 7 / succeeded 7 / failed 0 / **skipped 1** — the skipped version is `access_denied`, a state the classifier cannot accept |
| extract | processed 7 / succeeded 7 / failed 0 — resolved through the content-addressed fixture recordings |
| validate | processed 7 / succeeded 7 / failed 0 |
| evidence · score · novelty · route | each processed 6 / succeeded 6 / failed 0 |
| briefing | processed 1 / succeeded 1 / failed 0 (`pending_approval`) |
| gate | processed 1 / succeeded 0 / **failed 1** — `briefing must be approved or explicitly marked as an internal draft` |
| delivery | processed 0 / succeeded 0 / failed 0 — skipped after the gate stop |
| metrics · report · alerting | each processed 1 / succeeded 1 / failed 0 |

The single bounded failure is the gate stop. The summary is
`status: "partial_success"` with exit code `1`, `briefing_generated: true`, and
`delivery_invoked: false`. Metrics are `M3: meets_target` and
`M5: meets_target`; `M1`, `M2`, `M4`, and `M6` are `insufficient_data`, because
one fixture window holds no qualifying observation for them. Under `P4` a
missing sample stays `insufficient_data` rather than becoming a passing or
breached result. The report is archived below `storage/ops-reports/`.

### Already-processed-database rerun

Captured in
`.loop-engine/runs/02ecc401-5010-45a5-801f-f3c0317b2849/artifacts/run-briefing-cycle.repeat-run.txt`.
It uses the same scheduled occurrence, and therefore the same `briefing_id`,
against the database the first run left behind.

| Stage | Captured result |
| --- | --- |
| collect | processed 1 / succeeded 1 / failed 0 |
| classify | processed 0 / succeeded 0 / failed 0 / **skipped 8** — every collected version is already past the states the classifier accepts |
| extract · validate · evidence · score · novelty · route | each processed 0 — no document reached them |
| briefing | processed 1 / succeeded 1 / failed 0 (`pending_approval`) |
| gate | processed 1 / succeeded 0 / **failed 1** — the same approval stop as the first run |
| delivery | processed 0 / succeeded 0 / failed 0 |
| metrics · report · alerting | each processed 1 / succeeded 1 / failed 0 |

The rerun keeps the same single gate failure, the same `partial_success` status
and exit code `1`, and the same metric verdicts. It writes no per-document
alert artifact, because a document the pipeline already finished is a skip, not
a failure: collection returns every ingested version, including rows an earlier
run carried to a terminal state, and re-classifying those would be an invalid
transition. The skip count is the honest record of work that was not repeated.

Skipping is separate from the briefing and delivery idempotency boundary. The
same `(scheduled_for, trigger_type)` still yields one briefing and at most one
delivery, and reusing a briefing identity never makes a `failed`,
`background_only`, or `access_denied` document version eligible for
classification again.

### Partially supported candidates stay publishable

The publication gate requires Evidence for every claim a briefing item makes,
not for every field the renderer lists. A queued candidate that supports some
core fields and leaves the rest `unknown` therefore builds normally: the
evidenced claims are published with their quotes, and an unsupported field is
rendered empty with no Evidence attached.

The strict half of the contract is unchanged. An item that carries a claim
still fails with `MissingEvidenceError` when its Evidence is missing, and a
briefing item shape without a `claim` key keeps the original all-or-nothing
requirement. Only an item that carries an explicit, empty claim whose recorded
field status agrees it is unsupported may skip Evidence, so a field can never
lose its Evidence by turning up empty while its status still says `explicit`.

Before this boundary existed, one unsupported field of one queued candidate
raised `MissingEvidenceError` from `build_briefing` and took the whole
scheduled briefing down.

### Fixture recordings survive a rebuilt database

Every prompt embeds the row's `document_version_id`, which is a fresh UUID for
each ingestion, so a recording keyed only by prompt hash stops matching as soon
as the database is rebuilt. `llm/fixture.py` therefore resolves a prompt in two
steps: the exact prompt hash first, then the content-addressed key
`stable:{prompt_version}:{content_hash}` produced by `stable_prompt_key`. An
exact hit replays byte for byte; a content-addressed hit substitutes the runtime
`document_version_id` wherever the recording carries the
`{{document_version_id}}` placeholder, which downstream contracts require for
candidate `document_version_ids` and evidence rows. A prompt that matches
neither key still fails hard, so an unrecorded prompt can never turn into a
silent live call.

Recordings are rebuilt offline with
`scripts/build_offline_extraction_fixtures.py`, which captures the prompts one
cycle actually issues and writes records for `scripts/record_llm_fixtures.py`:

```
python scripts/build_offline_extraction_fixtures.py --out records.json
python scripts/record_llm_fixtures.py --input records.json \
    --mapping src/scalping_briefing/llm/fixtures/response-map.json
```

Each recorded field is filled only from a sentence the fixture document actually
contains, so every claim carries a quote that is a real substring of its source;
a field whose sentence is absent stays `unknown` under `P4`.
`tests/test_phase4b_offline_fixture_cycle.py` pins both halves of this: one
offline run must reach routing with real candidates, and a repeated run must
skip instead of re-reporting finished documents.
