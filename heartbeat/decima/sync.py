"""Sync transport — reconcile two real Weft instances by DAG union (SY2, offline).

specs/SYNC.md: peers converge by **union of immutable, signed events** — no peer
overwrites another's history, conflicts surface through the merge reducers (M1/M2),
and authorization is judged at an event's causal frontier, so sync can never
re-authorize a revoked grant. SY1 simulated peers as forks inside one Weft; this
module does the real thing between **two `Weft` objects**.

The protocol, one round, offline and in-process:

  1. **difference** — find the events the target is missing (the causal difference;
     `frontier()` is the bandwidth-optimized handshake a network would use, but the
     reference computes the difference from full have-sets for exactness);
  2. **transfer** — ship those events as raw wire records `(id, payload, author, sig)`;
  3. **verify** — on ingest, recompute the content id and check the signature under
     the **shared keyring** — exactly the checks `Weft.events()` runs on read; a
     tampered or unsignable event is REJECTED, never inserted;
  4. **union** — insert the verified foreign rows (the append-only log only grows);
  5. **converge** — both Wefts now fold to one identical `state_root`.

Trust model: both peers share the keyring (the HMAC profile's symmetric stand-in
for ed25519); under real ed25519 the verifier needs only public keys. Either way a
peer accepts a foreign event **only** if it verifies — possession of the id buys
nothing, and a forged/edited event cannot enter the union.

Acceptance is now the core `Weft.ingest()` — full WEFT §2 validation (integrity +
signature + parents-present + honest lamport), so a foreign event enters the DAG only
if it proves itself, and an out-of-order feed still unions a closed DAG (orphans are
deferred + retried). `sync_over_wire` adds the network-shaped path: peers exchange
have-sets and SERIALIZED feeds (a JSON string is the wire) rather than reading each
other's `.db`. Authorization is judged per-event at ORIGIN, so the union never
re-authorizes a revoked grant — sync is pure event union over signed, §2-valid events.
"""
import json

from decima.hashing import content_id


# ── difference / frontier ────────────────────────────────────────────────────
def event_ids(weft) -> set:
    """Every event id this Weft holds (its 'have' set)."""
    return {r[0] for r in weft.db.execute("SELECT id FROM events")}


def frontier(weft) -> set:
    """The DAG heads: events that are no other event's parent. A real transport
    exchanges these and walks ancestors to discover the difference; the reference
    diffs full have-sets (below), but the frontier is the protocol-faithful handle."""
    ids, parents = set(), set()
    for eid, payload in weft.db.execute("SELECT id, payload FROM events"):
        ids.add(eid)
        parents.update(json.loads(payload).get("parents", []))
    return ids - parents


def _rows(weft):
    """Raw wire records (id, payload, author, sig) in seq order — the offline
    stand-in for a network feed of a peer's events."""
    return weft.db.execute(
        "SELECT id, payload, author, sig FROM events ORDER BY seq").fetchall()


def missing_for(source, target) -> list:
    """The rows `source` holds that `target` lacks — the causal difference —
    topologically ordered. A parent's lamport is always strictly smaller than its
    child's (WEFT §2), so `(lamport, id)` order guarantees parents insert first."""
    have = event_ids(target)
    rows = [r for r in _rows(source) if r[0] not in have]
    rows.sort(key=lambda r: (json.loads(r[1])["lamport"], r[0]))
    return rows


# ── verify + ingest (the union step) ─────────────────────────────────────────
def verify_row(keyring, row) -> bool:
    """A foreign event is acceptable iff its bytes still hash to its id (no payload
    tampering) AND its signature verifies under the shared keyring (authentic
    author). These are exactly the checks `Weft.events()` makes on every read."""
    eid, payload_text, author, sig = row
    try:
        payload = json.loads(payload_text)
    except (ValueError, TypeError):
        return False
    if content_id(payload, kind="event") != eid:
        return False
    return keyring.verify(author, eid, sig)


