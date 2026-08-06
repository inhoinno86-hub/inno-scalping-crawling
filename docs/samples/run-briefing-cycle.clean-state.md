# `make run-briefing-cycle` — 깨끗한 DB에 대한 첫 실행 (오프라인, 2026-08-05)

```json
{
  "alerts_written": [
    "alerts/3486288d27fa48b482e7999197775ae2.json",
    "alerts/b21ddb28c4c393b946c7f8e32c5fade43aaa353728db72ae32d66093980240ae:M1.json",
    "alerts/b21ddb28c4c393b946c7f8e32c5fade43aaa353728db72ae32d66093980240ae:M2.json",
    "alerts/b21ddb28c4c393b946c7f8e32c5fade43aaa353728db72ae32d66093980240ae:M4.json",
    "alerts/b21ddb28c4c393b946c7f8e32c5fade43aaa353728db72ae32d66093980240ae:M6.json"
  ],
  "briefing_generated": true,
  "briefing_id": "briefing-ca09f3cd0240ffb3262ceb39e1bf72a94afb877a91ea8a747104a0544a11f639",
  "delivery_invoked": false,
  "delivery_mode": "dry_run",
  "delivery_status": null,
  "failures": [
    {
      "identifier": "briefing-ca09f3cd0240ffb3262ceb39e1bf72a94afb877a91ea8a747104a0544a11f639",
      "reason": "briefing must be approved or explicitly marked as an internal draft",
      "stage": "gate"
    }
  ],
  "llm_mode": "fixture",
  "metrics": {
    "M1": "insufficient_data",
    "M2": "insufficient_data",
    "M3": "meets_target",
    "M4": "insufficient_data",
    "M5": "meets_target",
    "M6": "insufficient_data"
  },
  "phase": "4b",
  "report_path": "storage/ops-reports/ops-report-b21ddb28c4c393b946c7f8e32c5fade43aaa353728db72ae32d66093980240ae.md",
  "scheduled_for": "2026-08-07T08:00:00+09:00",
  "stages": {
    "alerting": {
      "failed": 0,
      "processed": 1,
      "skipped": 0,
      "succeeded": 1
    },
    "briefing": {
      "failed": 0,
      "processed": 1,
      "skipped": 0,
      "succeeded": 1
    },
    "classify": {
      "failed": 0,
      "processed": 7,
      "skipped": 1,
      "succeeded": 7
    },
    "collect": {
      "failed": 0,
      "processed": 1,
      "skipped": 0,
      "succeeded": 1
    },
    "delivery": {
      "failed": 0,
      "processed": 0,
      "skipped": 0,
      "succeeded": 0
    },
    "evidence": {
      "failed": 0,
      "processed": 6,
      "skipped": 0,
      "succeeded": 6
    },
    "extract": {
      "failed": 0,
      "processed": 7,
      "skipped": 0,
      "succeeded": 7
    },
    "gate": {
      "failed": 1,
      "processed": 1,
      "skipped": 0,
      "succeeded": 0
    },
    "metrics": {
      "failed": 0,
      "processed": 1,
      "skipped": 0,
      "succeeded": 1
    },
    "novelty": {
      "failed": 0,
      "processed": 6,
      "skipped": 0,
      "succeeded": 6
    },
    "report": {
      "failed": 0,
      "processed": 1,
      "skipped": 0,
      "succeeded": 1
    },
    "route": {
      "failed": 0,
      "processed": 6,
      "skipped": 0,
      "succeeded": 6
    },
    "score": {
      "failed": 0,
      "processed": 6,
      "skipped": 0,
      "succeeded": 6
    },
    "validate": {
      "failed": 0,
      "processed": 7,
      "skipped": 0,
      "succeeded": 7
    }
  },
  "status": "partial_success",
  "trigger_type": "scheduled"
}
```
