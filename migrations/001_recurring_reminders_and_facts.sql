-- Migration 001 — recurring reminders + long-term facts
--
-- Run this once in Supabase SQL Editor (Dashboard -> SQL Editor -> New query).
-- Idempotent: can be re-run safely.

-- 1. Add recurrence support to reminders.
--    NULL  = one-shot (existing behavior, default).
--    Text  = pattern. Supported:
--              'daily'                  every day at the same HH:MM
--              'weekly:Sun,Mon,Wed'     selected weekdays at the same HH:MM
--              'monthly:15'             on day-of-month 15 at the same HH:MM
ALTER TABLE reminders
    ADD COLUMN IF NOT EXISTS recurrence TEXT;

-- 2. Long-term facts table — things miki should remember beyond the
--    short conversation window (preferences, family details, health, etc.).
CREATE TABLE IF NOT EXISTS facts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_facts_chat_id_category
    ON facts (chat_id, category);

-- Optional: row-level security off for service-role usage
-- (Supabase service-role key bypasses RLS by default; nothing to do here)
