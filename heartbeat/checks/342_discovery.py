"""Tool-DISCOVERY + deterministic semantic index — plug-in-or-forge.

The discovery lane makes the built-in research/discovery function load-bearing:
given a goal, RANK existing capabilities by a deterministic lexical embedding (a
stdlib stand-in for a vector model), and only forge a new organ when nothing —
neither the registry nor an injected research seam — already fits. This check
registers a handful of manifests and proves:

  - `embed` is DETERMINISTIC (same text → identical vector) and INTS-ONLY;
  - `similarity` is an INT in [0, SCALE] (identical → SCALE, disjoint → 0);
  - `search("email a customer")` ranks send_email first (semantic-ish overlap);
  - `discover` with a matching goal + low threshold → USE the right capability;
  - `discover` with an unmatchable goal + high threshold + no research → FORGE;
  - `discover` with an unmatchable goal + a research seam → PLUG_IN a candidate;
  - scores are ints and results are byte-identical across two calls (determinism).

Contract: run(k, line). Fail loud. Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import discovery as D


def run(k, line):
    line("\n== TOOL-DISCOVERY + SEMANTIC INDEX (plug-in-or-forge, deterministic) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # Register a small catalog of capabilities to rank over. ─────────────────────────
    M.register(kk, M.capability_manifest(
        "send_email", title="send an email message",
        description="send an email message to a recipient", archetype="EFFECT",
        effect_class="COMMUNICATION", tags=["email", "message", "notify"]))
    M.register(kk, M.capability_manifest(
        "geocode", title="geocode an address",
        description="convert a street address to latitude and longitude coordinates",
        archetype="COMPUTE", effect_class="READ", tags=["address", "coordinates", "maps"]))
    M.register(kk, M.capability_manifest(
        "charge_card", title="charge a credit card",
        description="charge a customer credit card for a payment", archetype="EFFECT",
        effect_class="FINANCIAL", tags=["payment", "card", "billing"]))

    # 1. embed is deterministic + ints-only. ─────────────────────────────────────────
    v1 = D.embed("email a customer")
    v2 = D.embed("email a customer")
    assert v1 == v2, "embed must be deterministic (same text → same vector)"
    assert v1 and all(isinstance(dim, int) and isinstance(cnt, int)
                      for dim, cnt in v1.items()), "embedding must be ints-only, non-empty"
    # Token counts fold: repeated tokens increment the same dimension.
    assert D.embed("email email")[D._dim("email")] == 2, "counts must accumulate as ints"
    line("  embed deterministic + ints-only (hashed bag-of-words; counts fold) ✓")

    # 2. similarity is an INT in [0, SCALE]; identical → SCALE, disjoint → 0. ─────────
    same = D.similarity(v1, v2)
    disjoint = D.similarity(D.embed("alpha beta"), D.embed("gamma delta"))
    assert isinstance(same, int) and same == D.SCALE, f"identical vectors → SCALE, got {same}"
    assert isinstance(disjoint, int) and disjoint == 0, f"disjoint vectors → 0, got {disjoint}"
    assert D.similarity({}, v1) == 0, "empty vector → 0"
    line(f"  similarity is INT in [0,{D.SCALE}] (identical={same}, disjoint={disjoint}) ✓")

    # 3. search ranks the semantically-closest capability first. ─────────────────────
    ranked = D.search(kk, "email a customer", top_k=3)
    assert ranked[0]["name"] == "send_email", f"send_email must rank first, got {ranked}"
    assert ranked[0]["score"] > 0 and all(isinstance(r["score"], int) for r in ranked), ranked
    # Deterministic across two calls (byte-identical result lists).
    assert D.search(kk, "email a customer", top_k=3) == ranked, "search must be deterministic"
    line(f"  search('email a customer') → {[(r['name'], r['score']) for r in ranked]} "
         f"(send_email first; scores int; deterministic) ✓")

    # 4. discover — USE an existing capability (match + low threshold). ───────────────
    used = D.discover(kk, "send an email to the customer", threshold=1)
    assert used["action"] == "use" and used["name"] == "send_email", used
    assert isinstance(used["score"], int) and used["score"] >= 1, used
    assert kk.weave().get(used["manifest"]) is not None, "manifest cell id must resolve"
    line(f"  discover(match, threshold=1) → action=use name={used['name']} "
         f"score={used['score']} (existing capability wins) ✓")

    # 5. discover — FORGE (unmatchable goal, high threshold, no research seam). ───────
    unmatchable = "quantum flux capacitor teleportation vortex"
    forged = D.discover(kk, unmatchable, threshold=900)
    assert forged["action"] == "forge" and forged["goal"] == unmatchable, forged
    assert forged["reason"] == "no existing capability matches", forged
    line(f"  discover(no match, threshold=900, no research) → action=forge "
         f"(Nona grows the organ — last resort) ✓")

    # 6. discover — PLUG_IN (unmatchable goal, but a research seam yields a candidate).
    def research(goal):
        # Injected seam: stands in for a web / MCP-registry lookup.
        return [{"name": "teleport_tool", "source": "web:example", "goal": goal}]
    plugged = D.discover(kk, unmatchable, threshold=900, research=research)
    assert plugged["action"] == "plug_in", plugged
    assert plugged["candidate"]["name"] == "teleport_tool", plugged
    line(f"  discover(no match, research seam yields candidate) → action=plug_in "
         f"candidate={plugged['candidate']['name']} (research before forge) ✓")

    # 7. determinism of the dispatcher itself (same inputs → identical dict). ─────────
    assert D.discover(kk, unmatchable, threshold=900, research=research) == plugged, \
        "discover must be deterministic given the same inputs"
    assert D.discover(kk, "send an email to the customer", threshold=1) == used
    line("  discover deterministic across repeated calls (ints only; no floats) ✓")

    line("  → plug-in-or-forge: registry-first discovery over a deterministic vector "
         "index, research seam second, forge only as a last resort — no ML dep, no float.")
