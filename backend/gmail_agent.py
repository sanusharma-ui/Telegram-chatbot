import os
import json
import logging
import re
import time
import random
from typing import Any, Dict, List, Optional, TypedDict, Literal

import groq
from groq import Groq, RateLimitError

from backend.gmail_integration import (
    get_auth_url_for_user,
    gmail_summary,
    gmail_smart_summary,
    create_draft,
    send_message_from_draft,
    disconnect_user,
    _get_gmail_service_for_user,
)
from backend.gmail_search import search_messages
from backend.gmail_inbox_ops import (
    read_full_email,
    mark_read,
    mark_unread,
    star_messages,
    unstar_messages,
    archive_messages,
    delete_messages,
    _get_message,
)
from backend.gmail_labels import list_labels, create_label, delete_label
from backend.gmail_threads import summarize_thread_ai
from backend.gmail_drafts import update_draft
from backend.gmail_attachments import list_attachments
from backend.groq_handler import load_persona_memory, save_persona_memory
from backend.safety_engine import (
    fast_harm_check,
    detect_harm_category,
    detect_suicide_emergency,
    CRISIS_RESPONSES,
)

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found! Please check your .env file.")

client = Groq(api_key=GROQ_API_KEY)

# === MODEL FALLBACK CHAIN (cheaper/faster models at the end) ===
AGENT_MODEL = os.getenv("GMAIL_AGENT_MODEL", "llama-3.3-70b-versatile")
AGENT_MODEL_FALLBACKS = [
    AGENT_MODEL,
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]

MAX_TOOL_ROUNDS = int(os.getenv("GMAIL_AGENT_MAX_TOOL_ROUNDS", "3"))

CONFIRM_WORDS = {
    "yes", "y", "haan", "ha", "hmm yes", "confirm", "confirmed",
    "ok", "okay", "ok send", "send it", "send now", "do it",
    "kar do", "bhej do", "bhejo", "yes send it", "yes send",
}

DENY_WORDS = {
    "no", "n", "cancel", "stop", "mat karo", "rehne do",
    "dont", "don't", "no thanks", "cancel it",
}

GMAIL_HINT_WORDS = (
    "gmail", "mail", "mails", "email", "emails", "inbox", "draft",
    "reply", "subject", "recipient", "thread", "attachment",
    "attachments", "archive", "unread", "star", "label", "labels",
    "send", "latest mail", "latest mails", "recent mail",
    "recent mails", "meri mail", "meri mails", "mail dikhao",
    "mails dikhao", "email dikhao", "emails dikhao", "inbox me",
    "mail kholo", "mail open", "reply karo", "draft banao",
    "label banao", "archive karo", "bhej do", "bhejo",
)

# === NEW: Smart Routing Helpers ===
class RouteResult(TypedDict, total=False):
    route: Literal["general_chat", "gmail", "clarify", "confirm_pending"]
    confidence: float
    clarification: str
    gmail_action: str
    reason: str
    cancel_pending: bool
    confirm_pending: bool


ROLEPLAY_HINTS = (
    "roleplay", "role play", "fake mail", "fictional", "pretend", "pretending",
    "sample mail", "demo mail", "mock mail", "example mail", "as if", "simulate",
    "imaginary", "made up", "story mail", "character mail"
)

GMAIL_HINTS_STRICT = (
    "gmail", "mail", "email", "inbox", "draft", "reply", "thread", "attachment",
    "attachments", "subject", "send", "archive", "delete", "label", "labels",
    "read", "open mail", "search mail", "compose", "forward", "unread", "star",
    "dikhao", "dikhado", "khojo", "dhundo", "dhoondo", "talash", "padho",
    "kholo", "bhejna", "bhejo", "bhej do", "connect karo", "latest", "recent"
)

SEARCH_HINTS = (
    "search", "find", "look for", "show me", "dikhao", "dikhado", "khojo",
    "dhundo", "dhoondo", "talash", "nikaalo", "nikalo"
)

INBOX_HINTS = (
    "inbox", "latest", "recent", "new mail", "new mails", "latest mail",
    "latest mails", "recent mail", "recent mails", "aayi hai", "aaya hai"
)

READ_HINTS = ("read", "open", "padho", "kholo", "dikhao full", "full mail")

ARCHIVE_HINTS = ("archive", "hata do inbox", "inbox se hata")

DELETE_HINTS = ("delete", "remove", "trash", "permanent", "mita do")

STAR_HINTS = ("star", "favorite", "favourite", "important mark")

UNREAD_HINTS = ("unread", "mark unread", "unread mark")

READ_MARK_HINTS = ("mark read", "read mark", "seen mark")

ORDINAL_WORDS = {
    "first": 1, "1st": 1, "one": 1, "pehla": 1, "pehli": 1, "pahla": 1, "pahli": 1,
    "second": 2, "2nd": 2, "dusra": 2, "dusri": 2, "doosra": 2, "doosri": 2,
    "third": 3, "3rd": 3, "teesra": 3, "teesri": 3,
    "fourth": 4, "4th": 4, "chautha": 4, "chauthi": 4,
    "fifth": 5, "5th": 5, "paanchwa": 5, "panchwa": 5,
    "last": -1, "latest": 1, "recent": 1, "that": 0, "this": 0, "it": 0,
    "wo": 0, "woh": 0, "ye": 0, "yeh": 0, "us": 0, "ussi": 0,
}

GMAIL_OBJECT_HINTS = (
    "gmail", "mail", "mails", "email", "emails", "inbox", "draft", "reply",
    "thread", "attachment", "attachments", "subject", "label", "labels"
)

CONFIRM_HINTS = (
    "yes", "y", "haan", "ha", "hmm yes", "confirm", "confirmed",
    "ok", "okay", "send it", "send now", "do it", "kar do", "bhej do", "bhejo"
)

DENY_HINTS = (
    "no", "n", "cancel", "stop", "mat karo", "rehne do", "don't", "dont", "nahin"
)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _looks_like_roleplay(text: str) -> bool:
    text = _clean_text(text)
    return any(h in text for h in ROLEPLAY_HINTS)


