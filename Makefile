SHELL := /bin/bash

.PHONY: dev dev-stop infra-up infra-down chrome doctor data-check setup python-setup ui-setup controller-evals perception-bench

dev:
	./scripts/dev-up.sh

# The controller regression suite (PLAN_controller_v1 M5): re-run curated + journaled bundles
# through decide(). Deterministic, offline, FREE (no model rung, no browser) — safe in low-data
# mode. Every controller correction becomes a permanent case in controller/eval_cases.json.
controller-evals:
	cd apps/controlplane-api && ../../.venv/bin/python -m pytest test_controller_evals.py -q

# Score the perception witnesses on the labeled corpus (PLAN_perception_v1 S18). Leave-one-out,
# offline, FREE — the default encoders download nothing. `--encoders clip` adds a ~600MB
# one-time fetch and is WIFI ONLY. Takes a few minutes; embeddings are cached after the first run.
perception-bench:
	cd apps/controlplane-api && ../../.venv/bin/python -m perception.bench

# Pre-flight for a roaming / hard-capped connection: "would anything download?"
# Exits non-zero if it would. See docs/LOW_DATA_MODE.md.
data-check:
	./scripts/data-check.sh

dev-stop:
	./scripts/dev-down.sh

infra-up:
	cd infra && docker compose up -d

infra-down:
	cd infra && docker compose down

chrome:
	@echo 'Chrome now starts from the Training session flow.'
	@echo 'Use make dev, open the UI, create a training session, then start Session Chrome.'

doctor:
	./scripts/dev-doctor.sh

setup: python-setup ui-setup

python-setup:
	./scripts/bootstrap-python.sh

ui-setup:
	cd apps/controlplane-ui && npm ci
