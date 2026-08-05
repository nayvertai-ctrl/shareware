# shareware

A single-file Splitwise-clone settle-up API. Flask + Python stdlib `sqlite3`, money stored as integer cents, all balances derived (never stored) from an immutable ledger of expenses, expense shares, and settlements.

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
```

`python3 app.py` wipes and re-seeds `splitwise.db` (a local SQLite file) on every start.

## Data model

Schema as defined in `app.py`:

| Table | Columns |
|-------|---------|
| `users` | `id` PK, `name` |
| `groups` | `id` PK, `name`, `simplify_debts` (0/1, default 0) |
| `expenses` | `id` PK, `group_id`, `paid_by`, `amount_cents`, `description`, `deleted_at` (soft-delete timestamp) |
| `expense_shares` | `expense_id`, `user_id`, `share_cents`; PK (`expense_id`, `user_id`) |
| `settlements` | `id` PK, `group_id`, `from_user`, `to_user`, `amount_cents` |
| `memberships` | `group_id`, `user_id`; PK (`group_id`, `user_id`) |

## API reference

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/users` | Create a user (`{name}`). |
| `GET` | `/users` | List all users. |
| `POST` | `/groups` | Create a group (`{name, simplify_debts?}`). |
| `GET` | `/groups` | List all groups. |
| `GET` | `/groups/<id>` | Group detail with members and each member's derived balance. |
| `POST` | `/groups/<id>/members` | Add a user to the group (`{user_id}`; idempotent). |
| `DELETE` | `/groups/<id>/members/<user_id>` | Remove a member; `409` if their balance isn't zero. |
| `POST` | `/groups/<id>/expenses` | Create an expense with a split; server computes and stores per-user shares. |
| `GET` | `/groups/<id>/expenses` | List a group's non-deleted expenses (newest first) with their shares. Query: `limit` (max 100, default 50), `offset`. |
| `PATCH` | `/expenses/<id>` | Edit an expense's description/amount/split; recomputes shares. |
| `DELETE` | `/expenses/<id>` | Soft-delete an expense (sets `deleted_at`); drops it from feed and balances. |
| `POST` | `/groups/<id>/settlements` | Record a cash payment from one user to another. |
| `GET` | `/groups/<id>/settle-up` | Return the payment plan that zeroes out the group. |

`PATCH /expenses/<id>` note: split type isn't persisted on the expense, so an amount-only edit re-splits **equally** over the current participants. Pass an explicit `split_type` + `splits`/`participants` to preserve a non-equal split.

`settle-up` behavior depends on the group's `simplify_debts` flag: when off, it lists each netted pairwise debt (pay whoever you transacted with); when on, it re-routes across the whole group with a greedy min-cash-flow algorithm (`settle_up`) for a near-minimal number of transactions (the exact minimum is NP-hard).

### curl examples (against the demo seed)

Seed group 1 = **Trip** (`simplify_debts` on) with Anna (1), Bob (2), Charlie (3): Dinner $60 paid by Anna split 3 ways, Taxi $30 paid by Bob split between Bob and Charlie. Balances: Anna +40, Bob −5, Charlie −35.

```bash
# Settle-up plan for the group
curl localhost:5000/groups/1/settle-up
# -> [{"from_user":3,"to_user":1,"amount":"35.00"},
#     {"from_user":2,"to_user":1,"amount":"5.00"}]

# List the group's expenses (newest first)
curl localhost:5000/groups/1/expenses

# Add an equal-split expense (Anna pays $12 snacks, split 3 ways)
curl -X POST localhost:5000/groups/1/expenses \
  -H 'Content-Type: application/json' \
  -d '{"paid_by":1,"amount":"12.00","split_type":"equal",
       "participants":[1,2,3],"description":"Snacks"}'

# Add an exact-split expense (must sum to the amount)
curl -X POST localhost:5000/groups/1/expenses \
  -H 'Content-Type: application/json' \
  -d '{"paid_by":2,"amount":"20.00","split_type":"exact",
       "splits":[{"user_id":1,"value":"5.00"},{"user_id":2,"value":"15.00"}]}'

# Charlie pays Anna back the $35 he owes
curl -X POST localhost:5000/groups/1/settlements \
  -H 'Content-Type: application/json' \
  -d '{"from_user":3,"to_user":1,"amount":"35.00"}'

# Edit an expense (change amount; shares re-split equally over participants)
curl -X PATCH localhost:5000/expenses/1 \
  -H 'Content-Type: application/json' \
  -d '{"amount":"66.00","description":"Dinner (updated)"}'

# Soft-delete expense 1
curl -X DELETE localhost:5000/expenses/1

# Create a user and a group, then add the user as a member
curl -X POST localhost:5000/users  -H 'Content-Type: application/json' -d '{"name":"Dana"}'
curl -X POST localhost:5000/groups -H 'Content-Type: application/json' -d '{"name":"Ski trip","simplify_debts":true}'
curl -X POST localhost:5000/groups/2/members -H 'Content-Type: application/json' -d '{"user_id":4}'
curl localhost:5000/groups/2   # detail with members + balances
```

## Split types

Set `split_type` on `POST /groups/<id>/expenses` (defaults to `equal`). Server-computed shares always sum exactly to the expense amount.

- **equal** — pass `participants: [user_ids]`; the amount is divided evenly.
- **exact** — pass `splits: [{user_id, value}]` where `value` is dollars; must sum to the amount or the request is rejected (400).
- **percent** — pass `splits: [{user_id, value}]` where `value` is a percentage; must sum to 100.
- **shares** — pass `splits: [{user_id, value}]` where `value` is any positive weight; the amount is divided proportionally.

For the non-exact types, leftover cents that don't divide evenly are distributed by **largest-remainder** (the users with the biggest fractional parts each get one extra cent), so the shares still sum to the total.

## Not built yet / intentionally skipped

- No authentication or authorization — every endpoint is open.
- Single currency; no multi-currency support.
- One payer per expense; no multi-payer expenses.
- Split type isn't persisted on expenses, so an amount-only `PATCH` re-splits equally (see the PATCH note above).