def _looks_like_gmail(text: str) -> bool:
    text = _clean_text(text)
    if "@" in text:
        return True
    if any(h in text for h in GMAIL_OBJECT_HINTS):
        return True
    if any(h in text for h in ("first one", "pehli wali", "that mail", "ye mail", "woh mail")):
        return True
    return False


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    text = _clean_text(text)
    return any(h in text for h in hints)


def _extract_email_addresses(text: str) -> List[str]:
    return re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text or "")


def _extract_max_results(text: str, default: int = 10) -> int:
    match = re.search(r"\b(\d{1,2})\b", text or "")
    if not match:
        return default
    return max(1, min(int(match.group(1)), 20))


def _extract_ordinal(text: str) -> Optional[int]:
    lower = _clean_text(text)
    for word, value in ORDINAL_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return value

    match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", lower)
    if match:
        return max(1, int(match.group(1)))
    return None


def _build_search_query(user_text: str) -> str:
    text = _clean_text(user_text)
    emails = _extract_email_addresses(user_text)

    if emails:
        if any(x in text for x in ("from", "sender", "bheja", "bheji", "se mail", "se email")):
            return f"from:{emails[0]}"
        if any(x in text for x in ("to", "sent to", "ko bheja", "ko mail")):
            return f"to:{emails[0]}"
        return emails[0]

    subject_match = re.search(r"(?:subject|sub(?:ject)? me|title me)\s+(.+)$", text)
    if subject_match:
        keyword = subject_match.group(1).strip(" '\"")
        return f"subject:{keyword}" if keyword else "in:inbox"

    quoted = re.findall(r"['\"]([^'\"]{2,80})['\"]", user_text or "")
    if quoted:
        return quoted[0].strip()

    cleaned = text
    cleanup_phrases = (
        "email search karo", "emails search karo", "mail search karo", "mails search karo",
        "search email", "search mail", "search mails", "find email", "find mail",
        "email dikhao", "emails dikhao", "mail dikhao", "mails dikhao",
        "khojo", "dhundo", "dhoondo", "talash karo", "show me", "look for",
        "please", "pls", "bhai", "yaar", "mere", "meri", "mera", "ke", "ka", "ki"
    )
    for phrase in cleanup_phrases:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;")

    if not cleaned or cleaned in {"email", "emails", "mail", "mails", "inbox"}:
        return "in:inbox"
    return cleaned


def _looks_like_confirmation(text: str) -> bool:
    text = _clean_text(text)
    return text in CONFIRM_HINTS or any(text.startswith(x + " ") for x in CONFIRM_HINTS)


def _looks_like_rejection(text: str) -> bool:
    text = _clean_text(text)
    return text in DENY_HINTS or any(text.startswith(x + " ") for x in DENY_HINTS)


def _safe_json_loads(raw: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    default = default or {}
    try:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw.replace("json\n", "", 1).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
        data = json.loads(raw)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


# === Robust Groq wrapper with model fallback (FIXED: never pass tool_choice=None, broad fallback on ALL errors) ===
def _safe_agent_completion(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict]] = None,
    tool_choice: str = "auto",
    temperature: float = 0.2,
    max_tokens: int = 700,
) -> Any:
    """
    Calls Groq with automatic fallback across models.
    FIXED: 
    - Never passes tool_choice when tools=None (prevents 400 invalid_request_error)
    - Broad except on ANY error so fallback actually tries all models (rate limit, 400, server errors etc.)
    - Better logging and sleep strategy
    """
    last_error = None

    for model_name in AGENT_MODEL_FALLBACKS:
        try:
            logger.info(f"→ Trying Groq model: {model_name}")
            
            create_kwargs = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if tools:
                create_kwargs["tools"] = tools
                create_kwargs["tool_choice"] = tool_choice

            resp = client.chat.completions.create(**create_kwargs)
            logger.info(f"✓ Success with model: {model_name}")
            return resp

        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            
            if isinstance(e, RateLimitError) or "429" in err_str or "rate" in err_str or "tpd" in err_str:
                logger.warning(f"⚠ Rate limit/TPD hit on {model_name}. Trying next model...")
                time.sleep(2.0 + random.uniform(0, 2.0))
            elif "invalid_request_error" in err_str or "400" in err_str:
                logger.warning(f"⚠ Bad request (400) on {model_name} (tool_choice/params issue?). Trying next model anyway...")
                time.sleep(0.8)
            else:
                logger.warning(f"⚠ Unexpected error on {model_name}: {e}. Trying next model...")
                time.sleep(1.2)
            continue

    # All models failed
    logger.error(f"❌ All Groq models exhausted. Last error: {last_error}")
    raise last_error or RuntimeError("All Groq fallback models failed.")


# === NEW: Dynamic Router (LLM + Rules) ===
ROUTER_SYSTEM_PROMPT = """
You are a message router inside a chat assistant.

Your job:
- Decide whether the user is asking for general chat, Gmail help, or needs clarification.
- Do NOT force Gmail just because the text mentions "mail" casually.
- Use "clarify" when the message is ambiguous.
- Use "clarify" when the user seems to want a fake/example/roleplay email but has not explicitly said example/demo/sample/roleplay.
- Use "general_chat" for normal questions, opinions, explanations, advice, and anything not clearly about Gmail.
- Use "confirm_pending" only when the user is clearly confirming or cancelling a previously pending Gmail action.

Return ONLY JSON with this shape:
{
  "route": "general_chat|gmail|clarify|confirm_pending",
  "confidence": 0.0,
  "clarification": "short question if needed",
  "gmail_action": "optional: connect|disconnect|inbox|search|read|thread|compose|reply|send|archive|delete|label|attachments|unknown",
  "reason": "very short"
}
""".strip()


