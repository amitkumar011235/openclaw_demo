"""
connectors/base.py — abstract base class for chat channels.

Each concrete connector (Telegram, Slack, WhatsApp, etc.) should:
  - Subclass BaseChannel.
  - Implement a few platform-specific methods for extracting the user id,
    extracting message text, and sending replies.
  - Delegate all session + memory logic to the gateway layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from gateway import service as gateway_service


class BaseChannel(ABC):
    """
    Base class for a chat channel.

    Subclasses must implement the abstract methods to adapt their
    platform's event objects into the simple interface used here.
    """

    @abstractmethod
    def get_channel_name(self) -> str:
        """
        Return a short name for this channel, e.g. "telegram" or "slack".
        """

    @abstractmethod
    def extract_user_id(self, raw_event) -> str:
        """
        Extract a stable user/chat identifier from the raw event object.

        This id will be used as channel_user_id in the bindings store so
        that we can maintain per-user sessions.
        """

    @abstractmethod
    def extract_text(self, raw_event) -> str | None:
        """
        Extract plain text from the incoming event.

        Return None for events that do not carry user text (stickers,
        images, etc.) so they can be ignored by the common handler.
        """

    @abstractmethod
    async def send_reply(self, raw_event, text: str) -> None:
        """
        Send a text reply back to the user on this channel.
        """

    async def handle_event(self, raw_event) -> None:
        """
        Entry point for processing a single incoming event.

        Common flow:
          1. Get channel name and user id.
          2. Extract message text.
          3. Handle commands like /new and /link <session_id>.
          4. For normal text, call the gateway to run the agent and
             send the response back.
        """
        channel = self.get_channel_name()
        user_id = self.extract_user_id(raw_event)
        text = self.extract_text(raw_event)

        if not text:
            # Nothing to do for non-text messages.
            return

        stripped = text.strip()

        # Command: /new  -> create a fresh session for this user.
        if stripped == "/new":
            session_id = await gateway_service.new_session_for_channel(
                channel, user_id
            )
            await self.send_reply(
                raw_event,
                f"Started a new session: `{session_id}`",
            )
            return

        # Command: /link <session_id>  -> bind this user to an existing session.
        if stripped.startswith("/link"):
            parts = stripped.split(maxsplit=1)
            if len(parts) == 2:
                target_session_id = parts[1].strip()
                ok = await gateway_service.link_channel_to_session(
                    channel, user_id, target_session_id
                )
                if ok:
                    await self.send_reply(
                        raw_event,
                        f"Linked to existing session `{target_session_id}`.",
                    )
                else:
                    await self.send_reply(
                        raw_event,
                        "Sorry, I couldn't find a session with that id.",
                    )
            else:
                await self.send_reply(
                    raw_event,
                    "Usage: /link <session_id>",
                )
            return

        # Normal user message — route through the gateway to the agent.
        response = await gateway_service.run_for_channel(
            channel, user_id, text
        )
        await self.send_reply(raw_event, response)

