-- Migration 002 — clean up duplicate facts that accumulated during testing.
--
-- Run once in Supabase SQL Editor. Idempotent.
-- Keeps the OLDEST row per (chat_id, category, content) and deletes the rest.

DELETE FROM facts a
USING facts b
WHERE a.id <> b.id
  AND a.chat_id = b.chat_id
  AND a.category = b.category
  AND a.content = b.content
  AND a.created_at > b.created_at;

-- Optional sanity report (run in same query window if you want to see counts)
-- SELECT category, content, count(*) FROM facts GROUP BY 1,2 ORDER BY 3 DESC;
