"""Conversation memory using Supabase (PostgreSQL).

Stores message history per phone number for multi-turn conversations.
Persistent across redeploys, viewable in the Supabase dashboard.
"""

import calendar
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from supabase import create_client, Client

from config import settings

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
_WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

logger = logging.getLogger("miki.database")

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in environment"
            )
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client


def init_db():
    """No-op: schema is managed via SQL migrations in Supabase."""
    try:
        _get_client().table("messages").select("id").limit(1).execute()
        logger.info("Supabase connection OK")
    except Exception as e:
        logger.error(f"Supabase connection failed: {e}")
        raise


def save_message(phone: str, role: str, content: str):
    """Save a message to Supabase."""
    _get_client().table("messages").insert(
        {"phone": phone, "role": role, "content": content}
    ).execute()


def get_history(phone: str, limit: int = 20) -> list[dict]:
    """Get recent conversation history for a phone number, oldest first."""
    response = (
        _get_client()
        .table("messages")
        .select("role, content")
        .eq("phone", phone)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = list(reversed(response.data or []))
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_state(key: str) -> str | None:
    """Read a value from the agent_state key/value table."""
    response = (
        _get_client()
        .table("agent_state")
        .select("value")
        .eq("key", key)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0]["value"] if rows else None


def set_state(key: str, value: str) -> None:
    """Upsert a value in the agent_state key/value table."""
    _get_client().table("agent_state").upsert(
        {"key": key, "value": value}, on_conflict="key"
    ).execute()


def replace_mirrored_events(events: list[dict]) -> int:
    """Replace today's mirrored events with a fresh set from the iPhone bridge.

    Each event needs at minimum a title and start_iso. id is auto-derived
    from title|start_iso if not supplied (the iPhone Shortcut can't always
    expose a stable identifier).
    """
    client = _get_client()
    client.table("mirrored_events").delete().neq("id", "__never__").execute()
    if not events:
        return 0
    rows = []
    for e in events:
        title = e.get("title") or "(ללא כותרת)"
        start_iso = e.get("start_iso", "")
        if not start_iso:
            continue
        event_id = e.get("id") or f"{title}|{start_iso}"
        rows.append({
            "id": event_id,
            "title": title,
            "start_iso": start_iso,
            "end_iso": e.get("end_iso", ""),
            "location": e.get("location", ""),
            "notes": e.get("notes", ""),
            "calendar_name": e.get("calendar_name", ""),
        })
    if rows:
        client.table("mirrored_events").upsert(rows, on_conflict="id").execute()
    return len(rows)


def list_mirrored_events_today(today_yyyy_mm_dd: str) -> list[dict]:
    """Return mirrored events whose start_iso falls on the given local date."""
    response = (
        _get_client()
        .table("mirrored_events")
        .select("*")
        .like("start_iso", f"{today_yyyy_mm_dd}%")
        .order("start_iso")
        .execute()
    )
    return response.data or []


def create_reminder(
    chat_id: str,
    text: str,
    fire_at_iso: str,
    recurrence: str | None = None,
) -> dict:
    """Insert a pending reminder. Returns the new row.

    recurrence is None for a one-shot, or a pattern: 'daily', 'weekly:Sun,Tue',
    'monthly:15'. The first fire is at fire_at_iso; later fires are computed
    after each delivery in mark_reminder_fired.
    """
    payload = {"chat_id": chat_id, "text": text, "fire_at": fire_at_iso}
    if recurrence:
        payload["recurrence"] = recurrence
    response = _get_client().table("reminders").insert(payload).execute()
    rows = response.data or []
    return rows[0] if rows else {}


def compute_next_fire_iso(recurrence: str, current_fire_at_iso: str) -> str | None:
    """Return the next fire_at for a recurring reminder, or None if invalid."""
    rule = (recurrence or "").strip()
    if not rule:
        return None
    try:
        current = datetime.fromisoformat(current_fire_at_iso)
    except ValueError:
        return None
    if current.tzinfo is None:
        current = current.replace(tzinfo=ISRAEL_TZ)

    if rule == "daily":
        return (current + timedelta(days=1)).isoformat()

    if rule.startswith("weekly:"):
        wanted = []
        for token in rule.split(":", 1)[1].split(","):
            idx = _WEEKDAY_INDEX.get(token.strip().capitalize()[:3])
            if idx is not None:
                wanted.append(idx)
        if not wanted:
            return None
        wanted_set = set(wanted)
        for delta in range(1, 8):
            candidate = current + timedelta(days=delta)
            if candidate.weekday() in wanted_set:
                return candidate.isoformat()
        return None

    if rule.startswith("monthly:"):
        try:
            day_of_month = int(rule.split(":", 1)[1])
        except ValueError:
            return None
        year = current.year
        month = current.month + 1
        if month > 12:
            month = 1
            year += 1
        last_day = calendar.monthrange(year, month)[1]
        target_day = min(day_of_month, last_day)
        return current.replace(year=year, month=month, day=target_day).isoformat()

    return None


def list_due_reminders(now_iso: str) -> list[dict]:
    """Return unfired reminders whose fire_at is at or before now."""
    response = (
        _get_client()
        .table("reminders")
        .select("*")
        .eq("fired", False)
        .lte("fire_at", now_iso)
        .order("fire_at")
        .execute()
    )
    return response.data or []


def list_pending_reminders(chat_id: str, limit: int = 20) -> list[dict]:
    """Return upcoming unfired reminders for a chat, soonest first."""
    response = (
        _get_client()
        .table("reminders")
        .select("*")
        .eq("chat_id", chat_id)
        .eq("fired", False)
        .order("fire_at")
        .limit(limit)
        .execute()
    )
    return response.data or []


def mark_reminder_fired(reminder_id: str) -> None:
    """Handle a delivered reminder.

    For one-shot reminders → mark fired=True.
    For recurring reminders → advance fire_at to the next occurrence and
    leave fired=False so /cron/check-reminders picks it up next time.
    """
    client = _get_client()
    response = (
        client.table("reminders")
        .select("id, fire_at, recurrence")
        .eq("id", reminder_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    row = rows[0] if rows else None
    if row and row.get("recurrence"):
        next_iso = compute_next_fire_iso(row["recurrence"], row["fire_at"])
        if next_iso:
            client.table("reminders").update({"fire_at": next_iso}).eq(
                "id", reminder_id
            ).execute()
            return
    client.table("reminders").update({"fired": True}).eq("id", reminder_id).execute()


def cancel_reminder(reminder_id: str) -> None:
    """Hard-delete a reminder by id."""
    _get_client().table("reminders").delete().eq("id", reminder_id).execute()


def add_fact(chat_id: str, category: str, content: str) -> dict:
    """Save a long-term fact. Returns the new row."""
    response = (
        _get_client()
        .table("facts")
        .insert({"chat_id": chat_id, "category": category, "content": content})
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else {}


def list_facts(chat_id: str, category: str | None = None) -> list[dict]:
    """Return all facts for a chat, optionally filtered by category."""
    query = (
        _get_client()
        .table("facts")
        .select("id, category, content, created_at")
        .eq("chat_id", chat_id)
        .order("category")
        .order("created_at")
    )
    if category:
        query = query.eq("category", category)
    response = query.execute()
    return response.data or []


def remove_fact(fact_id: str) -> None:
    """Hard-delete a fact by id."""
    _get_client().table("facts").delete().eq("id", fact_id).execute()
