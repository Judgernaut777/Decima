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

Scope: in-process, reads the source `.db` directly as the stand-in for a network
feed. A proper `Weft.ingest()` / feed API with full WEFT §2 acceptance validation
(authorization at the parent frontier, lease checks) is deferred to keep this lane
off the core `weft.py` (R1 owns it this cycle). This module edits no core source.
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
    """Union verified foreign rows into `target`. Each row is verified before insert;
    a duplicate (id already present) is skipped, a tampered/unsignable row is
    REJECTED. Returns {ingested, duplicate, rejected}. The log only ever grows."""
    keyring = keyring or target.keyring
    have = event_ids(target)
    counts = {"ingested": 0, "duplicate": 0, "rejected": 0}
    for row in rows:
        eid = row[0]
        if eid in have:
            counts["duplicate"] += 1
            continue
        if not verify_row(keyring, row):
            counts["rejected"] += 1
            continue
        target.db.execute(
            "INSERT INTO events (id, payload, author, sig) VALUES (?,?,?,?)", tuple(row))
        have.add(eid)
        counts["ingested"] += 1
    target.db.commit()
    if counts["ingested"]:
        _refresh_head(target)
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
