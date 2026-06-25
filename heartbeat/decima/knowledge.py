"""Knowledge — read-only graph queries over the folded Weave (KNOW1).

Memory's `recall` answers "which claims mention this text". This module answers
the *shape* questions: who is one edge away, is there a path between two cells,
what is reachable within N hops, and which cells are most related. Edges are
first-class folded relations (`Cell.edges_out` / `edges_in`, WEFT §4 EDGE), so a
knowledge query is a pure traversal of that already-materialized graph — it
asserts nothing and reads no untrusted content as instruction (the trust
boundary: a cell's *content* never steers the walk; only its typed edges do).

Every result is DETERMINISTIC: neighbors, frontiers, and rankings are produced
in a stable (sorted) order, and `path` returns a shortest edge path chosen by a
fixed tiebreak — so two folds of the same Weft give identical answers (FOLD
§11). Ints, never floats (the `related` score is an integer overlap count).

All functions take `k` (a live Kernel) and fold the Weave once per call via
`k.weave()`; cell arguments are accepted as a Cell, its id, or an unambiguous
id-prefix (resolved through `weave.get`, the same affordance the rest of the
Heartbeat uses).
"""
from __future__ import annotations

from collections import deque


def _weave(k):
    """Fold the read-only Weave. `k` may already be a Weave (so the helpers can be
    driven directly in a check) or a Kernel exposing `.weave()`."""
    return k if hasattr(k, "cells") else k.weave()


def _cid(weave, cell) -> str | None:
    """Resolve a cell argument (Cell, id, or unambiguous id-prefix) to a cell id
    that actually exists in the fold, else None."""
    if cell is None:
        return None
    cid = getattr(cell, "id", cell)
    if cid in weave.cells:
        return cid
    resolved = weave.get(cid)
    return resolved.id if resolved is not None else None


def _adjacent(weave, cid: str, *, rel: str | None):
    """The (rel, neighbor_id, direction) edges incident to `cid`, optionally
    filtered by relation. Direction is 'out' for edges_out, 'in' for edges_in.
    Self-loops surface once per stored edge. Deterministically ordered."""
    cell = weave.cells.get(cid)
    if cell is None:
        return []
    out = []
    for e in cell.edges_out:
        if rel is None or e["rel"] == rel:
            out.append((e["rel"], e["dst"], "out"))
    for e in cell.edges_in:
        if rel is None or e["rel"] == rel:
            out.append((e["rel"], e["src"], "in"))
    out.sort(key=lambda t: (t[1], t[0], t[2]))
    return out


def neighbors(k, cell, *, rel: str | None = None) -> list:
    """Cells exactly one edge away from `cell` (both directions), optionally
    restricted to relation `rel`. Returns the neighbor Cells, de-duplicated and
    sorted by id, excluding the cell itself. A cell whose only link is a missing
    endpoint is skipped. Read-only."""
    weave = _weave(k)
    cid = _cid(weave, cell)
    if cid is None:
        return []
    seen, result = set(), []
    for _, nid, _ in _adjacent(weave, cid, rel=rel):
        if nid == cid or nid in seen:
            continue
        seen.add(nid)
        nbr = weave.cells.get(nid)
        if nbr is not None:
            result.append(nbr)
    result.sort(key=lambda c: c.id)
    return result


def path(k, src, dst, *, max_depth: int) -> list | None:
    """A shortest EDGE path from `src` to `dst` traversing edges in either
    direction, or None if `dst` is unreachable within `max_depth` hops. Returns
    the path as a list of hop dicts {rel, src, dst, dir} — the actual edges
    walked — so the result is self-describing. BFS guarantees fewest hops;
    neighbors are explored in deterministic (neighbor-id, rel, dir) order, so
    among equally-short paths the chosen one is stable across folds.

    `max_depth` bounds the number of hops (edges); a src==dst query returns the
    empty path []. Pure traversal — asserts nothing."""
    weave = _weave(k)
    s = _cid(weave, src)
    d = _cid(weave, dst)
    if s is None or d is None or max_depth < 0:
        return None
    if s == d:
        return []
    # BFS over (rel, neighbor, dir) frontier; parent map reconstructs the path.
    parent: dict[str, tuple] = {s: None}
    frontier = deque([(s, 0)])
    while frontier:
        cur, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for rel, nid, direction in _adjacent(weave, cur, rel=None):
            if nid in parent:
                continue
            parent[nid] = (cur, rel, direction)
            if nid == d:
                return _reconstruct(parent, d)
            frontier.append((nid, depth + 1))
    return None