def _llm_route(user_id: str, user_text: str) -> Dict[str, Any]:
    mem, state = _load_agent_memory(user_id)
    recent = mem.get("conversations", [])[-6:]

    messages = [
        {
            "role": "system",
            "content": ROUTER_SYSTEM_PROMPT + "\n\n" + _state_summary(user_id, state),
        }
    ]

    for item in recent:
        role = item.get("role")
        msg = item.get("msg")
        if role in ("user", "assistant") and msg:
            messages.append({"role": role, "content": msg})

    messages.append({"role": "user", "content": user_text})

    try:
        resp = _safe_agent_completion(
            messages=messages,
            temperature=0,
            max_tokens=180,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _safe_json_loads(raw, {})
        route = str(data.get("route", "")).strip()
        if route in {"general_chat", "gmail", "clarify", "confirm_pending"}:
            data["route"] = route
            try:
                data["confidence"] = float(data.get("confidence", 0.5) or 0.5)
            except Exception:
                data["confidence"] = 0.5
            return data
    except Exception as e:
        logger.warning("Router LLM failed (all models): %s", e)

    return {}


def decide_route(user_id: str, user_text: str) -> RouteResult:
    text = _clean_text(user_text)
    mem, state = _load_agent_memory(user_id)

    pending = state.get("pending_action")
    if pending:
        if _looks_like_confirmation(text):
            return {"route": "confirm_pending", "confidence": 1.0, "confirm_pending": True}
        if _looks_like_rejection(text):
            return {"route": "confirm_pending", "confidence": 1.0, "cancel_pending": True}

        if _looks_like_gmail(text):
            return {"route": "gmail", "confidence": 0.85, "gmail_action": "unknown"}

        return {
            "route": "clarify",
            "confidence": 0.95,
            "clarification": "I’m waiting on a yes/no for the last Gmail action. Reply yes to continue or no to cancel.",
        }

    # Strong roleplay guard
    if _looks_like_roleplay(text) and not any(x in text for x in ("real", "actual", "send", "deliver", "to ", "@")):
        return {
            "route": "clarify",
            "confidence": 0.95,
            "clarification": "Do you want a real email draft or just a sample / roleplay version?",
        }

    # Strong Gmail signals
    if _looks_like_gmail(text):
        if any(x in text for x in (
            "draft", "compose", "reply", "send", "inbox", "thread", "read", "search",
            "archive", "delete", "label", "attachment", "attachments", "connect", "disconnect",
            "latest", "recent", "dikhao", "dikhado", "khojo", "dhundo", "dhoondo",
            "padho", "kholo", "bhejo", "bhej do"
        )) or "@" in text:
            return {"route": "gmail", "confidence": 0.9, "gmail_action": "unknown"}

        llm = _llm_route(user_id, user_text)
        if llm:
            return llm
        return {"route": "gmail", "confidence": 0.7, "gmail_action": "unknown"}

    # Not obviously Gmail → ask LLM once
    llm = _llm_route(user_id, user_text)
    if llm:
        return llm

    return {"route": "general_chat", "confidence": 0.8}


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_get_headers(meta: dict) -> dict:
    if not meta:
        return {}
    headers = {}
    for h in meta.get("payload", {}).get("headers", []):
        name = h.get("name")
        value = h.get("value")
        if name and value:
            headers[name] = value
    return headers


def _gmail_connected(user_id: str) -> bool:
    return _get_gmail_service_for_user(user_id) is not None


def _load_agent_memory(user_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    mem = load_persona_memory(user_id)

    user = mem.setdefault("user", {})
    notes = user.setdefault("notes", {})
    state = notes.setdefault(
        "gmail_agent",
        {
            "last_search_results": [],
            "last_labels": [],
            "last_draft_id": None,
            "last_message_id": None,
            "last_thread_id": None,
            "pending_action": None,
        },
    )
    return mem, state


def _save_agent_memory(user_id: str, mem: Dict[str, Any]) -> None:
    save_persona_memory(user_id, mem)


def _append_turn(mem: Dict[str, Any], user_text: str, assistant_text: str) -> None:
    conv = mem.setdefault("conversations", [])
    if user_text:
        conv.append({"role": "user", "msg": user_text[:200]})
    if assistant_text:
        conv.append({"role": "assistant", "msg": assistant_text[:200]})
    if len(conv) > 60:
        mem["conversations"] = conv[-60:]


def should_handle_gmail_message(user_id: str, user_text: str) -> bool:
    route = decide_route(user_id, user_text)
    return route.get("route") in {"gmail", "clarify", "confirm_pending"}


def _execute_pending_action(user_id: str, pending: Dict[str, Any]) -> str:
    action_type = pending.get("type")

    if action_type == "send_draft":
        draft_id = pending.get("draft_id")
        ok = send_message_from_draft(user_id, draft_id)
        return "✅ Mail sent." if ok else "❌ Failed to send draft."

    if action_type == "delete_messages":
        message_ids = pending.get("message_ids", [])
        ok = delete_messages(user_id, message_ids)
        return "🗑️ Messages deleted permanently." if ok else "❌ Failed to delete messages."

    if action_type == "delete_label":
        label_id = pending.get("label_id")
        ok = delete_label(user_id, label_id)
        return "✅ Label deleted." if ok else "❌ Failed to delete label."

    if action_type == "disconnect":
        disconnect_user(user_id)
        return "✅ Gmail disconnected."

    return "❌ Unknown pending action."


def _handle_pending_confirmation(user_id: str, user_text: str) -> Optional[Dict[str, Any]]:
    mem, state = _load_agent_memory(user_id)
    pending = state.get("pending_action")
    if not pending:
        return None

    lower = (user_text or "").strip().lower()

    if lower in DENY_WORDS or lower.startswith("no "):
        state["pending_action"] = None
        _save_agent_memory(user_id, mem)
        return {"handled": True, "reply": "Okay, cancelled."}

    if (
        lower in CONFIRM_WORDS
        or lower.startswith("yes ")
        or "confirm" in lower
        or lower == "send"
    ):
        reply = _execute_pending_action(user_id, pending)
        state["pending_action"] = None
        _save_agent_memory(user_id, mem)
        return {"handled": True, "reply": reply}

    return None


def _state_summary(user_id: str, state: Dict[str, Any]) -> str:
    search_results = state.get("last_search_results", [])[:5]
    labels = state.get("last_labels", [])[:10]

    search_text = "\n".join(
        [
            f"{idx}. message_id={item.get('message_id')} | thread_id={item.get('thread_id')} | from={item.get('from')} | subject={item.get('subject')}"
            for idx, item in enumerate(search_results, start=1)
        ]
    ) or "none"

    label_text = "\n".join(
        [f"{idx}. label_id={item.get('id')} | name={item.get('name')}" for idx, item in enumerate(labels, start=1)]
    ) or "none"

    return f"""
Current Gmail state for this user:
- gmail_connected: {_gmail_connected(user_id)}
- last_draft_id: {state.get("last_draft_id") or "none"}
- last_message_id: {state.get("last_message_id") or "none"}
- last_thread_id: {state.get("last_thread_id") or "none"}
- pending_action: {_json(state.get("pending_action")) if state.get("pending_action") else "none"}

Recent search results:
{search_text}

Recent labels:
{label_text}
""".strip()


def _connect_needed_response(user_id: str) -> Dict[str, Any]:
    try:
        connect_action = _tool_connect(user_id)
        return {
            "reply": "Gmail abhi connected nahi hai. Pehle securely connect kar lo, phir main inbox/search/draft ka kaam kar dunga.",
            "ui_actions": [connect_action],
        }
    except Exception:
        return {
            "reply": "Gmail abhi connected nahi hai. `/gmail connect` use karke pehle connect kar lo.",
            "ui_actions": [],
        }


def _resolve_message_ids_from_state(state: Dict[str, Any], user_text: str, allow_many: bool = False) -> List[str]:
    text = _clean_text(user_text)
    explicit_ids = re.findall(r"\b(?:msg_|[a-f0-9]{12,})\b", user_text or "", flags=re.IGNORECASE)
    if explicit_ids:
        return explicit_ids

    results = state.get("last_search_results", []) or []
    if not results:
        last_id = state.get("last_message_id")
        return [last_id] if last_id else []

    if allow_many and any(x in text for x in ("all", "sab", "saare", "sare", "these", "ye sab", "those")):
        return [item.get("message_id") for item in results if item.get("message_id")]

    ordinal = _extract_ordinal(text)
    if ordinal == -1:
        item = results[-1]
    elif ordinal and ordinal > 0:
        item = results[ordinal - 1] if len(results) >= ordinal else None
    else:
        item = results[0] if any(x in text for x in ("that", "this", "it", "wo", "woh", "ye", "yeh", "us")) else None

    if item and item.get("message_id"):
        return [item["message_id"]]

    last_id = state.get("last_message_id")
    return [last_id] if last_id else []


def _resolve_thread_id_from_state(state: Dict[str, Any], user_text: str) -> Optional[str]:
    explicit = re.search(r"\bthread(?:_id)?[:\s]+([A-Za-z0-9_-]+)", user_text or "", flags=re.IGNORECASE)
    if explicit:
        return explicit.group(1)

    results = state.get("last_search_results", []) or []
    ordinal = _extract_ordinal(user_text)
    if results:
        if ordinal == -1:
            item = results[-1]
        elif ordinal and ordinal > 0 and len(results) >= ordinal:
            item = results[ordinal - 1]
        else:
            item = results[0]
        return item.get("thread_id")
    return state.get("last_thread_id")


def _format_search_results(results: List[Dict[str, Any]]) -> str:
    if not results:
        return "Koi matching email nahi mili."

    lines = ["Ye emails mili:"]
    for idx, item in enumerate(results[:8], start=1):
        sender = item.get("from") or "Unknown sender"
        subject = item.get("subject") or "(no subject)"
        message_id = item.get("message_id") or ""
        lines.append(f"{idx}. {sender} | {subject}\nID: `{message_id}`")
    lines.append("Agar kisi ko kholna ho toh bolo: `pehli wali kholo` ya message ID bhejo.")
    return "\n".join(lines)


def _format_tool_result_for_user(tool_name: str, result: Dict[str, Any]) -> str:
    if result.get("error") == "gmail_not_connected":
        return "Gmail abhi connected nahi hai. Pehle connect karna padega."
    if result.get("needs_confirmation"):
        return result.get("message", "Is action ke liye confirmation chahiye. Reply yes to continue or no to cancel.")
    if result.get("needs_clarification"):
        return result.get("message", "Thoda clear batao kya karna hai?")

    if tool_name == "gmail_inbox_summary":
        return str(result.get("data") or "No recent emails found.")
    if tool_name == "gmail_search":
        return _format_search_results(result.get("results", []) or [])
    if tool_name == "gmail_read_message":
        email = result.get("email") or {}
        body = (email.get("body") or "").strip()
        return (
            f"From: {email.get('from')}\n"
            f"Subject: {email.get('subject')}\n"
            f"Date: {email.get('date')}\n\n"
            f"{body[:3500] if body else '(No plain-text body found.)'}"
        )
    if tool_name == "gmail_summarize_thread":
        return str(result.get("summary") or "Thread summarize nahi ho paya.")
    if tool_name == "gmail_modify_messages":
        return "Done." if result.get("ok") else f"Action failed: {result.get('error', 'unknown error')}"
    if tool_name == "gmail_create_label":
        label = result.get("label") or {}
        return f"Label created: {label.get('name', 'new label')}" if result.get("ok") else "Label create nahi ho paya."
    if tool_name == "gmail_list_labels":
        labels = result.get("labels") or []
        if not labels:
            return "Koi labels nahi mile."
        return "\n".join(["Your labels:"] + [f"- {x.get('name')} (`{x.get('id')}`)" for x in labels[:30]])
    if tool_name == "gmail_list_attachments":
        attachments = result.get("attachments") or []
        if not attachments:
            return "Is email me attachments nahi mile."
        return "\n".join(["Attachments:"] + [f"- {x.get('filename') or x.get('name') or 'attachment'}" for x in attachments])
    if tool_name == "gmail_create_draft":
        return (
            "Draft created.\n\n"
            f"To: {result.get('to')}\n"
            f"Subject: {result.get('subject')}\n\n"
            f"{result.get('body_preview')}\n\n"
            f"Send karna ho toh bolo: `send draft {result.get('draft_id')}`"
        )

    return "Done." if result.get("ok") else f"Action failed: {result.get('error', 'unknown error')}"


def _run_tool_and_format(user_id: str, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    result = _execute_tool(user_id, tool_name, args)
    ui_actions = [result] if result.get("ui_action") else []
    return {"reply": _format_tool_result_for_user(tool_name, result), "ui_actions": ui_actions}


def _try_direct_gmail_action(user_id: str, user_text: str) -> Optional[Dict[str, Any]]:
    text = _clean_text(user_text)
    mem, state = _load_agent_memory(user_id)

    if "disconnect" in text and not _gmail_connected(user_id):
        return {"reply": "Gmail already connected nahi lag raha, disconnect karne ke liye kuch pending nahi hai.", "ui_actions": []}

    if not _gmail_connected(user_id):
        return _connect_needed_response(user_id)

    if "connect" in text and "disconnect" not in text:
        return _run_tool_and_format(user_id, "gmail_connect", {})

    explicit_search = any(x in text for x in ("search", "find", "look for", "khojo", "dhundo", "dhoondo", "talash"))

    if _contains_any(text, INBOX_HINTS) and not explicit_search and not _extract_email_addresses(user_text):
        return _run_tool_and_format(
            user_id,
            "gmail_inbox_summary",
            {"smart": "summary" in text or "smart" in text, "max_results": _extract_max_results(text, 10)},
        )

    if explicit_search or _extract_email_addresses(user_text) or (
        _contains_any(text, SEARCH_HINTS) and any(x in text for x in GMAIL_OBJECT_HINTS)
    ):
        query = _build_search_query(user_text)
        return _run_tool_and_format(
            user_id,
            "gmail_search",
            {"query": query, "max_results": _extract_max_results(text, 10)},
        )

    if "thread" in text or "conversation" in text:
        thread_id = _resolve_thread_id_from_state(state, user_text)
        if not thread_id:
            return {"reply": "Kaunsa thread summarize karna hai? Pehle search result ya thread ID bhejo.", "ui_actions": []}
        return _run_tool_and_format(user_id, "gmail_summarize_thread", {"thread_id": thread_id})

    if _contains_any(text, READ_HINTS) or _extract_ordinal(text) is not None:
        message_ids = _resolve_message_ids_from_state(state, user_text)
        if not message_ids:
            return {"reply": "Kaunsi email kholni hai? Pehle search/inbox result dikhao ya message ID bhejo.", "ui_actions": []}
        return _run_tool_and_format(user_id, "gmail_read_message", {"message_id": message_ids[0]})

    action = None
    if _contains_any(text, ARCHIVE_HINTS):
        action = "archive"
    elif _contains_any(text, DELETE_HINTS):
        action = "delete"
    elif _contains_any(text, STAR_HINTS):
        action = "star"
    elif _contains_any(text, UNREAD_HINTS):
        action = "unread"
    elif _contains_any(text, READ_MARK_HINTS):
        action = "read"

    if action:
        message_ids = _resolve_message_ids_from_state(state, user_text, allow_many=True)
        if not message_ids:
            return {"reply": "Kaunsi email par action lena hai? Search result number ya message ID batao.", "ui_actions": []}
        return _run_tool_and_format(
            user_id,
            "gmail_modify_messages",
            {"action": action, "message_ids": message_ids, "confirmed": False},
        )

    if "attachment" in text or "attachments" in text:
        message_ids = _resolve_message_ids_from_state(state, user_text)
        if not message_ids:
            return {"reply": "Kaunsi email ke attachments dekhne hain? Message ID ya result number batao.", "ui_actions": []}
        return _run_tool_and_format(user_id, "gmail_list_attachments", {"message_id": message_ids[0]})

    if "label" in text and any(x in text for x in ("list", "dikhao", "show")):
        return _run_tool_and_format(user_id, "gmail_list_labels", {})

    return None


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "gmail_connect",
            "description": "Start Gmail OAuth connect flow for this user.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_disconnect",
            "description": "Disconnect Gmail for this user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmed": {"type": "boolean"}
                },
                "required": ["confirmed"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_inbox_summary",
            "description": "Get recent inbox emails. Use smart=true for AI summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "smart": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search",
            "description": "Search Gmail using Gmail search operators like from:, subject:, newer_than:, in:inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_read_message",
            "description": "Read the full body of an email by Gmail message ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"}
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_summarize_thread",
            "description": "Summarize an email thread by thread ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"}
                },
                "required": ["thread_id"],
                "additionalProperties": False,
            },
        },
    },
     {
        "type": "function",
        "function": {
            "name": "gmail_create_draft",
            "description": "Create a Gmail draft using natural instructions. Best tool when user wants to send or reply to an email. Hinglish instructions allowed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["to", "subject", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_update_draft",
            "description": "Update an existing Gmail draft.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string"},
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["draft_id", "to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_send_draft",
            "description": "Send an existing draft by draft ID. Use confirmed=false first unless the user has clearly confirmed now.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["draft_id", "confirmed"],
                "additionalProperties": False,
            },
        },
    },
      
    {
        "type": "function",
        "function": {
            "name": "gmail_modify_messages",
            "description": "Mark messages read/unread, star/unstar, archive, or delete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "unread", "star", "unstar", "archive", "delete"],
                    },
                    "message_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action", "message_ids", "confirmed"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_list_labels",
            "description": "List Gmail labels.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_create_label",
            "description": "Create a Gmail label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_delete_label",
            "description": "Delete a Gmail label by label ID. Use confirmed=false first unless user explicitly confirmed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["label_id", "confirmed"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_list_attachments",
            "description": "List attachments on an email message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"}
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
        },
    },
]


