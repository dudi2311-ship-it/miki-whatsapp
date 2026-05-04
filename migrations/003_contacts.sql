-- Migration 003 — structured contacts table
--
-- Run this once in Supabase SQL Editor (Dashboard -> SQL Editor -> New query).
-- Idempotent: can be re-run safely.
--
-- Replaces the ad-hoc "facts where category='contacts'" pattern with a real
-- table so miki can look up an email by name reliably when adding attendees
-- to calendar events.

CREATE TABLE IF NOT EXISTS contacts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    aliases     TEXT[] NOT NULL DEFAULT '{}',
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lookup-by-name (lowercased) is the hot path for "add X to the meeting".
CREATE INDEX IF NOT EXISTS idx_contacts_chat_id_name_lower
    ON contacts (chat_id, lower(name));

-- One contact per email per user — prevents duplicates when the agent saves
-- the same person multiple times across conversations.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_chat_id_email_unique
    ON contacts (chat_id, lower(email))
    WHERE email IS NOT NULL;
