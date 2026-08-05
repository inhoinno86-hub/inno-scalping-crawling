VENV_PYTHON := $(wildcard .venv/bin/python)
ifeq ($(VENV_PYTHON),)
PYTHON ?= python3
else
PYTHON ?= $(VENV_PYTHON)
endif
PYTEST ?= $(PYTHON) -m pytest

.PHONY: test run-briefing run-briefing-cycle review-api

test:
	PYTHONPATH=src $(PYTEST) -q

# Offline Phase 0 + 1 collection vertical slice; no briefing or delivery.
run-briefing:
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing; raise SystemExit(run_briefing())"

run-briefing-cycle:
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing_cycle; raise SystemExit(run_briefing_cycle())"

review-api:
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_review_api; raise SystemExit(run_review_api())"
