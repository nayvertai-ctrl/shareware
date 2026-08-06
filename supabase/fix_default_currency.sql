-- Fifth patch: changes the default currency for new groups from USD to
-- INR. schema.sql was already run on this project (see the other fix_*.sql
-- files), so editing schema.sql alone doesn't change what's live -- this
-- patch updates the already-created column default and function default.
-- Safe to run once.

alter table public.groups alter column currency set default 'INR';

create or replace function public.create_group_with_owner(
  p_name text, p_currency text default 'INR', p_simplify boolean default false
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
