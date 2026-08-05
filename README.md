# shareware

A single-file Splitwise-clone settle-up API + web app. Flask + Python stdlib `sqlite3`, money stored as integer cents, all balances derived (never stored) from an immutable ledger of expenses, expense shares, and settlements. Includes email/password accounts, invite links, per-group currency, and one-tap pay deep-links (UPI / PayPal / Venmo). The web UI is a single self-contained `index.html` served at `/`.

## Why it's designed this way

**Derived ledger.** Nothing stores "Bob owes Anna $5." The only persisted facts are ledger rows: who paid an expense (`expenses`), how that expense was split (`expense_shares`), and any cash paid back directly (`settlements`). A user's balance is a `SUM` over those rows computed on every read:

- Each expense share you owe but didn't pay counts against you.
- Each settlement you sent reduces what you owe the recipient.
- Netting across all pairs gives one number per user; **per group these always sum to zero**.

Because balances are recomputed, there is no denormalized total to keep in sync, no drift, and a soft-deleted expense simply stops contributing. Deleting or adding a row is the entire "update the balances" operation.

**Integer cents.** All money is stored and computed as integer cents to avoid floating-point rounding. Values are converted to cents on input via `Decimal` and rendered back as 2-decimal dollar strings (e.g. `"35.00"`) on output.

## Requirements & run

- Python 3
- Flask (the only dependency; `sqlite3` is stdlib)

```bash
pip install flask

python3 app.py test    # offline self-check, no server, exits when done
python3 app.py         # seed demo data + serve on http://localhost:5000
make demo              # start server, log in, run the API end-to-end, stop
```

`python3 app.py` wipes and re-seeds `splitwise.db` (a local SQLite file) on every start. Open http://localhost:5000 for the web app.

**Demo accounts:** `anna@example.com`, `bob@example.com`, `charlie@example.com` — all with password `password`.

## Authentication

