@echo off
uv run uvicorn core.main:app --host 0.0.0.0 --port 8222 --reload
