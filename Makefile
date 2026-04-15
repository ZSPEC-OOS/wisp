PY=python3

install:
	pip install -e .[dev]

dev:
	uvicorn apps.api.main:app --reload --port 8000

run:
	uvicorn apps.api.main:app --host 0.0.0.0 --port 8000

test:
	pytest -q

lint:
	ruff check .