def _reconstruct(parent: dict, dst: str) -> list:
    """Walk the BFS parent map back to the source, emitting hop dicts in path
    (source→dst) order."""
    hops = []
    node = dst
    while parent.get(node) is not None:
        prev, rel, direction = parent[node]
        hops.append({"rel": rel, "src": prev, "dst": node, "dir": direction})
        node = prev
    hops.reverse()
    return hops


def subgraph(k, cell, *, depth: int) -> dict:
    """The cells and edges reachable from `cell` within `depth` hops (edges
    traversed in either direction). Returns {"cells": [ids], "edges": [hop dicts],
    "depths": {id: hop-distance}} — all deterministically ordered. The seed cell
    is at depth 0 and always included (when it exists). Read-only frontier walk."""
    weave = _weave(k)
    cid = _cid(weave, cell)
    if cid is None:
        return {"cells": [], "edges": [], "depths": {}}
    depths = {cid: 0}
    edges = {}            # (rel, a, b) canonicalized → hop dict, de-duped
    frontier = deque([(cid, 0)])
    while frontier:
        cur, d = frontier.popleft()
        if d >= depth:
            continue
        for rel, nid, direction in _adjacent(weave, cur, rel=None):
            # Canonical undirected edge key so the same stored edge is recorded once.
            a, b = (cur, nid) if direction == "out" else (nid, cur)
            key = (rel, a, b)
            if key not in edges:
                edges[key] = {"rel": rel, "src": a, "dst": b}
            if nid not in depths:
                depths[nid] = d + 1
                frontier.append((nid, d + 1))
    cells = sorted(depths)
    edge_list = [edges[key] for key in sorted(edges)]
    return {"cells": cells, "edges": edge_list, "depths": depths}


def related(k, cell, *, limit: int | None = None) -> list:
    """Cells ranked by how strongly they relate to `cell`, by shared structure:
    a direct edge, a shared neighbor (two cells pointing at / pointed to by the
    same third cell), and a shared entity (both `about` the same entity Cell). The
    score is an INTEGER sum of these overlaps — never a float. Returns
    [{"cell": Cell, "score": int, "direct": bool, "shared": int}], highest score
    first, ties broken by id for determinism. Excludes the cell itself and
    retracted cells. Read-only.

    'Shared neighbor' is the graph generalization of memory's 'shared entity':
    cells co-cited by a common hub are topically related even with no direct edge.
    """
    weave = _weave(k)
    cid = _cid(weave, cell)
    if cid is None:
        return []

    direct = {nid for _, nid, _ in _adjacent(weave, cid, rel=None) if nid != cid}
    # Hubs this cell touches → the OTHER cells that also touch them are co-related.
    shared_counts: dict[str, int] = {}
    for _, hub, _ in _adjacent(weave, cid, rel=None):
        if hub == cid:
            continue
        for _, other, _ in _adjacent(weave, hub, rel=None):
            if other == cid or other == hub:
                continue
            shared_counts[other] = shared_counts.get(other, 0) + 1

    candidates = set(direct) | set(shared_counts)
    scored = []
    for nid in candidates:
        nbr = weave.cells.get(nid)
        if nbr is None or nbr.retracted:
            continue
        is_direct = nid in direct
        shared = shared_counts.get(nid, 0)
        score = (1 if is_direct else 0) + shared      # integer overlap
        if score <= 0:
            continue
        scored.append({"cell": nbr, "score": int(score),
                       "direct": is_direct, "shared": int(shared)})
    scored.sort(key=lambda r: (-r["score"], r["cell"].id))
    return scored if limit is None else scored[:limit]
