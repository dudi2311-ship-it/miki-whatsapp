"""Conversation memory using Supabase (PostgreSQL).

Stores message history per phone number for multi-turn conversations.
Persistent across redeploys, viewable in the Supabase dashboard.
"""

import logging
from supabase import create_client, Client

from config import settings

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
