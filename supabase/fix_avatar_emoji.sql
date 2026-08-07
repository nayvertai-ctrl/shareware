-- Seventh patch: lets a member pick an emoji as their avatar instead of the
-- generated initials tile. Chosen over photo uploads deliberately -- no
-- storage bucket, no image processing, no OAuth, and it renders identically
-- on every device.
--
-- Bounded length because an emoji can legitimately be several code points
-- (skin tones and ZWJ sequences like 👨‍👩‍👧‍👦 are 7+), but nothing sane is
-- longer than that. The client also trims to a single grapheme; this is the
-- backstop so a crafted request can't stash a paragraph here and blow up
-- every row that renders the member.

alter table public.profiles
  add column if not exists avatar_emoji text
  check (avatar_emoji is null or char_length(avatar_emoji) <= 12);

-- Extend the column grant from fix_shadow_flag.sql so you can set your own
-- avatar. is_shadow stays off the list -- still service-role only.
revoke update on public.profiles from authenticated;
grant update (name, upi_id, paypal_me, venmo, avatar_emoji)
  on public.profiles to authenticated;
