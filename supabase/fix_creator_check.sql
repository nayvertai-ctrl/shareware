-- Third patch: "memberships insert by creator" checked group ownership via a
-- raw subquery into public.groups (itself RLS-protected, whose own policy
-- calls is_group_member on memberships) -- that nested chain wasn't
-- resolving reliably in testing. Same fix as the original recursion bug:
-- a SECURITY DEFINER function that reads groups directly, bypassing RLS.

create function public.is_group_creator(gid bigint)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.groups
    where id = gid and created_by = auth.uid()
  );
$$;

drop policy if exists "memberships insert by creator" on public.memberships;
create policy "memberships insert by creator" on public.memberships for insert
  with check (user_id = auth.uid() and public.is_group_creator(group_id));
