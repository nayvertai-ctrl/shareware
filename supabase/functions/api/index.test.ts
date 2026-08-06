// Runnable check for the pure money/split logic ported from app.py's
// selfcheck(). Run with: deno test supabase/functions/api/index.test.ts
import { assert, assertEquals } from "jsr:@std/assert@1";
import { netBalances, settleUp, splitShares, toCents } from "./index.ts";

Deno.test("toCents parses dollars without float drift", () => {
  assertEquals(toCents("12.05"), 1205);
  assertEquals(toCents(20), 2000);
  assertEquals(toCents("0.1"), 10);
});

Deno.test("splitShares equal: largest-remainder distributes leftover cents", () => {
  const s = splitShares(1000, "equal", ["a", "b", "c"]);
  assertEquals(s.a + s.b + s.c, 1000);
  assertEquals(new Set(Object.values(s)).size <= 2, true); // at most 1 cent apart
});

Deno.test("splitShares exact must sum to the amount", () => {
  assertEquals(splitShares(1000, "exact", { a: "6.00", b: "4.00" }), { a: 600, b: 400 });
  let threw = false;
  try {
    splitShares(1000, "exact", { a: "6.00", b: "3.00" });
  } catch {
    threw = true;
  }
  assert(threw);
});

Deno.test("splitShares percent must sum to 100", () => {
  const s = splitShares(1000, "percent", { a: 25, b: 75 });
  assertEquals(s, { a: 250, b: 750 });
});

Deno.test("seed-shaped scenario: Anna paid 60, Bob paid 30, split three ways", () => {
  // Same fixture as app.py's seed(): Anna paid $60 split equally among
  // Anna/Bob/Charlie; Bob paid $30 split equally between Bob/Charlie.
  const e1 = splitShares(6000, "equal", ["anna", "bob", "charlie"]); // 2000 each
  const e2 = splitShares(3000, "equal", ["bob", "charlie"]); // 1500 each

  // net = paid - owed, per person, across both expenses.
  const paid: Record<string, number> = { anna: 6000, bob: 3000, charlie: 0 };
  const owed: Record<string, number> = { anna: e1.anna, bob: e1.bob + e2.bob, charlie: e1.charlie + e2.charlie };
  const real = new Map<string, number>();
  for (const u of ["anna", "bob", "charlie"]) real.set(u, paid[u] - owed[u]);

  assertEquals(real.get("anna"), 4000);
  assertEquals(real.get("bob"), -500);
  assertEquals(real.get("charlie"), -3500);
  assertEquals([...real.values()].reduce((a, b) => a + b, 0), 0);

  const plan = settleUp(real);
  const got = new Map<string, number>();
  for (const [f, t, amt] of plan) {
    assert(amt > 0 && (real.get(f) ?? 0) < 0 && (real.get(t) ?? 0) > 0);
    got.set(f, (got.get(f) ?? 0) - amt);
    got.set(t, (got.get(t) ?? 0) + amt);
  }
  assertEquals(got, real);

  // smallest debtor first (bob, -500), then charlie (-3500); both to anna
  assertEquals(plan.map(([f, t, c]) => [f, t, c / 100]), [["bob", "anna", 5], ["charlie", "anna", 35]]);
});

Deno.test("netBalances round-trips a pairwise map", () => {
  const pw = new Map([["bob", 500], ["charlie", 3500]].map(([u, c]) => [`${u}|anna`, c as number]));
  const net = netBalances(pw);
  assertEquals(net.get("anna"), 4000);
  assertEquals(net.get("bob"), -500);
  assertEquals(net.get("charlie"), -3500);
});
