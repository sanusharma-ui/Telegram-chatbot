import base64
import logging
from typing import List, Optional, Dict
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

LABEL_INBOX = "INBOX"
LABEL_UNREAD = "UNREAD"
LABEL_STARRED = "STARRED"

def _get_message(service, msg_id: str, format: str = "full") -> Dict:
    return service.users().messages().get(userId="me", id=msg_id, format=format).execute()

def get_message_metadata(user_id: str, msg_id: str) -> Optional[Dict]:
    """
    Returns message metadata (From, Subject, Date, threadId) or None if service unavailable/error.
    Safe wrapper used by polling_entry to avoid calling private helpers directly.
    """
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        meta = svc.users().messages().get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        return meta
    except HttpError as e:
        logger.exception("get_message_metadata failed: %s", e)
        return None

def read_full_email(user_id: str, message_id: str) -> Optional[Dict]:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        msg = _get_message(svc, message_id, format="full")
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

        body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part["body"].get("data")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                        break
        else:
            data = payload.get("body", {}).get("data")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

        return {
            "id": msg["id"],
            "threadId": msg.get("threadId"),
            "from": headers.get("From"),
            "to": headers.get("To"),
            "subject": headers.get("Subject"),
            "date": headers.get("Date"),
            "body": body.strip()
        }

    except HttpError as e:
        logger.exception("read_full_email failed: %s", e)
        return None

def _modify_messages(user_id: str, message_ids: List[str], add: Optional[List[str]] = None, remove: Optional[List[str]] = None) -> bool:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc or not message_ids:
        return False

    body = {"ids": message_ids, "addLabelIds": add or [], "removeLabelIds": remove or []}
    try:
        svc.users().messages().batchModify(userId="me", body=body).execute()
        return True
    except HttpError as e:
        logger.exception("batchModify failed: %s", e)
        return False

def mark_read(user_id: str, message_ids: List[str]) -> bool:
    return _modify_messages(user_id, message_ids, remove=[LABEL_UNREAD])

def mark_unread(user_id: str, message_ids: List[str]) -> bool:
    return _modify_messages(user_id, message_ids, add=[LABEL_UNREAD])

def star_messages(user_id: str, message_ids: List[str]) -> bool:
    return _modify_messages(user_id, message_ids, add=[LABEL_STARRED])

def unstar_messages(user_id: str, message_ids: List[str]) -> bool:
    return _modify_messages(user_id, message_ids, remove=[LABEL_STARRED])

def archive_messages(user_id: str, message_ids: List[str]) -> bool:
    return _modify_messages(user_id, message_ids, remove=[LABEL_INBOX])

def delete_messages(user_id: str, message_ids: List[str]) -> bool:
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return False
    try:
        svc.users().messages().batchDelete(userId="me", body={"ids": message_ids}).execute()
        return True
    except HttpError as e:
        logger.exception("delete_messages failed: %s", e)
        return False