def _tool_connect(user_id: str) -> Dict[str, Any]:
    url = get_auth_url_for_user(user_id, need_send=True)
    return {
        "ok": True,
        "ui_action": "gmail_connect",
        "connect_url": url,
        "message": "OAuth link generated.",
    }


def _tool_disconnect(user_id: str, confirmed: bool) -> Dict[str, Any]:
    mem, state = _load_agent_memory(user_id)

    if not confirmed:
        state["pending_action"] = {"type": "disconnect"}
        _save_agent_memory(user_id, mem)
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": "Disconnecting Gmail needs confirmation.",
        }

    disconnect_user(user_id)
    state["pending_action"] = None
    _save_agent_memory(user_id, mem)
    return {"ok": True, "message": "Gmail disconnected."}


def _tool_inbox_summary(user_id: str, smart: bool = False, max_results: int = 10) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    max_results = _as_int(max_results, 10)
    data = gmail_smart_summary(user_id) if smart else gmail_summary(user_id, max_results=max_results)
    return {"ok": True, "data": data or "No recent emails found."}


def _tool_search(user_id: str, query: str, max_results: int = 10) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    max_results = _as_int(max_results, 10)
    results = search_messages(user_id, query, max_results=max_results) or []

    svc = _get_gmail_service_for_user(user_id)
    hydrated = []

    for item in results:
        mid = item.get("id")
        tid = item.get("threadId", "")
        sender = "?"
        subject = "(no subject)"

        try:
            meta = _get_message(svc, mid, format="metadata")
            headers = _safe_get_headers(meta)
            sender = headers.get("From", sender)
            subject = headers.get("Subject", subject)
        except Exception:
            pass

        hydrated.append(
            {
                "message_id": mid,
                "thread_id": tid,
                "from": sender,
                "subject": subject,
            }
        )

    mem, state = _load_agent_memory(user_id)
    state["last_search_results"] = hydrated[:10]
    _save_agent_memory(user_id, mem)

    return {"ok": True, "results": hydrated}


