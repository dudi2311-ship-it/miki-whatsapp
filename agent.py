"""מיקי - AI conversation logic using Gemini.

Capabilities:
- Web search via Google Search grounding
- Google Calendar read/write via function calling
- Conversation memory via Supabase
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

from config import settings
from database import get_history, save_message
import calendar_service

logger = logging.getLogger("miki.agent")

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

_client = genai.Client(api_key=settings.GEMINI_API_KEY)


def list_my_events(days_ahead: int = 7, include_work: bool = False) -> dict:
    """List upcoming events from the user's Google Calendar.

    Use this when the user asks about their schedule, what's coming up,
    or specific upcoming events. Work meetings are filtered out by default
    (per user preference); set include_work=True only if the user explicitly
    asks to include work meetings.

    Args:
        days_ahead: How many days into the future to look. 1 = today, 7 = week.
        include_work: Set True only if user explicitly asks for work meetings.

    Returns:
        A dict with 'events' (list of events with id, title, start, end, location).
    """
    try:
        events = calendar_service.list_events(
            days_ahead=days_ahead, include_work=include_work
        )
        return {"events": events, "count": len(events)}
    except Exception as e:
        logger.exception("list_my_events failed")
        return {"error": str(e)}


def create_calendar_event(
    title: str,
    start_iso: str,
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
) -> dict:
    """Create a new event in the user's Google Calendar.

    Args:
        title: Event title (in Hebrew or English, as the user requested).
        start_iso: Start time as ISO 8601 with Israel timezone, e.g.
            '2026-04-30T10:00:00+03:00'. If the user gives relative time
            ('tomorrow at 10'), convert to absolute first using current time.
        duration_minutes: Length in minutes (default 60).
        description: Optional notes / agenda.
        location: Optional location.

    Returns:
        The created event details.
    """
    try:
        event = calendar_service.create_event(
            title=title,
            start_iso=start_iso,
            duration_minutes=duration_minutes,
            description=description,
            location=location,
        )
        return {"created": event}
    except Exception as e:
        logger.exception("create_calendar_event failed")
        return {"error": str(e)}


def update_calendar_event(
    event_id: str,
    title: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Update an existing event. Use the event_id from list_my_events.

    Only pass the fields you want to change; leave the rest as None.

    Args:
        event_id: The event's ID (from list_my_events).
        title: New title, or None to keep current.
        start_iso: New start (ISO 8601 with timezone), or None.
        end_iso: New end, or None.
        description: New description, or None.
        location: New location, or None.
    """
    try:
        event = calendar_service.update_event(
            event_id=event_id,
            title=title,
            start_iso=start_iso,
            end_iso=end_iso,
            description=description,
            location=location,
        )
        return {"updated": event}
    except Exception as e:
        logger.exception("update_calendar_event failed")
        return {"error": str(e)}


def delete_calendar_event(event_id: str) -> dict:
    """Delete an event by its ID. Confirm with the user before calling this.

    Args:
        event_id: The event's ID (from list_my_events).
    """
    try:
        return calendar_service.delete_event(event_id)
    except Exception as e:
        logger.exception("delete_calendar_event failed")
        return {"error": str(e)}


_TOOLS = [
    list_my_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
]


def _build_system_prompt() -> str:
    """System prompt with current Israel time injected."""
    now = datetime.now(ISRAEL_TZ)
    weekday_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"][now.weekday()]
    return f"""{settings.SYSTEM_PROMPT}

כלים זמינים:
- ליומן Google Calendar: list_my_events, create_calendar_event, update_calendar_event, delete_calendar_event
- פגישות עבודה (משרד הבריאות, Teams, ועוד) מסוננות אוטומטית — אם דודי שואל על "פגישות" סתם, מציגים רק פרטיות. רק אם הוא אומר "כולל עבודה" / "פגישות עבודה" — מעבירים include_work=True

מידע נוכחי:
- היום: יום {weekday_he}, {now.strftime('%d/%m/%Y')}
- השעה: {now.strftime('%H:%M')} (שעון ישראל)

הנחיות חשובות:
- כשיוצרים אירוע: השתמש בפורמט ISO 8601 עם איזור זמן ישראל (+03:00 או +02:00 לפי DST)
- לפני מחיקת אירוע — תאשר עם דודי שזה האירוע הנכון
- יוזמות: רק אם יש משהו מיוחד (התנגשות, פער חריג, או שאלה ברורה ממה שדודי כתב). לא להציע סתם.
"""


def _to_gemini_contents(history: list[dict], new_user_message: str) -> list[types.Content]:
    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
        )
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=new_user_message)])
    )
    return contents


def _extract_text(response) -> str:
    try:
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        chunks = [p.text for p in parts if getattr(p, "text", None)]
        return "".join(chunks).strip()
    except (AttributeError, IndexError) as e:
        logger.error(f"Failed to extract text from Gemini response: {e}")
        return ""


def get_response(phone: str, message: str, sender_name: str = "") -> str:
    history = get_history(phone, limit=settings.MAX_HISTORY)
    contents = _to_gemini_contents(history, message)

    config = types.GenerateContentConfig(
        system_instruction=_build_system_prompt(),
        tools=_TOOLS,
        max_output_tokens=2000,
        temperature=0.7,
    )

    response = _client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=config,
    )

    reply = _extract_text(response)
    if not reply:
        reply = "סליחה, לא הצלחתי לענות הפעם. נסה שוב."

    save_message(phone, "user", message)
    save_message(phone, "assistant", reply)

    return reply
