# backend/gmail_search.py

import logging
from typing import List, Optional, Dict
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# A3.1 Raw Gmail search (safe passthrough)
# ─────────────────────────────────────────────
def search_messages(
    user_id: str,
    query: str,
    max_results: int = 10
) -> Optional[List[Dict]]:
    from backend.gmail_integration import _get_gmail_service_for_user

    svc = _get_gmail_service_for_user(user_id)
    if not svc or not query:
        return None

    try:
        res = svc.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results
        ).execute()

        return res.get("messages", [])
    except HttpError as e:
        logger.exception("search_messages failed: %s", e)
        return None

# ─────────────────────────────────────────────
# A3.2 AI-friendly helpers (optional use)
# ─────────────────────────────────────────────
def search_from_sender(user_id: str, sender: str) -> Optional[List[Dict]]:
    return search_messages(user_id, f"from:{sender}")

def search_subject(user_id: str, keyword: str) -> Optional[List[Dict]]:
    return search_messages(user_id, f"subject:{keyword}")

def search_recent_days(user_id: str, days: int = 7) -> Optional[List[Dict]]:
    return search_messages(user_id, f"newer_than:{days}d")
