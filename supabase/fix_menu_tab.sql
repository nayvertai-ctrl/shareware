-- Tenth patch: the two data-backed items behind the new Menu tab.
--
--   1. groups delete policy      -- "delete this group", creator only
--   2. public.personal_entries   -- your own monthly expenses & savings
--
-- Run in Supabase Dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run.

-- 1. Delete a group -----------------------------------------------------------
--
-- Every table that references groups(id) does so `on delete cascade`
-- (memberships, expenses, settlements, invites -- and expense_shares cascades
-- from expenses), so removing the single groups row removes the whole ledger.
-- That makes deletion expressible as plain RLS: no Edge Function needed, since
-- there is no math to get right and nothing to validate beyond "is this yours".
--
-- Creator only. Any member wanting out uses `remove_member` ("leave group"),
-- which is balance-gated and reversible. Deleting destroys shared history for
-- everyone in the group, so it stays with whoever created it.
--
-- Known gap: shadow profiles that existed only inside a deleted group are left
-- behind -- dropping an auth user needs the service role, which RLS can't
-- reach. They keep showing up under "add an existing person", the same litter
-- remove_member cleans via maybeDeleteOrphanShadow. Not worth an Edge Function
-- round-trip for the rarer path.
drop policy if exists "groups delete by creator" on public.groups;
create policy "groups delete by creator" on public.groups for delete
  using (created_by = auth.uid());
grant delete on public.groups to authenticated;

-- 2. Personal monthly ledger --------------------------------------------------
--
-- Private to one user and outside the group ledger entirely: nothing here
-- affects anybody's balance, so there is no invariant to protect and no Edge
-- Function involved. RLS pinning every row to its owner is the whole security
-- model.
--
-- `month` is the literal 'YYYY-MM' string an <input type="month"> produces.
-- Text on purpose: a date column would drag in a timezone and an "is it really
-- the 1st?" check, and nothing here does date arithmetic. Amounts are integer
-- cents like every other amount in this schema.
create table if not exists public.personal_entries (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  month text not null check (month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  kind text not null check (kind in ('expense','saving')),
  label text not null check (char_length(label) between 1 and 80),
  amount_cents bigint not null check (amount_cents > 0),
  created_at timestamptz not null default now()
);
create index if not exists personal_entries_user_month
  on public.personal_entries (user_id, month);

alter table public.personal_entries enable row level security;
drop policy if exists "personal entries own" on public.personal_entries;
create policy "personal entries own" on public.personal_entries for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update, delete on public.personal_entries to authenticated;
