"""Gossip / anti-entropy — N-peer convergence over the Merkle diff (GX1).

SY2 reconciles two Wefts; this generalizes to **N** peers via epidemic anti-entropy:
peers reconcile pairwise in rounds, and because each reconcile is a verified DAG
union (SY2) localized by a Merkle diff (merkle.py), the whole population converges
to one shared `state_root` while moving only the events each pair is missing — never
the whole log. A revoked grant, being just a RETRACT event in the union, propagates
like any other and stays revoked everywhere (SYNC §4).

Offline / in-process: peers are `Weft` objects sharing the kernel keyring (the HMAC
profile), so each verifies the others' signatures. Deterministic schedule (a ring),
so convergence is reproducible — no randomness. Builds on the public `sync` + `merkle`
API; no core edit.
"""
from decima import sync, merkle


def _rows_by_id(weft, ids):
    """Fetch the raw wire records for specific event ids, in (lamport, id) order so
    a parent is offered before its child (tidy; the fold re-sorts regardless)."""
    if not ids:
        return []
    rows = [r for r in sync._rows(weft) if r[0] in ids]
    import json
    rows.sort(key=lambda r: (json.loads(r[1])["lamport"], r[0]))
    return rows


def reconcile(a, b, *, keyring=None) -> dict:
    """One anti-entropy exchange between two peers, Merkle-localized. If their root
    hashes match, nothing moves (one comparison). Otherwise the Merkle diff names the
    divergent events and ONLY those are transferred, verified, and unioned both ways."""
    ta, tb = merkle.of_weft(a), merkle.of_weft(b)
    if ta.root_hash == tb.root_hash:
        return {"converged": True, "moved": 0, "visited": 1,
                "a_pulled": 0, "b_pulled": 0}
    d = merkle.diff(ta, tb)
    ra = sync.ingest(a, _rows_by_id(b, d["only_b"]), keyring=keyring)  # a learns b's events
    rb = sync.ingest(b, _rows_by_id(a, d["only_a"]), keyring=keyring)  # b learns a's events
    return {"converged": False, "visited": d["visited"],
            "moved": len(d["only_a"]) + len(d["only_b"]),
            "a_pulled": ra["ingested"], "b_pulled": rb["ingested"]}


def converged(peers) -> bool:
    """All peers hold the same event set (equal Merkle roots ⇒ identical sets)."""
    return len({merkle.of_weft(p).root_hash for p in peers}) <= 1


def gossip(peers, *, keyring=None, max_rounds=64) -> dict:
    """Run ring anti-entropy until the population converges (or max_rounds). Each
    round, every peer i reconciles with peer (i+1) mod N; a ring propagates any
    event all the way around in at most N-1 rounds. Returns a convergence report."""
    n = len(peers)
    rounds, moved_total, visited_total, history = 0, 0, 0, []
    while not converged(peers) and rounds < max_rounds:
        rounds += 1
        moved = visited = 0
        for i in range(n):
            r = reconcile(peers[i], peers[(i + 1) % n], keyring=keyring)
            moved += r["moved"]
            visited += r["visited"]
        moved_total += moved
        visited_total += visited
        history.append({"round": rounds, "moved": moved})
    root = merkle.of_weft(peers[0]).root_hash if peers else None
    return {"peers": n, "rounds": rounds, "converged": converged(peers),
            "moved_total": moved_total, "visited_total": visited_total,
            "merkle_root": root, "history": history}
