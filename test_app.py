"""Standalone tests for app.py. No pytest: `python3 test_app.py`.

Isolates from any concurrent `python3 app.py test` by pointing app.DB at a
private temp file BEFORE seeding, so we never touch splitwise.db.
"""
import tempfile
import app

app.DB = tempfile.mktemp(suffix=".db")
app.seed()
c = app.app.test_client()


def test_settle_up_seed():
    # simplify ON: Charlie owes 35, Bob owes 5, both to Anna.
    plan = c.get("/groups/1/settle-up").get_json()
    assert {(p["from_user"], p["to_user"], p["amount"]) for p in plan} == {
        (3, 1, "35.00"), (2, 1, "5.00")}


def test_post_equal_split_sums_to_total():
    r = c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "9.00",
               "split_type": "equal", "participants": [1, 2, 3]})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert sum(float(s["share"]) for s in body["shares"]) == 9.00
    c.delete(f"/expenses/{body['id']}")  # keep balances clean for later tests


def test_post_cent_distribution():
    r = c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "10.00",
               "split_type": "equal", "participants": [1, 2, 3]})
    assert r.status_code == 201
    body = r.get_json()
    cents = sorted(round(float(s["share"]) * 100) for s in body["shares"])
    assert cents == [333, 333, 334], cents
    c.delete(f"/expenses/{body['id']}")


def test_exact_split_mismatch_400():
    r = c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "10.00",
               "split_type": "exact", "splits": [{"user_id": 1, "value": "3.00"}]})
    assert r.status_code == 400, r.get_json()


def test_feed_newest_first():
    r = c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "1.00",
               "split_type": "equal", "participants": [1], "description": "Newest"})
    eid = r.get_json()["id"]
    feed = c.get("/groups/1/expenses").get_json()
    assert feed[0]["id"] == eid and feed[0]["description"] == "Newest"
    assert [e["id"] for e in feed] == sorted((e["id"] for e in feed), reverse=True)
    c.delete(f"/expenses/{eid}")


def test_settlement_removes_edge():
    # Bob owes Anna $5; after he settles it, no edge from Bob remains.
    before = c.get("/groups/1/settle-up").get_json()
    assert any(p["from_user"] == 2 for p in before), before
    r = c.post("/groups/1/settlements",
               json={"from_user": 2, "to_user": 1, "amount": "5.00"})
    assert r.status_code == 201, r.get_json()
    after = c.get("/groups/1/settle-up").get_json()
    assert not any(p["from_user"] == 2 for p in after), after


def test_delete_removes_from_feed_and_balances():
    # New group-2-free expense in group 1 that shifts balances, then delete it.
    r = c.post("/groups/1/expenses", json={"paid_by": 2, "amount": "12.00",
               "split_type": "equal", "participants": [1, 2], "description": "Gone"})
    eid = r.get_json()["id"]
    assert any(p["to_user"] == 2 for p in c.get("/groups/1/settle-up").get_json())
    before = len(c.get("/groups/1/expenses").get_json())
    d = c.delete(f"/expenses/{eid}")
    assert d.status_code == 200 and d.get_json()["deleted"] is True
    assert len(c.get("/groups/1/expenses").get_json()) == before - 1
    assert not any(p["to_user"] == 2 for p in c.get("/groups/1/settle-up").get_json())


def test_unknown_group_404():
    assert c.get("/groups/999/settle-up").status_code == 404
    assert c.get("/groups/999/expenses").status_code == 404
    assert c.post("/groups/999/expenses", json={"paid_by": 1, "amount": "5.00",
                  "participants": [1]}).status_code == 404


# --- new CRUD / PATCH routes ------------------------------------------------

def test_user_and_group_creation():
    u = c.post("/users", json={"name": "Dana"})
    assert u.status_code == 201
    uid = u.get_json()["id"]
    assert any(x["id"] == uid for x in c.get("/users").get_json())
    g = c.post("/groups", json={"name": "Ski", "simplify_debts": True})
    assert g.status_code == 201
    gid = g.get_json()["id"]
    assert any(x["id"] == gid for x in c.get("/groups").get_json())
    # add the new user to the fresh group, then read detail
    assert c.post(f"/groups/{gid}/members", json={"user_id": uid}).status_code == 201
    detail = c.get(f"/groups/{gid}").get_json()
    assert {m["user_id"] for m in detail["members"]} == {uid}
    assert detail["members"][0]["balance"] == "0.00"       # no expenses yet
    assert c.get("/groups/999").status_code == 404


def test_group_detail_balances():
    # seed group 1: Anna +40, Bob -5, Charlie -35 (before any test mutations)
    bals = {m["user_id"]: m["balance"]
            for m in c.get("/groups/1").get_json()["members"]}
    assert bals == {1: "40.00", 2: "-5.00", 3: "-35.00"}, bals


def test_member_removal_blocked_when_owing():
    # Bob (2) owes money in group 1 -> 409; unknown member -> 404
    assert c.delete("/groups/1/members/2").status_code == 409
    assert c.delete("/groups/1/members/999").status_code == 404


def test_patch_amount_resplits():
    r = c.post("/groups/1/expenses", json={"paid_by": 1, "amount": "9.00",
               "split_type": "equal", "participants": [1, 2, 3], "description": "P"})
    eid = r.get_json()["id"]
    p = c.patch(f"/expenses/{eid}", json={"amount": "12.00"})
    assert p.status_code == 200
    body = p.get_json()
    assert body["amount"] == "12.00"
    assert sum(float(s["share"]) for s in body["shares"]) == 12.00   # re-split to new total
    assert c.patch(f"/expenses/{eid}", json={"split_type": "exact",
                   "splits": [{"user_id": 1, "value": "1.00"}]}).status_code == 400
    c.delete(f"/expenses/{eid}")
    assert c.patch(f"/expenses/{eid}", json={"amount": "5.00"}).status_code == 404  # deleted


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all tests passed")
