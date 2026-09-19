# Agent Oath: Trust Verification Layer
#
# One-command local dev: `make dev` runs backend and frontend together.
# Windows: install GNU make once with `winget install ezwinports.make`.

SHELL := /bin/sh

BACKEND_DIR := backend
FRONTEND_DIR := frontend
VENV := $(BACKEND_DIR)/.venv
PY := $(VENV)/bin/python
ifeq ($(OS),Windows_NT)
PY := $(VENV)/Scripts/python
endif

.PHONY: help install install-backend install-frontend dev dev-backend dev-frontend \
        test lint format build clean

help:
	@echo "install  - create the backend venv and install both dependency sets"
	@echo "dev      - run backend (port 8000) and frontend (port 5173) together"
	@echo "test     - run the pytest suite"
	@echo "lint     - ruff check + ruff format check + prettier check"
	@echo "format   - ruff format + prettier write"
	@echo "build    - typecheck and build the frontend"

install: install-backend install-frontend

install-backend:
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r $(BACKEND_DIR)/requirements-dev.txt

install-frontend:
	cd $(FRONTEND_DIR) && npm install

dev:
	@$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd $(BACKEND_DIR) && ../$(PY) -m uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev

test:
	cd $(BACKEND_DIR) && ../$(PY) -m pytest

lint:
	cd $(BACKEND_DIR) && ../$(PY) -m ruff check .
	cd $(BACKEND_DIR) && ../$(PY) -m ruff format --check .
	cd $(FRONTEND_DIR) && npm run format:check

format:
	cd $(BACKEND_DIR) && ../$(PY) -m ruff format .
	cd $(BACKEND_DIR) && ../$(PY) -m ruff check --fix .
	cd $(FRONTEND_DIR) && npm run format

build:
	cd $(FRONTEND_DIR) && npm run build

clean:
	rm -rf $(VENV) $(FRONTEND_DIR)/node_modules $(FRONTEND_DIR)/dist
