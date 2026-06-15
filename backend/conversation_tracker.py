# backend/conversation_tracker.py
"""
Conversation Inactivity Tracker + Smart Auto Follow-up.

Tracks chats where someone messaged and the admin has not replied within the
configured inactivity window. The worker sends one natural reply, then waits for
new human activity before another auto-reply can happen.
"""

import os
import time
import json
import logging
import redis
from typing import Dict, Any

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
AUTO_FOLLOWUP_COOLDOWN = int(os.getenv("AUTO_FOLLOWUP_COOLDOWN", "1800"))
STATE_TTL_SECONDS = int(os.getenv("AUTO_FOLLOWUP_STATE_TTL_SECONDS", str(86400 * 2)))

ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

if not ADMIN_ID:
    logger.warning("⚠️ ADMIN_ID not set in environment — Auto followup will be disabled.")

if r is None or not ADMIN_ID:
    logger.warning("⚠️ Auto Smart Follow-up feature is currently DISABLED.")


def _get_key(chat_id: int) -> str:
    return f"conv_state:{chat_id}"


def _now() -> float:
    return time.time()


def _load_state(chat_id: int) -> Dict[str, Any]:
    if r is None:
        return {}

    try:
        raw = r.get(_get_key(chat_id))
        return json.loads(raw) if raw else {}
    except Exception as e:
        logger.error(f"Redis state load error: {e}")
        return {}


def _save_state(chat_id: int, data: Dict[str, Any]) -> None:
    if r is None:
        return

    try:
        r.set(_get_key(chat_id), json.dumps(data), ex=STATE_TTL_SECONDS)
    except Exception as e:
        logger.error(f"Redis state save error: {e}")


def track_incoming_message(chat_id: int, user_id: str, text: str):
    """Call this when a non-admin person sends a message."""
    if not ADMIN_ID or r is None:
        return

    current = _load_state(chat_id)
    data = {
        **current,
        "chat_id": chat_id,
        "last_sender_id": str(user_id),
        "last_incoming_user_id": str(user_id),
        "last_incoming_time": _now(),
        "last_incoming_message": (text or "")[:800],
        "last_message": (text or "")[:800],
        "user_id": str(user_id),
        "pending_admin_reply": True,
        "auto_replied": False,
    }
    _save_state(chat_id, data)


def track_admin_reply(chat_id: int, user_id: str, text: str):
    """Call this when ADMIN replies, so the auto-responder stands down."""
    if not ADMIN_ID or r is None:
        return

    current = _load_state(chat_id)
    data = {
        **current,
        "chat_id": chat_id,
        "last_sender_id": str(user_id),
        "last_admin_time": _now(),
        "last_admin_message": (text or "")[:800],
        "pending_admin_reply": False,
        "auto_replied": False,
    }
    _save_state(chat_id, data)


def track_user_message(chat_id: int, user_id: str, text: str):
    """Backward-compatible wrapper used by older entrypoints."""
    if str(user_id) == ADMIN_ID:
        track_admin_reply(chat_id, user_id, text)
    else:
        track_incoming_message(chat_id, user_id, text)


def get_inactive_conversations() -> list[Dict[str, Any]]:
    """Return chats where a non-admin message is waiting for admin reply."""
    if not ADMIN_ID or r is None:
        return []

    inactive = []
    now = _now()
    try:
        for key in r.scan_iter("conv_state:*"):
            try:
                raw = r.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                if not data.get("pending_admin_reply"):
                    continue
                if data.get("auto_replied"):
                    continue

                last_time = float(data.get("last_incoming_time", 0))
                last_followup = float(data.get("last_followup_time", 0))

                if now - last_time > INACTIVITY_SECONDS:
                    if now - last_followup < AUTO_FOLLOWUP_COOLDOWN:
                        continue
                    inactive.append(data)
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Redis scan error: {e}")
    return inactive


def generate_smart_followup(last_message: str, user_id: str, chat_id: int) -> str:
    prompt = f"""Telegram par saamne wale person ka last message tha: "{last_message}"

Admin ne 5 minute ke andar reply nahi kiya, isliye tum admin ki taraf se ek natural human-like reply bhej rahe ho.
Rules:
- Aisa na lage ki bot auto-message kar raha hai
- Last message ka context pakdo aur simple response do
- Agar context unclear ho toh ek warm, light question poochho
- Zyada pushy, salesy, ya robotic mat banao
- Hinglish/English wahi use karo jo message ke tone se natural lage
- Max 1-2 short lines

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

    data = _load_state(chat_id)
    if data:
        data["last_followup_time"] = _now()
        data["auto_replied"] = True
        data["pending_admin_reply"] = False
        _save_state(chat_id, data)


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
