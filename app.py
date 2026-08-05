"""Splitwise-clone settle-up service. Flask + stdlib sqlite3, money in integer cents.

    python3 app.py          seed demo data + run server on :5000
    python3 app.py test     run the self-check and exit

Endpoint:
    GET /groups/<id>/settle-up   -> minimal payment plan (honors simplify_debts)
"""
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from flask import Flask, jsonify, abort, request

DB = "splitwise.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users   (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS groups  (id INTEGER PRIMARY KEY, name TEXT,
                                    simplify_debts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, group_id INTEGER,
                                    paid_by INTEGER, amount_cents INTEGER,
                                    description TEXT, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS expense_shares(expense_id INTEGER, user_id INTEGER,
                                          share_cents INTEGER,
                                          PRIMARY KEY(expense_id, user_id));
CREATE TABLE IF NOT EXISTS settlements(id INTEGER PRIMARY KEY, group_id INTEGER,
                                       from_user INTEGER, to_user INTEGER,
                                       amount_cents INTEGER);
CREATE TABLE IF NOT EXISTS memberships(group_id INTEGER, user_id INTEGER,
                                       PRIMARY KEY(group_id, user_id));
"""


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


# --- core logic -------------------------------------------------------------

def pairwise_debts(conn, group_id):
    """debt[(a,b)] = cents a owes b, netted so only one direction per pair is >0."""
    raw = defaultdict(int)  # (debtor, creditor) -> cents
    exp = conn.execute(
        "SELECT id, paid_by FROM expenses WHERE group_id=? AND deleted_at IS NULL",
        (group_id,)).fetchall()
    for e in exp:
        for s in conn.execute(
                "SELECT user_id, share_cents FROM expense_shares WHERE expense_id=?",
                (e["id"],)):
            if s["user_id"] != e["paid_by"]:
                raw[(s["user_id"], e["paid_by"])] += s["share_cents"]
    for s in conn.execute(
            "SELECT from_user, to_user, amount_cents FROM settlements WHERE group_id=?",
            (group_id,)):
        # from_user paid to_user back -> reduces what from_user owes to_user
        raw[(s["from_user"], s["to_user"])] -= s["amount_cents"]

    net = {}
    seen = set()
    for (a, b) in list(raw):
        if (a, b) in seen:
            continue
        seen.add((a, b)); seen.add((b, a))
        d = raw[(a, b)] - raw[(b, a)]
        if d > 0:
            net[(a, b)] = d
        elif d < 0:
            net[(b, a)] = -d
    return net


def net_balances(pairwise):
    """Per-user net position from pairwise debts. Negative = owes, positive = owed."""
    bal = defaultdict(int)
    for (debtor, creditor), cents in pairwise.items():
        bal[debtor] -= cents
        bal[creditor] += cents
    return bal


def split_shares(amount_cents, split_type, spec):
    """Return {user_id: owed_cents} summing exactly to amount_cents.

    spec depends on split_type:
      equal   -> list of user_ids
      exact   -> {user_id: dollars}   (must sum to the amount)
      percent -> {user_id: percent}   (must sum to 100)
      shares  -> {user_id: weight}    (any positive weights)
    Non-exact splits use largest-remainder to place leftover cents fairly."""
    if split_type == "exact":
        shares = {u: int((Decimal(str(v)) * 100).to_integral_value()) for u, v in spec.items()}
        if sum(shares.values()) != amount_cents:
            raise ValueError("exact shares must sum to the amount")
        return shares

    if split_type == "equal":
        weights = {u: Decimal(1) for u in spec}
    elif split_type == "percent":
        weights = {u: Decimal(str(v)) for u, v in spec.items()}
        if sum(weights.values()) != 100:
            raise ValueError("percentages must sum to 100")
    elif split_type == "shares":
        weights = {u: Decimal(str(v)) for u, v in spec.items()}
    else:
        raise ValueError("unknown split_type")
    if not weights or any(w <= 0 for w in weights.values()):
        raise ValueError("split needs positive weights")

    total = sum(weights.values())
    floors, remainders = {}, {}
    for u, w in weights.items():
        raw = Decimal(amount_cents) * w / total
        floors[u] = int(raw)
        remainders[u] = raw - floors[u]
    leftover = amount_cents - sum(floors.values())
    for u in sorted(remainders, key=lambda u: remainders[u], reverse=True)[:leftover]:
        floors[u] += 1
    return floors


def settle_up(net):
    """Greedy min-cash-flow: match biggest debtor to biggest creditor.
    Near-minimal transaction count; exact-min is NP-hard (ponytail: greedy is
    what Splitwise ships). Returns [(from, to, cents)]."""
    debtors = sorted(([u, -b] for u, b in net.items() if b < 0), key=lambda x: x[1])
    creditors = sorted(([u, b] for u, b in net.items() if b > 0), key=lambda x: -x[1])
    plan, i, j = [], 0, 0
    while i < len(debtors) and j < len(creditors):
        pay = min(debtors[i][1], creditors[j][1])
        plan.append((debtors[i][0], creditors[j][0], pay))
        debtors[i][1] -= pay; creditors[j][1] -= pay
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return plan


def settle_plan(conn, group_id):
    g = conn.execute("SELECT simplify_debts FROM groups WHERE id=?",
                     (group_id,)).fetchone()
    if g is None:
        return None
    pw = pairwise_debts(conn, group_id)
    if g["simplify_debts"]:
        edges = settle_up(net_balances(pw))          # re-route across the group
    else:
        edges = [(a, b, c) for (a, b), c in pw.items()]  # pay who you transacted with
    return [{"from_user": f, "to_user": t, "amount": f"{c/100:.2f}"}
            for f, t, c in edges if c > 0]


# --- API --------------------------------------------------------------------

app = Flask(__name__)


@app.get("/")
def index():
    # ponytail: static index page so the API root isn't a bare 404
    return """<!doctype html><meta charset=utf-8>
