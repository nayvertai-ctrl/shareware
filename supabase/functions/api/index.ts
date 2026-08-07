// shareware Edge Function: the handful of actions that need business logic
// RLS can't express (split math, settle-up, invite-by-token, balance-gated
// member removal). Everything else is a direct PostgREST call from the
// client under RLS -- see supabase/schema.sql for what's covered where.
//
// Single POST endpoint, JSON body {action, ...params}. verify_jwt is on
// (see config.toml), so the gateway already rejects unauthenticated
// requests before this code runs.
import { createClient, SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

// --- money -------------------------------------------------------------
// String-based parsing (never float multiply dollars*100) so 12.05 can't
// drift the way `12.05 * 100` does in float64.
export function toCents(amount: unknown): number {
  const s = String(amount).trim();
  const m = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(s);
  if (!m) throw new HttpError(400, "amount must be a number");
  const [, sign, whole, frac = ""] = m;
  const cents = parseInt(whole + frac.padEnd(2, "0"), 10);
  return sign ? -cents : cents;
}

// --- split math (port of split_shares in app.py) ------------------------
export function splitShares(
  amountCents: number,
  splitType: string,
  spec: Record<string, unknown> | string[],
): Record<string, number> {
  if (splitType === "exact") {
    const entries = Object.entries(spec as Record<string, unknown>);
    const shares: Record<string, number> = {};
    for (const [u, v] of entries) shares[u] = toCents(v);
    const total = Object.values(shares).reduce((a, b) => a + b, 0);
    if (total !== amountCents) throw new HttpError(400, "exact shares must sum to the amount");
    return shares;
  }

  let weights: Record<string, number> = {};
  if (splitType === "equal") {
    for (const u of spec as string[]) weights[u] = 1;
  } else if (splitType === "percent") {
    for (const [u, v] of Object.entries(spec as Record<string, unknown>)) weights[u] = Number(v);
    const total = Object.values(weights).reduce((a, b) => a + b, 0);
    if (Math.abs(total - 100) > 1e-9) throw new HttpError(400, "percentages must sum to 100");
  } else if (splitType === "shares") {
    for (const [u, v] of Object.entries(spec as Record<string, unknown>)) weights[u] = Number(v);
  } else {
    throw new HttpError(400, "unknown split_type");
  }
  const entries = Object.entries(weights);
  if (entries.length === 0 || entries.some(([, w]) => !(w > 0))) {
    throw new HttpError(400, "split needs positive weights");
  }

  const total = entries.reduce((a, [, w]) => a + w, 0);
  const floors: Record<string, number> = {};
  const remainders: [string, number][] = [];
  let floorSum = 0;
  for (const [u, w] of entries) {
    const raw = (amountCents * w) / total;
    const f = Math.floor(raw);
    floors[u] = f;
    floorSum += f;
    remainders.push([u, raw - f]);
  }
  const leftover = amountCents - floorSum;
  remainders.sort((a, b) => b[1] - a[1]);
  for (let i = 0; i < leftover; i++) floors[remainders[i][0]] += 1;
  return floors;
}

// --- balances (port of pairwise_debts / net_balances / settle_up) -------
type Pairwise = Map<string, number>; // "debtor|creditor" -> cents

async function pairwiseDebts(svc: SupabaseClient, groupId: number): Promise<Pairwise> {
  const raw = new Map<string, number>();
  const { data: expenses } = await svc
    .from("expenses").select("id, paid_by").eq("group_id", groupId).is("deleted_at", null);
  for (const e of expenses ?? []) {
    const { data: shares } = await svc
      .from("expense_shares").select("user_id, share_cents").eq("expense_id", e.id);
    for (const s of shares ?? []) {
      if (s.user_id !== e.paid_by) {
        const key = `${s.user_id}|${e.paid_by}`;
        raw.set(key, (raw.get(key) ?? 0) + s.share_cents);
      }
    }
  }
  const { data: settlements } = await svc
    .from("settlements").select("from_user, to_user, amount_cents").eq("group_id", groupId);
  for (const s of settlements ?? []) {
    const key = `${s.from_user}|${s.to_user}`;
    raw.set(key, (raw.get(key) ?? 0) - s.amount_cents);
  }

  const net: Pairwise = new Map();
  const seen = new Set<string>();
  for (const key of raw.keys()) {
    if (seen.has(key)) continue;
    const [a, b] = key.split("|");
    seen.add(`${a}|${b}`); seen.add(`${b}|${a}`);
    const d = (raw.get(`${a}|${b}`) ?? 0) - (raw.get(`${b}|${a}`) ?? 0);
    if (d > 0) net.set(`${a}|${b}`, d);
    else if (d < 0) net.set(`${b}|${a}`, -d);
  }
  return net;
}

export function netBalances(pairwise: Pairwise): Map<string, number> {
  const bal = new Map<string, number>();
  for (const [key, cents] of pairwise) {
    const [debtor, creditor] = key.split("|");
    bal.set(debtor, (bal.get(debtor) ?? 0) - cents);
    bal.set(creditor, (bal.get(creditor) ?? 0) + cents);
  }
  return bal;
}

// Greedy min-cash-flow, same sort order as app.py's settle_up: smallest
// debtor first, biggest creditor first. Not literally "biggest-to-biggest"
// despite the docstring there -- this is the exact behavior selfcheck()
// already asserts against, so it's ported bug-for-bug.
export function settleUp(net: Map<string, number>): [string, string, number][] {
  const debtors = [...net.entries()].filter(([, b]) => b < 0)
    .map(([u, b]) => [u, -b] as [string, number]).sort((a, b) => a[1] - b[1]);
  const creditors = [...net.entries()].filter(([, b]) => b > 0)
    .map(([u, b]) => [u, b] as [string, number]).sort((a, b) => b[1] - a[1]);
  const plan: [string, string, number][] = [];
  let i = 0, j = 0;
  while (i < debtors.length && j < creditors.length) {
    const pay = Math.min(debtors[i][1], creditors[j][1]);
    plan.push([debtors[i][0], creditors[j][0], pay]);
    debtors[i][1] -= pay; creditors[j][1] -= pay;
    if (debtors[i][1] === 0) i++;
    if (creditors[j][1] === 0) j++;
  }
  return plan;
}

// Spend, for budget meters. Deliberately NOT balance: the group's spend is
// every expense in it, and a member's spend is the sum of their own shares --
// what they consumed, regardless of who fronted the cash. Settlements are
// irrelevant here; paying someone back doesn't un-spend the money.
async function groupSpend(svc: SupabaseClient, groupId: number) {
  const { data: expenses } = await svc
    .from("expenses").select("id, amount_cents").eq("group_id", groupId).is("deleted_at", null);
  const ids = (expenses ?? []).map((e) => e.id);
  const total = (expenses ?? []).reduce((a, e) => a + e.amount_cents, 0);

  const byUser = new Map<string, number>();
  if (ids.length) {
    const { data: shares } = await svc
      .from("expense_shares").select("user_id, share_cents").in("expense_id", ids);
    for (const s of shares ?? []) {
      byUser.set(s.user_id, (byUser.get(s.user_id) ?? 0) + s.share_cents);
    }
  }
  return { total, byUser };
}

async function settlePlan(svc: SupabaseClient, groupId: number) {
  const { data: grp } = await svc.from("groups").select("simplify_debts").eq("id", groupId).maybeSingle();
  if (!grp) return null;
  const pw = await pairwiseDebts(svc, groupId);
  const edges: [string, string, number][] = grp.simplify_debts
    ? settleUp(netBalances(pw))
    : [...pw.entries()].map(([key, c]) => {
      const [a, b] = key.split("|");
      return [a, b, c];
    });
  return edges.filter(([, , c]) => c > 0)
    .map(([f, t, c]) => ({ from_user: f, to_user: t, amount: (c / 100).toFixed(2) }));
}

// --- helpers shared by actions -------------------------------------------
async function requireMember(svc: SupabaseClient, groupId: number, userId: string) {
  const { data } = await svc.from("memberships").select("user_id")
    .eq("group_id", groupId).eq("user_id", userId).maybeSingle();
  if (!data) throw new HttpError(403, "not a member of this group");
}

async function requireKnownUsers(svc: SupabaseClient, userIds: string[]) {
  const { data } = await svc.from("profiles").select("id").in("id", userIds);
  const known = new Set((data ?? []).map((r) => r.id));
  if (userIds.some((u) => !known.has(u))) throw new HttpError(400, "unknown user");
}

function money(cents: number) {
  return (cents / 100).toFixed(2);
}

function sharesSpec(body: Record<string, unknown>): [string, Record<string, unknown> | string[]] {
  const splitType = (body.split_type as string) ?? "equal";
  if (splitType === "equal") {
    const participants = body.participants;
    if (!Array.isArray(participants) || participants.length === 0) {
      throw new HttpError(400, "participants required for equal split");
    }
    return [splitType, participants as string[]];
  }
  const splits = body.splits;
  if (!Array.isArray(splits) || splits.length === 0) throw new HttpError(400, "splits required");
  const spec: Record<string, unknown> = {};
  for (const s of splits as { user_id: string; value: unknown }[]) spec[s.user_id] = s.value;
  return [splitType, spec];
}

// --- actions ---------------------------------------------------------------

async function previewInvite(svc: SupabaseClient, callerId: string, token: string) {
  const { data: row } = await svc
    .from("invites").select("group_id, groups(name)").eq("token", token).maybeSingle();
  if (!row) throw new HttpError(404, "invalid invite");
  const groupId = row.group_id;
  const { data: member } = await svc.from("memberships").select("user_id")
    .eq("group_id", groupId).eq("user_id", callerId).maybeSingle();
  const { count } = await svc.from("memberships").select("*", { count: "exact", head: true })
    .eq("group_id", groupId);
  return {
    group_id: groupId,
    name: (row.groups as unknown as { name: string }).name,
    members: count ?? 0,
    already_member: !!member,
  };
}

async function acceptInvite(svc: SupabaseClient, callerId: string, token: string) {
  const { data: row } = await svc.from("invites").select("group_id").eq("token", token).maybeSingle();
  if (!row) throw new HttpError(404, "invalid invite");
  const groupId = row.group_id;
  await svc.from("memberships").upsert({ group_id: groupId, user_id: callerId }, { onConflict: "group_id,user_id" });
  const { data: grp } = await svc.from("groups").select("name").eq("id", groupId).single();
  return { id: groupId, name: grp!.name, joined: true };
}

async function removeMember(svc: SupabaseClient, callerId: string, groupId: number, userId: string) {
  await requireMember(svc, groupId, callerId);
  const { data: target } = await svc.from("memberships").select("user_id")
    .eq("group_id", groupId).eq("user_id", userId).maybeSingle();
  if (!target) throw new HttpError(404, "member not found");
  const net = netBalances(await pairwiseDebts(svc, groupId)).get(userId) ?? 0;
  if (net !== 0) throw new HttpError(409, "member has a nonzero balance");
  await svc.from("memberships").delete().eq("group_id", groupId).eq("user_id", userId);
  const accountDeleted = await maybeDeleteOrphanShadow(svc, userId);
  return { group_id: groupId, user_id: userId, removed: true, account_deleted: accountDeleted };
}

// A shadow member exists only as a label inside groups -- nobody can log in as
// them. Once they belong to no group and have no ledger rows anywhere, the
// account is unreachable litter that would still show up in every "add an
// existing person" list, which is how duplicate entries pile up. Drop it.
//
// Guarded on having zero ledger references: expenses.paid_by,
// expense_shares.user_id and settlements.from_user/to_user all reference
// auth.users WITHOUT on delete cascade, so deleting a user who appears in the
// ledger would fail anyway -- and must, since that history has to stay intact.
async function maybeDeleteOrphanShadow(svc: SupabaseClient, userId: string): Promise<boolean> {
  const { data: profile } = await svc.from("profiles").select("is_shadow").eq("id", userId).maybeSingle();
  if (!profile?.is_shadow) return false;

  const head = { count: "exact" as const, head: true };
  const refs = await Promise.all([
    svc.from("memberships").select("*", head).eq("user_id", userId),
    svc.from("expenses").select("*", head).eq("paid_by", userId),
    svc.from("expense_shares").select("*", head).eq("user_id", userId),
    svc.from("settlements").select("*", head).or(`from_user.eq.${userId},to_user.eq.${userId}`),
    svc.from("groups").select("*", head).eq("created_by", userId),
  ]);
  if (refs.some((r) => (r.count ?? 0) > 0)) return false;

  const { error } = await svc.auth.admin.deleteUser(userId);  // cascades the profile
  return !error;
}

async function groupDetail(svc: SupabaseClient, callerId: string, groupId: number) {
  const { data: grp } = await svc.from("groups")
    .select("id, name, simplify_debts, currency, budget_cents")
    .eq("id", groupId).maybeSingle();
  if (!grp) throw new HttpError(404, "group not found");
  await requireMember(svc, groupId, callerId);
  const net = netBalances(await pairwiseDebts(svc, groupId));
  const spend = await groupSpend(svc, groupId);
  // memberships.user_id and profiles.id both reference auth.users independently
  // -- no direct FK between the two tables, so PostgREST can't auto-embed one
  // under the other. Two plain queries, joined in TS, instead.
  const { data: memberRows } = await svc.from("memberships").select("user_id, budget_cents")
    .eq("group_id", groupId).order("user_id");
  const userIds = (memberRows ?? []).map((m) => m.user_id);
  const { data: profileRows } = userIds.length
    ? await svc.from("profiles").select("id, name, upi_id, paypal_me, venmo, is_shadow, avatar_emoji").in("id", userIds)
    : { data: [] };
  const profileById = new Map((profileRows ?? []).map((p) => [p.id, p]));

  const budgetByUser = new Map(
    (memberRows ?? []).map((m) => [m.user_id, m.budget_cents as number | null]),
  );

  return {
    id: grp.id, name: grp.name, simplify_debts: grp.simplify_debts, currency: grp.currency,
    budget: grp.budget_cents == null ? null : money(grp.budget_cents),
    spend: money(spend.total),
    members: userIds.map((uid) => {
      const p = profileById.get(uid)!;
      const b = budgetByUser.get(uid);
      return {
        user_id: uid, name: p.name, balance: money(net.get(uid) ?? 0),
        is_shadow: p.is_shadow, avatar_emoji: p.avatar_emoji,
        budget: b == null ? null : money(b),
        spend: money(spend.byUser.get(uid) ?? 0),
        pay: { upi: p.upi_id, paypal: p.paypal_me, venmo: p.venmo },
      };
    }),
  };
}

// Edits a shadow member (someone added by name who has no account): their
// display name and/or emoji avatar. Deliberately refuses real users -- their
// profile is their own, editable only by them via "profiles update own".
async function updateMember(
  svc: SupabaseClient, callerId: string, groupId: number, userId: string,
  name: unknown, avatarEmoji: unknown,
) {
  const patch: { name?: string; avatar_emoji?: string | null } = {};
  if (name !== undefined) {
    const cleanName = String(name).trim();
    if (!cleanName) throw new HttpError(400, "name required");
    patch.name = cleanName;
  }
  if (avatarEmoji !== undefined) {
    const e = String(avatarEmoji ?? "").trim();
    if ([...e].length > 12) throw new HttpError(400, "avatar must be a single emoji");
    patch.avatar_emoji = e || null;          // blank clears it
  }
  if (!Object.keys(patch).length) throw new HttpError(400, "nothing to update");
  await requireMember(svc, groupId, callerId);
  const { data: target } = await svc.from("memberships").select("user_id")
    .eq("group_id", groupId).eq("user_id", userId).maybeSingle();
  if (!target) throw new HttpError(404, "member not found");

  const { data: profile } = await svc.from("profiles").select("is_shadow").eq("id", userId).maybeSingle();
  if (!profile) throw new HttpError(404, "member not found");
  if (!profile.is_shadow) {
    throw new HttpError(403, "only members without an account can be edited");
  }

  const { data: updated, error } = await svc.from("profiles").update(patch)
    .eq("id", userId).select("name, avatar_emoji").single();
  if (error) throw new HttpError(400, error.message);
  return { user_id: userId, name: updated.name, avatar_emoji: updated.avatar_emoji };
}

// Adds a person with no account of their own -- a real (but login-less,
// random-credential) auth user + profile via the admin API, so they can
// still be picked as a payer/participant like any other member.
async function createShadowMember(svc: SupabaseClient, callerId: string, groupId: number, name: unknown) {
  const cleanName = String(name ?? "").trim();
  if (!cleanName) throw new HttpError(400, "name required");
  const { data: grp } = await svc.from("groups").select("id").eq("id", groupId).maybeSingle();
  if (!grp) throw new HttpError(404, "group not found");
  await requireMember(svc, groupId, callerId);

  const { data: created, error } = await svc.auth.admin.createUser({
    email: `shadow-${crypto.randomUUID()}@shareware.invalid`,
    password: crypto.randomUUID() + crypto.randomUUID(),
    email_confirm: true,
    user_metadata: { name: cleanName },
  });
  if (error || !created?.user) throw new HttpError(500, error?.message ?? "could not create member");

  // Mark the profile the signup trigger just made, so the app knows this
  // member has no account and may be renamed by any member of the group.
  await svc.from("profiles").update({ is_shadow: true }).eq("id", created.user.id);
  await svc.from("memberships").insert({ group_id: groupId, user_id: created.user.id });
  return { user_id: created.user.id, name: cleanName };
}

async function createExpense(svc: SupabaseClient, callerId: string, body: Record<string, unknown>) {
  const groupId = body.group_id as number;
  const paidBy = body.paid_by as string | undefined;
  if (groupId == null || paidBy == null || body.amount == null) {
    throw new HttpError(400, "group_id, paid_by and amount required");
  }
  const amountCents = toCents(body.amount);
  if (amountCents <= 0) throw new HttpError(400, "amount must be positive");
  const [splitType, spec] = sharesSpec(body);
  const shares = splitShares(amountCents, splitType, spec);

  const { data: grp } = await svc.from("groups").select("id").eq("id", groupId).maybeSingle();
  if (!grp) throw new HttpError(404, "group not found");
  await requireMember(svc, groupId, callerId);
  await requireKnownUsers(svc, [paidBy, ...Object.keys(shares)]);

  const { data: expense, error } = await svc.from("expenses").insert({
    group_id: groupId, paid_by: paidBy, amount_cents: amountCents,
    description: body.description ?? null,
  }).select("id").single();
  if (error) throw new HttpError(500, error.message);

  await svc.from("expense_shares").insert(
    Object.entries(shares).map(([user_id, share_cents]) => ({ expense_id: expense.id, user_id, share_cents })),
  );

  return {
    id: expense.id, paid_by: paidBy, amount: money(amountCents),
    description: body.description ?? null,
    shares: Object.entries(shares).map(([user_id, c]) => ({ user_id, share: money(c) })),
  };
}

async function updateExpense(svc: SupabaseClient, callerId: string, body: Record<string, unknown>) {
  const expenseId = body.expense_id as number;
  if (expenseId == null) throw new HttpError(400, "expense_id required");
  const { data: row } = await svc.from("expenses")
    .select("id, group_id, paid_by, amount_cents, description")
    .eq("id", expenseId).is("deleted_at", null).maybeSingle();
  if (!row) throw new HttpError(404, "expense not found");
  await requireMember(svc, row.group_id, callerId);

  let amountCents = row.amount_cents;
  if (body.amount != null) {
    amountCents = toCents(body.amount);
    if (amountCents <= 0) throw new HttpError(400, "amount must be positive");
  }

  let shares: Record<string, number> | null = null;
  if (body.split_type != null) {
    const [splitType, spec] = sharesSpec(body);
    shares = splitShares(amountCents, splitType, spec);
  } else if (body.amount != null) {
    const { data: existing } = await svc.from("expense_shares").select("user_id").eq("expense_id", expenseId);
    shares = splitShares(amountCents, "equal", (existing ?? []).map((s) => s.user_id));
  }
  if (shares) await requireKnownUsers(svc, Object.keys(shares));

  const description = "description" in body ? (body.description as string | null) : row.description;
  await svc.from("expenses").update({ amount_cents: amountCents, description }).eq("id", expenseId);

  if (shares) {
    await svc.from("expense_shares").delete().eq("expense_id", expenseId);
    await svc.from("expense_shares").insert(
      Object.entries(shares).map(([user_id, share_cents]) => ({ expense_id: expenseId, user_id, share_cents })),
    );
  }

  const { data: out } = await svc.from("expense_shares").select("user_id, share_cents").eq("expense_id", expenseId);
  return {
    id: expenseId, paid_by: row.paid_by, amount: money(amountCents), description,
    shares: (out ?? []).map((s) => ({ user_id: s.user_id, share: money(s.share_cents) })),
  };
}

async function settleUpAction(svc: SupabaseClient, callerId: string, groupId: number) {
  const { data: grp } = await svc.from("groups").select("id").eq("id", groupId).maybeSingle();
  if (!grp) throw new HttpError(404, "group not found");
  await requireMember(svc, groupId, callerId);
  return await settlePlan(svc, groupId);
}

async function userSummary(svc: SupabaseClient, callerId: string) {
  const { data: memberships } = await svc.from("memberships").select("group_id").eq("user_id", callerId);
  const groupIds = (memberships ?? []).map((m) => m.group_id);
  const { data: groups } = groupIds.length
    ? await svc.from("groups").select("id, name, currency").in("id", groupIds).order("id")
    : { data: [] };

  const perGroup: { group_id: number; name: string; currency: string; balance: string }[] = [];
  const buckets = new Map<string, [number, number]>();
  for (const grp of groups ?? []) {
    const net = netBalances(await pairwiseDebts(svc, grp.id)).get(callerId) ?? 0;
    if (net === 0) continue;
    perGroup.push({ group_id: grp.id, name: grp.name, currency: grp.currency, balance: money(net) });
    const b = buckets.get(grp.currency) ?? [0, 0];
    if (net > 0) b[0] += net; else b[1] += -net;
    buckets.set(grp.currency, b);
  }
  const byCurrency: Record<string, { owed: string; owe: string; net: string }> = {};
  for (const [cur, [o, w]] of buckets) byCurrency[cur] = { owed: money(o), owe: money(w), net: money(o - w) };
  return { user_id: callerId, by_currency: byCurrency, groups: perGroup };
}

// --- router ------------------------------------------------------------

async function handle(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "POST") return json(405, { error: "POST only" });

  try {
    const authClient = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_ANON_KEY")!,
      { global: { headers: { Authorization: req.headers.get("Authorization") ?? "" } } },
    );
    const { data: { user }, error: authErr } = await authClient.auth.getUser();
    if (authErr || !user) throw new HttpError(401, "authentication required");

    const svc = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    const body = await req.json().catch(() => ({}));
    const action = body.action as string;

    let result: unknown;
    switch (action) {
      case "preview_invite":
        result = await previewInvite(svc, user.id, body.token);
        break;
      case "accept_invite":
        result = await acceptInvite(svc, user.id, body.token);
        break;
      case "remove_member":
        result = await removeMember(svc, user.id, body.group_id, body.user_id);
        break;
      case "group_detail":
        result = await groupDetail(svc, user.id, body.group_id);
        break;
      case "create_shadow_member":
        result = await createShadowMember(svc, user.id, body.group_id, body.name);
        break;
      case "update_member":
        result = await updateMember(svc, user.id, body.group_id, body.user_id, body.name, body.avatar_emoji);
        break;
      case "create_expense":
        result = await createExpense(svc, user.id, body);
        break;
      case "update_expense":
        result = await updateExpense(svc, user.id, body);
        break;
      case "settle_up":
        result = await settleUpAction(svc, user.id, body.group_id);
        break;
      case "user_summary":
        result = await userSummary(svc, user.id);
        break;
      default:
        throw new HttpError(400, `unknown action: ${action}`);
    }
    return json(action === "create_expense" ? 201 : 200, result);
  } catch (e) {
    if (e instanceof HttpError) return json(e.status, { error: e.message });
    console.error(e);
    return json(500, { error: "internal error" });
  }
}

if (import.meta.main) Deno.serve(handle);