def ingest(target, rows, *, keyring=None) -> dict:
    """Union foreign rows into `target` through `Weft.ingest` — the core WEFT §2
    ACCEPTANCE gate (integrity + signature + parents-present + honest lamport). An
    "orphan" (a parent not yet present) is DEFERRED and retried until the batch reaches
    a fixpoint, so an OUT-OF-ORDER feed still unions a closed DAG; a row still orphaned
    when no progress remains is truly dangling and REJECTED. A tampered/forged/
    §2-violating row is rejected and never inserted. Returns {ingested, duplicate,
    rejected}. (`keyring` is accepted for call-compat; `Weft.ingest` verifies under the
    target's own keyring — the shared keyring in every caller.)"""
    counts = {"ingested": 0, "duplicate": 0, "rejected": 0}
    pending = list(rows)
    while pending:
        progressed, still = False, []
        for row in pending:
            status = target.ingest(row)
            if status == "orphan":
                still.append(row)                 # parents not here yet — retry a pass
                continue
            progressed = True
            counts["ingested" if status == "ingested"
                   else "duplicate" if status == "duplicate"
                   else "rejected"] += 1
        pending = still
        if not progressed:                        # no forward progress → dangling
            counts["rejected"] += len(pending)
            break
    return counts


def _refresh_head(weft):
    """After a union, refresh the Weft's `head`/`lamport` so a later LOCAL append
    still gets a strictly-greater lamport (causality preserved across the merge).
    `head` is the max-`(lamport, seq)` event — one deterministic frontier head; a
    real multi-parent local append would descend from the whole `frontier()`."""
    best_head, best_key, max_lamport = None, (-1, -1), 0
    for eid, payload, seq in weft.db.execute("SELECT id, payload, seq FROM events"):
        lam = json.loads(payload)["lamport"]
        max_lamport = max(max_lamport, lam)
        if (lam, seq) > best_key:
            best_key, best_head = (lam, seq), eid
    weft.head, weft.lamport = best_head, max_lamport


# ── one-shot reconcile ───────────────────────────────────────────────────────
def pull(source, target, *, keyring=None) -> dict:
    """Transfer source→target the events target is missing (one direction)."""
    return ingest(target, missing_for(source, target), keyring=keyring)


def sync(a, b, *, keyring=None) -> dict:
    """Bidirectional reconcile of two Wefts. Pulls each way, folds both, and reports
    whether they converged to one `state_root`. Order-independent: a then b or b then
    a yields the same union, hence the same fold (M1/M2 arrival-order independence)."""
    from decima.weave import Weave
    a_to_b = pull(a, b, keyring=keyring)
    b_to_a = pull(b, a, keyring=keyring)
    ra = Weave.fold(a).state_root()
    rb = Weave.fold(b).state_root()
    return {"a_to_b": a_to_b, "b_to_a": b_to_a,
            "converged": ra == rb, "state_root": ra if ra == rb else None}


# ── networked wire transport ─────────────────────────────────────────────────
# The functions above read a peer's `.db` directly. A real transport instead crosses
# a byte channel: a peer announces the ids it HAS, the other serializes the events the
# announcer lacks, and those bytes are ingested through `Weft.ingest` (full §2
# validation) on arrival. These functions model exactly that — a JSON string is the
# wire — so the union is transport-decoupled and could ride a socket unchanged.

def feed(source, have_ids) -> str:
    """`source`'s reply to a peer that already HAS `have_ids`: the events the peer
    lacks, serialized to WIRE BYTES (a JSON string), topologically ordered so parents
    precede children. This is what would cross the socket."""
    have = set(have_ids)
    rows = [list(r) for r in _rows(source) if r[0] not in have]
    rows.sort(key=lambda r: (json.loads(r[1])["lamport"], r[0]))
    return json.dumps(rows)


def apply_feed(target, wire: str, *, keyring=None) -> dict:
    """Ingest a serialized `feed` (wire bytes) into `target` through the §2 acceptance
    gate. Deserialization is part of the boundary — malformed JSON is a rejected feed."""
    try:
        rows = json.loads(wire)
    except (ValueError, TypeError):
        return {"ingested": 0, "duplicate": 0, "rejected": 0, "bad_feed": True}
    return ingest(target, [tuple(r) for r in rows], keyring=keyring)


def sync_over_wire(a, b, *, keyring=None) -> dict:
    """Bidirectional sync across the WIRE (serialized bytes), the network-shaped path:
    each peer announces its have-set, the other returns a serialized `feed`, and the
    feed is ingested through `Weft.ingest` (full §2 validation). Converges to one root —
    the same union as `sync`, but nothing reads the other peer's DB directly."""
    from decima.weave import Weave
    to_a = apply_feed(a, feed(b, event_ids(a)), keyring=keyring)   # b → wire → a
    to_b = apply_feed(b, feed(a, event_ids(b)), keyring=keyring)   # a → wire → b
    ra, rb = Weave.fold(a).state_root(), Weave.fold(b).state_root()
    return {"to_a": to_a, "to_b": to_b,
            "converged": ra == rb, "state_root": ra if ra == rb else None}
