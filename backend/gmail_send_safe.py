# backend/gmail_send_safe.py
import re
import base64
import logging
from typing import Dict, List, Tuple, Optional
from email.mime.text import MIMEText
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# --- Basic safety heuristics (expand later with ML)
PROFANITY_RE = re.compile(r"\b(fuck|shit|bitch|asshole|motherfucker|madarchod|bhosdike)\b", re.IGNORECASE)
ALL_CAPS_RATIO_THRESHOLD = 0.6
EXCESS_EXCLAMATION_THRESHOLD = 6

def _basic_tone_check(text: str) -> Tuple[bool, List[str]]:
    """
    Returns (ok_to_send, reasons). If ok_to_send is False -> reasons explain why.
    Conservative: catches profanity, excessive all-caps, too many exclamations.
    """
    reasons: List[str] = []
    t = (text or "").strip()
    if not t:
        reasons.append("Empty email body.")
    # profanity
    if PROFANITY_RE.search(t):
        reasons.append("Contains profanity or abusive words.")
    # all-caps proportion
    letters = [c for c in t if c.isalpha()]
    if letters:
        caps = sum(1 for c in letters if c.isupper())
        ratio = caps / len(letters)
        if ratio >= ALL_CAPS_RATIO_THRESHOLD and len(letters) > 10:
            reasons.append("Large portion of text is ALL CAPS (appears aggressive).")
    # exclamation marks
    excls = t.count("!")
    if excls >= EXCESS_EXCLAMATION_THRESHOLD:
        reasons.append(f"Contains many exclamation marks ({excls}). Consider toning down.")
    return (len(reasons) == 0, reasons)

def _make_raw_message(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return raw

def send_raw_message(user_id: str, raw_message: str) -> bool:
    """Send raw base64-encoded message. Returns True on success."""
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return False
    try:
        svc.users().messages().send(userId="me", body={"raw": raw_message}).execute()
        return True
    except HttpError as e:
        logger.exception("send_raw_message failed: %s", e)
        return False

def send_safely(
    user_id: str,
    to: str,
    subject: str,
    body: str,
    force_send: bool = False
) -> Dict:
    """
    Safety wrapper for sending emails.
    - Performs basic checks (profanity, all-caps, exclamations).
    - If issues found and force_send is False, returns requires_confirmation=True and reasons.
    - If force_send True, sends anyway.
    Returns: { 'ok': bool, 'sent': bool, 'requires_confirmation': bool, 'reasons': [...]}.
    """
    ok, reasons = _basic_tone_check(body)
    if not ok and not force_send:
        return {"ok": False, "sent": False, "requires_confirmation": True, "reasons": reasons}

    try:
        raw = _make_raw_message(to, subject, body)
        sent = send_raw_message(user_id, raw)
        return {"ok": sent, "sent": sent, "requires_confirmation": False, "reasons": [] if sent else ["send failed"]}
    except Exception as e:
        logger.exception("send_safely error: %s", e)
        return {"ok": False, "sent": False, "requires_confirmation": False, "reasons": [str(e)]}

# Helper to re-use existing draft send API if you prefer sending drafts:
def send_draft_by_id(user_id: str, draft_id: str) -> bool:
    """Wrapper around existing gmail_integration.send_message_from_draft"""
    from backend.gmail_integration import send_message_from_draft
    return send_message_from_draft(user_id, draft_id)
