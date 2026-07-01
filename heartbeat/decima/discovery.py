"""Tool-DISCOVERY + a deterministic semantic/vector index over capabilities.

Policy — PLUG-IN-OR-FORGE, in strict order:
  1. Find an EXISTING capability that fits the goal (rank the manifest registry by
     semantic similarity — the `manifest.py` catalog is the searchable surface).
  2. If nothing in the registry clears the bar, optionally consult a `research` seam
     (an injected callable that returns candidate tool descriptors from the web /
     external registries / an MCP index) — plug one in rather than reinvent it.
  3. Only if BOTH miss, signal Nona to FORGE a new capability. Forging is the last
     resort, never the first move — this is what makes the built-in research/discovery
     function load-bearing.

The "vector embeddings" projection over capabilities (Method's data architecture) is
implemented here as a DETERMINISTIC LEXICAL EMBEDDING: tokenize → hash each token to a
dimension (BLAKE2b mod N) → an integer bag-of-words vector. It is NOT a real ML model —
it is a stdlib stand-in that occupies the exact seam where a real embedder wraps later
(swap `embed`/`similarity`, keep `search`/`discover`). Everything recorded is an INT:
no float ever enters a score. Same inputs → same vector → same ranking, forever.

Pure stdlib. Composes public `manifest`/`hashing`/`kernel` APIs only — no core edit.
"""
import hashlib
import math
import re

from decima import manifest as M
from decima.hashing import nfc

# Dimensionality of the hashed embedding space. A token maps to exactly one dimension;
# collisions are possible (as in any hashed feature space) but deterministic.
DIM = 1 << 20

# The similarity scale: cosine ∈ [0,1] is projected to an INT in [0, SCALE].
SCALE = 1000

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list:
    """Lowercase and split on any non-alphanumeric run — the deterministic tokenizer."""
    return _TOKEN.findall(nfc(str(text)).lower())


def _dim(token: str) -> int:
    """Map a token to a hashed dimension: BLAKE2b(token) mod DIM. Deterministic, stdlib."""
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") % DIM


def embed(text) -> dict:
    """A deterministic lexical embedding: {dimension: count}, all INTS.

    Tokenize the text (lowercase, split on non-alphanumerics), hash each token to a
    dimension, and count occurrences. Pure and deterministic — the SAME text always
    yields the SAME vector. This is the seam where a real vector model wraps in later;
    for now it stands in with zero ML deps and no floats."""
    vec: dict = {}
    for tok in _tokens(text):
        d = _dim(tok)
        vec[d] = vec.get(d, 0) + 1
    return vec


def similarity(a: dict, b: dict) -> int:
    """An INT similarity score in [0, SCALE] — SCALE×cosine, computed with integer math.

    cosine = dot(a,b) / (‖a‖·‖b‖). To keep every value an int we compute
    SCALE·dot // isqrt(‖a‖²·‖b‖²) — no float ever appears. Disjoint or empty vectors
    score 0. Identical vectors score SCALE."""
    if not a or not b:
        return 0
    # Iterate the smaller vector's shared dimensions for the dot product.
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = 0
    for d, v in small.items():
        w = large.get(d)
        if w:
            dot += v * w
    if dot == 0:
        return 0
    norm_a_sq = sum(v * v for v in a.values())
    norm_b_sq = sum(v * v for v in b.values())
    denom = math.isqrt(norm_a_sq * norm_b_sq)
    if denom == 0:
        return 0
    return (SCALE * dot) // denom


def _manifest_text(m: dict) -> str:
    """The searchable text of a manifest: name + title + description + tags."""
    return " ".join([m["name"], m.get("title", ""), m.get("description", ""),
                     " ".join(m.get("tags", []))])


def search(k, goal, *, top_k: int = 5, archetype=None, effect_class=None) -> list:
    """Rank the manifest registry against `goal` by semantic similarity.

    Embeds the goal and every candidate manifest's name+title+description+tags, scores
    each with `similarity`, applies optional exact filters, and returns the top_k as
    [{name, score:int, manifest_cell_id}]. This is the deterministic vector-index
    projection over capabilities. Ties break by name so results are stable across runs."""
    q = embed(goal)
    scored = []
    for c in M.registry(k):
        m = c.content
        if archetype and m["archetype"] != archetype:
            continue
        if effect_class and m["effect_class"] != effect_class:
            continue
        score = similarity(q, embed(_manifest_text(m)))
        scored.append({"name": m["name"], "score": int(score), "manifest_cell_id": c.id})
    # Highest score first; deterministic tiebreak on name.
    scored.sort(key=lambda r: (-r["score"], r["name"]))
    return scored[: int(top_k)]


def discover(k, goal, *, threshold: int, research=None) -> dict:
    """The PLUG-IN-OR-FORGE dispatcher. Deterministic given the same inputs.

    - Search the registry. If the best score >= `threshold` (an INT), USE it:
      {"action":"use", "name":..., "score":int, "manifest": cell_id}.
    - Else, if a `research(goal) -> list` seam is injected and yields a candidate tool
      descriptor, PLUG IT IN: {"action":"plug_in", "candidate": <descriptor>}.
    - Else FORGE as a last resort: {"action":"forge", "goal":..., "reason":...},
      signaling Nona to grow the organ.

    Find an existing tool first (registry → research seam); forge only when nothing
    matches. `threshold` is an int; scores are ints; no float is ever recorded."""
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise ValueError("threshold must be an int (no floats in a recorded score)")
    ranked = search(k, goal, top_k=1)
    best = ranked[0] if ranked else None
    if best is not None and best["score"] >= threshold:
        return {"action": "use", "name": best["name"], "score": best["score"],
                "manifest": best["manifest_cell_id"]}
    if research is not None:
        candidates = research(goal) or []
        if candidates:
            return {"action": "plug_in", "candidate": candidates[0]}
    return {"action": "forge", "goal": nfc(str(goal)),
            "reason": "no existing capability matches"}
