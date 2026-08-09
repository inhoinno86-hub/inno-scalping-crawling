VENV_PYTHON := $(wildcard .venv/bin/python)
ifeq ($(VENV_PYTHON),)
PYTHON ?= python3
else
PYTHON ?= $(VENV_PYTHON)
endif
PYTEST ?= $(PYTHON) -m pytest

.PHONY: test run-briefing run-briefing-cycle review-api run-briefing-cycle-live-llm

test:
	PYTHONPATH=src $(PYTEST) -q

# Offline Phase 0 + 1 collection vertical slice; no briefing or delivery.
run-briefing:
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing; raise SystemExit(run_briefing())"

run-briefing-cycle:
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing_cycle; raise SystemExit(run_briefing_cycle())"

# Same cycle, but against the local Ollama model (LLM_MODE=live) instead of
# fixtures. Local inference has no API billing (LLM_MONTHLY_BUDGET_USD=0 is
# a nominal value, not a real budget) and LLM_RUN_MAX_TOKENS bounds each
# call via Ollama's num_predict. Approval is a call argument, never
# persisted config (see config.load_config docstring), so this target -- not
# a bare env var -- is what actually turns live mode on; every other target
# stays on the fixture default even if these env vars leak into the shell.
run-briefing-cycle-live-llm:
	LLM_MODE=live LLM_MONTHLY_BUDGET_USD=0 LLM_RUN_MAX_TOKENS=2000 \
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing_cycle; raise SystemExit(run_briefing_cycle(approvals=['llm_live']))"

review-api:
	PYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_review_api; raise SystemExit(run_review_api())"
