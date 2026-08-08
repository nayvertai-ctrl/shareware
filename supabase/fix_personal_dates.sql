-- Eleventh patch: personal_entries goes from a month to a real date.
--
-- The monthly screen now shows a calendar and per-day figures, which a
-- 'YYYY-MM' string cannot answer. `month` was the right call when the only
-- question was "what did I spend in August"; it is the wrong shape the moment
-- the question is "what did I spend on the 14th".
--
-- `month` is dropped rather than kept alongside: it is entirely derivable from
-- entry_date, and the one thing this schema is strict about is not storing what
-- it can derive (see the whole balances design). Month queries become a range
-- scan on entry_date, which the new index serves exactly.
--
-- NOTE ON BACKFILL: rows written before this patch only ever knew their month,
-- so they land on the 1st. Nothing better is recoverable. If an early entry
-- matters, delete and re-add it with the right date.
--
-- Run in Supabase Dashboard -> SQL Editor -> New query -> paste -> Run.

alter table public.personal_entries add column if not exists entry_date date;

update public.personal_entries
   set entry_date = (month || '-01')::date
 where entry_date is null;

alter table public.personal_entries alter column entry_date set not null;

-- drops the column and, with it, personal_entries_user_month
alter table public.personal_entries drop column if exists month;

-- (user_id, entry_date): user_id first because RLS pins every query to
-- auth.uid() anyway, so it is always an equality on the leading column, with
-- the month's range scan riding on entry_date behind it.
create index if not exists personal_entries_user_date
  on public.personal_entries (user_id, entry_date);
