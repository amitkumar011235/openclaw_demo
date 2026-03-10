# OpenClaw Demo

A multi-channel AI assistant with a LangChain-powered agent, SQLite-backed session memory, and pluggable connectors (Streamlit web UI, Telegram bot). The agent has access to file-system and terminal tools (read/write/edit/delete files, run commands, grep) with safety checks.

---

## Architecture

The project is organized in **three layers**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CONNECTORS (channel-specific)                                           │
│  Streamlit (chat_ui.py)  │  Telegram (telegram_bot.py)  │  … future     │
│  Talks to /ws or /run    │  Uses BaseChannel + gateway  │                │
└──────────────────────────┼──────────────────────────────┼────────────────┘
                           │                              │
                           ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  GATEWAY (channel ↔ session binding)                                     │
│  gateway/bindings.py  — (channel, user_id) → session_id                  │
│  gateway/service.py  — run_for_channel(), new_session(), link_session()  │
└─────────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  CORE (AI + memory)                                                      │
│  core/main.py  — FastAPI: /run, /ws, /sessions/*                         │
│  core/agent.py — LangChain agent + AsyncSqliteSaver (short-term memory)  │
│  core/sessions.py — session metadata (id, title, created_at)              │
│  core/tools.py — read_file, write_file, edit_file, delete_file,          │
│                  run_terminal, grep_search (sandboxed, blocklisted)      │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Core**: Single “brain” — one LangChain agent, one session store, one set of tools. No knowledge of Telegram or Streamlit.
- **Gateway**: Resolves “this user on this channel” to a backend `session_id`, so the same conversation can be continued across channels (e.g. start in Streamlit, continue in Telegram via `/link <session_id>`).
- **Connectors**: Thin adapters. Streamlit talks directly to the FastAPI `/ws` endpoint. Telegram (and future Slack/WhatsApp) use the `BaseChannel` class and call the gateway in-process; the gateway then calls the core agent.

---

## Implementation Details

### Core layer

| File | Role |
|------|------|
| `core/main.py` | FastAPI app: `GET /health`, `POST /run`, `WS /ws`, `GET/POST/DELETE/PATCH /sessions` and `/sessions/{id}/history`. Loads `.env` and runs agent lifespan (init/shutdown SQLite checkpointer). |
| `core/agent.py` | LangChain agent (GPT-4o) with `create_agent()`, AsyncSqliteSaver for short-term memory. Exposes `call_agent(query, thread_id)`, `stream_agent(query, thread_id)`, `get_history(thread_id)`. `thread_id` = backend session id. |
| `core/sessions.py` | SQLite table `sessions` (id, title, created_at). `create_session()`, `list_sessions()`, `get_session(id)`, `update_title()`, `delete_session()`. |
| `core/tools.py` | Six LangChain tools: `read_file`, `write_file`, `edit_file`, `delete_file`, `run_terminal`, `grep_search`. All file ops sandboxed to `WORKSPACE`; terminal commands blocklisted (e.g. rm -rf /, format, shutdown). |

Session and checkpoint data live under `data/` (SQLite). The agent is initialized asynchronously in FastAPI’s lifespan so the async checkpointer is created inside the event loop.

### Gateway layer

| File | Role |
|------|------|
| `gateway/bindings.py` | SQLite table `channel_bindings` (channel, channel_user_id, session_id, updated_at). `get_binding()`, `set_binding()`, `clear_binding()`. DB path: `data/bindings.db`. |
| `gateway/service.py` | Facade used by connectors. `run_for_channel(channel, channel_user_id, text)` → resolve or create session, call `call_agent(text, session_id)` from core, return response. `new_session_for_channel()` creates session + binding. `link_channel_to_session()` binds to an existing session if it exists (via `core.sessions.get_session()`). |

Connectors that use the gateway (e.g. Telegram) run in the same process as the gateway and import it; they do not call the core over HTTP.

### Connectors layer

| File | Role |
|------|------|
| `connectors/base.py` | Abstract `BaseChannel`: `get_channel_name()`, `extract_user_id(raw_event)`, `extract_text(raw_event)`, `send_reply(raw_event, text)`. Shared `handle_event()` parses `/new`, `/link <session_id>`, and normal text → calls gateway and sends reply. |
| `connectors/telegram_channel.py` | `TelegramChannel(BaseChannel)` for python-telegram-bot `Update`: channel name `"telegram"`, user id = `effective_chat.id`, text = `effective_message.text`, reply via `chat.send_message()`. |
| `connectors/telegram_bot.py` | Entrypoint: loads `TELEGRAM_BOT_TOKEN`, builds `Application`, registers `/start` (help) and a text handler that delegates to `TelegramChannel.handle_event()`, runs `run_polling()`. |
| `chat_ui.py` | Streamlit app: sidebar sessions (from `GET /sessions`), chat input, WebSocket to `/ws` with `{ "session_id", "query" }`. **Unchanged by gateway** — still talks to FastAPI only. |

Adding a new channel (e.g. Slack) means implementing a new subclass of `BaseChannel` and a small entry script that receives platform events and calls `handle_event()`.

---

## Prerequisites

- **Python 3.11+**
- **uv** (recommended) or pip for dependency management
- **OpenAI API key** (for the LangChain agent)
- **Telegram bot token** (optional; only for the Telegram connector)

---

## Environment

Copy `.env.example` to `.env` and set:

```env
# Required for the core agent
OPENAI_API_KEY=your_openai_api_key_here

# Optional: port for the FastAPI server (default 8222)
PORT=8222

# Optional: for Telegram connector
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
```

Optional overrides:

- `DATA_DIR` — directory for SQLite DBs (default `data/`)
- `TOOL_WORKSPACE` — sandbox root for file tools (default current working directory)

---

## How to Run

### 1. Install dependencies

```bash
uv sync
# or: pip install -r requirements.txt
```

### 2. Start the core backend (required for all clients)

From the project root:

```bash
# Windows
.\dev

# Or manually
uv run uvicorn core.main:app --host 0.0.0.0 --port 8222 --reload
```

The API will be at `http://localhost:8222`. Docs: `http://localhost:8222/docs`.

### 3. Run the Streamlit chat UI (optional)

In a **second** terminal:

```bash
uv run streamlit run chat_ui.py
```

Open the URL shown (e.g. `http://localhost:8501`). Use the sidebar to create or switch sessions; chat uses the WebSocket `/ws` endpoint with the selected session id.

### 4. Run the Telegram bot (optional)

In a **third** terminal, after setting `TELEGRAM_BOT_TOKEN` in `.env`:

```bash
uv run python -m connectors.telegram_bot
```

Or from Windows: `.\run_telegram.cmd`

**Only one bot instance** can run per token at a time. If you see "Conflict: terminated by other getUpdates request", stop any other `run_telegram.cmd` or `python -m connectors.telegram_bot` process, wait a few seconds, then start again.

- **Normal messages**: Gateway resolves or creates a session for that Telegram chat and returns the agent’s reply (same session = same memory).
- **`/new`**: Creates a new backend session and binds this chat to it.
- **`/link <session_id>`**: Binds this chat to an existing session (e.g. one from Streamlit or from another device). Use the session id shown in Streamlit or from `GET /sessions`.

The Telegram bot and the FastAPI app can run on the same machine; the connector calls the gateway (and thus the core agent) in-process, so the backend must be running for the agent to work (Streamlit and Telegram both depend on it).

---

## API summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check, returns `{"status": "ok"}`. |
| POST | `/run` | Body: `{ "query": string, "session_id"?: string }`. Returns `{ "response": string, "session_id": string }`. If `session_id` omitted, a new session is created. |
| WS | `/ws` | Send JSON `{ "session_id", "query" }`; receive streaming text chunks, then `[DONE]`. |
| GET | `/sessions` | List sessions. |
| POST | `/sessions` | Create session, returns `{ id, title, created_at }`. |
| GET | `/sessions/{id}` | Get session metadata. |
| GET | `/sessions/{id}/history` | Get conversation history for that session. |
| PATCH | `/sessions/{id}` | Update title (body: `{ "title": string }`). |
| DELETE | `/sessions/{id}` | Delete session metadata. |

---

## Project layout

```
openclaw_demo/
├── README.md
├── .env
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── dev.cmd              # Run backend (uvicorn core.main:app)
├── chat_ui.py           # Streamlit UI (uses /ws)
├── core/                # Core layer
│   ├── __init__.py
│   ├── main.py          # FastAPI entry
│   ├── agent.py         # LangChain agent + memory
│   ├── sessions.py      # Session metadata
│   └── tools.py         # Agent tools (files, terminal, grep)
├── gateway/
│   ├── bindings.py      # Channel ↔ session binding store
│   └── service.py       # run_for_channel, new_session, link_session
├── connectors/
│   ├── base.py          # BaseChannel abstract class
│   ├── telegram_channel.py
│   └── telegram_bot.py  # Telegram polling entrypoint
└── data/                # SQLite DBs (gitignored)
    ├── checkpoints.db   # LangGraph state
    ├── sessions.db      # Session metadata
    └── bindings.db      # Channel bindings
```

---

## License

Use and modify as needed for your project.
