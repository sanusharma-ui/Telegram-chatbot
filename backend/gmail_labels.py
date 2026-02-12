# backend/gmail_labels.py

import logging
from typing import List, Optional, Dict
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# A2.1 List all labels
# ─────────────────────────────────────────────
def list_labels(user_id: str) -> Optional[List[Dict]]:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        res = svc.users().labels().list(userId="me").execute()
        return res.get("labels", [])
    except HttpError as e:
        logger.exception("list_labels failed: %s", e)
        return None

# ─────────────────────────────────────────────
# A2.2 Create new label
# ─────────────────────────────────────────────
def create_label(
    user_id: str,
    name: str,
    visibility: str = "labelShow"
) -> Optional[Dict]:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc or not name:
        return None

    body = {
        "name": name,
        "labelListVisibility": visibility,
        "messageListVisibility": "show"
    }

    try:
        label = svc.users().labels().create(
            userId="me",
            body=body
        ).execute()
        return label
    except HttpError as e:
        logger.exception("create_label failed: %s", e)
        return None

# ─────────────────────────────────────────────
# A2.3 Delete label
# ─────────────────────────────────────────────
def delete_label(user_id: str, label_id: str) -> bool:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc or not label_id:
        return False

    try:
        svc.users().labels().delete(
            userId="me",
            id=label_id
        ).execute()
        return True
    except HttpError as e:
        logger.exception("delete_label failed: %s", e)
        return False

# ─────────────────────────────────────────────
# A2.4 Apply label to messages (batch)
# ─────────────────────────────────────────────
def apply_label(
    user_id: str,
    message_ids: List[str],
    label_id: str
) -> bool:
    return _modify_messages(user_id, message_ids, add=[label_id])

# ─────────────────────────────────────────────
# A2.5 Remove label from messages (batch)
# ─────────────────────────────────────────────
def remove_label(
    user_id: str,
    message_ids: List[str],
    label_id: str
) -> bool:
    return _modify_messages(user_id, message_ids, remove=[label_id])

# ─────────────────────────────────────────────
# INTERNAL: batch modify helper
# ─────────────────────────────────────────────
def _modify_messages(
    user_id: str,
    message_ids: List[str],
    add: Optional[List[str]] = None,
    remove: Optional[List[str]] = None
) -> bool:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc or not message_ids:
        return False

    body = {
        "ids": message_ids,
        "addLabelIds": add or [],
        "removeLabelIds": remove or []
    }

    try:
        svc.users().messages().batchModify(
            userId="me",
            body=body
        ).execute()
        return True
    except HttpError as e:
        logger.exception("label batchModify failed: %s", e)
        return False
