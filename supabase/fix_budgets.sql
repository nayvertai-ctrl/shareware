-- Ninth patch: optional spending budgets, at two scopes.
--
--   groups.budget_cents       -- one shared cap for the whole group
--   memberships.budget_cents  -- your own cap, per group
--
-- Both nullable: null means "no budget", which is the default and shows no
-- meter at all. No period/reset -- a group here is trip-shaped ("Goa"), so
-- the budget covers the whole trip. Monthly budgets would need period
-- boundaries, a timezone and rollover rules; deliberately not built.
--
-- What counts against a budget is SPEND, not balance: the group meter uses
-- the sum of every expense, and a member's meter uses the sum of their own
-- shares. Those are different questions from "who owes whom" -- you can be
-- fully settled up and still have spent your entire budget.

alter table public.groups
  add column if not exists budget_cents bigint
  check (budget_cents is null or budget_cents > 0);

alter table public.memberships
  add column if not exists budget_cents bigint
  check (budget_cents is null or budget_cents > 0);

-- The group budget is shared, so any member may set it. Column-scoped so this
-- can't be used to rename the group or switch its currency -- those still have
-- no client write path at all.
drop policy if exists "groups update budget if member" on public.groups;
create policy "groups update budget if member" on public.groups for update
  using (public.is_group_member(id))
  with check (public.is_group_member(id));
revoke update on public.groups from authenticated;
grant update (budget_cents) on public.groups to authenticated;

-- A personal budget is yours alone: the policy pins the row to you, so you
-- cannot set (or clear) somebody else's. Column-scoped so this update path
-- can't be used to move a membership to another group or user.
drop policy if exists "memberships update own" on public.memberships;
create policy "memberships update own" on public.memberships for update
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
revoke update on public.memberships from authenticated;
grant update (budget_cents) on public.memberships to authenticated;
