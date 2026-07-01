"""DISCOVERY wired into the live loop — an unmatched goal finds a tool in the catalog.

The modularity substrate (manifest/registry/discovery/builtins) was inert until the
agent consulted it. This wires DISCOVERY into `kernel.say`: when a turn resolves to
nothing the single-action decide can handle (and no multi-step plan fires), Decima
searches the capability catalog and — if a registered manifest CONFIDENTLY fits the
goal (score ≥ DISCOVERY_THRESHOLD) — surfaces it and records a `discovery` suggestion
Cell, instead of shrugging "no capability matched." "Find a tool that fits," made live.

This check proves:
  - with the bundled catalog registered, an unmatched goal ("charge a customer's credit
    card") makes `say` SURFACE the right capability (stripe_rail) and record a
    `discovery` Cell citing goal/found/score — the found capability is NOT auto-granted
    (no new capability cell; activation would still go through authorize/Morta);
  - discovery routes to the right builtin for several goals;
  - a chitchat/no-match turn falls through to the ordinary reply (no discovery Cell) —
    additive, not a hijack;
  - with an EMPTY catalog, `say` behaves exactly as before (no discovery Cell) — the
    live wire is inert when there's nothing to discover.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import builtin_manifests, discovery


def _say(kk, text):
    return "\n".join(kk.say(text))


def run(k, line):
    line("\n== DISCOVERY LIVE — an unmatched goal finds a tool in the catalog ==")

    # 1. Empty catalog → the live wire is inert (behaves exactly as before). ────────────
    empty = Kernel(os.path.join(tempfile.mkdtemp(), "e.db"), fresh=True)
    before = len(empty.weave().of_type("discovery"))
    out0 = _say(empty, "charge a customer's credit card")
    assert len(empty.weave().of_type("discovery")) == before, "empty catalog must record no discovery"
    assert "catalog has" not in out0, out0
    line("  empty catalog → no discovery surfaced; `say` unchanged (inert wire) ✓")

    # 2. Bundled catalog registered → an unmatched goal SURFACES the right tool. ─────────
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "c.db"), fresh=True)
    builtin_manifests.register_builtins(kk)
    out = _say(kk, "charge a customer's credit card")
    assert "catalog has" in out and "stripe_rail" in out, out
    discs = [c for c in kk.weave().of_type("discovery")]
    assert len(discs) == 1, discs
    dc = discs[0].content
    assert dc["found"] == "stripe_rail" and dc["action"] == "use" and isinstance(dc["score"], int), dc
    assert dc["goal"] == "charge a customer's credit card", dc
    # The found capability was NOT granted — a suggestion is data, not authority.
    assert not any(c.content.get("name") == "stripe_rail" for c in kk.weave().of_type("capability")), \
        "discovery must not auto-grant the found capability"
    line("  unmatched goal → surfaces stripe_rail + records a `discovery` Cell "
         "(goal/found/score); grants nothing ✓")

    # 3. Routes to the right builtin for several goals. ─────────────────────────────────
    for goal, expect in [("send a text message to a customer", "comms"),
                         ("get the weather forecast", "weather_engine"),
                         ("verify someone's identity documents", "kyc")]:
        d = discovery.discover(kk, goal, threshold=kk.DISCOVERY_THRESHOLD)
        assert d["action"] == "use" and d["name"] == expect, (goal, d)
    line("  discovery routes goals → comms / weather_engine / kyc (right catalog tool) ✓")

    # 4. Chitchat / no match → falls through to the ordinary reply (no hijack). ─────────
    n_before = len(kk.weave().of_type("discovery"))
    chit = _say(kk, "salutations everyone")
    assert "catalog has" not in chit and "heard" in chit.lower(), chit
    assert len(kk.weave().of_type("discovery")) == n_before, "chitchat must record no discovery"
    line("  chitchat below threshold → ordinary reply, no discovery Cell (additive) ✓")

    line("  → discovery is live: an unmatched goal now finds the fitting capability in "
         "the catalog and surfaces it (data, not authority); empty/irrelevant turns are "
         "untouched. The plug-in layer reaches the running agent.")
