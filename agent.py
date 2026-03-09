"""
agent.py — Google ADK agent setup and execution helpers.

This module does three things:
  1. build_agent()    -> constructs the ADK LlmAgent (backed by OpenAI GPT-4o
                         via the LiteLLM translation layer).
  2. call_agent()     -> sends a single query to the agent and returns the
                         complete text response (used by the HTTP /run endpoint).
  3. stream_agent()   -> an async generator that yields response text chunks
                         as they arrive (used by the WebSocket /ws endpoint).

How the pieces fit together:
  - LiteLlm wraps the model string "openai/gpt-4o" so ADK can talk to OpenAI.
  - InMemorySessionService keeps lightweight session state in RAM.
  - Runner ties the agent + session service together and exposes run_async().
"""

import uuid
from typing import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "openclaw_demo"
AGENT_NAME = "assistant"
MODEL_STRING = "openai/gpt-4o"

SYSTEM_INSTRUCTION = (
    "You are a helpful, concise AI assistant. "
    "Answer the user's question clearly. "
    "Keep responses short unless the user asks for detail."
)

# ---------------------------------------------------------------------------
# Module-level singletons (created once, reused across requests)
# ---------------------------------------------------------------------------

# We create the session service at module level so every request shares the
# same pool of sessions.  If we let InMemoryRunner create its own service
# internally, sessions created in one call would not be visible to the next —
# a well-known ADK gotcha.
session_service = InMemorySessionService()

# Build the agent once at import time.
agent = LlmAgent(
    model=LiteLlm(model=MODEL_STRING),
    name=AGENT_NAME,
    instruction=SYSTEM_INSTRUCTION,
)

# The Runner connects the agent to the session service.
runner = Runner(
    agent=agent,
    app_name=APP_NAME,
    session_service=session_service,
)


# ---------------------------------------------------------------------------
# Helper: get or create a session
# ---------------------------------------------------------------------------
async def _ensure_session(user_id: str, session_id: str) -> str:
    """
    Return the session id after making sure it exists.

    If the session already exists in the service, this is a no-op.
    If it doesn't, a new one is created with the given ids.

    Args:
        user_id:    Identifies the user (can be any stable string).
        session_id: Desired session identifier.

    Returns:
        The session_id that was created or already existed.
    """
    # get_session returns None when the id is unknown.
    existing = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if existing is None:
        # First time for this session — create it.
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        return session.id
    return existing.id


# ---------------------------------------------------------------------------
# Public API: call_agent  (request-response, used by POST /run)
# ---------------------------------------------------------------------------
async def call_agent(query: str) -> str:
    """
    Send *query* to the LLM agent and return the full text response.

    A fresh session is created for every call so conversations don't leak
    between unrelated HTTP requests.  If you want multi-turn chat, pass a
    stable session_id instead of generating a new UUID each time.

    Args:
        query: The user's question or prompt.

    Returns:
        The agent's complete text reply as a single string.

    Raises:
        RuntimeError: If the agent finishes without producing a final response.
    """
    # Each HTTP call gets its own throwaway session.
    user_id = "http_user"
    session_id = str(uuid.uuid4())
    await _ensure_session(user_id, session_id)

    # Wrap the plain string in ADK's Content / Part format.
    user_message = types.Content(
        role="user",
        parts=[types.Part(text=query)],
    )

    # run_async is an async generator that yields Event objects.
    # We iterate until we find the one flagged as the final response.
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_message,
    ):
        # is_final_response() marks the last event the agent will produce.
        if event.is_final_response():
            # The event's content may contain multiple parts; concatenate them.
            if event.content and event.content.parts:
                final_text = "".join(
                    part.text for part in event.content.parts
                    if part.text is not None
                )
            break

    if not final_text:
        raise RuntimeError("Agent did not return a final response.")

    return final_text


# ---------------------------------------------------------------------------
# Public API: stream_agent  (streaming, used by WebSocket /ws)
# ---------------------------------------------------------------------------
async def stream_agent(query: str) -> AsyncGenerator[str, None]:
    """
    Async generator that yields text chunks as the agent produces them.

    This is the streaming counterpart of call_agent().  Instead of waiting
    for the complete reply, every event that carries text is yielded
    immediately so the WebSocket handler can push it to the client in
    real time.

    Args:
        query: The user's question or prompt.

    Yields:
        Strings containing partial response text, one per ADK event.
    """
    user_id = "ws_user"
    session_id = str(uuid.uuid4())
    await _ensure_session(user_id, session_id)

    user_message = types.Content(
        role="user",
        parts=[types.Part(text=query)],
    )

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_message,
    ):
        # Yield every chunk of text we receive, whether partial or final.
        if event.content and event.content.parts:
            text = "".join(
                part.text for part in event.content.parts
                if part.text is not None
            )
            if text:
                yield text

        # Stop iterating once the agent signals it's done.
        if event.is_final_response():
            break
