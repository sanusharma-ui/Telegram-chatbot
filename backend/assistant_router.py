import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict

from backend.gmail_nl_agent import route_dynamic_gmail
from backend.groq_handler import generate_response

logger = logging.getLogger(__name__)

SendFunc = Callable[[str], Awaitable[None]]

# very simple in-memory session state
USER_STATE: Dict[str, Dict[str, Any]] = {}


def _state(user_id: str) -> Dict[str, Any]:
    if user_id not in USER_STATE:
        USER_STATE[user_id] = {
            "last_search_results": [],
            "selected_message_id": None,
            "selected_thread_id": None,
            "last_draft_id": None,
            "pending_action": None,
        }
    return USER_STATE[user_id]


async def handle_assistant_message(
    user_id: str,
    chat_id: int,
    text: str,
    send_func: SendFunc,
) -> None:
    st = _state(user_id)

    try:
        gmail_reply = await asyncio.to_thread(route_dynamic_gmail, user_id, text, st)
        if gmail_reply is not None:
            await send_func(gmail_reply)
            return
    except Exception:
        logger.exception("dynamic gmail routing failed")

    try:
        reply = await asyncio.to_thread(
            generate_response,
            user_message=text,
            persona_key=user_id,
            user_ip=user_id,
        )
        await send_func(reply)
    except Exception:
        logger.exception("chat fallback failed")
        await send_func("Reply generate karte waqt issue aaya.")