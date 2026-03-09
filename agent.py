"""
agent.py — LangChain agent with async SQLite-backed short-term memory.

The agent is lazily initialized via init_agent() which is called from
FastAPI's lifespan hook.  This ensures the async SQLite checkpointer
is set up inside a running event loop.

Public API:
  init_agent()                   -> one-time async setup (called at startup)
  call_agent(query, thread_id)   -> full response string   (POST /run)
  stream_agent(query, thread_id) -> async token generator   (WS /ws)
  get_history(thread_id)         -> list of message dicts   (GET /sessions/{id}/history)

Docs:
  - Agents:  https://docs.langchain.com/oss/python/langchain/agents
  - Memory:  https://docs.langchain.com/oss/python/langchain/short-term-memory
"""

import os
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
load_dotenv(override=True)

from langchain.agents import create_agent
from langchain.messages import AIMessageChunk, HumanMessage, AIMessage

from tools import ALL_TOOLS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = "openai:gpt-4o"

SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to file-system and terminal tools. "
    "You can read, write, edit, and delete files, run shell commands, and search "
    "through files using grep. Always confirm destructive actions before executing. "
    "Keep responses clear and concise unless the user asks for detail."
)

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Module-level state (populated by init_agent)
# ---------------------------------------------------------------------------
_checkpointer_cm = None   # the context manager (for cleanup)
_checkpointer = None      # the actual AsyncSqliteSaver instance
_agent = None


async def init_agent() -> None:
    """Create the async SQLite checkpointer and build the agent.

    Must be called exactly once inside a running event loop (e.g. from
    FastAPI's lifespan).
    """
    global _checkpointer_cm, _checkpointer, _agent

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = str(DATA_DIR / "checkpoints.db")
    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(db_path)
    _checkpointer = await _checkpointer_cm.__aenter__()

    _agent = create_agent(
        model=MODEL,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=_checkpointer,
    )


async def shutdown_agent() -> None:
    """Close the checkpointer connection cleanly."""
    global _checkpointer_cm
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer_cm = None


def _get_agent():
    assert _agent is not None, (
        "Agent not initialised — call init_agent() at startup."
    )
    return _agent


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ---------------------------------------------------------------------------
# Public API: call_agent
# ---------------------------------------------------------------------------
async def call_agent(query: str, thread_id: str) -> str:
    """Send *query* to the agent within *thread_id* and return the full reply."""
    agent = _get_agent()
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": query}]},
        _config(thread_id),
    )
    final_message = result["messages"][-1]
    if not final_message.content:
        raise RuntimeError("Agent did not return a response.")
    return final_message.content


# ---------------------------------------------------------------------------
# Public API: stream_agent
# ---------------------------------------------------------------------------
async def stream_agent(query: str, thread_id: str) -> AsyncGenerator[str, None]:
    """Async generator that yields text tokens for *thread_id*."""
    agent = _get_agent()
    async for token, metadata in agent.astream(
        {"messages": [{"role": "user", "content": query}]},
        _config(thread_id),
        stream_mode="messages",
    ):
        if isinstance(token, AIMessageChunk) and token.content:
            yield token.content


# ---------------------------------------------------------------------------
# Public API: get_history
# ---------------------------------------------------------------------------
async def get_history(thread_id: str) -> list[dict]:
    """Return the conversation history for *thread_id*.

    Only user and assistant messages are returned (tool calls filtered out).
    """
    agent = _get_agent()
    state = await agent.aget_state(_config(thread_id))
    messages = state.values.get("messages", [])
    history: list[dict] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            history.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage) and m.content and not m.tool_calls:
            history.append({"role": "assistant", "content": m.content})
    return history
