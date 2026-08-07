-- shareware on Supabase: tables + Row Level Security.
-- Run once in Supabase Dashboard -> SQL Editor -> New query -> paste this whole
-- file -> Run. Safe to re-run only on a fresh project (uses `create table`,
-- not `create table if not exists`, so it errors loudly on a second run
-- instead of silently doing nothing).
--
-- Design: RLS handles every read and the few safe direct writes (settlements
-- insert, expense soft-delete). Everything with real logic -- atomic group
-- creation, invite accept, split computation, settle-up math -- has NO client
-- insert policy here on purpose; those happen through the `api` Edge Function
-- (supabase/functions/api), which uses the service-role key to bypass RLS
-- only after it has validated the request itself. This mirrors exactly which
-- routes in the original app.py had real logic vs. were thin CRUD.

create extension if not exists pgcrypto;

-- One row per authenticated user. auth.users (managed by Supabase Auth) holds
-- email/password; this table holds the app-specific profile fields.
-- is_shadow marks a profile created by the Edge Function's create_shadow_member
-- ("add someone with no account"). Nobody can log in as a shadow member, so
-- any group member may rename one; a real user's name stays their own. Set
-- only by the service role -- see the column grant further down.
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null,
  upi_id text,
  paypal_me text,
  venmo text,
  is_shadow boolean not null default false
);

-- Auto-create a profile row on signup (name comes from the signup call's
-- user metadata; falls back to the email's local part).
create function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, name)
  values (new.id, coalesce(new.raw_user_meta_data->>'name', split_part(new.email, '@', 1)));
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

