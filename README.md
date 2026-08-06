# shareware

**Live app: https://nayvertai-ctrl.github.io/shareware/**

A Splitwise-style expense splitter. Money is stored as integer cents and all
balances are derived (never stored) from an immutable ledger of expenses,
expense shares, and settlements. Includes email/password accounts, invite links,
per-group currency, and one-tap pay deep-links (UPI / PayPal / Venmo).

The whole frontend is a single self-contained `index.html` on GitHub Pages,
talking directly to Supabase. There is no application server — see
[DEPLOY.md](DEPLOY.md) for the architecture and how to deploy changes.

```
index.html  ──►  Supabase Auth        (email + password)
                 Supabase PostgREST   (reads + safe writes, guarded by RLS)
                 Edge Function `api`  (the logic RLS can't express)
```

## Why it's designed this way

**Derived ledger.** Nothing stores "Bob owes Anna ₹5." The only persisted facts
are ledger rows: who paid an expense (`expenses`), how that expense was split
(`expense_shares`), and any cash paid back directly (`settlements`). A user's
balance is a sum over those rows computed on every read:

- Each expense share you owe but didn't pay counts against you.
- Each settlement you sent reduces what you owe the recipient.
- Netting across all pairs gives one number per user; **per group these always sum to zero**.

Because balances are recomputed, there is no denormalized total to keep in sync,
no drift, and a soft-deleted expense simply stops contributing. Deleting or
adding a row is the entire "update the balances" operation.

**Integer cents.** All money is stored and computed as integer cents to avoid
floating-point rounding. Dollar strings are parsed to cents by string
manipulation — never `amount * 100`, which drifts in float64 — and rendered back
as 2-decimal strings (e.g. `"35.00"`) on output.

**Settle-up.** With `simplify_debts` off, you pay back exactly who you
transacted with. With it on, debts are re-routed across the whole group by a
greedy min-cash-flow pass (match debtor to creditor, repeat), which gets a
near-minimal number of transfers — the exact minimum is NP-hard, and greedy is
what Splitwise itself ships.

**Security boundary.** Row Level Security is the real enforcement, not the UI.
Every read and the writes that can't break an invariant go straight to
PostgREST under RLS. Anything with real logic — split math, settle-up,
invite-by-token, balance-gated member removal — goes through the `api` Edge
Function, which uses the service-role key only *after* validating the request
itself. `supabase/schema.sql` documents which tables are deliberately left
without a client write policy, and why.

## Data model

Defined in [`supabase/schema.sql`](supabase/schema.sql). Accounts live in
Supabase's managed `auth.users`; `profiles` holds the app-specific fields.

| Table | Columns |
|-------|---------|
| `profiles` | `id` PK → `auth.users`, `name`, `upi_id`, `paypal_me`, `venmo` |
| `groups` | `id` PK, `name`, `simplify_debts` (default false), `currency` (default `INR`), `created_by`, `created_at` |
| `memberships` | `group_id`, `user_id`; PK (`group_id`, `user_id`) |
| `expenses` | `id` PK, `group_id`, `paid_by`, `amount_cents`, `description`, `deleted_at` (soft-delete), `created_at` |
| `expense_shares` | `expense_id`, `user_id`, `share_cents`; PK (`expense_id`, `user_id`) |
| `settlements` | `id` PK, `group_id`, `from_user`, `to_user`, `amount_cents`, `created_at` |
| `invites` | `token` PK (18-char secret), `group_id`, `created_by`, `created_at` |

## Split types

Server-computed shares always sum exactly to the expense amount.

- **equal** — pass `participants: [user_ids]`; the amount is divided evenly.
- **exact** — pass `splits: [{user_id, value}]` where `value` is dollars; must sum to the amount or the request is rejected (400).
- **percent** — pass `splits: [{user_id, value}]` where `value` is a percentage; must sum to 100.
- **shares** — pass `splits: [{user_id, value}]` where `value` is any positive weight; the amount is divided proportionally.

For the non-exact types, leftover cents that don't divide evenly are distributed
by **largest-remainder** (the users with the biggest fractional parts each get
one extra cent), so the shares still sum to the total.

## Currencies

Each group has one currency (`INR` default; also `USD EUR GBP CAD AUD SGD AED`).
Balances and settle-up stay within that currency — there is no cross-currency
conversion, so the cross-group summary is bucketed per currency. Only 2-decimal
currencies are supported (keeps the integer-cents model uniform).

## Development

```bash
make serve   # serve index.html on :5000 (static; talks to hosted Supabase)
make test    # Edge Function money-logic tests (offline, no network)
make check   # type-check the Edge Function
make deploy  # test, then deploy the Edge Function
```

## Not built yet / intentionally skipped

- No password reset (email is just the login identifier).
- No UI for editing an expense — the Edge Function supports `update_expense`, but nothing calls it.
- Per-expense currencies with live FX conversion — deferred; needs an exchange-rate source. Currency is per **group**, not per expense.
- 0-decimal (JPY) / 3-decimal currencies — excluded to keep the integer-cents model uniform.
- One payer per expense; no multi-payer expenses.
- Couples/households are a client-side view grouping (per browser via `localStorage`), not shared server-side.
- Split type isn't persisted on expenses, so an amount-only update re-splits equally over the existing participants.

## History

The original implementation was a single-file Flask + stdlib `sqlite3` service
(`app.py`), with the web UI served at `/`. It was removed once the frontend moved
to Supabase, since it kept a second, drifting copy of the split and settle-up
math. To read it: `git show 152e778:app.py`.
