-- Sixth patch: marks profiles that were created by "add a person with no
-- account" (create_shadow_member) so the app can tell them apart from real
-- signed-up users.
--
-- Why it matters: a shadow member is just a label the group owns -- nobody
-- can log in as them -- so any member may rename one to fix a typo or tell
-- two same-named entries apart. A REAL user's profile is their own identity
-- and stays editable only by them ("profiles update own"). Without this flag
-- the two cases are indistinguishable from the client.
--
-- The flag is not client-writable: profiles' update policy restricts rows to
-- your own, and this column is revoked below so you can't relabel yourself
-- as a shadow (which would let others rename you).

alter table public.profiles
  add column if not exists is_shadow boolean not null default false;

-- Backfill any shadow accounts created before this patch. create_shadow_member
-- mints them with an unroutable shadow-<uuid>@shareware.invalid address.
update public.profiles p
set is_shadow = true
from auth.users u
where u.id = p.id and u.email like 'shadow-%@shareware.invalid';

-- Clients may still edit their own name and payment handles, but never
-- is_shadow -- only the Edge Function (service role) sets that.
revoke update on public.profiles from authenticated;
grant update (name, upi_id, paypal_me, venmo) on public.profiles to authenticated;