Passwords are hashed with PBKDF2-SHA256 (per-user salt); login returns a session token. **Every data route requires `Authorization: Bearer <token>`** (or `?token=` for convenience); the auth routes below do not. Authorization is membership-based: you only see and act on groups you belong to.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/auth/signup` | Create an account (`{name, email, password}`) → `{token, user}`. |
| `POST` | `/auth/login` | `{email, password}` → `{token, user}`. |
| `GET` | `/auth/me` | Current user, including payment handles. |
| `PATCH` | `/auth/me` | Update your `name` and/or payment handles (`upi`, `paypal`, `venmo`; blank clears). |
| `POST` | `/auth/logout` | Invalidate the current session token. |

## Data model

Schema as defined in `app.py`:

| Table | Columns |
|-------|---------|
| `users` | `id` PK, `name`, `email` (unique), `password_hash`, `upi_id`, `paypal_me`, `venmo` |
| `groups` | `id` PK, `name`, `simplify_debts` (0/1, default 0), `currency` (default `USD`) |
| `expenses` | `id` PK, `group_id`, `paid_by`, `amount_cents`, `description`, `deleted_at` (soft-delete timestamp) |
| `expense_shares` | `expense_id`, `user_id`, `share_cents`; PK (`expense_id`, `user_id`) |
| `settlements` | `id` PK, `group_id`, `from_user`, `to_user`, `amount_cents` |
| `memberships` | `group_id`, `user_id`; PK (`group_id`, `user_id`) |
| `sessions` | `token` PK, `user_id`, `created_at` (30-day expiry) |
| `invites` | `token` PK, `group_id`, `created_by`, `created_at` |

## API reference

All routes below require a Bearer token (see [Authentication](#authentication)).

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/users` | Create a passwordless "placeholder" person to add to a group (`{name}`). |
| `GET` | `/users` | List all users (id + name). |
| `GET` | `/users/<id>/summary` | Your cross-group balance totals, bucketed by currency (self only). |
| `POST` | `/groups` | Create a group (`{name, simplify_debts?, currency?}`); creator auto-joins. |
| `GET` | `/groups` | List the groups you belong to. |
| `GET` | `/groups/<id>` | Group detail: currency, members, each member's derived balance + payment handles. |
| `POST` | `/groups/<id>/members` | Add a user to the group (`{user_id}`; idempotent). |
| `DELETE` | `/groups/<id>/members/<user_id>` | Remove a member; `409` if their balance isn't zero. |
| `POST` | `/groups/<id>/invite` | Get/create the group's stable invite link (`{token, url}`). |
| `GET` | `/invites/<token>` | Preview an invite (group name, member count, whether you're already in). |
| `POST` | `/invites/<token>/accept` | Join the group behind the invite. |
| `POST` | `/groups/<id>/expenses` | Create an expense with a split; server computes and stores per-user shares. |
| `GET` | `/groups/<id>/expenses` | List a group's non-deleted expenses (newest first) with their shares. Query: `limit` (max 100, default 50), `offset`. |
| `PATCH` | `/expenses/<id>` | Edit an expense's description/amount/split; recomputes shares. |
| `DELETE` | `/expenses/<id>` | Soft-delete an expense (sets `deleted_at`); drops it from feed and balances. |
| `POST` | `/groups/<id>/settlements` | Record a cash payment from one user to another. |
| `GET` | `/groups/<id>/settle-up` | Return the payment plan that zeroes out the group. |

`PATCH /expenses/<id>` note: split type isn't persisted on the expense, so an amount-only edit re-splits **equally** over the current participants. Pass an explicit `split_type` + `splits`/`participants` to preserve a non-equal split.

`settle-up` behavior depends on the group's `simplify_debts` flag: when off, it lists each netted pairwise debt (pay whoever you transacted with); when on, it re-routes across the whole group with a greedy min-cash-flow algorithm (`settle_up`) for a near-minimal number of transactions (the exact minimum is NP-hard).

### curl examples (against the demo seed)

Seed group 1 = **Trip** (`simplify_debts` on, USD) with Anna (1), Bob (2), Charlie (3): Dinner $60 paid by Anna split 3 ways, Taxi $30 paid by Bob split between Bob and Charlie. Balances: Anna +40, Bob −5, Charlie −35.

```bash
# 1. Log in and capture a token (every call below sends it as a Bearer header)
TOKEN=$(curl -s localhost:5000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"anna@example.com","password":"password"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"

# Settle-up plan for the group
curl -H "$AUTH" localhost:5000/groups/1/settle-up
# -> [{"from_user":3,"to_user":1,"amount":"35.00"},
#     {"from_user":2,"to_user":1,"amount":"5.00"}]

# List the group's expenses (newest first)
curl -H "$AUTH" localhost:5000/groups/1/expenses

# Add an equal-split expense (Anna pays $12 snacks, split 3 ways)
curl -X POST -H "$AUTH" localhost:5000/groups/1/expenses \
  -H 'Content-Type: application/json' \
  -d '{"paid_by":1,"amount":"12.00","split_type":"equal",
       "participants":[1,2,3],"description":"Snacks"}'

# Charlie pays Anna back the $35 he owes
curl -X POST -H "$AUTH" localhost:5000/groups/1/settlements \
  -H 'Content-Type: application/json' \
  -d '{"from_user":3,"to_user":1,"amount":"35.00"}'

# Set your payment handles so members can pay you back in one tap
curl -X PATCH -H "$AUTH" localhost:5000/auth/me \
  -H 'Content-Type: application/json' -d '{"upi":"anna@okhdfc","paypal":"annapay"}'

# Create an INR group, then get a shareable invite link for it
curl -X POST -H "$AUTH" localhost:5000/groups \
  -H 'Content-Type: application/json' -d '{"name":"Goa","currency":"INR"}'
curl -X POST -H "$AUTH" localhost:5000/groups/2/invite   # -> {"token":..., "url":".../?invite=..."}
```

Opening the invite URL in a browser, once signed in, joins you to that group.

## Split types

Set `split_type` on `POST /groups/<id>/expenses` (defaults to `equal`). Server-computed shares always sum exactly to the expense amount.

- **equal** — pass `participants: [user_ids]`; the amount is divided evenly.
- **exact** — pass `splits: [{user_id, value}]` where `value` is dollars; must sum to the amount or the request is rejected (400).
- **percent** — pass `splits: [{user_id, value}]` where `value` is a percentage; must sum to 100.
- **shares** — pass `splits: [{user_id, value}]` where `value` is any positive weight; the amount is divided proportionally.

For the non-exact types, leftover cents that don't divide evenly are distributed by **largest-remainder** (the users with the biggest fractional parts each get one extra cent), so the shares still sum to the total.

## Currencies

Each group has one currency (`USD` default; also `EUR GBP INR CAD AUD SGD AED`). Balances and settle-up stay within that currency — there is no cross-currency conversion, so the cross-group summary is bucketed per currency. Only 2-decimal currencies are supported (keeps the integer-cents model uniform).

## Not built yet / intentionally skipped

- No email verification or password reset (email is just the login identifier — there's no mail server).
- Per-expense currencies with live FX conversion — deferred; needs an exchange-rate source. Currency is per **group**, not per expense.
- 0-decimal (JPY) / 3-decimal currencies — excluded to keep the integer-cents model uniform.
- One payer per expense; no multi-payer expenses.
- Couples/households are a client-side view grouping (per browser via `localStorage`), not shared server-side.
- Split type isn't persisted on expenses, so an amount-only `PATCH` re-splits equally (see the PATCH note above).