def _tool_read_message(user_id: str, message_id: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    email = read_full_email(user_id, message_id)
    if not email:
        return {"ok": False, "error": "message_not_found"}

    mem, state = _load_agent_memory(user_id)
    state["last_message_id"] = email.get("id")
    state["last_thread_id"] = email.get("threadId")
    _save_agent_memory(user_id, mem)

    return {"ok": True, "email": email}


def _tool_summarize_thread(user_id: str, thread_id: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    summary = summarize_thread_ai(user_id, thread_id)

    mem, state = _load_agent_memory(user_id)
    state["last_thread_id"] = thread_id
    _save_agent_memory(user_id, mem)

    return {"ok": True, "summary": summary or "Unable to summarize thread."}


def _tool_create_draft(user_id: str, to: str, subject: str, instructions: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    combined = _clean_text(f"{to} {subject} {instructions}")

    # anti-roleplay / anti-fake-mail guard
    if _looks_like_roleplay(combined) and not any(x in combined for x in ("real", "actual", "sample", "demo", "example")):
        return {
            "ok": False,
            "needs_clarification": True,
            "message": "Do you want a real email draft or just a sample / roleplay version?",
        }

    email_prompt = f"""Write a real, natural email body.

To: {to}
Subject: {subject}

User instructions: {instructions}

Rules:
- Write only a real email draft, not a roleplay scene
- Do not invent facts, commitments, names, or events
- If the user asked for an example/demo/sample, keep it clearly labeled as a sample tone
- Sound human and concise
- Use short paragraphs
- No robotic AI phrases
- No emojis unless the user explicitly asks for them
- Keep it under 250 words
"""

    try:
        # Use safe completion with fallback for email body generation
        body_response = _safe_agent_completion(
            messages=[{"role": "user", "content": email_prompt}],
            temperature=0.5,
            max_tokens=600,
        )
        body = (body_response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("Failed to generate smart email body (all models): %s", e)
        body = instructions  # fallback to raw instructions

    created = create_draft(user_id, to, subject, body)
    if not created or not created.get("id"):
        return {"ok": False, "error": "draft_create_failed"}

    draft_id = created["id"]

    mem, state = _load_agent_memory(user_id)
    state["last_draft_id"] = draft_id
    _save_agent_memory(user_id, mem)

    return {
        "ok": True,
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "body_preview": body[:900] + "..." if len(body) > 900 else body,
    }


def _tool_update_draft(user_id: str, draft_id: str, to: str, subject: str, body: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    updated = update_draft(user_id, draft_id, to, subject, body)

    mem, state = _load_agent_memory(user_id)
    state["last_draft_id"] = draft_id
    _save_agent_memory(user_id, mem)

    return {
        "ok": bool(updated),
        "draft_id": draft_id,
        "updated": bool(updated),
        "body_preview": body[:1000],
    }


def _tool_send_draft(user_id: str, draft_id: str, confirmed: bool) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    mem, state = _load_agent_memory(user_id)

    if not confirmed:
        state["pending_action"] = {"type": "send_draft", "draft_id": draft_id}
        _save_agent_memory(user_id, mem)
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": f"Sending draft {draft_id} needs explicit confirmation.",
        }

    ok = send_message_from_draft(user_id, draft_id)
    state["pending_action"] = None
    state["last_draft_id"] = draft_id
    _save_agent_memory(user_id, mem)

    return {"ok": ok, "sent": ok, "draft_id": draft_id}


def _tool_modify_messages(user_id: str, action: str, message_ids: List[str], confirmed: bool) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    message_ids = message_ids or []
    if not message_ids:
        return {"ok": False, "error": "message_ids_missing"}

    mem, state = _load_agent_memory(user_id)

    if action == "delete" and not confirmed:
        state["pending_action"] = {"type": "delete_messages", "message_ids": message_ids}
        _save_agent_memory(user_id, mem)
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": f"Deleting {len(message_ids)} message(s) needs explicit confirmation.",
        }

    mapping = {
        "read": mark_read,
        "unread": mark_unread,
        "star": star_messages,
        "unstar": unstar_messages,
        "archive": archive_messages,
        "delete": delete_messages,
    }

    fn = mapping[action]
    ok = fn(user_id, message_ids)

    if len(message_ids) == 1:
        state["last_message_id"] = message_ids[0]

    if action == "delete":
        state["pending_action"] = None

    _save_agent_memory(user_id, mem)

    return {"ok": ok, "action": action, "message_ids": message_ids}


def _tool_list_labels(user_id: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    labels = list_labels(user_id) or []

    mem, state = _load_agent_memory(user_id)
    state["last_labels"] = labels[:20]
    _save_agent_memory(user_id, mem)

    return {"ok": True, "labels": labels}


def _tool_create_label(user_id: str, name: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    label = create_label(user_id, name)
    if not label:
        return {"ok": False, "error": "label_create_failed"}

    mem, state = _load_agent_memory(user_id)
    existing = state.get("last_labels", [])
    state["last_labels"] = ([label] + existing)[:20]
    _save_agent_memory(user_id, mem)

    return {"ok": True, "label": label}


def _tool_delete_label(user_id: str, label_id: str, confirmed: bool) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    mem, state = _load_agent_memory(user_id)

    if not confirmed:
        state["pending_action"] = {"type": "delete_label", "label_id": label_id}
        _save_agent_memory(user_id, mem)
        return {
            "ok": False,
            "needs_confirmation": True,
            "message": f"Deleting label {label_id} needs explicit confirmation.",
        }

    ok = delete_label(user_id, label_id)
    state["pending_action"] = None
    state["last_labels"] = [x for x in state.get("last_labels", []) if x.get("id") != label_id]
    _save_agent_memory(user_id, mem)

    return {"ok": ok, "label_id": label_id}


def _tool_list_attachments(user_id: str, message_id: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    items = list_attachments(user_id, message_id) or []
    return {"ok": True, "attachments": items}


TOOL_IMPL = {
    "gmail_connect": _tool_connect,
    "gmail_disconnect": _tool_disconnect,
    "gmail_inbox_summary": _tool_inbox_summary,
    "gmail_search": _tool_search,
    "gmail_read_message": _tool_read_message,
    "gmail_summarize_thread": _tool_summarize_thread,
    "gmail_create_draft": _tool_create_draft,
    "gmail_update_draft": _tool_update_draft,
    "gmail_send_draft": _tool_send_draft,
    "gmail_modify_messages": _tool_modify_messages,
    "gmail_list_labels": _tool_list_labels,
    "gmail_create_label": _tool_create_label,
    "gmail_delete_label": _tool_delete_label,
    "gmail_list_attachments": _tool_list_attachments,
}


# === IMPROVED SYSTEM_PROMPT for better Agentic behavior ===
SYSTEM_PROMPT = """
You are Aisha, a helpful and efficient Gmail assistant inside a Telegram bot.

You can chat normally OR use tools to perform real Gmail actions.

CRITICAL RULES FOR EFFICIENT AGENT BEHAVIOR:
1. Use the MINIMUM number of tools necessary to solve the user's request.
2. After getting search results, if the user wants to read/summarize/draft, call the next tool immediately.
3. Once you have enough information, STOP calling tools and give a clear, natural final reply.
4. Never call the same tool repeatedly with similar arguments.
5. If a tool returns an error (especially "gmail_not_connected"), tell the user clearly instead of retrying.
6. For any destructive action (send, delete, disconnect), always ask for explicit confirmation first using confirmed=false.
7. Never invent email content, recipients, or facts.
8. Keep final replies short, friendly, and in Hinglish when user speaks Hinglish.
9. If the request is not clearly about Gmail, just reply normally without using any Gmail tools.
10. For "search/find/khojo/dhundo/dikhao" requests, use gmail_search. If the user gives an email address, search that address.
11. For "first one/pehli wali/that mail/ye mail" use the current state instead of asking again.

Current state is always shown below. Use it to resolve "first one", "that mail", "last draft" etc.
""".strip()


def _build_messages(user_id: str, user_text: str) -> List[Dict[str, Any]]:
    mem, state = _load_agent_memory(user_id)
    recent_conv = mem.get("conversations", [])[-8:]

    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT + "\n\n" + _state_summary(user_id, state),
        }
    ]

    for item in recent_conv:
        role = item.get("role")
        msg = item.get("msg")
        if role in ("user", "assistant") and msg:
            messages.append({"role": role, "content": msg})

    messages.append({"role": "user", "content": user_text})
    return messages


def _repair_tool_args(user_id: str, name: str, arguments: Dict[str, Any], user_text: str = "") -> Dict[str, Any]:
    args = dict(arguments or {})
    mem, state = _load_agent_memory(user_id)

    if name == "gmail_search":
        query = str(args.get("query") or "").strip()
        if not query or query.lower() in {"email", "mail", "emails", "mails", "search"}:
            args["query"] = _build_search_query(user_text)
        args["max_results"] = _as_int(args.get("max_results"), _extract_max_results(user_text, 10))

    if name == "gmail_inbox_summary":
        args["max_results"] = _as_int(args.get("max_results"), _extract_max_results(user_text, 10))
        args["smart"] = bool(args.get("smart")) or "summary" in _clean_text(user_text)

    if name in {"gmail_read_message", "gmail_list_attachments"} and not args.get("message_id"):
        ids = _resolve_message_ids_from_state(state, user_text)
        if ids:
            args["message_id"] = ids[0]

    if name == "gmail_summarize_thread" and not args.get("thread_id"):
        thread_id = _resolve_thread_id_from_state(state, user_text)
        if thread_id:
            args["thread_id"] = thread_id

    if name == "gmail_modify_messages":
        if not args.get("message_ids"):
            args["message_ids"] = _resolve_message_ids_from_state(state, user_text, allow_many=True)
        action = str(args.get("action") or "").lower()
        if action == "delete":
            args["confirmed"] = False
        elif "confirmed" not in args:
            args["confirmed"] = False

    if name == "gmail_send_draft":
        if not args.get("draft_id") and state.get("last_draft_id"):
            args["draft_id"] = state["last_draft_id"]
        if "confirmed" not in args:
            args["confirmed"] = False

    if name in {"gmail_disconnect", "gmail_delete_label"} and "confirmed" not in args:
        args["confirmed"] = False

    if name == "gmail_delete_label" and not args.get("label_id"):
        labels = state.get("last_labels", []) or []
        ordinal = _extract_ordinal(user_text)
        if labels and ordinal and ordinal > 0 and len(labels) >= ordinal:
            args["label_id"] = labels[ordinal - 1].get("id")

    return args


def _execute_tool(user_id: str, name: str, arguments: Dict[str, Any], user_text: str = "") -> Dict[str, Any]:
    fn = TOOL_IMPL.get(name)
    if not fn:
        logger.warning("Unknown tool called: %s", name)
        return {"ok": False, "error": f"unknown_tool:{name}"}

    arguments = _repair_tool_args(user_id, name, arguments, user_text)

    logger.info("→ Executing tool: %s | args=%s", name, arguments)

    try:
        result = fn(user_id=user_id, **(arguments or {}))
        logger.info("← Tool result (%s): ok=%s, keys=%s", name, result.get("ok"), list(result.keys())[:6])
        return result
    except TypeError as e:
        logger.exception("Bad tool args for %s: %s", name, e)
        return {"ok": False, "error": f"bad_arguments:{name}", "detail": str(e)}
    except Exception as e:
        logger.exception("Tool execution failed for %s: %s", name, e)
        return {"ok": False, "error": f"tool_exception:{name}", "detail": str(e)}


def run_conversational_gmail_agent(user_id: str, user_text: str) -> Dict[str, Any]:
    
    if fast_harm_check(user_text):
        return {"reply": CRISIS_RESPONSES["harm"], "ui_actions": []}

    is_harmful, harm_category = detect_harm_category(user_text)
    if is_harmful:
        if detect_suicide_emergency(user_text):
            reply = CRISIS_RESPONSES.get("suicide_emergency", CRISIS_RESPONSES["suicide"])
        else:
            reply = CRISIS_RESPONSES.get(harm_category, CRISIS_RESPONSES["harm"])

        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, reply)
        _save_agent_memory(user_id, mem)
        return {"reply": reply, "ui_actions": []}

    # === NEW ROUTING BLOCK (with early returns for non-Gmail) ===
    route_info = decide_route(user_id, user_text)
    
    if route_info.get("route") == "clarify":
        reply = route_info.get("clarification", "Bhai isko thoda clear batao, kya karna hai?")
        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, reply)
        _save_agent_memory(user_id, mem)
        return {"reply": reply, "ui_actions": []}
        
    if route_info.get("route") == "general_chat":
        # Bypass heavy Gmail tool loops for standard chats
        messages = _build_messages(user_id, user_text)
        try:
            resp = _safe_agent_completion(messages=messages, tools=None, temperature=0.6, max_tokens=600)
            reply = resp.choices[0].message.content.strip()
        except Exception:
            reply = "Bhai abhi Groq me thoda issue hai, thodi der baad try karna."
        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, reply)
        _save_agent_memory(user_id, mem)
        return {"reply": reply, "ui_actions": []}
 
    pending_result = _handle_pending_confirmation(user_id, user_text)
    if pending_result:
        reply = pending_result["reply"]
        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, reply)
        _save_agent_memory(user_id, mem)
        return {"reply": reply, "ui_actions": []}

    direct_result = _try_direct_gmail_action(user_id, user_text)
    if direct_result:
        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, direct_result.get("reply", "Done."))
        _save_agent_memory(user_id, mem)
        return direct_result

    # === GMAIL PATH: now safe, messages is always defined here ===
    messages = _build_messages(user_id, user_text)
    ui_actions: List[Dict[str, Any]] = []

    tool_round = 0
    reply = ""
    seen_tool_calls = set()

    for _ in range(MAX_TOOL_ROUNDS):
        tool_round += 1
        logger.info("Gmail Agent round %d/%d for user=%s", tool_round, MAX_TOOL_ROUNDS, user_id)

        try:
            completion = _safe_agent_completion(
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=700,
            )
        except Exception as e:
            logger.error(f"Gmail agent LLM call failed after all fallbacks in round {tool_round}: {e}")
            reply = (
                "Bhai Groq ka daily token limit almost khatam ho gaya hai. "
                "Thodi der (10-15 min) baad try karna. Abhi simple requests se kaam chala lo."
            )
            mem, _ = _load_agent_memory(user_id)
            _append_turn(mem, user_text, reply)
            _save_agent_memory(user_id, mem)
            return {"reply": reply, "ui_actions": ui_actions}

        msg = completion.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        # ===================== NO TOOL CALLS → Final Answer =====================
        if not tool_calls:
            reply = (msg.content or "").strip() or "Done."
            break

        # ===================== TOOL CALLS PRESENT =====================
        logger.info("Round %d: Model called %d tool(s): %s", tool_round,
                    len(tool_calls), [tc.function.name for tc in tool_calls])

        assistant_tool_call_payload = []
        for tc in tool_calls:
            assistant_tool_call_payload.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": assistant_tool_call_payload,
            }
        )

        stop_tool_loop = False

        for tc in tool_calls:
            raw_args = tc.function.arguments or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except Exception:
                parsed_args = {}

            call_signature = (tc.function.name, json.dumps(parsed_args, sort_keys=True, ensure_ascii=False))
            if call_signature in seen_tool_calls:
                logger.warning("Duplicate Gmail tool call stopped: %s %s", tc.function.name, parsed_args)
                result = {
                    "ok": False,
                    "error": "duplicate_tool_call_stopped",
                    "message": "The same tool call was already tried. Stop and answer from the available results.",
                }
                stop_tool_loop = True
            else:
                seen_tool_calls.add(call_signature)
                result = _execute_tool(user_id, tc.function.name, parsed_args, user_text=user_text)

            if result.get("ui_action"):
                ui_actions.append(result)

            if (
                result.get("error") in {"gmail_not_connected", "duplicate_tool_call_stopped"}
                or result.get("needs_confirmation")
                or result.get("needs_clarification")
            ):
                stop_tool_loop = True

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.function.name,
                "content": _json(result)
            })

        if stop_tool_loop:
            try:
                final_completion = _safe_agent_completion(
                    messages=messages, tools=None, temperature=0.25, max_tokens=450
                )
                reply = (final_completion.choices[0].message.content or "").strip()
            except Exception:
                reply = "Bhai connection ya permission ka scene hai, please check karo."
            break

    # Safety limit hit
    if tool_round >= MAX_TOOL_ROUNDS and not reply:
        logger.warning("Gmail Agent hit MAX_TOOL_ROUNDS (%d) for user=%s", MAX_TOOL_ROUNDS, user_id)
        reply = (
            "Main thoda zyada tools use kar raha tha. "
            "Thoda simple request se shuru karo ya specific batao kya chahiye (jaise 'latest 3 mails dikhao')."
        )

    if not reply:
        reply = "Bhai request samajh gaya, but complete karne se pehle thoda specific bata do kya action chahiye."

    mem, _ = _load_agent_memory(user_id)
    _append_turn(mem, user_text, reply)
    _save_agent_memory(user_id, mem)

    logger.info("Gmail Agent finished in %d rounds", tool_round)
    return {"reply": reply, "ui_actions": ui_actions}
