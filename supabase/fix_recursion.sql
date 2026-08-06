-- Fixes "infinite recursion detected in policy for relation memberships"
-- (Postgres 42P17) on a project that already ran the original schema.sql.
-- Safe to run once. Paste into SQL Editor -> Run.

create or replace function public.is_group_member(gid bigint)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.memberships
    where group_id = gid and user_id = auth.uid()
  );
$$;

drop policy if exists "groups select if member" on public.groups;
drop policy if exists "memberships select if member" on public.memberships;
drop policy if exists "memberships delete if member" on public.memberships;
drop policy if exists "expenses select if member" on public.expenses;
drop policy if exists "expenses soft-delete if member" on public.expenses;
drop policy if exists "expense_shares select if member" on public.expense_shares;
drop policy if exists "settlements select if member" on public.settlements;
drop policy if exists "settlements insert if member" on public.settlements;

create policy "groups select if member" on public.groups for select
  using (public.is_group_member(id));

create policy "memberships select if member" on public.memberships for select
  using (public.is_group_member(group_id));
create policy "memberships delete if member" on public.memberships for delete
  using (public.is_group_member(group_id));

create policy "expenses select if member" on public.expenses for select
  using (public.is_group_member(group_id));
create policy "expenses soft-delete if member" on public.expenses for update
  using (public.is_group_member(group_id));

create policy "expense_shares select if member" on public.expense_shares for select
  using (exists (
    select 1 from public.expenses e
    where e.id = expense_shares.expense_id and public.is_group_member(e.group_id)));

create policy "settlements select if member" on public.settlements for select
  using (public.is_group_member(group_id));
create policy "settlements insert if member" on public.settlements for insert
  with check (public.is_group_member(group_id));
