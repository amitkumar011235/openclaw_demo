"""
main.py — FastAPI server entry point.

Endpoints:
  GET   /health               -> {"status": "ok"}
  POST  /run                  -> {"response": "..."} (accepts session_id)
  WS    /ws                   -> streaming via WebSocket (JSON protocol)
  GET   /sessions             -> list all sessions
  POST  /sessions             -> create a new session
  GET   /sessions/{id}        -> get session metadata
  GET   /sessions/{id}/history -> get conversation history for a session
  DELETE /sessions/{id}       -> delete a session
  PATCH /sessions/{id}        -> update session title

Usage (from project root):
    uv run uvicorn core.main:app --host 0.0.0.0 --port 8222 --reload
"""

import json
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .agent import call_agent, stream_agent, get_history, init_agent, shutdown_agent
from .sessions import (
    create_session,
    list_sessions,
    get_session,
    update_title,
    delete_session,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — initialise async resources (SQLite checkpointer) on startup
# and clean them up on shutdown.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initialising agent (async SQLite checkpointer)…")
    await init_agent()
    yield
    logger.info("Shutting down agent checkpointer…")
    await shutdown_agent()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="OpenClaw Demo",
    description="FastAPI + LangChain agent with SQLite-backed session memory.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    query: str
    session_id: Optional[str] = None


class RunResponse(BaseModel):
    response: str
    session_id: str


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: str


class TitleUpdate(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /run — send a query, get full response (with session memory)
# ---------------------------------------------------------------------------
@app.post("/run", response_model=RunResponse)
async def run_agent(body: RunRequest):
    """If no session_id is provided, a new session is auto-created."""
    if body.session_id:
        sid = body.session_id
        if not get_session(sid):
            sess = create_session()
            sid = sess["id"]
    else:
        sess = create_session()
        sid = sess["id"]

    logger.info("POST /run  session=%s  query=%s", sid, body.query)

    try:
        result = await call_agent(body.query, thread_id=sid)
    except RuntimeError as exc:
        logger.error("Agent error: %s", exc)
        return RunResponse(response=f"Error: {exc}", session_id=sid)

    logger.info("POST /run  response length=%d chars", len(result))
    return RunResponse(response=result, session_id=sid)


# ---------------------------------------------------------------------------
# WS /ws — streaming WebSocket with session support
#
# Protocol (JSON-based):
#   Client sends:  {"session_id": "abc123", "query": "Hello"}
#                  session_id is optional — one is auto-created if missing.
#   Server sends:  text chunks as plain strings, then "[DONE]".
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket client connected")

    try:
        while True:
            raw = await ws.receive_text()

            try:
                payload = json.loads(raw)
                query = payload.get("query", raw)
                session_id = payload.get("session_id")
            except (json.JSONDecodeError, AttributeError):
                query = raw
                session_id = None

            if not session_id:
                sess = create_session()
                session_id = sess["id"]
                await ws.send_text(json.dumps({"type": "session", "session_id": session_id}))
            elif not get_session(session_id):
                sess = create_session()
                session_id = sess["id"]

            logger.info("WS session=%s  query=%s", session_id, query)

            async for chunk in stream_agent(query, thread_id=session_id):
                await ws.send_text(chunk)

            await ws.send_text("[DONE]")
            logger.info("WS response streamed, sent [DONE]")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


# ---------------------------------------------------------------------------
# Session management endpoints
# ---------------------------------------------------------------------------

@app.get("/sessions", response_model=list[SessionOut])
async def api_list_sessions():
    return list_sessions()


@app.post("/sessions", response_model=SessionOut, status_code=201)
async def api_create_session():
    return create_session()


@app.get("/sessions/{session_id}", response_model=SessionOut)
async def api_get_session(session_id: str):
    sess = get_session(session_id)
    if not sess:
        from fastapi import HTTPException
        raise HTTPException(404, "Session not found")
    return sess


@app.get("/sessions/{session_id}/history")
async def api_get_history(session_id: str):
    """Return the conversation messages for the given session."""
    return await get_history(session_id)


@app.patch("/sessions/{session_id}", response_model=SessionOut)
async def api_update_session(session_id: str, body: TitleUpdate):
    if not update_title(session_id, body.title):
        from fastapi import HTTPException
        raise HTTPException(404, "Session not found")
    return get_session(session_id)


@app.delete("/sessions/{session_id}")
async def api_delete_session(session_id: str):
    if not delete_session(session_id):
        from fastapi import HTTPException
        raise HTTPException(404, "Session not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Entry point (when run as python -m core.main from project root)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8222"))
    logger.info("Starting server on port %d", port)
    uvicorn.run("core.main:app", host="0.0.0.0", port=port, reload=False)
