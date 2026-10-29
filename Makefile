.PHONY: help setup api web test measure build clean

help:
	@echo "make setup    - install backend venv and frontend deps"
	@echo "make api      - run the RouteVoice server on :8000"
	@echo "make web      - run the web UI on :5174"
	@echo "make test     - run the backend test suite"
	@echo "make measure  - run the evaluation scenarios -> docs/measurements.md"
	@echo "make build    - production build of the web UI"

setup:
	cd server && python3.11 -m venv .venv && ./.venv/bin/pip install -q -r requirements-dev.txt
	cd web && npm install

api:
	cd server && ./.venv/bin/python -m uvicorn app.main:app --reload --port 8000

web:
	cd web && npm run dev

test:
	cd server && ./.venv/bin/python -m pytest tests -q

measure:
	cd server && ./.venv/bin/python scripts/measure_scenarios.py --runs 5 --out ../docs/measurements.md

build:
	cd web && npm run build

clean:
	rm -rf server/.venv server/.pytest_cache web/node_modules web/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
