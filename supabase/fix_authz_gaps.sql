-- Second patch: closes two real access-control gaps found while
-- cross-checking the RLS design against the original app, and adds insert
-- policies so create_group / add_member / create_invite work as plain
-- database calls (no Edge Function needed for those).
-- Run in SQL Editor after fix_recursion.sql. Safe to run once.

-- Gap 1: memberships DELETE was open to any member, letting someone bypass
-- the "can't leave while you owe money" rule with a raw delete. Removing a
-- member must go through the Edge Function's remove_member action instead
-- (it checks the balance is zero, using the service role to then delete).
drop policy if exists "memberships delete if member" on public.memberships;

-- Gap 2: invites were readable by ANY signed-in user, unconditionally --
-- meaning someone could list every group's invite token without ever
-- receiving a link. Restrict to existing members; previewing/accepting an
-- invite by token (for someone who ISN'T yet a member) becomes an Edge
-- Function action instead.
drop policy if exists "invites select if authenticated" on public.invites;
create policy "invites select if member" on public.invites for select
  using (public.is_group_member(group_id));
create policy "invites insert if member" on public.invites for insert
  with check (public.is_group_member(group_id) and created_by = auth.uid());

-- groups: allow direct creation (naming yourself creator), and let the
-- creator see their own group even before their membership row exists.
-- Replaces the narrower "groups select if member" from fix_recursion.sql.
drop policy if exists "groups select if member" on public.groups;
create policy "groups select if member or creator" on public.groups for select
  using (public.is_group_member(id) or created_by = auth.uid());
create policy "groups insert as creator" on public.groups for insert
  with check (created_by = auth.uid());

-- memberships: let an existing member add someone else (add_member), and
-- let a group's creator join their own brand-new group right after creating
-- it (the only two direct-insert cases; accept_invite still requires the
-- Edge Function since a token-holder is neither yet).
create policy "memberships insert by existing member" on public.memberships for insert
  with check (public.is_group_member(group_id));
create policy "memberships insert by creator" on public.memberships for insert
  with check (
    user_id = auth.uid()
    and exists (select 1 from public.groups g
                where g.id = memberships.group_id and g.created_by = auth.uid())
  );

-- Defense in depth: block a nonsensical self-settlement at the schema level.
alter table public.settlements add constraint settlements_from_ne_to check (from_user <> to_user);
