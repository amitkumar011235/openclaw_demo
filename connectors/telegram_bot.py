"""
connectors/telegram_bot.py — entrypoint for the Telegram bot.

This script wires python-telegram-bot to our TelegramChannel adapter
and starts long-polling for updates.

Usage (from project root; run as module so relative imports work):
    uv run python -m connectors.telegram_bot

Important: Only one instance of this bot can run per TELEGRAM_BOT_TOKEN at a time.
If you see "Conflict: terminated by other getUpdates request", stop all other
bot processes (other terminals, background runs) and try again after a few seconds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.error import Conflict, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .telegram_channel import TelegramChannel

load_dotenv(override=True)

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# One bot instance per token: Telegram returns Conflict if another process is polling.
CONFLICT_MSG = (
    "Telegram Conflict: only one bot instance can run per token.\n"
    "Stop any other run_telegram.cmd / python -m connectors.telegram_bot processes,\n"
    "wait a few seconds, then start this again."
)

telegram_channel = TelegramChannel()


async def _on_post_init(application: Application) -> None:
    """Called after Application.initialize() — spin up the LangChain agent."""
    from core.agent import init_agent
    logger.info("Initialising LangChain agent (SQLite checkpointer)…")
    await init_agent()
    logger.info("Agent ready.")


async def _on_post_shutdown(application: Application) -> None:
    """Called after Application.shutdown() — release the checkpointer."""
    from core.agent import shutdown_agent
    logger.info("Shutting down agent checkpointer…")
    await shutdown_agent()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start handler — shows a brief help message.
    """
    help_text = (
        "Hi! I'm your OpenClaw assistant on Telegram.\n\n"
        "- Send any message and I'll reply using the same session.\n"
        "- Use `/new` to start a fresh session.\n"
        "- Use `/link <session_id>` to attach this chat to an existing "
        "session from another client (e.g. Streamlit).\n"
    )
    await update.effective_chat.send_message(help_text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main text message handler — delegate to TelegramChannel.
    """
    await telegram_channel.handle_event(update)


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Global error handler.  Intermittent 409 Conflicts are harmless (Telegram
    retries automatically), so we just log a warning.  Other errors get the
    full traceback.
    """
    exc = context.error
    if isinstance(exc, Conflict):
        logger.warning("Transient 409 Conflict on getUpdates — Telegram will retry.")
    else:
        logger.exception("Update %s caused error: %s", update, exc)


def _check_single_instance(token: str) -> None:
    """
    Call getUpdates once. If Conflict is raised, another instance is already
    polling this token — exit with a clear message instead of entering the loop.
    """
    async def _check() -> None:
        bot = Bot(token=token)
        try:
            await bot.get_updates(timeout=1)
        except Conflict:
            # Definitive signal that another instance is polling right now.
            print(CONFLICT_MSG, file=sys.stderr)
            logger.error(CONFLICT_MSG)
            sys.exit(1)
        except TimedOut:
            # Network timeout talking to Telegram. This is usually transient or
            # local networking; don't block startup, just log and continue.
            logger.warning(
                "Pre-flight getUpdates timed out — continuing without "
                "strict single-instance check."
            )
        except Exception as exc:
            # Any other pre-flight error: log and continue so the main
            # Application can handle retries.
            logger.warning(
                "Pre-flight getUpdates failed (%s); continuing anyway.", exc
            )

    asyncio.run(_check())


def main() -> None:
    """
    Build the bot application and start polling.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set in the environment or .env file."
        )

    _check_single_instance(token)

    app = (
        Application.builder()
        .token(token)
        .post_init(_on_post_init)
        .post_shutdown(_on_post_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_command))
    # Include COMMAND so /new and /link reach handle_text -> base.handle_event
    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    try:
        app.run_polling()
    except Conflict:
        logger.error(CONFLICT_MSG)
        print(CONFLICT_MSG, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

