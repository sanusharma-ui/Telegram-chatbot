# backend/gmail_integration.py
import os
import base64
import json
import logging
from typing import Optional, Dict

from cryptography.fernet import Fernet, InvalidToken
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# env
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
OAUTH_REDIRECT = os.getenv("OAUTH_REDIRECT_URI")
TOKEN_KEY = os.getenv("TOKEN_ENC_KEY")
REDIS_URL = os.getenv("REDIS_URL")

SCOPES_READ = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_DRAFT = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]

# storage
use_redis = False
try:
    import redis
    if REDIS_URL:
        r = redis.from_url(REDIS_URL, decode_responses=False)
        r.ping()
        use_redis = True
except Exception:
    r = None

# encryption
fernet = Fernet(TOKEN_KEY.encode()) if TOKEN_KEY else None

def _encrypt(obj: Dict) -> bytes:
    raw = json.dumps(obj).encode()
    if not fernet:
        raise RuntimeError("Encryption key missing")
    return fernet.encrypt(raw)

def _decrypt(token_bytes: bytes) -> Dict:
    if not fernet:
        raise RuntimeError("Encryption key missing")
    try:
        decoded = fernet.decrypt(token_bytes)
        return json.loads(decoded)
    except InvalidToken:
        logger.exception("Invalid token decrypt")
        return {}

def store_tokens_for_user(user_id: str, token_obj: Dict):
    key = f"gmail:tokens:{user_id}"
    enc = _encrypt(token_obj)
    if use_redis and r:
        r.set(key, enc)
    else:
        path = os.path.join(os.path.dirname(__file__), "gmail_tokens")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, f"{user_id}.token"), "wb") as f:
            f.write(enc)

def load_tokens_for_user(user_id: str) -> Optional[Dict]:
    key = f"gmail:tokens:{user_id}"
    try:
        if use_redis and r:
            enc = r.get(key)
            if not enc:
                return None
            return _decrypt(enc)
        else:
            path = os.path.join(os.path.dirname(__file__), "gmail_tokens", f"{user_id}.token")
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                enc = f.read()
            return _decrypt(enc)
    except Exception as e:
        logger.exception("load tokens error: %s", e)
        return None

def build_flow(state: Optional[str] = None, scopes=SCOPES_READ):
    client_config = {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [OAUTH_REDIRECT],  # Library isse automatically use karegi
        }
    }
    flow = Flow.from_client_config(client_config, scopes=scopes, state=state)
    return flow

def get_auth_url_for_user(user_id: str, need_send: bool = False) -> str:
    scopes = SCOPES_READ + (SCOPES_DRAFT if need_send else [])
    flow = build_flow(scopes=scopes)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent"
        # redirect_uri yahan mat daal – client_config se le lega, duplicate nahi hoga
    )
    state_key = f"gmail:state:{state}"
    if use_redis and r:
        r.set(state_key, user_id, ex=600)
    else:
        path = os.path.join(os.path.dirname(__file__), "gmail_states")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, state), "w") as f:
            f.write(user_id)
    return auth_url

def handle_oauth_callback(state: str, code: str) -> Optional[str]:
    state_key = f"gmail:state:{state}"
    user_id = None
    try:
        if use_redis and r:
            user_id = r.get(state_key).decode() if r.get(state_key) else None
            if user_id:
                r.delete(state_key)
        else:
            path = os.path.join(os.path.dirname(__file__), "gmail_states", state)
            if os.path.exists(path):
                with open(path) as f:
                    user_id = f.read().strip()
                os.remove(path)
    except Exception:
        logger.exception("state read failed")

    if not user_id:
        logger.warning("State missing or expired")
        return None

    # Flow banao aur redirect_uri explicitly set karo token exchange ke liye
    flow = build_flow()
    flow.redirect_uri = OAUTH_REDIRECT

    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_obj = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
        store_tokens_for_user(user_id, token_obj)
        return user_id
    except Exception as e:
        logger.exception("token exchange failed: %s", e)
        return None

def _get_gmail_service_for_user(user_id: str) -> Optional[object]:
    tok = load_tokens_for_user(user_id)
    if not tok:
        return None
    creds = Credentials(
        tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri"),
        client_id=tok.get("client_id"),
        client_secret=tok.get("client_secret"),
        scopes=tok.get("scopes"),
    )
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            store_tokens_for_user(user_id, {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            })
    except Exception:
        logger.exception("refresh failed (may still work)")
    try:
        service = build("gmail", "v1", credentials=creds)
        return service
    except Exception:
        logger.exception("gmail build failed")
        return None

def gmail_summary(user_id: str, max_results: int = 5) -> Optional[str]:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        msgs = svc.users().messages().list(
            userId="me",
            maxResults=max_results,
            q="in:inbox -category:promotions",
        ).execute()
        items = msgs.get("messages", []) or []
        summary = []
        for m in items:
            msg = svc.users().messages().get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summary.append(f"{headers.get('From','?')} | {headers.get('Subject','(no subject)')} | {headers.get('Date','')}")
        if not summary:
            return "No recent emails found."
        return "\n".join(summary)
    except HttpError as e:
        logger.exception("gmail list failed: %s", e)
        return None

def create_draft(user_id: str, to: str, subject: str, body: str) -> Optional[Dict]:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = {"message": {"raw": raw}}
        return svc.users().drafts().create(userId="me", body=draft).execute()
    except Exception as e:
        logger.exception("create draft failed: %s", e)
        return None

def send_message_from_draft(user_id: str, draft_id: str) -> bool:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return False
    try:
        draft = svc.users().drafts().get(userId="me", id=draft_id).execute()
        msg = draft["message"]
        svc.users().messages().send(userId="me", body={"raw": msg["raw"]}).execute()
        return True
    except Exception as e:
        logger.exception("send draft failed: %s", e)
        return False

def disconnect_user(user_id: str):
    key = f"gmail:tokens:{user_id}"
    if use_redis and r:
        r.delete(key)
    else:
        path = os.path.join(os.path.dirname(__file__), "gmail_tokens", f"{user_id}.token")
        if os.path.exists(path):
            os.remove(path)