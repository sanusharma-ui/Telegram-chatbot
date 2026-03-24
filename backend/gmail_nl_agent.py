import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.gmail_drafts import get_draft, update_draft
from backend.gmail_inbox_ops import (
    _get_message,
    archive_messages,
    delete_messages,
    mark_read,
    mark_unread,
    read_full_email,
    star_messages,
)
from backend.gmail_integration import (
    _get_gmail_service_for_user,
    create_draft,
    get_auth_url_for_user,
    gmail_smart_summary,
    gmail_summary,
    send_message_from_draft,
)
from backend.gmail_labels import apply_label, create_label, delete_label, list_labels
from backend.gmail_search import search_messages
from backend.gmail_threads import summarize_thread_ai
from backend.groq_handler import MODEL_PRIORITY, client

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """
You are a Gmail action planner for a Telegram AI assistant.

Return exactly one JSON object. No markdown. No explanation.

Allowed actions:
- gmail_connect
- gmail_inbox
- gmail_search
- gmail_read
- gmail_thread_summary
- gmail_create_draft
- gmail_update_draft
- gmail_preview_draft
- gmail_send
- gmail_mark_read
- gmail_mark_unread
- gmail_star
- gmail_archive
- gmail_delete
- gmail_labels_list
- gmail_label_create
- gmail_label_delete
- gmail_label_apply
- chat

JSON schema:
{
  "action": "one allowed action",
  "query": "gmail search query or empty string",
  "message_ref": "selected|latest|first|second|third|id:<message_id>|none",
  "message_refs": ["selected|latest|first|second|third|id:<message_id>"],
  "thread_ref": "selected|id:<thread_id>|none",
  "draft_ref": "last|id:<draft_id>|none",
  "to": "recipient email or empty string",
  "subject": "subject or empty string",
  "instruction": "what the email should say / edit instruction / user goal",
  "label_name": "gmail label name or empty string",
  "label_id": "gmail label id or empty string",
  "smart": false,
  "confidence": 0.0,
  "needs_gmail": true,
  "reply_hint": "very short note for the executor"
}

Rules:
- If the user asks to check inbox / important mails, use gmail_inbox.
- If the user asks to find/search/look for emails, use gmail_search.
- If the user asks to open/read/show one of the found mails, use gmail_read.
- If the user asks to summarize the selected thread/conversation, use gmail_thread_summary.
- If the user asks to draft/write/compose/reply to an email, use gmail_create_draft.
- If the user asks to edit/update/change the latest draft, use gmail_update_draft.
- If the user asks to send the latest draft or says bhej do/send it, use gmail_send.
- If the user asks to archive/delete/star/mark read/unread, choose the matching gmail_* action.
- If the user asks to list labels, create a label, delete a label, or apply a label, choose the matching label action.
- If the message is not about Gmail, return action=chat.
- Prefer selected/latest when user refers to "isko", "usko", "that one", "pehla", "latest".
- Use Gmail search operators when helpful, e.g. from:, subject:, newer_than:, in:inbox.
- confidence should be between 0 and 1.
""".strip()

EMAIL_WRITER_SYSTEM_PROMPT = """
You write clean operational emails for a Gmail assistant.

Rules:
- Output plain text only.
- No markdown.
- No emojis.
- No dramatic language.
- No persona, flirting, roleplay, or edgy tone.
- Keep the tone professional and human.
- Use a subject only if explicitly asked; otherwise return only the body.
- Do not add a signature unless the instruction explicitly asks for one.
""".strip()

GMAIL_KEYWORDS = {
    "gmail", "mail", "mails", "email", "emails", "inbox", "draft", "reply",
    "sender", "subject", "thread", "archive", "unread", "read", "star", "label",
    "labels", "compose", "send", "search", "find", "look for", "open", "khol",
    "dhoondo", "dhundo", "summarize", "summary",
}

ACTIONABLE_FOLLOWUPS = (
    "pehla", "first", "second", "dusra", "teesra", "third", "latest", "recent",
    "isko", "usko", "that one", "yeh", "ye", "khol", "open", "read", "archive",
    "delete", "star", "unread", "read", "bhej do", "send it", "preview draft",
    "draft dikhao", "thread summarize", "summary do",
)


def _looks_like_yes(text: str) -> bool:
    t = text.strip().lower()
    return t in {"yes", "y", "haan", "ha", "send", "send it", "bhej do", "kar do", "ok send"}