<title>shareware API</title>
<style>body{font:16px/1.5 system-ui;max-width:44rem;margin:3rem auto;padding:0 1rem}
code{background:#f4f4f4;padding:.1em .3em;border-radius:3px}li{margin:.3em 0}</style>
<h1>shareware</h1>
<p>Splitwise-clone settle-up API. Balances are derived from an immutable
ledger; money in integer cents. Try the demo group (Anna/Bob/Charlie):</p>
<ul>
<li><a href="/groups">/groups</a> &middot; <a href="/users">/users</a></li>
<li><a href="/groups/1">/groups/1</a> &mdash; members + balances</li>
<li><a href="/groups/1/expenses">/groups/1/expenses</a> &mdash; expense feed</li>
<li><a href="/groups/1/settle-up">/groups/1/settle-up</a> &mdash; who pays whom</li>
</ul>
<p>Full API in the <code>README.md</code>.</p>
"""


@app.post("/groups/<int:group_id>/settlements")
def create_settlement(group_id):
    body = request.get_json(silent=True) or {}
    frm, to, amount = body.get("from_user"), body.get("to_user"), body.get("amount")
    if frm is None or to is None or amount is None:
        abort(400, "from_user, to_user, amount required")
    if frm == to:
        abort(400, "from_user and to_user must differ")
    try:
        cents = int((Decimal(str(amount)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        abort(400, "amount must be a number")
    if cents <= 0:
        abort(400, "amount must be positive")
    with db() as conn:
        if conn.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() is None:
            abort(404, "group not found")
        members = {r["id"] for r in conn.execute("SELECT id FROM users")}
        if frm not in members or to not in members:
            abort(400, "unknown user")
        cur = conn.execute(
            "INSERT INTO settlements(group_id, from_user, to_user, amount_cents) "
            "VALUES (?,?,?,?)", (group_id, frm, to, cents))
        conn.commit()
        return jsonify({"id": cur.lastrowid, "group_id": group_id, "from_user": frm,
                        "to_user": to, "amount": f"{cents/100:.2f}"}), 201


@app.post("/groups/<int:group_id>/expenses")
def create_expense(group_id):
    body = request.get_json(silent=True) or {}
    paid_by, amount = body.get("paid_by"), body.get("amount")
    split_type = body.get("split_type", "equal")
    if paid_by is None or amount is None:
        abort(400, "paid_by and amount required")
    try:
        amount_cents = int((Decimal(str(amount)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        abort(400, "amount must be a number")
    if amount_cents <= 0:
        abort(400, "amount must be positive")

    if split_type == "equal":
        spec = body.get("participants")
        if not spec:
            abort(400, "participants required for equal split")
    else:
        splits = body.get("splits")
        if not splits:
            abort(400, "splits required")
        spec = {s["user_id"]: s["value"] for s in splits}
    try:
        shares = split_shares(amount_cents, split_type, spec)
    except (ValueError, KeyError, TypeError, InvalidOperation) as e:
        abort(400, str(e) or "invalid split")

    with db() as conn:
        if conn.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() is None:
            abort(404, "group not found")
        members = {r["id"] for r in conn.execute("SELECT id FROM users")}
        if paid_by not in members or any(u not in members for u in shares):
            abort(400, "unknown user")
        cur = conn.execute(
            "INSERT INTO expenses(group_id, paid_by, amount_cents, description) "
            "VALUES (?,?,?,?)", (group_id, paid_by, amount_cents, body.get("description")))
        eid = cur.lastrowid
        conn.executemany("INSERT INTO expense_shares VALUES (?,?,?)",
                         [(eid, u, c) for u, c in shares.items()])
        conn.commit()
    return jsonify({"id": eid, "paid_by": paid_by, "amount": f"{amount_cents/100:.2f}",
                    "description": body.get("description"),
                    "shares": [{"user_id": u, "share": f"{c/100:.2f}"}
                               for u, c in shares.items()]}), 201


@app.get("/groups/<int:group_id>/expenses")
def list_expenses(group_id):
    limit = min(request.args.get("limit", 50, type=int), 100)
    offset = request.args.get("offset", 0, type=int)
    with db() as conn:
        if conn.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() is None:
            abort(404, "group not found")
        rows = conn.execute(
            "SELECT id, paid_by, amount_cents, description FROM expenses "
            "WHERE group_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT ? OFFSET ?",
            (group_id, limit, offset)).fetchall()
        out = []
        for e in rows:
            shares = conn.execute(
                "SELECT user_id, share_cents FROM expense_shares WHERE expense_id=?",
                (e["id"],)).fetchall()
            out.append({
                "id": e["id"], "paid_by": e["paid_by"],
                "amount": f"{e['amount_cents']/100:.2f}",
                "description": e["description"],
                "shares": [{"user_id": s["user_id"], "share": f"{s['share_cents']/100:.2f}"}
                           for s in shares],
            })
    return jsonify(out)


@app.delete("/expenses/<int:expense_id>")
def delete_expense(expense_id):
    with db() as conn:
        row = conn.execute(
            "SELECT id, group_id, paid_by, amount_cents, description FROM expenses "
            "WHERE id=? AND deleted_at IS NULL", (expense_id,)).fetchone()
        if row is None:
            abort(404, "expense not found")
        conn.execute("UPDATE expenses SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",
                     (expense_id,))
        conn.commit()
    return jsonify({"id": row["id"], "group_id": row["group_id"],
                    "paid_by": row["paid_by"], "amount": f"{row['amount_cents']/100:.2f}",
                    "description": row["description"], "deleted": True})


@app.post("/users")
def create_user():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not name:
        abort(400, "name required")
    with db() as conn:
        cur = conn.execute("INSERT INTO users(name) VALUES (?)", (name,))
        conn.commit()
        return jsonify({"id": cur.lastrowid, "name": name}), 201


@app.get("/users")
def list_users():
    with db() as conn:
        rows = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])


@app.get("/users/<int:user_id>/summary")
def user_summary(user_id):
    """Splitwise-style headline: this user's net across every group, plus totals.
    net>0 = owed to you, net<0 = you owe. total_net = owed - owe."""
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
            abort(404, "user not found")
        groups = conn.execute("SELECT id, name FROM groups ORDER BY id").fetchall()
        per_group, owed, owe = [], 0, 0
        for g in groups:
            net = net_balances(pairwise_debts(conn, g["id"])).get(user_id, 0)
            if net == 0:
                continue
            per_group.append({"group_id": g["id"], "name": g["name"],
                              "balance": f"{net/100:.2f}"})
            if net > 0:
                owed += net
            else:
                owe += -net
    return jsonify({"user_id": user_id,
                    "total_owed": f"{owed/100:.2f}",     # summed across groups
                    "total_owe": f"{owe/100:.2f}",
                    "total_net": f"{(owed - owe)/100:.2f}",
                    "groups": per_group})


@app.post("/groups")
def create_group():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not name:
        abort(400, "name required")
    simplify = 1 if body.get("simplify_debts") else 0
    with db() as conn:
        cur = conn.execute("INSERT INTO groups(name, simplify_debts) VALUES (?,?)",
                           (name, simplify))
        conn.commit()
        return jsonify({"id": cur.lastrowid, "name": name,
                        "simplify_debts": bool(simplify)}), 201


@app.get("/groups")
def list_groups():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, simplify_debts FROM groups ORDER BY id").fetchall()
    return jsonify([{"id": r["id"], "name": r["name"],
                     "simplify_debts": bool(r["simplify_debts"])} for r in rows])


@app.get("/groups/<int:group_id>")
def get_group(group_id):
    with db() as conn:
        g = conn.execute("SELECT id, name, simplify_debts FROM groups WHERE id=?",
                         (group_id,)).fetchone()
        if g is None:
            abort(404, "group not found")
        net = net_balances(pairwise_debts(conn, group_id))
        members = conn.execute(
            "SELECT m.user_id, u.name FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.group_id=? ORDER BY m.user_id", (group_id,)).fetchall()
    return jsonify({"id": g["id"], "name": g["name"],
                    "simplify_debts": bool(g["simplify_debts"]),
                    "members": [{"user_id": m["user_id"], "name": m["name"],
                                 "balance": f"{net[m['user_id']]/100:.2f}"}
                                for m in members]})


@app.post("/groups/<int:group_id>/members")
def add_member(group_id):
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    if user_id is None:
        abort(400, "user_id required")
    with db() as conn:
        if conn.execute("SELECT 1 FROM groups WHERE id=?", (group_id,)).fetchone() is None:
            abort(404, "group not found")
        if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
            abort(404, "user not found")
        conn.execute("INSERT OR IGNORE INTO memberships(group_id, user_id) VALUES (?,?)",
                     (group_id, user_id))
        conn.commit()
    return jsonify({"group_id": group_id, "user_id": user_id}), 201


@app.delete("/groups/<int:group_id>/members/<int:user_id>")
def remove_member(group_id, user_id):
    with db() as conn:
        if conn.execute("SELECT 1 FROM memberships WHERE group_id=? AND user_id=?",
                        (group_id, user_id)).fetchone() is None:
            abort(404, "member not found")
        net = net_balances(pairwise_debts(conn, group_id))
        if net[user_id] != 0:
            abort(409, "member has a nonzero balance")
        conn.execute("DELETE FROM memberships WHERE group_id=? AND user_id=?",
                     (group_id, user_id))
        conn.commit()
    return jsonify({"group_id": group_id, "user_id": user_id, "removed": True})


@app.patch("/expenses/<int:expense_id>")
def update_expense(expense_id):
    body = request.get_json(silent=True) or {}
    with db() as conn:
        row = conn.execute(
            "SELECT id, paid_by, amount_cents, description FROM expenses "
            "WHERE id=? AND deleted_at IS NULL", (expense_id,)).fetchone()
        if row is None:
            abort(404, "expense not found")

        amount_cents = row["amount_cents"]
        if "amount" in body:
            try:
                amount_cents = int((Decimal(str(body["amount"])) * 100).to_integral_value())
            except (InvalidOperation, ValueError):
                abort(400, "amount must be a number")
            if amount_cents <= 0:
                abort(400, "amount must be positive")

        shares = None
        split_type = body.get("split_type")
        if split_type is not None:
            if split_type == "equal":
                spec = body.get("participants")
                if not spec:
                    abort(400, "participants required for equal split")
            else:
                splits = body.get("splits")
                if not splits:
                    abort(400, "splits required")
                spec = {s["user_id"]: s["value"] for s in splits}
            try:
                shares = split_shares(amount_cents, split_type, spec)
            except (ValueError, KeyError, TypeError, InvalidOperation) as e:
                abort(400, str(e) or "invalid split")
        elif "amount" in body:
            # amount changed without a new split -> re-split equally over existing participants
            participants = [s["user_id"] for s in conn.execute(
                "SELECT user_id FROM expense_shares WHERE expense_id=?", (expense_id,))]
            shares = split_shares(amount_cents, "equal", participants)

        if shares is not None:
            members = {r["id"] for r in conn.execute("SELECT id FROM users")}
            if any(u not in members for u in shares):
                abort(400, "unknown user")

        description = body.get("description", row["description"])
        conn.execute("UPDATE expenses SET amount_cents=?, description=? WHERE id=?",
                     (amount_cents, description, expense_id))
        if shares is not None:
            conn.execute("DELETE FROM expense_shares WHERE expense_id=?", (expense_id,))
            conn.executemany("INSERT INTO expense_shares VALUES (?,?,?)",
                             [(expense_id, u, c) for u, c in shares.items()])
        conn.commit()
        out = conn.execute(
            "SELECT user_id, share_cents FROM expense_shares WHERE expense_id=?",
            (expense_id,)).fetchall()
    return jsonify({"id": expense_id, "paid_by": row["paid_by"],
                    "amount": f"{amount_cents/100:.2f}", "description": description,
                    "shares": [{"user_id": s["user_id"], "share": f"{s['share_cents']/100:.2f}"}
                               for s in out]})


@app.get("/groups/<int:group_id>/settle-up")
def settle_up_endpoint(group_id):
    with db() as conn:
        plan = settle_plan(conn, group_id)
    if plan is None:
        abort(404, "group not found")
    return jsonify(plan)


# --- seed + self-check ------------------------------------------------------

def seed():
    with db() as conn:
        conn.executescript(SCHEMA)
        conn.executescript("DELETE FROM users; DELETE FROM groups; DELETE FROM expenses;"
                           "DELETE FROM expense_shares; DELETE FROM settlements;"
                           "DELETE FROM memberships;")
        conn.executemany("INSERT INTO users VALUES (?,?)",
                         [(1, "Anna"), (2, "Bob"), (3, "Charlie")])
        conn.execute("INSERT INTO groups VALUES (1,'Trip',1)")   # simplify ON
        conn.executemany("INSERT INTO memberships VALUES (?,?)",
                         [(1, 1), (1, 2), (1, 3)])
        # Anna paid $60, split 3 ways ($20 each)
        conn.execute("INSERT INTO expenses VALUES (1,1,1,6000,'Dinner',NULL)")
        conn.executemany("INSERT INTO expense_shares VALUES (?,?,?)",
                         [(1, 1, 2000), (1, 2, 2000), (1, 3, 2000)])
        # Bob paid $30, split between Bob & Charlie ($15 each)
        conn.execute("INSERT INTO expenses VALUES (2,1,2,3000,'Taxi',NULL)")
        conn.executemany("INSERT INTO expense_shares VALUES (?,?,?)",
                         [(2, 2, 1500), (2, 3, 1500)])
        conn.commit()


def selfcheck():
    seed()
    with db() as conn:
        pw = pairwise_debts(conn, 1)
        net = net_balances(pw)
        # Anna: paid 6000, owes 2000 -> +4000 ; Bob: paid 3000 owes 2000+1500 -> -500
        # Charlie: owes 2000+1500 -> -3500 ; sums to 0
        assert net[1] == 4000 and net[2] == -500 and net[3] == -3500, dict(net)
        assert sum(net.values()) == 0

        plan = settle_up(net)
        # plan must preserve every net position and only move debtor->creditor
        got = defaultdict(int)
        for f, t, amt in plan:
            assert amt > 0 and net[f] < 0 and net[t] > 0
            got[f] -= amt; got[t] += amt
        assert got == net, (dict(got), dict(net))

        # API-shaped output
        out = settle_plan(conn, 1)
        assert all(float(p["amount"]) > 0 for p in out)
        print("ok:", out)   # Charlie->Anna 35.00, Bob->Anna 5.00

    # user summary (fresh seed): Anna owed 40, Bob owes 5, Charlie owes 35
    c = app.test_client()
    anna = c.get("/users/1/summary").get_json()
    assert anna["total_owed"] == "40.00" and anna["total_owe"] == "0.00"
    assert anna["total_net"] == "40.00" and anna["groups"][0]["group_id"] == 1
    bob = c.get("/users/2/summary").get_json()
    assert bob["total_owe"] == "5.00" and bob["total_net"] == "-5.00"
    assert c.get("/users/999/summary").status_code == 404

    # POST settlements: validation + effect on the plan
    assert c.post("/groups/1/settlements", json={"from_user": 3}).status_code == 400
    assert c.post("/groups/1/settlements",
                  json={"from_user": 3, "to_user": 3, "amount": 5}).status_code == 400
    assert c.post("/groups/1/settlements",
                  json={"from_user": 3, "to_user": 1, "amount": -5}).status_code == 400
    assert c.post("/groups/9/settlements",
                  json={"from_user": 3, "to_user": 1, "amount": 5}).status_code == 404
    # Charlie pays Anna the $35 he owes -> his edge disappears from the plan
    r = c.post("/groups/1/settlements",
               json={"from_user": 3, "to_user": 1, "amount": "35.00"})
    assert r.status_code == 201, r.get_json()
    with db() as conn:
        after = settle_plan(conn, 1)
    assert not any(p["from_user"] == 3 for p in after), after
    print("after settlement:", after)   # only Bob->Anna 5.00 remains

    # GET expenses: feed shape, newest-first, share sums == amount, 404
    ex = c.get("/groups/1/expenses").get_json()
    assert [e["id"] for e in ex] == [2, 1]                       # DESC order
    for e in ex:
        assert sum(float(s["share"]) for s in e["shares"]) == float(e["amount"])
    assert len(c.get("/groups/1/expenses?limit=1").get_json()) == 1
    assert c.get("/groups/9/expenses").status_code == 404
    print("expenses:", ex)

    # split_shares: leftover cents distributed, always sums to the total
    assert sorted(split_shares(1000, "equal", [1, 2, 3]).values()) == [333, 333, 334]
    assert sum(split_shares(1000, "equal", [1, 2, 3]).values()) == 1000
    assert split_shares(1000, "percent", {1: 25, 2: 75}) == {1: 250, 2: 750}
    assert sum(split_shares(1000, "shares", {1: 1, 2: 2}).values()) == 1000

    # POST expenses: happy path shows up in feed; bad splits rejected
    r = c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "10.00",
               "split_type": "equal", "participants": [1, 2, 3], "description": "Snacks"})
    assert r.status_code == 201, r.get_json()
    new = r.get_json()
    assert sum(float(s["share"]) for s in new["shares"]) == 10.00
    assert c.get("/groups/1/expenses").get_json()[0]["description"] == "Snacks"  # newest
    assert c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "10.00",
               "split_type": "exact", "splits": [{"user_id": 1, "value": "3.00"}]}
               ).status_code == 400   # doesn't sum to amount
    assert c.post("/groups/9/expenses", json={"paid_by": 1, "amount": "5.00",
               "participants": [1]}).status_code == 404
    print("created:", new)

    # DELETE expense: soft-deletes, drops from feed & balances, idempotent-404 on repeat
    before = len(c.get("/groups/1/expenses").get_json())
    d = c.delete(f"/expenses/{new['id']}")
    assert d.status_code == 200 and d.get_json()["deleted"] is True
    assert len(c.get("/groups/1/expenses").get_json()) == before - 1
    with db() as conn:                       # balances drop the deleted expense
        assert settle_plan(conn, 1) == [{"from_user": 2, "to_user": 1, "amount": "5.00"}]
    assert c.delete(f"/expenses/{new['id']}").status_code == 404   # already gone
    assert c.delete("/expenses/999").status_code == 404
    print("deleted:", d.get_json())

    # users + groups CRUD
    u = c.post("/users", json={"name": "Dana"})
    assert u.status_code == 201
    uid = u.get_json()["id"]
    assert any(x["id"] == uid for x in c.get("/users").get_json())
    g = c.post("/groups", json={"name": "Ski", "simplify_debts": True})
    assert g.status_code == 201 and g.get_json()["simplify_debts"] is True
    gid = g.get_json()["id"]
    assert any(x["id"] == gid for x in c.get("/groups").get_json())
    print("created user/group:", uid, gid)

    # members: add + list via GET /groups/<id>, 404s on unknown group/user
    assert c.post(f"/groups/{gid}/members", json={"user_id": uid}).status_code == 201
    assert c.post(f"/groups/{gid}/members", json={"user_id": uid}).status_code == 201  # OR IGNORE
    assert c.post(f"/groups/{gid}/members", json={"user_id": 999}).status_code == 404
    assert c.post("/groups/999/members", json={"user_id": uid}).status_code == 404
    grp = c.get(f"/groups/{gid}").get_json()
    assert [m["user_id"] for m in grp["members"]] == [uid]
    assert grp["members"][0]["balance"] == "0.00"
    assert c.get("/groups/999").status_code == 404

    # GET /groups/1 derives balances; leaving with a nonzero balance -> 409
    g1 = c.get("/groups/1").get_json()
    bal = {m["user_id"]: m["balance"] for m in g1["members"]}
    assert bal == {1: "5.00", 2: "-5.00", 3: "0.00"}, bal
    assert c.delete("/groups/1/members/2").status_code == 409   # Bob still owes $5
    assert c.delete("/groups/1/members/3").status_code == 200   # Charlie settled -> ok
    assert c.delete("/groups/1/members/3").status_code == 404   # no longer a member
    print("members:", g1["members"])

    # PATCH expense: amount change re-splits, shares still sum to new total
    r = c.patch("/expenses/2", json={"amount": "40.00"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["amount"] == "40.00"
    assert sum(float(s["share"]) for s in r.get_json()["shares"]) == 40.00
    # explicit split + description edit
    r = c.patch("/expenses/2", json={"amount": "30.00", "split_type": "exact",
                "splits": [{"user_id": 2, "value": "10.00"}, {"user_id": 3, "value": "20.00"}]})
    assert {s["user_id"]: s["share"] for s in r.get_json()["shares"]} == {2: "10.00", 3: "20.00"}
    assert c.patch("/expenses/2", json={"description": "Cab"}).get_json()["description"] == "Cab"
    # bad split -> 400 ; missing/deleted expense -> 404
    assert c.patch("/expenses/2", json={"amount": "10.00", "split_type": "exact",
                "splits": [{"user_id": 2, "value": "3.00"}]}).status_code == 400
    assert c.patch("/expenses/999", json={"description": "x"}).status_code == 404
    print("patched:", c.get("/groups/1/expenses").get_json()[-1])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        selfcheck()
    else:
        seed()
        print("seeded. try: curl localhost:5000/groups/1/settle-up")
        app.run(port=5000)
