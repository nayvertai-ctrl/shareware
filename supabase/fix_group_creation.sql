-- Fourth patch: replaces the two-step client-side "insert group, then insert
-- membership" flow (which relied on is_group_creator() being called from
-- inside memberships' WITH CHECK -- correct when called directly via RPC,
-- but not reliably enforced when embedded in another table's insert policy
-- this way) with ONE atomic function that does both inserts as the function
-- owner. Cleans up the now-unnecessary policies and helper function.

drop policy if exists "groups select if member or creator" on public.groups;
create policy "groups select if member" on public.groups for select
  using (public.is_group_member(id));

drop policy if exists "groups insert as creator" on public.groups;
drop policy if exists "memberships insert by creator" on public.memberships;
drop function if exists public.is_group_creator(bigint);

create function public.create_group_with_owner(
  p_name text, p_currency text default 'USD', p_simplify boolean default false
) returns public.groups
language plpgsql
security definer
set search_path = public
as $$
declare
  new_group public.groups;
begin
  if auth.uid() is null then
    raise exception 'authentication required';
  end if;
  if p_currency not in ('USD','EUR','GBP','INR','CAD','AUD','SGD','AED') then
    raise exception 'unsupported currency';
  end if;
  insert into public.groups (name, currency, simplify_debts, created_by)
  values (p_name, p_currency, p_simplify, auth.uid())
  returning * into new_group;

  insert into public.memberships (group_id, user_id) values (new_group.id, auth.uid());

  return new_group;
end;
$$;
revoke all on function public.create_group_with_owner(text, text, boolean) from public;
grant execute on function public.create_group_with_owner(text, text, boolean) to authenticated;
