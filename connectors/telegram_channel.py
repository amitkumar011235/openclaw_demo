"""
connectors/telegram_channel.py — Telegram-specific channel adapter.

This module defines TelegramChannel, a concrete implementation of
BaseChannel that knows how to:
  - Extract a user/chat id from a python-telegram-bot Update.
  - Extract text messages from the Update.
  - Send replies back to the same chat.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.error import BadRequest

from .base import BaseChannel


logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    """
    Concrete BaseChannel implementation for Telegram.

    The "raw_event" in all methods is a python-telegram-bot Update object.
    """

    def get_channel_name(self) -> str:
        # This short name is used in the bindings store.
        return "telegram"

    def extract_user_id(self, raw_event: Update) -> str:
        """
        Use the chat id as the stable identifier for this conversation.

        This means each Telegram chat (private chat, group, etc.) will
        have its own backend session unless explicitly /link-ed.
        """
        chat = raw_event.effective_chat
        return str(chat.id) if chat is not None else "unknown"

    def extract_text(self, raw_event: Update) -> Optional[str]:
        """
        Extract plain text from the Update, ignoring non-text messages.
        """
        message = raw_event.effective_message
        if message and message.text:
            return message.text
        return None

    async def send_reply(self, raw_event: Update, text: str) -> None:
        """
        Send a reply back to the originating chat.

        We first try with Markdown formatting; if Telegram rejects the message
        (e.g. because of unbalanced backticks), we fall back to plain text so
        the user still sees a response instead of nothing.
        """
        chat = raw_event.effective_chat
        if chat is None:
            return

        try:
            await chat.send_message(text, parse_mode="Markdown")
        except BadRequest as exc:  # e.g. "can't parse entities"
            logger.warning(
                "Telegram markdown send failed (%s); retrying without parse_mode.",
                exc,
            )
            await chat.send_message(text)

