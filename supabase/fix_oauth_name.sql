-- Eighth patch: make the signup trigger tolerant of how different providers
-- spell the display name.
--
-- Email signup sends `name` (we set it ourselves in the signUp call). Google
-- sends `name` AND `full_name`; other OAuth providers send only one or the
-- other. The original trigger read `name` alone and fell back to the email
-- local part, so a provider that only sends full_name would give everyone a
-- profile named after their email prefix.
--
-- Safe to re-run: create or replace.

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, name)
  values (
    new.id,
    coalesce(
      nullif(trim(new.raw_user_meta_data->>'name'), ''),
      nullif(trim(new.raw_user_meta_data->>'full_name'), ''),
      split_part(new.email, '@', 1)
    )
  );
  return new;
end;
$$;
