# Protected requirement test map

P1–P10 are protected requirements from the project intent. Every entry below is a real pytest node ID collected by the default test configuration. The mapping records both the safety boundary and its executable check.

| Requirement | pytest node IDs | Coverage |
| --- | --- | --- |
| P1 | `tests/test_net_guards.py::test_allowlist_rejection_happens_before_http_client_call`; `tests/test_net_retry_robots.py::test_robots_decision_records_fields_and_tie_prefers_allow` | Source allowlist, robots decision fields, and no access-policy bypass. |
| P2 | `tests/test_sanitize_gate.py::test_publication_gate_never_accepts_original_full_text` | Full original body cannot pass publication gate. |
| P3 | `tests/test_schemas.py::test_evidence_requires_document_version_id` | Evidence must point to a document version. |
| P4 | `tests/test_schemas.py::test_field_status_and_robots_decision_contracts`; `tests/test_protected_mapping.py::test_field_status_preserves_unknown_values` | Unknown/conflicting field status remains explicit; empty values cannot be guessed. |
| P5 | `tests/test_connectors_repo_html.py::test_html_sanitize_runs_before_normalized_storage`; `tests/test_sanitize_gate.py::test_sanitize_removes_executable_markup_and_preserves_injection_as_text` | HTML is sanitized before normalized storage; prompt-injection text remains data. |
| P6 | `tests/test_sanitize_gate.py::test_publication_gate_rejects_banned_investment_language` | Investment advice, signal, and guarantee language is rejected. |
| P7 | `tests/test_phase1_dod.py::test_phase1_dod2_changed_fixture_creates_new_version_with_change_summary` | Changed content appends a Version and retains prior history. |
| P8 | `tests/test_models_migrations.py::test_delivery_guard_rejects_success_resend_without_two_part_approval`; `tests/test_protected_mapping.py::test_delivery_idempotency_requires_approval` | Idempotency key and approved-resend safeguards prevent duplicate delivery. |
| P9 | `tests/test_config.py::test_defaults_cover_appendix_a_and_are_safe`; `tests/test_config.py::test_explicit_approval_is_call_scoped`; `tests/test_protected_mapping.py::test_run_briefing_is_fixture_dry_run` | Safe defaults, scoped approvals, fixture-only collection, and no delivery invocation. |
| P10 | `tests/test_logging_setup.py::test_environment_secret_is_masked_even_inside_message`; `tests/test_protected_mapping.py::test_no_literal_secrets_in_repository` | Secret values are masked and literal credentials are absent from repository files. |

The mapping test checks these strings against `pytest --collect-only`, so a renamed or deleted node fails the mapping rather than silently weakening coverage.
