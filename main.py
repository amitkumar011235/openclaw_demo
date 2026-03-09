"""
main.py — FastAPI server entry point.

This module wires together the FastAPI application and the LangChain agent:

  Endpoints:
    GET  /health  -> simple health-check that returns {"status": "ok"}.
    POST /run     -> accepts {"query": "..."}, calls the agent, returns the
                     full response as {"response": "..."}.
    WS   /ws      -> WebSocket endpoint for real-time streaming.  The client
                     sends a text message (the query) and receives chunks of
                     the agent's reply as they are generated, followed by a
                     "[DONE]" sentinel when the response is complete.

  Startup:
    1. Loads environment variables from .env (OPENAI_API_KEY, PORT).
    2. Starts uvicorn on 0.0.0.0:<PORT> (default 8222).

Usage:
    python main.py
"""

import os
import logging

import uvicorn
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env FIRST, before importing agent.py.
# agent.py creates the ChatOpenAI instance at import time, so the
# OPENAI_API_KEY must already be in the environment by then.
# ---------------------------------------------------------------------------
load_dotenv(override=True)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from agent import call_agent, stream_agent

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="OpenClaw Demo",
    description="A simple FastAPI + LangChain agent server using OpenAI GPT-4o.",
)


# -- Request / response models for the /run endpoint -----------------------

class RunRequest(BaseModel):
    """JSON body expected by POST /run."""
    query: str


class RunResponse(BaseModel):
    """JSON body returned by POST /run."""
    response: str


# ---------------------------------------------------------------------------
# GET /health — quick liveness probe
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Return a simple status object so load-balancers or scripts can verify
    the server is alive."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /run — send a query to the agent and get the full response back
# ---------------------------------------------------------------------------
@app.post("/run", response_model=RunResponse)
async def run_agent(body: RunRequest):
    """
    Accept a user query, forward it to the ADK agent, and return the
    complete text response.

    Request body:
        {"query": "What is Python?"}

    Response body:
        {"response": "Python is a programming language …"}
    """
    logger.info("POST /run  query=%s", body.query)

    try:
        # call_agent() awaits the full LLM response before returning.
        result = await call_agent(body.query)
    except RuntimeError as exc:
        logger.error("Agent error: %s", exc)
        return RunResponse(response=f"Error: {exc}")

    logger.info("POST /run  response length=%d chars", len(result))
    return RunResponse(response=result)


# ---------------------------------------------------------------------------
# WS /ws — streaming WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket handler for real-time streaming communication.

    Protocol:
      1. Client connects to ws://host:port/ws
      2. Client sends a plain text message (the query).
      3. Server streams back partial response chunks as plain text frames.
      4. Server sends the string "[DONE]" to signal end-of-response.
      5. Client may send another query on the same connection (go to step 2).
      6. Either side can close the connection at any time.
    """
    await ws.accept()
    logger.info("WebSocket client connected")

    try:
        # Keep the connection open for multiple query/response rounds.
        while True:
            # Step 2 — wait for the client to send a query.
            query = await ws.receive_text()
            logger.info("WS query=%s", query)

            # Step 3 — stream the agent's response chunk-by-chunk.
            async for chunk in stream_agent(query):
                await ws.send_text(chunk)

            # Step 4 — tell the client the response is complete.
            await ws.send_text("[DONE]")
            logger.info("WS response streamed, sent [DONE]")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")


# ---------------------------------------------------------------------------
# Entry point — start uvicorn when the script is run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8222"))
    logger.info("Starting server on port %d", port)
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
