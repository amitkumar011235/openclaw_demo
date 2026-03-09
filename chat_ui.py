"""
chat_ui.py — Streamlit chat interface for testing WebSocket streaming.

This app connects to the FastAPI WebSocket endpoint at ws://localhost:8222/ws
and displays the LLM response in real time, token by token.

How it works:
  1. User types a question in the chat input box at the bottom.
  2. The app opens a WebSocket connection to the FastAPI server.
  3. It sends the query as a plain text frame.
  4. It reads response chunks as they arrive and displays them live
     with a typewriter cursor effect.
  5. When the server sends "[DONE]", streaming stops.
  6. The connection is closed after each query (simple, stateless).

Usage:
  Make sure the FastAPI server is running first (.\\dev or python main.py),
  then in a second terminal:

      streamlit run chat_ui.py
"""

import asyncio
import os

import streamlit as st
import websockets
from dotenv import load_dotenv

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Config — reads PORT from .env so it matches the FastAPI server.
# ---------------------------------------------------------------------------
PORT = os.getenv("PORT", "8222")
WS_URL = f"ws://localhost:{PORT}/ws"

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(page_title="OpenClaw Chat", page_icon="💬")
st.title("OpenClaw Chat")
st.caption("Streaming responses from the ADK agent via WebSocket")

# ---------------------------------------------------------------------------
# Session state — keeps conversation history across Streamlit reruns.
# Every widget interaction causes Streamlit to rerun the entire script,
# so we store messages in session_state to persist them.
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

# ---------------------------------------------------------------------------
# Display existing chat history (from previous turns in this session).
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ---------------------------------------------------------------------------
# WebSocket streaming function
# ---------------------------------------------------------------------------
async def stream_from_ws(query: str, placeholder) -> str:
    """
    Connect to the FastAPI WebSocket, send the query, and stream
    the response into the given Streamlit placeholder.

    Args:
        query:       The user's question to send to the agent.
        placeholder: An st.empty() element to update with each chunk.

    Returns:
        The complete response text once streaming finishes.
    """
    full_response = ""

    # Open a fresh WebSocket connection for this query.
    async with websockets.connect(WS_URL) as ws:
        # Send the user's question as a plain text frame.
        await ws.send(query)

        # Read chunks until the server signals completion with "[DONE]".
        async for chunk in ws:
            if chunk == "[DONE]":
                break

            # Append the new chunk and update the placeholder in place.
            # The "▌" character acts as a blinking cursor effect.
            full_response += chunk
            placeholder.markdown(full_response + "▌")

    # Final render without the cursor.
    placeholder.markdown(full_response)
    return full_response


# ---------------------------------------------------------------------------
# Chat input — pinned to the bottom of the page by Streamlit.
# When the user submits text, this block runs.
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask something..."):

    # 1. Show the user's message in the chat.
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Show the assistant's response with live streaming.
    with st.chat_message("assistant"):
        # st.empty() creates a single placeholder element that we can
        # overwrite repeatedly to get the typewriter streaming effect.
        placeholder = st.empty()

        # Run the async WebSocket call synchronously.
        # Streamlit doesn't natively support async, so we use asyncio.run().
        response = asyncio.run(stream_from_ws(prompt, placeholder))

    # 3. Save the complete response to session state for chat history.
    st.session_state.messages.append({"role": "assistant", "content": response})