def _looks_like_no(text: str) -> bool:
    t = text.strip().lower()
    return t in {"no", "n", "nah", "cancel", "mat bhejo", "rehne do", "stop"}


def _gmail_connected(user_id: str) -> bool:
    try:
        return _get_gmail_service_for_user(user_id) is not None
    except Exception:
        return False


def _looks_gmail_related(text: str, state: Dict[str, Any]) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return False
    if state.get("pending_action"):
        return True
    if any(k in lowered for k in GMAIL_KEYWORDS):
        return True
    if state.get("last_search_results") and any(k in lowered for k in ACTIONABLE_FOLLOWUPS):
        return True
    if state.get("last_draft_id") and any(k in lowered for k in ("send", "bhej do", "edit", "update", "change", "preview")):
        return True
    return False


def _llm_complete(messages: List[Dict[str, str]], temperature: float = 0.1, max_tokens: int = 350) -> str:
    last_error: Optional[Exception] = None
    for model in MODEL_PRIORITY:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=0.9,
            )
            msg = completion.choices[0].message
            content = (msg.content or "").strip()
            if content:
                return content
        except Exception as exc:
            last_error = exc
            logger.exception("gmail_nl_agent llm call failed for model=%s", model)
    if last_error:
        raise last_error
    raise RuntimeError("No LLM response produced")


