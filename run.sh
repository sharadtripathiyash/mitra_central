#!/usr/bin/env bash
set -e
uvicorn app.main:app --host "${APP_HOST:-0.0.0.0}" --port "${APP_PORT:-8000}" --workers "${APP_WORKERS:-2}"
