# Deploying shareware

**Live app: https://nayvertai-ctrl.github.io/shareware/**

Everything below is already set up. This document explains how it fits together
and how to update it.

## Architecture

shareware is a static frontend talking directly to Supabase. There is no
application server.

```
index.html  ──►  GitHub Pages          (static hosting, free)
     │
     ├────────►  Supabase Auth         (email + password, signup is open)
     ├────────►  Supabase PostgREST    (reads + simple writes, guarded by RLS)
     └────────►  Edge Function `api`   (the logic RLS can't express)
```

**Why the split.** Row Level Security handles every read, plus the writes that
can't break an invariant: recording a settlement, soft-deleting an expense,
adding an already-registered member, creating an invite, and editing your own
profile. Creating a group is the `create_group_with_owner` RPC, so the group and
its first membership row are inserted atomically.

Anything with real logic goes through the `api` Edge Function, which uses the
service-role key to bypass RLS *only after validating the request itself*:

| Action | Why it can't be a plain table write |
|---|---|
| `create_expense` / `update_expense` | Splits must always sum to the expense total |
| `settle_up` | Greedy min-cash-flow over the whole group |
| `group_detail` | Per-member balances are computed, not stored |
| `user_summary` | Cross-group totals, bucketed per currency |
| `accept_invite` / `preview_invite` | A token holder isn't a member yet, so RLS has nothing to check |
| `remove_member` | Only allowed when that member's balance is zero |
| `create_shadow_member` | Creates an auth user for someone with no account |

`supabase/schema.sql` documents which tables are reachable directly and which
deliberately have no client write policy.

## Updating

### Frontend
```bash
git push origin main
```
GitHub Pages redeploys automatically (~1 min). Check with:
```bash
gh api repos/nayvertai-ctrl/shareware/pages --jq .status   # "built" when done
```

### Edge Function
```bash
supabase functions deploy api
```
Run the logic tests first — they cover the money math with no network needed:
```bash
deno test supabase/functions/api/index.test.ts
```

### Database schema
`schema.sql` is the complete, current definition — it already includes
everything the `fix_*.sql` patches did. Those patches exist only because the
live database was built from an earlier `schema.sql` and had to be migrated
in place; they are history, not setup steps.

For a new change, add another patch file and run it against the live database:
```bash
supabase db query --linked -f supabase/your_patch.sql
```
(or paste it into the Supabase Dashboard → SQL Editor), **and** fold the same
change into `schema.sql` so a fresh project still gets it.

## Setting this up again from scratch

1. Create a Supabase project.
2. SQL Editor → run `supabase/schema.sql`. That's the whole schema.
   **Do not run the `fix_*.sql` files** — they are in-place migrations for the
   existing database and will fail on a fresh one (duplicate constraint,
   function already exists).
3. Put the new project's URL and **publishable** key at the top of the
   `<script>` block in `index.html`.
4. `supabase link --project-ref <ref>` then `supabase functions deploy api`.
5. Enable GitHub Pages: repo Settings → Pages → source `main` / root.
   (The repo must be public, or Pages needs a paid plan.)

## Notes

- **The anon/publishable key in `index.html` is meant to be public.** It
  identifies the project, it does not grant access. RLS is the actual security
  boundary — an anonymous caller reading `profiles` gets an empty array. Never
  put the **service-role** key in the frontend; it belongs only in the Edge
  Function, where Supabase injects it as an environment variable.
- **Signup is open** — anyone with the URL can create an account. They see
  nothing until someone adds them to a group or shares an invite link.
- **Invite links** are `…/shareware/?invite=<token>`. The token *is* the
  authorization, so treat it like a password.
- **Backups**: Supabase takes daily backups on the free tier — Dashboard →
  Database → Backups. Pulling your own copy with
  `supabase db dump --linked -f backup.sql` needs **Docker Desktop installed and
  running** (it isn't, on this machine — the command fails with a docker error),
  or `brew install libpq` for a direct `pg_dump` using the connection string and
  database password from Dashboard → Settings → Database.
- **`app.py` is legacy.** The frontend no longer calls it. It's the original
  Flask + SQLite implementation, kept for reference — note that its split and
  settle-up logic is now duplicated in TypeScript in the Edge Function, so the
  two can drift. Don't deploy it.
