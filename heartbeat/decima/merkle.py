"""Merkle trie over a Weft's event ids — O(log n) divergence detection for sync.

SY2 reconciles two Wefts by exchanging their full id sets (O(n) every round). At
scale you want to find *where* two peers diverge without listing everything: two
peers compare one root hash; if it matches they are already in sync (one
comparison, zero transfer); if not, they descend only into the subtrees whose
hashes differ, reaching the divergent events in O(log n) steps. That is a
Merkle-DAG diff (specs/SYNC.md §3–4), and it is what makes gossip cheap.

Why a trie keyed by event id (not a positional Merkle tree over a sorted list):
event ids are content-addressed hashes, so they are uniformly distributed — a trie
on their hex digits is balanced, and a single inserted event changes exactly one
leaf and the hashes on its root path. A positional tree would re-align every leaf
after an insertion, defeating the localization. Each node's hash commits to its
children, so an identical subtree (same set of ids below it) has an identical hash
on both peers and is pruned wholesale during the diff.

Pure stdlib; reads only event ids (the public `sync.event_ids`). No core edit.
"""
from decima.hashing import blob_id


def _h(label: str, parts) -> str:
    """Hash a node's commitment. Domain-separated, deterministic over sorted parts."""
    return blob_id((label + "\x00" + "\x00".join(parts)).encode("utf-8"), kind="merkle")


def _build(ids: list, depth: int) -> dict:
    """A node over the ids sharing the prefix consumed so far. A node with ≤1 id (or
    at max key depth) is a leaf committing to its id set; otherwise it branches on
    the next hex digit and commits to its children's hashes."""
    if len(ids) <= 1 or depth >= len(ids[0]):
        return {"leaf": True, "ids": ids, "hash": _h("L", ids)}
    children = {}
    buckets: dict[str, list] = {}
    for i in ids:
        buckets.setdefault(i[depth], []).append(i)
    for nib in sorted(buckets):
        children[nib] = _build(buckets[nib], depth + 1)
    digest = _h("N", [f"{nib}:{children[nib]['hash']}" for nib in sorted(children)])
    return {"leaf": False, "children": children, "hash": digest}


class MerkleTrie:
    """A Merkle commitment to a set of event ids. `root_hash` summarizes the whole
    set; two equal root hashes ⇒ identical sets (collision-resistant)."""

    def __init__(self, ids):
        self.ids = sorted(set(ids))
        self.root = _build(self.ids, 0) if self.ids else {"leaf": True, "ids": [], "hash": _h("L", [])}

    @property
    def root_hash(self) -> str:
        return self.root["hash"]


def _all_ids(node) -> list:
    if node is None:
        return []
    if node.get("leaf"):
        return list(node["ids"])
    out = []
    for child in node["children"].values():
        out.extend(_all_ids(child))
    return out


def diff(a: MerkleTrie, b: MerkleTrie) -> dict:
    """Localize the divergence between two tries by descending only where node
    hashes differ. Returns {only_a, only_b, visited}: `only_a` = ids `a` has that
    `b` lacks (and vice-versa), `visited` = nodes compared — far below the total
    when the peers mostly agree, because matching subtrees are pruned at their root.
    """
    only_a, only_b, visited = set(), set(), [0]

    def walk(na, nb):
        visited[0] += 1
        if na is None:                       # whole subtree exists only on b
            only_b.update(_all_ids(nb))
            return
        if nb is None:                       # whole subtree exists only on a
            only_a.update(_all_ids(na))
            return
        if na["hash"] == nb["hash"]:         # identical subtree — prune (the point)
            return
        if na.get("leaf") or nb.get("leaf"):
            ia, ib = set(_all_ids(na)), set(_all_ids(nb))
            only_a.update(ia - ib)
            only_b.update(ib - ia)
            return
        for nib in sorted(set(na["children"]) | set(nb["children"])):
            walk(na["children"].get(nib), nb["children"].get(nib))

    walk(a.root, b.root)
    return {"only_a": only_a, "only_b": only_b, "visited": visited[0]}


def of_weft(weft) -> MerkleTrie:
    """Build the Merkle trie of a Weft's event ids (the public read path)."""
    from decima import sync
    return MerkleTrie(sync.event_ids(weft))