create table public.groups (
  id bigint generated always as identity primary key,
  name text not null,
  simplify_debts boolean not null default false,
  currency text not null default 'INR'
    check (currency in ('USD','EUR','GBP','INR','CAD','AUD','SGD','AED')),
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

create table public.memberships (
  group_id bigint not null references public.groups(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  primary key (group_id, user_id)
);

create table public.expenses (
  id bigint generated always as identity primary key,
  group_id bigint not null references public.groups(id) on delete cascade,
  paid_by uuid not null references auth.users(id),
  amount_cents bigint not null check (amount_cents > 0),
  description text,
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

create table public.expense_shares (
  expense_id bigint not null references public.expenses(id) on delete cascade,
  user_id uuid not null references auth.users(id),
  share_cents bigint not null,
  primary key (expense_id, user_id)
);

create table public.settlements (
  id bigint generated always as identity primary key,
  group_id bigint not null references public.groups(id) on delete cascade,
  from_user uuid not null references auth.users(id),
  to_user uuid not null references auth.users(id),
  amount_cents bigint not null check (amount_cents > 0),
  created_at timestamptz not null default now()
);

-- token is the invite's capability: an unguessable 18-char secret, not a
-- sequential id. Knowing it IS the authorization, same model as the original
-- app's /invites/<token>.
create table public.invites (
  token text primary key default encode(gen_random_bytes(9), 'hex'),
  group_id bigint not null references public.groups(id) on delete cascade,
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

-- Row Level Security ---------------------------------------------------------

alter table public.profiles enable row level security;
alter table public.groups enable row level security;
alter table public.memberships enable row level security;
alter table public.expenses enable row level security;
alter table public.expense_shares enable row level security;
alter table public.settlements enable row level security;
alter table public.invites enable row level security;

-- profiles: readable by any signed-in user (the original GET /users listed
-- every user's name to any authenticated caller); writable only by the owner.
create policy "profiles select" on public.profiles for select
  using (auth.role() = 'authenticated');
create policy "profiles update own" on public.profiles for update
  using (auth.uid() = id);
-- You may edit your own name and payment handles, but never is_shadow --
-- relabelling yourself as a shadow would let other members rename you.
revoke update on public.profiles from authenticated;
grant update (name, upi_id, paypal_me, venmo) on public.profiles to authenticated;

-- Membership check as a SECURITY DEFINER function, not a raw subquery on
-- memberships. A policy on `memberships` that queries `memberships` again
-- would trigger its own policy recursively (Postgres error 42P17); this
-- function's body runs with the function owner's privileges, bypassing RLS
-- internally, so it terminates. Every policy below that needs "is this user
-- in this group" calls this instead of repeating the subquery.
create function public.is_group_member(gid bigint)
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

-- groups: visible only to members. Group CREATION does not go through a
-- client insert policy at all -- it's an atomic RPC (create_group_with_owner,
-- below) so "create the group" and "join it as the first member" can never
-- happen as two separate, independently-RLS-checked steps (which, besides
-- the atomicity risk, turned out not to evaluate reliably: a WITH CHECK
-- calling a SECURITY DEFINER function that itself reads a *different*
-- RLS-protected table did not behave consistently in testing -- the function
-- was correct when called directly, but not when embedded in another
-- table's policy this way. A same-table check, like is_group_member used
-- below, does not have this problem).
create policy "groups select if member" on public.groups for select
  using (public.is_group_member(id));

-- memberships: a member can see other members of their groups, and can add
-- someone else (add_member). No delete policy -- removing a member requires
-- checking their balance is zero first, which is business logic RLS can't
-- express; that always goes through the Edge Function's remove_member
-- action (service role). Leaving a client-facing delete policy here would
-- let anyone bypass that check with a raw DELETE request.
create policy "memberships select if member" on public.memberships for select
  using (public.is_group_member(group_id));
create policy "memberships insert by existing member" on public.memberships for insert
  with check (public.is_group_member(group_id));

-- Atomic group creation: insert the group and the creator's own membership
-- row in one transaction, as the function owner (bypasses RLS internally --
-- safe, because the function itself pins everything to auth.uid() and
-- rejects unauthenticated callers, so it can't be used to act as anyone
-- else or create orphaned/memberless groups).
create function public.create_group_with_owner(
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

-- expenses: members can read and soft-delete (deleted_at) directly. Amount,
-- description, and split changes go through the Edge Function's
-- create_expense/update_expense actions, which recompute expense_shares so
-- "shares always sum to the total" never breaks. The column-level grant below
-- makes this a hard rule, not just convention: even a hand-crafted PATCH
-- request cannot touch amount_cents or description from the client role.
create policy "expenses select if member" on public.expenses for select
  using (public.is_group_member(group_id));
create policy "expenses soft-delete if member" on public.expenses for update
  using (public.is_group_member(group_id));
revoke update on public.expenses from authenticated;
grant update (deleted_at) on public.expenses to authenticated;

-- expense_shares: read-only from the client; always written by the Edge
-- Function (service role bypasses RLS) alongside the expense they belong to.
create policy "expense_shares select if member" on public.expense_shares for select
  using (exists (
    select 1 from public.expenses e
    where e.id = expense_shares.expense_id and public.is_group_member(e.group_id)));

-- settlements: a simple, safe direct write -- recording a payment never
-- touches the split invariant, so this is a plain PostgREST insert from the
-- frontend, same as the original POST /groups/<id>/settlements. The check
-- constraint blocks a nonsensical self-settlement (the original enforced
-- this in Flask; enforcing it in the schema means it holds no matter which
-- client calls the API).
alter table public.settlements add constraint settlements_from_ne_to check (from_user <> to_user);
create policy "settlements select if member" on public.settlements for select
  using (public.is_group_member(group_id));
create policy "settlements insert if member" on public.settlements for insert
  with check (public.is_group_member(group_id));

-- invites: readable ONLY by existing members of the group -- the token
-- itself is what authorizes a non-member to preview/accept an invite, and
-- that has to bypass RLS deliberately (a non-member has no membership row to
-- check against), so it's an Edge Function action (preview_invite /
-- accept_invite), never a direct client select. A member may create their
-- own group's invite directly.
create policy "invites select if member" on public.invites for select
  using (public.is_group_member(group_id));
create policy "invites insert if member" on public.invites for insert
  with check (public.is_group_member(group_id) and created_by = auth.uid());
