# backend/conversation_tracker.py
"""
Conversation Inactivity Tracker + Smart Auto Follow-up
Uses existing ADMIN_ID and REDIS_URL from your environment (Render safe).
"""

import os
import time
import json
import logging
import redis
from typing import Optional, Dict, Any

from backend.groq_handler import generate_response

logger = logging.getLogger(__name__)

# === CONFIG (Safe for Render + GitHub) ===
REDIS_URL = os.getenv("REDIS_URL")

if not REDIS_URL:
    logger.warning("⚠️ REDIS_URL not found in environment variables!")
    r = None
else:
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        logger.info("✅ Redis connected successfully for Auto Follow-up")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Redis: {e}")
        r = None

INACTIVITY_SECONDS = int(os.getenv("AUTO_FOLLOWUP_INACTIVITY_SECONDS", "300"))
AUTO_FOLLOWUP_COOLDOWN = int(os.getenv("AUTO_FOLLOWUP_COOLDOWN", "600"))

ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

if not ADMIN_ID:
    logger.warning("⚠️ ADMIN_ID not set in environment — Auto followup will be disabled.")

if r is None or not ADMIN_ID:
    logger.warning("⚠️ Auto Smart Follow-up feature is currently DISABLED.")


def _get_key(chat_id: int) -> str:
    return f"conv_state:{chat_id}"


def track_user_message(chat_id: int, user_id: str, text: str):
    """Call this when ADMIN sends a message."""
    if not ADMIN_ID or r is None:
        return

    key = _get_key(chat_id)
    data = {
        "last_user_time": time.time(),
        "last_message": (text or "")[:800],
        "user_id": str(user_id),
        "chat_id": chat_id,
        "last_followup_time": 0
    }
    try:
        r.set(key, json.dumps(data), ex=86400 * 2)
    except Exception as e:
        logger.error(f"Redis track error: {e}")


def get_inactive_conversations() -> list[Dict[str, Any]]:
    """Return chats where admin has been inactive > INACTIVITY_SECONDS."""
    if not ADMIN_ID or r is None:
        return []

    inactive = []
    try:
        for key in r.scan_iter("conv_state:*"):
            try:
                raw = r.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                last_time = float(data.get("last_user_time", 0))
                last_followup = float(data.get("last_followup_time", 0))

                if time.time() - last_time > INACTIVITY_SECONDS:
                    if time.time() - last_followup < AUTO_FOLLOWUP_COOLDOWN:
                        continue
                    inactive.append(data)
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Redis scan error: {e}")
    return inactive


def generate_smart_followup(last_message: str, user_id: str, chat_id: int) -> str:
    prompt = f"""User ka last message tha: "{last_message}"

Ab 5 minute se zyada ho gaye hain aur user ne reply nahi kiya.
Tum ek smart, friendly aur natural follow-up message likho (Hinglish mix allowed).
- Zyada pushy mat banao
- Topic continue karo ya light sawal poochho
- Short aur natural rakho (max 2-3 lines)

Sirf message likho, koi explanation mat do."""

    try:
        reply = generate_response(
            user_message=prompt,
            persona_key=user_id,
            user_ip=str(user_id)
        )
        return (reply or "Bhai, sab theek? Kya chal raha hai?").strip()
    except Exception as e:
        logger.warning(f"Followup generation failed: {e}")
        return "Bhai, kuch baat karni thi kya? 😊"


def mark_followup_sent(chat_id: int):
    if r is None:
        return
    key = _get_key(chat_id)
    try:
        raw = r.get(key)
        if raw:
            data = json.loads(raw)
            data["last_followup_time"] = time.time()
            r.set(key, json.dumps(data), ex=86400 * 2)
    except Exception as e:
        logger.error(f"mark_followup_sent error: {e}")


async def send_auto_followup(bot, chat_id: int, text: str):
    try:
        from interaction.printer import send_human
        await send_human(bot, chat_id, text)
        logger.info(f"✅ Auto followup sent to chat {chat_id}")
    except Exception as e:
        logger.error(f"Failed to send auto followup: {e}")
        try:
            await bot.send_message(chat_id, text)
        except Exception as e2:
            logger.error(f"Fallback send failed: {e2}")
