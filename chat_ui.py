"""
chat_ui.py — Streamlit chat interface with session management.

Features:
  - Sidebar lists existing sessions (fetched from the FastAPI backend).
  - "New Chat" button creates a fresh session.
  - Clicking a session loads its conversation history from the server.
  - Messages are streamed token-by-token over WebSocket.
  - Sessions persist across page refreshes (stored in server-side SQLite).

Usage:
    streamlit run chat_ui.py
"""

import asyncio
import json
import os

import requests
import streamlit as st
import websockets
from dotenv import load_dotenv

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PORT = os.getenv("PORT", "8222")
BASE_URL = f"http://localhost:{PORT}"
WS_URL = f"ws://localhost:{PORT}/ws"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def api_list_sessions() -> list[dict]:
    try:
        r = requests.get(f"{BASE_URL}/sessions", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def api_create_session() -> dict:
    r = requests.post(f"{BASE_URL}/sessions", timeout=5)
    r.raise_for_status()
    return r.json()


def api_get_history(session_id: str) -> list[dict]:
    try:
        r = requests.get(f"{BASE_URL}/sessions/{session_id}/history", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def api_delete_session(session_id: str):
    try:
        requests.delete(f"{BASE_URL}/sessions/{session_id}", timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="OpenClaw Chat", page_icon="💬", layout="wide")


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
if "current_session" not in st.session_state:
    st.session_state.current_session = None
if "messages" not in st.session_state:
    st.session_state.messages = {}
if "sessions_list" not in st.session_state:
    st.session_state.sessions_list = []


def refresh_sessions():
    st.session_state.sessions_list = api_list_sessions()


def switch_session(session_id: str):
    st.session_state.current_session = session_id
    if session_id not in st.session_state.messages:
        st.session_state.messages[session_id] = api_get_history(session_id)


def new_chat():
    sess = api_create_session()
    refresh_sessions()
    st.session_state.current_session = sess["id"]
    st.session_state.messages[sess["id"]] = []


# Load sessions on first run
if not st.session_state.sessions_list:
    refresh_sessions()

# Auto-create a session if none exist
if not st.session_state.sessions_list:
    new_chat()

# If no session selected, pick the most recent
if not st.session_state.current_session and st.session_state.sessions_list:
    first = st.session_state.sessions_list[0]
    switch_session(first["id"])


# ---------------------------------------------------------------------------
# Sidebar — session management
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Sessions")

    if st.button("➕  New Chat", use_container_width=True):
        new_chat()
        st.rerun()

    st.divider()

    if st.button("🔄  Refresh", use_container_width=True):
        refresh_sessions()
        st.rerun()

    for sess in st.session_state.sessions_list:
        sid = sess["id"]
        is_active = sid == st.session_state.current_session

        col1, col2 = st.columns([5, 1])
        with col1:
            label = f"**{sess['title']}**" if is_active else sess["title"]
            if st.button(label, key=f"sess_{sid}", use_container_width=True):
                switch_session(sid)
                st.rerun()
        with col2:
            if st.button("🗑", key=f"del_{sid}"):
                api_delete_session(sid)
                if st.session_state.current_session == sid:
                    st.session_state.current_session = None
                st.session_state.messages.pop(sid, None)
                refresh_sessions()
                if st.session_state.sessions_list:
                    switch_session(st.session_state.sessions_list[0]["id"])
                else:
                    new_chat()
                st.rerun()


# ---------------------------------------------------------------------------
# Main chat area
# ---------------------------------------------------------------------------
current = st.session_state.current_session
if not current:
    st.info("Create a new chat to get started.")
    st.stop()

st.title("OpenClaw Chat")
st.caption(f"Session: `{current}`")

# Ensure messages are loaded
if current not in st.session_state.messages:
    st.session_state.messages[current] = api_get_history(current)

# Display chat history
for msg in st.session_state.messages[current]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ---------------------------------------------------------------------------
# WebSocket streaming
# ---------------------------------------------------------------------------
async def stream_from_ws(query: str, session_id: str, placeholder) -> str:
    full_response = ""
    async with websockets.connect(WS_URL) as ws:
        payload = json.dumps({"session_id": session_id, "query": query})
        await ws.send(payload)

        async for chunk in ws:
            if chunk == "[DONE]":
                break
            # Skip JSON control messages (like session assignment)
            if chunk.startswith("{"):
                try:
                    ctrl = json.loads(chunk)
                    if ctrl.get("type") == "session":
                        continue
                except (json.JSONDecodeError, AttributeError):
                    pass

            full_response += chunk
            placeholder.markdown(full_response + "▌")

    placeholder.markdown(full_response)
    return full_response


# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask something..."):
    st.session_state.messages[current].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        response = asyncio.run(stream_from_ws(prompt, current, placeholder))

    st.session_state.messages[current].append({"role": "assistant", "content": response})
