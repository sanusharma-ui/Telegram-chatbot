# backend/gmail_integration.py
import os
import json
import time
import base64
import logging
from typing import Optional, Dict

from cryptography.fernet import Fernet, InvalidToken

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ---------- ENV / CONFIG ----------
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
OAUTH_REDIRECT = os.getenv("OAUTH_REDIRECT_URI")  # must exactly match Google Console redirect
TOKEN_KEY = os.getenv("TOKEN_ENC_KEY")  # fernet key: use Fernet.generate_key().decode()
REDIS_URL = os.getenv("REDIS_URL")

# Scopes
SCOPES_READ = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_DRAFT = ["https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.send"]

# ---------- Storage backend (Redis optional, else filesystem) ----------
use_redis = False
r = None
try:
    import redis
    if REDIS_URL:
        r = redis.from_url(REDIS_URL, decode_responses=False)
        r.ping()
        use_redis = True
        logger.info("Using Redis for state/token storage")
except Exception:
    r = None
    use_redis = False
    logger.info("Redis unavailable; falling back to filesystem storage")

# ---------- Encryption helpers ----------
fernet = None
if TOKEN_KEY:
    try:
        fernet = Fernet(TOKEN_KEY.encode() if isinstance(TOKEN_KEY, str) else TOKEN_KEY)
    except Exception as e:
        logger.exception("Invalid TOKEN_ENC_KEY: %s", e)
        fernet = None

def _encrypt(obj: Dict) -> bytes:
    raw = json.dumps(obj).encode()
    if not fernet:
        raise RuntimeError("Encryption key missing/invalid")
    return fernet.encrypt(raw)

def _decrypt(token_bytes: bytes) -> Dict:
    if not fernet:
        raise RuntimeError("Encryption key missing/invalid")
    try:
        decoded = fernet.decrypt(token_bytes)
        return json.loads(decoded)
    except InvalidToken:
        logger.exception("Invalid token decrypt")
        return {}

# ---------- File paths ----------
BASE_DIR = os.path.dirname(__file__)
TOKENS_DIR = os.path.join(BASE_DIR, "gmail_tokens")
STATES_DIR = os.path.join(BASE_DIR, "gmail_states")
os.makedirs(TOKENS_DIR, exist_ok=True)
os.makedirs(STATES_DIR, exist_ok=True)

def store_tokens_for_user(user_id: str, token_obj: Dict):
    key = f"gmail:tokens:{user_id}"
    enc = _encrypt(token_obj)
    if use_redis and r:
        r.set(key, enc)
    else:
        path = os.path.join(TOKENS_DIR, f"{user_id}.token")
        with open(path, "wb") as f:
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
            path = os.path.join(TOKENS_DIR, f"{user_id}.token")
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                enc = f.read()
            return _decrypt(enc)
    except Exception as e:
        logger.exception("load tokens error: %s", e)
        return None

# ---------- OAuth state (we store both user_id and scopes so callback can reconstruct flow) ----------
def _save_state_blob(state: str, payload: Dict):
    if use_redis and r:
        r.set(f"gmail:state:{state}", json.dumps(payload), ex=600)
    else:
        path = os.path.join(STATES_DIR, state)
        with open(path, "w") as f:
            f.write(json.dumps(payload))

def _load_state_blob(state: str) -> Optional[Dict]:
    if use_redis and r:
        v = r.get(f"gmail:state:{state}")
        if not v:
            return None
        try:
            return json.loads(v)
        finally:
            r.delete(f"gmail:state:{state}")
    else:
        path = os.path.join(STATES_DIR, state)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            payload = json.load(f)
        try:
            os.remove(path)
        except Exception:
            pass
        return payload

# ---------- Flow builder ----------
def build_flow(state: Optional[str] = None, scopes: Optional[list] = None):
    if not CLIENT_ID or not CLIENT_SECRET or not OAUTH_REDIRECT:
        raise RuntimeError("Missing Google OAuth configuration (CLIENT_ID/CLIENT_SECRET/OAUTH_REDIRECT)")
    # Use the v2 auth endpoint (recommended)
    client_config = {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            # v2 auth endpoint handled by underlying library; redirect_uris must be correct
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [OAUTH_REDIRECT]
        }
    }
    scopes = scopes or SCOPES_READ
    flow = Flow.from_client_config(client_config, scopes=scopes, redirect_uri=OAUTH_REDIRECT, state=state)
    return flow

# ---------- Public: get auth url ----------
def get_auth_url_for_user(user_id: str, need_send: bool = False) -> str:
    scopes = list(SCOPES_READ) + (SCOPES_DRAFT if need_send else [])
    # Create flow with scopes explicitly
    flow = build_flow(scopes=scopes)
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    # Save state blob containing user_id and scopes so callback can recreate same flow
    _save_state_blob(state, {"user_id": user_id, "scopes": scopes})
    return auth_url

# ---------- Callback handler ----------
def handle_oauth_callback(state: str, code: str) -> Optional[str]:
    blob = _load_state_blob(state)
    if not blob:
        logger.warning("State missing or expired for state=%s", state)
        return None
    user_id = blob.get("user_id")
    scopes = blob.get("scopes", SCOPES_READ)
    try:
        flow = build_flow(scopes=scopes)
        # exchange code for tokens
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_obj = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else scopes
        }
        store_tokens_for_user(user_id, token_obj)
        return user_id
    except Exception as e:
        logger.exception("token exchange failed: %s", e)
        return None

# ---------- Gmail service helper ----------
def _get_gmail_service_for_user(user_id: str):
    tok = load_tokens_for_user(user_id)
    if not tok:
        return None
    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri"),
        client_id=tok.get("client_id"),
        client_secret=tok.get("client_secret"),
        scopes=tok.get("scopes")
    )
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # save updated token set
            store_tokens_for_user(user_id, {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes
            })
    except Exception:
        logger.exception("refresh failed (may still work)")
    try:
        service = build("gmail", "v1", credentials=creds)
        return service
    except Exception:
        logger.exception("gmail build failed")
        return None

# ---------- Simple helpers (summary / draft / send / disconnect) ----------
def gmail_summary(user_id: str, max_results: int = 5) -> Optional[str]:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        msgs = svc.users().messages().list(userId="me", maxResults=max_results, q="in:inbox -category:promotions").execute()
        items = msgs.get("messages", []) or []
        summary = []
        for m in items:
            msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From","Subject","Date"]).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            summary.append(f"{headers.get('From','?')} | {headers.get('Subject','(no subject)')} | {headers.get('Date','')}")
        if not summary:
            return "No recent emails found."
        return "\n".join(summary)
    except HttpError as e:
        logger.exception("gmail list failed: %s", e)
        return None

# Draft/send helpers
from email.mime.text import MIMEText

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
        created = svc.users().drafts().create(userId="me", body=draft).execute()
        return created
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
        path = os.path.join(TOKENS_DIR, f"{user_id}.token")
        if os.path.exists(path):
            os.remove(path)
