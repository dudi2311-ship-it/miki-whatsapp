"""Gmail service for miki — read recent mail, fetch a thread, send email."""

import base64
import logging
from email.message import EmailMessage

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import settings

logger = logging.getLogger("miki.gmail")

_service = None

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def _get_service():
    global _service
    if _service is None:
        if not settings.GOOGLE_REFRESH_TOKEN:
            raise RuntimeError("GOOGLE_REFRESH_TOKEN missing in environment")
        creds = Credentials(
            token=None,
            refresh_token=settings.GOOGLE_REFRESH_TOKEN,
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=GMAIL_SCOPES,
        )
        _service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _service


def _header(headers: list[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _extract_plain_text(payload: dict) -> str:
    """Walk the message payload and extract the best plain-text body we can find."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")

    if mime == "text/plain" and data:
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""

    parts = payload.get("parts") or []
    for p in parts:
        if p.get("mimeType") == "text/plain":
            d = p.get("body", {}).get("data")
            if d:
                try:
                    return base64.urlsafe_b64decode(d).decode("utf-8", errors="replace")
                except Exception:
                    pass
    for p in parts:
        text = _extract_plain_text(p)
        if text:
            return text
    return ""


def search_messages(query: str = "", max_results: int = 10) -> list[dict]:
    """Search Gmail with a query (Gmail search syntax). Returns short summaries.

    Examples:
        query='is:unread'
        query='from:bank.com newer_than:7d'
        query='subject:invoice'
    """
    service = _get_service()
    response = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    msg_ids = [m["id"] for m in response.get("messages", [])]
    if not msg_ids:
        return []

    summaries = []
    for mid in msg_ids:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=mid, format="metadata",
                 metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        summaries.append({
            "id": mid,
            "thread_id": msg.get("threadId"),
            "from": _header(headers, "From"),
            "subject": _header(headers, "Subject"),
            "date": _header(headers, "Date"),
            "snippet": msg.get("snippet", ""),
            "unread": "UNREAD" in (msg.get("labelIds") or []),
        })
    return summaries


def get_message(message_id: str) -> dict:
    """Fetch a single message with its plain-text body."""
    service = _get_service()
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    headers = msg.get("payload", {}).get("headers", [])
    body = _extract_plain_text(msg.get("payload", {}))
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "body": body[:8000],
        "snippet": msg.get("snippet", ""),
    }


def send_email(to: str, subject: str, body: str) -> dict:
    """Send a plain-text email from the authorized account."""
    service = _get_service()
    message = EmailMessage()
    message.set_content(body)
    message["To"] = to
    message["Subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return {"sent": True, "id": sent.get("id"), "thread_id": sent.get("threadId")}
