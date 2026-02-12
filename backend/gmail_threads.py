# backend/gmail_threads.py
import logging
from typing import List, Optional, Dict
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

def fetch_thread(user_id: str, thread_id: str) -> Optional[Dict]:
    """
    Returns thread object from Gmail API with messages (minimal parsing).
    """
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        msgs = []
        for m in thread.get("messages", []):
            payload = m.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            # naive body extraction (text/plain)
            body = ""
            if payload.get("parts"):
                for p in payload["parts"]:
                    if p.get("mimeType") == "text/plain":
                        data = p.get("body", {}).get("data")
                        if data:
                            import base64
                            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                            break
            else:
                data = payload.get("body", {}).get("data")
                if data:
                    import base64
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            msgs.append({
                "id": m.get("id"),
                "threadId": m.get("threadId"),
                "from": headers.get("From"),
                "to": headers.get("To"),
                "subject": headers.get("Subject"),
                "date": headers.get("Date"),
                "snippet": m.get("snippet"),
                "body": body
            })
        return {"id": thread.get("id"), "messages": msgs}
    except HttpError as e:
        logger.exception("fetch_thread failed: %s", e)
        return None

def summarize_thread_simple(user_id: str, thread_id: str, max_chars: int = 800) -> Optional[str]:
    """
    Simple heuristic summary: concat subject + first N chars of each message.
    Useful as a fallback when LLM is unavailable.
    """
    thread = fetch_thread(user_id, thread_id)
    if not thread:
        return None
    parts = []
    for m in thread.get("messages", []):
        parts.append(f"From: {m.get('from')}\n{(m.get('body') or m.get('snippet') or '')[:200]}\n---")
    joined = "\n".join(parts)
    return joined[:max_chars]

def summarize_thread_ai(user_id: str, thread_id: str) -> Optional[str]:
    """
    AI-assisted summary using existing generate_response function (LLM).
    This will call your generate_response (Groq/OpenAI wrapper) — keep in mind
    it can be slower and may consume tokens.
    """
    try:
        thread = fetch_thread(user_id, thread_id)
        if not thread:
            return None
        concat = "\n\n".join([f"From: {m.get('from')}\n{m.get('body') or m.get('snippet')}" for m in thread.get("messages", [])])
        prompt = (
            "SUMMARIZE_EMAIL_THREAD:\n"
            "Provide a short (3-5 lines) summary of this email thread focusing on: "
            "purpose, required actions, open questions, and deadlines.\n\n"
            f"Thread content:\n{concat}\n\n"
            "Output ONLY the summary."
        )
        # Import dynamically to avoid circular import at module load
        from backend.groq_handler import generate_response
        # generate_response is synchronous in our codebase
        summary = generate_response(user_message=prompt, persona_key="default", user_ip=user_id)
        return (summary or "").strip()
    except Exception as e:
        logger.exception("summarize_thread_ai failed: %s", e)
        return None
