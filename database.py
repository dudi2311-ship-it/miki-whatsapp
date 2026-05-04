"""Conversation memory using Supabase (PostgreSQL).

Stores message history per phone number for multi-turn conversations.
Persistent across redeploys, viewable in the Supabase dashboard.
"""

import calendar
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from supabase import create_client, Client

from config import settings

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
_WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

_FACT_LEADING_NAME = re.compile(r"^\s*דודי\s+")
_FACT_TRAILING_PUNCT = re.compile(r"[.!?…]+\s*$")
_FACT_WHITESPACE = re.compile(r"\s+")


def _normalize_fact(content: str) -> str:
    """Loose-match key for fact dedup.

    Removes leading 'דודי ' (the agent talks about him in 3rd person),
    strips trailing punctuation, collapses internal whitespace, lowercases.
    """
    if not content:
        return ""
    text = _FACT_LEADING_NAME.sub("", content)
    text = _FACT_TRAILING_PUNCT.sub("", text)
    text = _FACT_WHITESPACE.sub(" ", text).strip()
    return text.lower()

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


def search_messages_history(phone: str, query: str, limit: int = 10) -> list[dict]:
    """Substring search across the conversation log for one chat.

    Case-insensitive ilike against the `content` column. Returns the most
    recent matches first with role + content + created_at.
    """
    if not query or not query.strip():
        return []
    needle = "%" + query.strip().replace("%", r"\%").replace("_", r"\_") + "%"
    response = (
        _get_client()
        .table("messages")
        .select("role, content, created_at")
        .eq("phone", phone)
        .ilike("content", needle)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []


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
    """Save a long-term fact.

    Skips insertion if a near-identical fact already exists (same chat_id +
    category, and content matches after normalization — punctuation and
    leading 'דודי' are ignored).

    Returns the existing row when it's a duplicate, or the newly inserted one.
    """
    client = _get_client()
    norm_new = _normalize_fact(content)
    if not norm_new:
        return {}

    existing = (
        client.table("facts")
        .select("id, category, content")
        .eq("chat_id", chat_id)
        .eq("category", category)
        .execute()
    )
    for row in existing.data or []:
        if _normalize_fact(row.get("content", "")) == norm_new:
            return row

    response = (
        client.table("facts")
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


def add_contact(
    chat_id: str,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    aliases: list[str] | None = None,
    notes: str | None = None,
) -> dict:
    """Insert or update a contact.

    Match strategy: if email is given and a contact with that email exists,
    update it. Otherwise if a contact with the same name exists (case
    insensitive), update it. Otherwise insert a new row.
    """
    client = _get_client()
    name_clean = (name or "").strip()
    if not name_clean:
        return {}

    email_clean = (email or "").strip() or None
    phone_clean = (phone or "").strip() or None
    aliases_clean = [a.strip() for a in (aliases or []) if a and a.strip()]

    existing = None
    if email_clean:
        match = (
            client.table("contacts")
            .select("*")
            .eq("chat_id", chat_id)
            .ilike("email", email_clean)
            .limit(1)
            .execute()
        )
        if match.data:
            existing = match.data[0]
    if existing is None:
        match = (
            client.table("contacts")
            .select("*")
            .eq("chat_id", chat_id)
            .ilike("name", name_clean)
            .limit(1)
            .execute()
        )
        if match.data:
            existing = match.data[0]

    if existing:
        merged_aliases = list({*(existing.get("aliases") or []), *aliases_clean})
        payload = {
            "name": name_clean,
            "email": email_clean or existing.get("email"),
            "phone": phone_clean or existing.get("phone"),
            "aliases": merged_aliases,
            "notes": notes if notes is not None else existing.get("notes"),
            "updated_at": datetime.now(ISRAEL_TZ).isoformat(),
        }
        response = (
            client.table("contacts")
            .update(payload)
            .eq("id", existing["id"])
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else existing

    payload = {
        "chat_id": chat_id,
        "name": name_clean,
        "email": email_clean,
        "phone": phone_clean,
        "aliases": aliases_clean,
        "notes": notes,
    }
    response = client.table("contacts").insert(payload).execute()
    rows = response.data or []
    return rows[0] if rows else {}


def find_contacts(chat_id: str, query: str) -> list[dict]:
    """Find contacts whose name or aliases contain the query (case insensitive)."""
    q = (query or "").strip()
    if not q:
        return []
    pattern = f"%{q}%"
    response = (
        _get_client()
        .table("contacts")
        .select("*")
        .eq("chat_id", chat_id)
        .or_(f"name.ilike.{pattern},aliases.cs.{{{q}}}")
        .limit(10)
        .execute()
    )
    return response.data or []


def list_contacts(chat_id: str) -> list[dict]:
    """Return every contact for a chat, sorted by name."""
    response = (
        _get_client()
        .table("contacts")
        .select("*")
        .eq("chat_id", chat_id)
        .order("name")
        .execute()
    )
    return response.data or []


def remove_contact(contact_id: str) -> None:
    """Hard-delete a contact by id."""
    _get_client().table("contacts").delete().eq("id", contact_id).execute()
