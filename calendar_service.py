"""Google Calendar service for miki.

Provides read/create/update/delete operations against the user's primary
calendar, with automatic filtering of work meetings (per CLAUDE.md rules).
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import settings

logger = logging.getLogger("miki.calendar")

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

WORK_EMAIL_DOMAINS = ("@moh.gov.il",)
WORK_KEYWORDS = (
    "שוטף", "סטטוס", "פ״ע", "פע", "ris", "מרשמים דיגיטליים",
    "מיתוג", "היערכות", "teams", "טימס", "חדר ישיבות",
)
WORK_ORGANIZERS = (
    "נעמה פרי-כהן", "סיני יהודה", "ניר מקובר",
    "רביב שמואלי", "דניאל זוהר", "חיה ברקאי",
)

_service = None


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
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _is_work_event(event: dict) -> bool:
    """Heuristic: True if the event looks like a work meeting we should hide."""
    summary = (event.get("summary") or "").lower()
    description = (event.get("description") or "").lower()
    location = (event.get("location") or "").lower()
    organizer_name = (event.get("organizer", {}).get("displayName") or "").lower()

    if any(domain in str(event.get("attendees", "")).lower() for domain in WORK_EMAIL_DOMAINS):
        return True
    if "teams.microsoft.com" in description or "teams.microsoft.com" in location:
        return True
    for kw in WORK_KEYWORDS:
        if kw.lower() in summary or kw.lower() in description or kw.lower() in location:
            return True
    for org in WORK_ORGANIZERS:
        if org.lower() in organizer_name:
            return True
    return False


def _format_event(event: dict) -> dict:
    """Convert a raw Google Calendar event to a compact dict for the LLM."""
    start = event.get("start", {})
    end = event.get("end", {})
    start_str = start.get("dateTime") or start.get("date") or ""
    end_str = end.get("dateTime") or end.get("date") or ""
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(ללא כותרת)",
        "start": start_str,
        "end": end_str,
        "location": event.get("location") or "",
        "description": (event.get("description") or "")[:200],
        "is_work": _is_work_event(event),
    }


def list_events(
    days_ahead: int = 7,
    include_work: bool = True,
    max_results: int = 50,
) -> list[dict]:
    """List upcoming events. Work events are included by default."""
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    response = (
        _get_service()
        .events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = [_format_event(e) for e in response.get("items", [])]
    if not include_work:
        events = [e for e in events if not e["is_work"]]
    return events


def create_event(
    title: str,
    start_iso: str,
    end_iso: str | None = None,
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
) -> dict:
    """Create a new calendar event.

    start_iso must be ISO 8601 (e.g., '2026-04-30T10:00:00+03:00').
    If end_iso is None, end = start + duration_minutes.
    """
    if end_iso is None:
        start_dt = datetime.fromisoformat(start_iso)
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        end_iso = end_dt.isoformat()

    body = {
        "summary": title,
        "start": {"dateTime": start_iso, "timeZone": "Asia/Jerusalem"},
        "end": {"dateTime": end_iso, "timeZone": "Asia/Jerusalem"},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    created = _get_service().events().insert(calendarId="primary", body=body).execute()
    return _format_event(created)


def update_event(
    event_id: str,
    title: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Update an existing event. Only the provided fields are changed."""
    service = _get_service()
    event = service.events().get(calendarId="primary", eventId=event_id).execute()

    if title is not None:
        event["summary"] = title
    if start_iso is not None:
        event["start"] = {"dateTime": start_iso, "timeZone": "Asia/Jerusalem"}
    if end_iso is not None:
        event["end"] = {"dateTime": end_iso, "timeZone": "Asia/Jerusalem"}
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location

    updated = service.events().update(
        calendarId="primary", eventId=event_id, body=event
    ).execute()
    return _format_event(updated)


def delete_event(event_id: str) -> dict:
    """Delete an event by ID."""
    _get_service().events().delete(calendarId="primary", eventId=event_id).execute()
    return {"deleted": True, "id": event_id}


def now_in_israel() -> str:
    """Current time in Israel as ISO string — useful for the LLM context."""
    return datetime.now(ISRAEL_TZ).isoformat()
