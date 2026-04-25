SHELL := /bin/bash

.PHONY: dev dev-stop infra-up infra-down chrome doctor setup python-setup ui-setup

dev:
	./scripts/dev-up.sh

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
