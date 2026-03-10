"""
gateway/service.py — Facade between channel connectors and the core agent.

Connectors (Telegram, WhatsApp, etc.) call the async functions in this
module instead of talking to the LangChain agent directly. This keeps
all channel↔session binding logic in one place.
"""

from __future__ import annotations

import uuid
from typing import Optional

from core.agent import call_agent
from core.sessions import create_session, get_session

from .bindings import get_binding, set_binding


def _generate_session_id() -> str:
    """
    Generate a human-friendly session id.

    We use a short hex string to make it easy to copy/paste in commands
    like /link <session_id>.
    """
    return uuid.uuid4().hex[:12]


async def new_session_for_channel(channel: str, channel_user_id: str) -> str:
    """
    Create a new backend session and bind this (channel, user) to it.

    Returns:
        The new session_id.
    """
    # Register metadata with the existing sessions module so this session
    # also shows up in your Streamlit sidebar and HTTP API.
    metadata = create_session()
    session_id = metadata["id"]

    set_binding(channel, channel_user_id, session_id)
    return session_id


async def link_channel_to_session(
    channel: str, channel_user_id: str, session_id: str
) -> bool:
    """
    Bind this (channel, user) to an existing session if it exists.

    Returns:
        True if the session exists and binding was updated, False otherwise.
    """
    if not get_session(session_id):
        return False

    set_binding(channel, channel_user_id, session_id)
    return True


async def run_for_channel(
    channel: str, channel_user_id: str, text: str
) -> str:
    """
    Run a user message coming from a specific channel/user through the agent.

    This function:
      1. Resolves (or creates) a backend session_id for the channel user.
      2. Calls the core agent with that session_id.
      3. Returns the agent's text response.
    """
    session_id: Optional[str] = get_binding(channel, channel_user_id)

    if session_id is None:
        # No binding yet — create a new session and bind this user to it.
        metadata = create_session()
        session_id = metadata["id"]
        set_binding(channel, channel_user_id, session_id)

    # Delegate to the core agent, which handles all LLM + memory logic.
    response = await call_agent(text, thread_id=session_id)
    return response

