# bot/background_worker.py
"""
Background Worker for Auto Smart Follow-up
Runs a loop that checks for inactive conversations every 60 seconds.
Safe for GitHub (no hardcoded user ID).
"""

import asyncio
import logging
from typing import Optional

from backend.conversation_tracker import (
    get_inactive_conversations,
    generate_smart_followup,
    mark_followup_sent,
    send_auto_followup,
)

logger = logging.getLogger(__name__)


class AutoResponder:
    def __init__(self, bot):
        self.bot = bot
        self.running = False
        self.task: Optional[asyncio.Task] = None

    async def check_and_respond(self):
        """Main loop - runs forever"""
        logger.info("🚀 Auto Smart Responder started (5 min inactivity check)")

        while self.running:
            try:
                inactive_chats = get_inactive_conversations()

                for data in inactive_chats:
                    chat_id = data.get("chat_id")
                    user_id = data.get("user_id")
                    last_msg = data.get("last_message", "")

                    if not chat_id or not user_id:
                        continue

                    logger.info(f"Detected inactive chat: {chat_id} (user {user_id})")

                    # Generate smart followup
                    followup_text = generate_smart_followup(last_msg, user_id, chat_id)

                    # Send it
                    await send_auto_followup(self.bot, chat_id, followup_text)

                    # Mark that we sent a followup (cooldown)
                    mark_followup_sent(chat_id)

                    await asyncio.sleep(2)

            except Exception as e:
                logger.exception(f"Auto responder loop error: {e}")

            await asyncio.sleep(60)

    def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self.check_and_respond())
        logger.info("✅ Auto Responder background task started")

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
        logger.info("🛑 Auto Responder stopped")


auto_responder: Optional[AutoResponder] = None


def start_auto_responder(bot):
    global auto_responder
    if auto_responder is None:
        auto_responder = AutoResponder(bot)
    auto_responder.start()
    return auto_responder


def stop_auto_responder():
    global auto_responder
    if auto_responder:
        auto_responder.stop()