def _extract_json_object(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Could not parse planner JSON: {raw[:200]}")
        return json.loads(match.group(0))


def _heuristic_plan(text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    t = text.lower().strip()
    base = {
        "action": "chat",
        "query": "",
        "message_ref": "none",
        "message_refs": [],
        "thread_ref": "none",
        "draft_ref": "none",
        "to": "",
        "subject": "",
        "instruction": "",
        "label_name": "",
        "label_id": "",
        "smart": False,
        "confidence": 0.45,
        "needs_gmail": False,
        "reply_hint": "",
    }

    if any(x in t for x in ["connect gmail", "gmail connect", "gmail login", "gmail link"]):
        base.update({"action": "gmail_connect", "needs_gmail": False, "confidence": 0.98})
        return base

    if any(x in t for x in ["inbox", "important mails", "important emails", "mail dikhao", "emails dikhao"]):
        base.update({
            "action": "gmail_inbox",
            "smart": any(x in t for x in ["smart", "important"]),
            "needs_gmail": True,
            "confidence": 0.8,
        })
        return base

    if any(x in t for x in ["search", "find", "dhoondo", "dhundo", "look for"]) and any(x in t for x in ["mail", "email", "gmail"]):
        cleaned = t
        for phrase in ["search", "find", "dhoondo", "dhundo", "look for", "mail", "mails", "email", "emails", "gmail"]:
            cleaned = cleaned.replace(phrase, " ")
        cleaned = " ".join(cleaned.split()) or "in:inbox"
        base.update({"action": "gmail_search", "query": cleaned, "needs_gmail": True, "confidence": 0.8})
        return base

    if any(x in t for x in ["open", "read", "khol", "show", "details"]):
        ref = "selected"
        if "first" in t or "pehla" in t:
            ref = "first"
        elif "second" in t or "dusra" in t:
            ref = "second"
        elif "third" in t or "teesra" in t:
            ref = "third"
        elif "latest" in t or "recent" in t:
            ref = "latest"
        base.update({"action": "gmail_read", "message_ref": ref, "needs_gmail": True, "confidence": 0.75})
        return base

    if "thread" in t and any(x in t for x in ["summary", "summarize", "batao"]):
        base.update({"action": "gmail_thread_summary", "thread_ref": "selected", "needs_gmail": True, "confidence": 0.75})
        return base

    if any(x in t for x in ["draft", "compose", "write email", "write mail", "reply draft", "mail likh"]):
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
        if email_match:
            base.update({
                "action": "gmail_create_draft",
                "to": email_match.group(0),
                "subject": "No subject",
                "instruction": text,
                "needs_gmail": True,
                "confidence": 0.7,
            })
            return base

    if any(x in t for x in ["send it", "send draft", "bhej do", "mail bhejo", "email bhejo"]):
        base.update({"action": "gmail_send", "draft_ref": "last", "needs_gmail": True, "confidence": 0.8})
        return base

    if state.get("last_search_results") and any(x in t for x in ACTIONABLE_FOLLOWUPS):
        ref = "selected"
        if "first" in t or "pehla" in t:
            ref = "first"
        elif "second" in t or "dusra" in t:
            ref = "second"
        elif "third" in t or "teesra" in t:
            ref = "third"
        elif "latest" in t or "recent" in t:
            ref = "latest"

        if any(x in t for x in ["archive"]):
            base.update({"action": "gmail_archive", "message_refs": [ref], "needs_gmail": True})
            return base
        if any(x in t for x in ["delete"]):
            base.update({"action": "gmail_delete", "message_refs": [ref], "needs_gmail": True})
            return base
        if any(x in t for x in ["star"]):
            base.update({"action": "gmail_star", "message_refs": [ref], "needs_gmail": True})
            return base
        if any(x in t for x in ["unread"]):
            base.update({"action": "gmail_mark_unread", "message_refs": [ref], "needs_gmail": True})
            return base
        if any(x in t for x in ["read"]):
            base.update({"action": "gmail_mark_read", "message_refs": [ref], "needs_gmail": True})
            return base

        base.update({"action": "gmail_read", "message_ref": ref, "needs_gmail": True})
        return base

    return base


def _plan_action(text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    state_summary = {
        "has_search_results": bool(state.get("last_search_results")),
        "selected_message_id": state.get("selected_message_id"),
        "selected_thread_id": state.get("selected_thread_id"),
        "last_draft_id": state.get("last_draft_id"),
        "pending_action": state.get("pending_action"),
    }

    user_prompt = (
        f"User message: {text}\n"
        f"State: {json.dumps(state_summary, ensure_ascii=False)}\n"
        "Plan the Gmail action now."
    )

    try:
        raw = _llm_complete(
            [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=320,
        )
        plan = _extract_json_object(raw)
        if not isinstance(plan, dict) or not plan.get("action"):
            raise ValueError("Planner returned invalid structure")
        return plan
    except Exception:
        logger.exception("Planner failed, using heuristic plan")
        return _heuristic_plan(text, state)


def _extract_headers(meta: dict) -> dict:
    hdrs = {}
    for h in meta.get("payload", {}).get("headers", []):
        name = h.get("name")
        value = h.get("value")
        if name and value:
            hdrs[name] = value
    return hdrs


def _resolve_message_ref(message_ref: str, state: Dict[str, Any]) -> Optional[str]:
    results = state.get("last_search_results") or []
    selected = state.get("selected_message_id")

    if not message_ref or message_ref == "none":
        return selected
    if message_ref.startswith("id:"):
        return message_ref.split(":", 1)[1].strip() or None
    if message_ref in {"selected", "latest"}:
        return selected or (results[0]["id"] if results else None)
    if message_ref == "first":
        return results[0]["id"] if len(results) >= 1 else selected
    if message_ref == "second":
        return results[1]["id"] if len(results) >= 2 else None
    if message_ref == "third":
        return results[2]["id"] if len(results) >= 3 else None
    return selected


def _resolve_message_refs(message_refs: List[str], state: Dict[str, Any]) -> List[str]:
    refs = message_refs or []
    resolved: List[str] = []
    for ref in refs:
        msg_id = _resolve_message_ref(ref, state)
        if msg_id and msg_id not in resolved:
            resolved.append(msg_id)

    if not resolved:
        fallback = _resolve_message_ref("selected", state)
        if fallback:
            resolved.append(fallback)

    return resolved


def _resolve_thread_ref(thread_ref: str, state: Dict[str, Any]) -> Optional[str]:
    if thread_ref and thread_ref.startswith("id:"):
        return thread_ref.split(":", 1)[1].strip() or None
    return state.get("selected_thread_id")


def _resolve_draft_ref(draft_ref: str, state: Dict[str, Any]) -> Optional[str]:
    if draft_ref and draft_ref.startswith("id:"):
        return draft_ref.split(":", 1)[1].strip() or None
    return state.get("last_draft_id")


def _format_search_results(user_id: str, results: List[Dict[str, Any]], state: Dict[str, Any]) -> str:
    svc = _get_gmail_service_for_user(user_id)
    formatted: List[str] = []
    stored_results: List[Dict[str, Any]] = []

    for idx, result in enumerate(results[:5], start=1):
        msg_id = result.get("id")
        thread_id = result.get("threadId")
        if not msg_id:
            continue

        stored_results.append({"id": msg_id, "threadId": thread_id})
        line = f"{idx}. {msg_id}"

        if svc:
            try:
                meta = _get_message(svc, msg_id, format="metadata")
                headers = _extract_headers(meta)
                sender = headers.get("From", "?")
                subject = headers.get("Subject", "(no subject)")
                date = headers.get("Date", "")
                line = f"{idx}. {sender} — {subject}"
                if date:
                    line += f"\n   Date: {date}"
            except Exception:
                logger.exception("Failed to format search result metadata for %s", msg_id)

        formatted.append(line)

    state["last_search_results"] = stored_results
    state["selected_message_id"] = stored_results[0]["id"] if stored_results else None
    state["selected_thread_id"] = stored_results[0]["threadId"] if stored_results else None

    if not formatted:
        return "Mujhe matching emails nahi mile."

    return (
        "Maine matching emails dhoond liye:\n\n"
        + "\n".join(formatted)
        + "\n\nBolo: 'pehla kholo', 'latest open karo', 'isko archive karo', ya 'reply draft karo'."
    )


def _extract_plain_body(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""

    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            except Exception:
                return ""

    for part in payload.get("parts", []) or []:
        body = _extract_plain_body(part)
        if body:
            return body

    data = payload.get("body", {}).get("data")
    if data:
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    return ""


def _parse_draft_preview(raw_draft: Dict[str, Any]) -> Dict[str, str]:
    message = raw_draft.get("message", {}) if raw_draft else {}
    payload = message.get("payload", {})
    headers = {h.get("name"): h.get("value") for h in payload.get("headers", []) if h.get("name")}
    body = _extract_plain_body(payload).strip()
    return {
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "body": body,
    }


def _generate_email_body(to_addr: str, subject: str, instruction: str, existing_body: str = "") -> str:
    user_prompt = (
        f"Recipient: {to_addr}\n"
        f"Subject: {subject}\n"
        f"Instruction: {instruction}\n"
    )
    if existing_body:
        user_prompt += f"Current draft body:\n{existing_body}\n\nRewrite the draft accordingly."
    else:
        user_prompt += "Write the email body now."

    result = _llm_complete(
        [
            {"role": "system", "content": EMAIL_WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=500,
    )

    cleaned = (result or "").strip()
    if cleaned.startswith("Subject:"):
        cleaned = cleaned.split("\n", 1)[1].strip() if "\n" in cleaned else ""
    return cleaned


def _preview_draft_text(to_addr: str, subject: str, body: str, draft_id: str) -> str:
    preview = body[:1400] + ("..." if len(body) > 1400 else "")
    return (
        f"Draft ready hai.\n\n"
        f"Draft ID: {draft_id}\n"
        f"To: {to_addr}\n"
        f"Subject: {subject}\n\n"
        f"{preview}\n\n"
        "Agar theek lage toh bolo: 'bhej do'. Agar edit karna hai toh bolo: 'subject change karo ...' ya 'draft ko polite banao'."
    )


def route_dynamic_gmail(user_id: str, text: str, state: Dict[str, Any]) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None

    pending = state.get("pending_action")
    if pending and _looks_like_yes(text):
        if pending.get("type") == "send_draft":
            draft_id = pending.get("draft_id")
            ok = send_message_from_draft(user_id, draft_id)
            state["pending_action"] = None
            return "Done — draft bhej diya gaya." if ok else "Send karne me issue aaya. Draft ko ek baar Gmail me check kar lo."

    if pending and _looks_like_no(text):
        state["pending_action"] = None
        return "Theek hai, maine draft send nahi kiya."

    if not _looks_gmail_related(text, state):
        return None

    plan = _plan_action(text, state)
    action = (plan.get("action") or "chat").strip()

    if action == "chat":
        return None

    if action != "gmail_connect" and plan.get("needs_gmail", True) and not _gmail_connected(user_id):
        return "Pehle Gmail connect karna padega. Bolo 'connect gmail' aur main link de dungi."

    if action == "gmail_connect":
        try:
            url = get_auth_url_for_user(user_id, need_send=True)
            return f"Gmail connect karne ke liye ye link use karo:\n{url}"
        except Exception:
            logger.exception("gmail_connect failed")
            return "Gmail connect link generate karne me issue aaya."

    if action == "gmail_inbox":
        try:
            summary = gmail_smart_summary(user_id) if plan.get("smart") else gmail_summary(user_id, max_results=10)
            return summary or "Inbox me kuch recent mails nahi mile."
        except Exception:
            logger.exception("gmail_inbox failed")
            return "Inbox fetch karte waqt issue aaya."

    if action == "gmail_search":
        try:
            query = (plan.get("query") or "").strip() or "in:inbox"
            results = search_messages(user_id, query, max_results=10)
            return _format_search_results(user_id, results or [], state)
        except Exception:
            logger.exception("gmail_search failed")
            return "Search karte waqt issue aaya."

    if action == "gmail_read":
        try:
            msg_id = _resolve_message_ref(plan.get("message_ref", "selected"), state)
            if not msg_id:
                return "Kaunsa mail kholna hai ye clear nahi hua. Pehle search kara do ya bolo 'latest mail kholo'."

            email = read_full_email(user_id, msg_id)
            if not email:
                return "Wo mail open nahi ho paya."

            state["selected_message_id"] = msg_id
            if email.get("threadId"):
                state["selected_thread_id"] = email.get("threadId")

            body = (email.get("body") or "")[:2800]
            return (
                f"From: {email.get('from')}\n"
                f"To: {email.get('to')}\n"
                f"Subject: {email.get('subject')}\n"
                f"Date: {email.get('date')}\n\n"
                f"{body}"
            )
        except Exception:
            logger.exception("gmail_read failed")
            return "Mail read karte waqt issue aaya."

    if action == "gmail_thread_summary":
        try:
            thread_id = _resolve_thread_ref(plan.get("thread_ref", "selected"), state)
            if not thread_id:
                return "Abhi mere paas selected thread nahi hai. Pehle koi mail search ya open kara do."

            summary = summarize_thread_ai(user_id, thread_id)
            return summary or "Thread summarize nahi ho paya."
        except Exception:
            logger.exception("gmail_thread_summary failed")
            return "Thread summary nikalte waqt issue aaya."

    if action == "gmail_create_draft":
        try:
            to_addr = (plan.get("to") or "").strip()
            subject = (plan.get("subject") or "").strip() or "No subject"
            instruction = (plan.get("instruction") or "").strip() or text

            if not to_addr:
                email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
                if email_match:
                    to_addr = email_match.group(0)

            if not to_addr:
                return (
                    "Draft bana sakti hoon, bas recipient clear chahiye.\n\n"
                    "Best format: draft to someone@example.com | Subject here | what you want to say"
                )

            body = _generate_email_body(to_addr, subject, instruction)
            if not body:
                return "Draft body generate nahi ho payi."

            created = create_draft(user_id, to_addr, subject, body)
            if not created or not created.get("id"):
                return "Draft save karne me issue aaya."

            draft_id = created["id"]
            state["last_draft_id"] = draft_id
            state["pending_action"] = {"type": "send_draft", "draft_id": draft_id}
            return _preview_draft_text(to_addr, subject, body, draft_id)
        except Exception:
            logger.exception("gmail_create_draft failed")
            return "Draft banate waqt issue aaya."

    if action == "gmail_preview_draft":
        try:
            draft_id = _resolve_draft_ref(plan.get("draft_ref", "last"), state)
            if not draft_id:
                return "Abhi koi recent draft ready nahi hai."

            raw_draft = get_draft(user_id, draft_id)
            if not raw_draft:
                return "Draft preview nahi mil paya."

            parsed = _parse_draft_preview(raw_draft)
            return _preview_draft_text(parsed.get("to", ""), parsed.get("subject", ""), parsed.get("body", ""), draft_id)
        except Exception:
            logger.exception("gmail_preview_draft failed")
            return "Draft preview nikalte waqt issue aaya."

    if action == "gmail_update_draft":
        try:
            draft_id = _resolve_draft_ref(plan.get("draft_ref", "last"), state)
            if not draft_id:
                return "Edit karne ke liye koi recent draft nahi mila."

            raw_draft = get_draft(user_id, draft_id)
            if not raw_draft:
                return "Existing draft load nahi ho paya."

            parsed = _parse_draft_preview(raw_draft)
            to_addr = (plan.get("to") or parsed.get("to") or "").strip()
            subject = (plan.get("subject") or parsed.get("subject") or "No subject").strip()
            instruction = (plan.get("instruction") or text).strip()

            new_body = _generate_email_body(to_addr, subject, instruction, existing_body=parsed.get("body", ""))
            if not new_body:
                return "Draft update generate nahi ho paya."

            updated = update_draft(user_id, draft_id, to_addr, subject, new_body)
            if not updated:
                return "Draft update karte waqt issue aaya."

            state["last_draft_id"] = draft_id
            state["pending_action"] = {"type": "send_draft", "draft_id": draft_id}
            return _preview_draft_text(to_addr, subject, new_body, draft_id)
        except Exception:
            logger.exception("gmail_update_draft failed")
            return "Draft edit karte waqt issue aaya."

    if action == "gmail_send":
        draft_id = _resolve_draft_ref(plan.get("draft_ref", "last"), state)
        if not draft_id:
            return "Abhi koi recent draft ready nahi hai."

        state["pending_action"] = {"type": "send_draft", "draft_id": draft_id}
        return "Recent draft ready hai. Confirm karne ke liye 'bhej do' bolo, ya cancel ke liye 'no'."

    if action == "gmail_mark_read":
        ids = _resolve_message_refs(plan.get("message_refs") or [plan.get("message_ref", "selected")], state)
        if not ids:
            return "Kaunsa mail mark read karna hai ye clear nahi hua."
        return "Done — mail read mark ho gaya." if mark_read(user_id, ids) else "Mail mark read karne me issue aaya."

    if action == "gmail_mark_unread":
        ids = _resolve_message_refs(plan.get("message_refs") or [plan.get("message_ref", "selected")], state)
        if not ids:
            return "Kaunsa mail unread mark karna hai ye clear nahi hua."
        return "Done — mail unread mark ho gaya." if mark_unread(user_id, ids) else "Mail unread mark karne me issue aaya."

    if action == "gmail_star":
        ids = _resolve_message_refs(plan.get("message_refs") or [plan.get("message_ref", "selected")], state)
        if not ids:
            return "Kaunsa mail star karna hai ye clear nahi hua."
        return "Done — mail starred ho gaya." if star_messages(user_id, ids) else "Mail star karne me issue aaya."

    if action == "gmail_archive":
        ids = _resolve_message_refs(plan.get("message_refs") or [plan.get("message_ref", "selected")], state)
        if not ids:
            return "Kaunsa mail archive karna hai ye clear nahi hua."
        return "Done — mail archive ho gaya." if archive_messages(user_id, ids) else "Mail archive karne me issue aaya."

    if action == "gmail_delete":
        ids = _resolve_message_refs(plan.get("message_refs") or [plan.get("message_ref", "selected")], state)
        if not ids:
            return "Kaunsa mail delete karna hai ye clear nahi hua."
        return "Done — selected mail delete ho gaya." if delete_messages(user_id, ids) else "Mail delete karne me issue aaya."

    if action == "gmail_labels_list":
        try:
            labels = list_labels(user_id) or []
            if not labels:
                return "Koi labels nahi mile."
            lines = [f"{lbl.get('name', '(no name)')} — {lbl.get('id', '')}" for lbl in labels]
            return "Available labels:\n\n" + "\n".join(lines[:50])
        except Exception:
            logger.exception("gmail_labels_list failed")
            return "Labels list karte waqt issue aaya."

    if action == "gmail_label_create":
        label_name = (plan.get("label_name") or "").strip()
        if not label_name:
            return "Kaunsa label create karna hai, uska naam chahiye."
        created = create_label(user_id, label_name)
        return f"Label create ho gaya: {created.get('name')}" if created else "Label create karne me issue aaya."

    if action == "gmail_label_delete":
        label_id = (plan.get("label_id") or "").strip()
        if not label_id:
            labels = list_labels(user_id) or []
            wanted_name = (plan.get("label_name") or "").strip().lower()
            for lbl in labels:
                if lbl.get("name", "").lower() == wanted_name:
                    label_id = lbl.get("id", "")
                    break
        if not label_id:
            return "Label delete karne ke liye label id ya exact name chahiye."
        return "Label delete ho gaya." if delete_label(user_id, label_id) else "Label delete karne me issue aaya."

    if action == "gmail_label_apply":
        try:
            label_name = (plan.get("label_name") or "").strip()
            if not label_name:
                return "Kaunsa label lagana hai ye clear nahi hua."

            labels = list_labels(user_id) or []
            label_id = ""
            for lbl in labels:
                if lbl.get("name", "").lower() == label_name.lower():
                    label_id = lbl.get("id", "")
                    break

            if not label_id:
                return "Wo label mila nahi. Pehle label create kar lo ya exact naam bolo."

            ids = _resolve_message_refs(plan.get("message_refs") or [plan.get("message_ref", "selected")], state)
            if not ids:
                return "Kis mail pe label lagana hai ye clear nahi hua."

            ok = apply_label(user_id, ids, label_id)
            return f"Done — '{label_name}' label apply ho gaya." if ok else "Label apply karne me issue aaya."
        except Exception:
            logger.exception("gmail_label_apply failed")
            return "Label apply karte waqt issue aaya."

    return None