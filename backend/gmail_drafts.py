# backend/gmail_drafts.py

import base64
import logging
from typing import Optional, Dict
from email.mime.text import MIMEText
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# A4.1 Update existing draft
# ─────────────────────────────────────────────
def update_draft(
    user_id: str,
    draft_id: str,
    to: str,
    subject: str,
    body: str
) -> Optional[Dict]:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft_body = {
            "id": draft_id,
            "message": {"raw": raw}
        }

        return svc.users().drafts().update(
            userId="me",
            id=draft_id,
            body=draft_body
        ).execute()

    except HttpError as e:
        logger.exception("update_draft failed: %s", e)
        return None

# ─────────────────────────────────────────────
# A4.2 Delete draft
# ─────────────────────────────────────────────
def delete_draft(user_id: str, draft_id: str) -> bool:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return False

    try:
        svc.users().drafts().delete(
            userId="me",
            id=draft_id
        ).execute()
        return True
    except HttpError as e:
        logger.exception("delete_draft failed: %s", e)
        return False

# ─────────────────────────────────────────────
# A4.3 Fetch draft (preview)
# ─────────────────────────────────────────────
def get_draft(user_id: str, draft_id: str) -> Optional[Dict]:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        return svc.users().drafts().get(
            userId="me",
            id=draft_id,
            format="full"
        ).execute()
    except HttpError as e:
        logger.exception("get_draft failed: %s", e)
        return None
