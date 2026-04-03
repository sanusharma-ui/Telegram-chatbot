import os
import json
import logging
from typing import Any, Dict, List, Optional

from groq import Groq

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

AGENT_MODEL = os.getenv("GMAIL_AGENT_MODEL", "llama-3.3-70b-versatile")
MAX_TOOL_ROUNDS = 4

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
    "gmail", "mail", "email", "inbox", "draft", "reply",
    "subject", "recipient", "thread", "attachment", "attachments",
    "archive", "unread", "star", "label", "labels", "send",
)


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
    text = (user_text or "").strip()
    lower = text.lower()
    if not lower:
        return False

    _, state = _load_agent_memory(user_id)

    if state.get("pending_action"):
        return True

    if any(word in lower for word in GMAIL_HINT_WORDS):
        return True

    if "@" in text and any(word in lower for word in ("send", "mail", "email", "draft", "reply", "subject")):
        return True

    if state.get("last_draft_id") and any(
        phrase in lower for phrase in ("send it", "send this", "mail it", "bhej do", "bhejo")
    ):
        return True

    if state.get("last_search_results") and any(
        phrase in lower
        for phrase in ("first", "second", "third", "that one", "last one", "read it", "open it", "reply to that")
    ):
        return True

    return False


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
            "description": "Create a Gmail draft. Prefer this before sending a new email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
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
    state["pending_action"] = None
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


def _tool_create_draft(user_id: str, to: str, subject: str, body: str) -> Dict[str, Any]:
    if not _gmail_connected(user_id):
        return {"ok": False, "error": "gmail_not_connected"}

    created = create_draft(user_id, to, subject, body)
    if not created or not created.get("id"):
        return {"ok": False, "error": "draft_create_failed"}

    draft_id = created["id"]

    mem, state = _load_agent_memory(user_id)
    state["last_draft_id"] = draft_id
    state["pending_action"] = None
    _save_agent_memory(user_id, mem)

    return {
        "ok": True,
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "body_preview": body[:1000],
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

SYSTEM_PROMPT = """
You are Aisha, a natural conversational assistant inside a Telegram bot.

You can:
- chat normally,
- understand natural language,
- perform Gmail actions through tools.

Rules:
- If the user wants Gmail help, use tools instead of telling them to type commands.
- If Gmail is not connected, call gmail_connect.
- Prefer creating drafts before sending a new email.
- For sending drafts, deleting emails, deleting labels, or disconnecting Gmail, use confirmed=false first unless the user has explicitly confirmed in the current message.
- You may use the recent search results and labels shown in system state to resolve phrases like "first one", "that email", "that label", or "send it".
- Never invent IDs or tool results.
- Keep replies concise, clear, and natural.
- If the request is not about Gmail, you may still reply normally.

When a tool returns needs_confirmation=true, ask a short confirmation question.
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


def _execute_tool(user_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    fn = TOOL_IMPL.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown_tool:{name}"}

    try:
        return fn(user_id=user_id, **arguments)
    except TypeError as e:
        logger.exception("Bad tool args for %s: %s", name, e)
        return {"ok": False, "error": f"bad_arguments:{name}"}
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

    pending_result = _handle_pending_confirmation(user_id, user_text)
    if pending_result:
        reply = pending_result["reply"]
        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, reply)
        _save_agent_memory(user_id, mem)
        return {"reply": reply, "ui_actions": []}

    messages = _build_messages(user_id, user_text)
    ui_actions: List[Dict[str, Any]] = []

    for _ in range(MAX_TOOL_ROUNDS):
        completion = client.chat.completions.create(
            model=AGENT_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=700,
        )

        msg = completion.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls:
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

            for tc in tool_calls:
                raw_args = tc.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args)
                except Exception:
                    parsed_args = {}

                result = _execute_tool(user_id, tc.function.name, parsed_args)

                if result.get("ui_action"):
                    ui_actions.append(result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": _json(result),
                    }
                )
            continue

        reply = (msg.content or "").strip() or "Done."

        mem, _ = _load_agent_memory(user_id)
        _append_turn(mem, user_text, reply)
        _save_agent_memory(user_id, mem)

        return {"reply": reply, "ui_actions": ui_actions}

    reply = "I completed part of the task, but the tool loop hit its safety limit. Please continue with a follow-up message."
    mem, _ = _load_agent_memory(user_id)
    _append_turn(mem, user_text, reply)
    _save_agent_memory(user_id, mem)
    return {"reply": reply, "ui_actions": ui_actions}