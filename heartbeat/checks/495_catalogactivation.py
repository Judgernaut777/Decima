"""CATALOG ACTIVATION — a discovery "use" suggestion becomes an approvable INSTALL.

The 4th-quality re-audit found the discovery "use" path DECORATIVE: `discover()`
surfaced a use-suggestion and `kernel.say` told the human "approve to activate it",
but no ApprovalInbox item was ever submitted and no installer mapped the found
manifest to a real handler — approval had nothing to fire. The fix lives at the
discovery/inbox seam (`discovery.submit_activation`, called INSIDE `discover()` on
every use-suggestion, so kernel.say's existing hook inherits it with no core edit):
a Morta-gated activation ENACTOR (`catalog.activate:<name>`, requires_approval) is
enqueued as a durable `inbox_item`; a human `approve()` enacts it through the SAME
approve_invocation/authorize/Morta spine and the enactor INSTALLS the manifest as a
real gated capability via the PUBLIC `kernel.integrate_tool`.

This check proves, offline + deterministically:

  (a) SUGGESTION → APPROVAL → INSTALL (load-bearing): register_builtins; drive
      `discover()` for a bundled goal → a use-suggestion whose activation is a
      PENDING ApprovalInbox item (nothing installed yet — the capability does NOT
      exist); a direct approval-less invoke of the enactor is DENIED at the gate;
      re-discovery reuses the SAME pending item (idempotent, deterministic dict);
      `approve()` → the manifest is INSTALLED (`integrate_tool` ran) and the
      capability is now invokable through the ordinary `kernel.invoke` path;
  (a2) THE RUNNING PATH: `kernel.say` itself — the production caller — reaches the
      seam: an unmatched turn surfaces the tool AND submits the activation item;
      approving it installs a Morta-gated rail that STAYS Morta-gated (invoke is
      denied until the capability itself is human-approved — ordinary spine);
  (b) FAIL CLOSED: without approval nothing installs; `deny()` installs nothing and
      records the denial Cell; with NO handler bound, even an approve() installs
      nothing (the enactor refuses — never a stub); no auto-activation anywhere;
  (c) DATA: the queued activation item is `instruction_eligible: False` and the
      manifest content never carries instruction eligibility — a suggestion can
      describe, never instruct.

Mutation (the load-bearing line, decima/discovery.py in the enactor `_install`):
    cap_id = _k.integrate_tool(_name, handler, caveats=dict(mc.content.get("caveats") or {}))
revert it (approve() no longer calls integrate_tool) → (a) goes RED: approval
installs nothing and the capability never becomes invokable.

Contract: run(k, line). Fail loud (assert). Owns a fresh, offline Kernel; binds its
OWN hermetic activation handlers ('cav_probe' shapes) and restores every binding.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import builtin_manifests
from decima import discovery as D
from decima.inbox import ApprovalInbox, DECISION, ITEM


def run(k, line):
    line("\n== CATALOG ACTIVATION (use-suggestion → ApprovalInbox → installer) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    builtin_manifests.register_builtins(kk)
    ib = ApprovalInbox(kk)

    def agent():
        return kk.weave().get(kk.decima_agent_id)

    def cap_named(name):
        return next((c for c in kk.weave().of_type("capability")
                     if not c.retracted and c.content.get("name") == name), None)

    def invokes_of(cap_id):
        return [i for i in kk.weave().invocations if i.cap == cap_id]

    # The check's OWN hermetic effect handlers (cav_probe shapes — never 'echo').
    w_calls, s_calls = [], []

    def w_probe(impl, args):
        w_calls.append(dict(args))
        return {"out": "cav_probe: conditions recorded", "probe": "cav",
                "readings": 1, "instruction_eligible": False}

    def s_probe(impl, args):
        s_calls.append(dict(args))
        return {"out": "cav_probe: charge recorded", "probe": "cav",
                "amount_cents": int(args.get("amount_cents", 0)),
                "instruction_eligible": False}

    prev_w = D.bind_activation_handler("weather_engine", w_probe)
    prev_s = D.bind_activation_handler("stripe_rail", s_probe)
    try:
        # ── (a) SUGGESTION → APPROVAL → INSTALL ──────────────────────────────────
        goal = "get the weather forecast"
        assert cap_named("weather_engine") is None, \
            "the capability must NOT exist before approval (baseline)"
        d = D.discover(kk, goal, threshold=kk.DISCOVERY_THRESHOLD)
        assert d["action"] == "use" and d["name"] == "weather_engine", d
        act = d.get("activation")
        assert act is not None and act["status"] == "pending" and act.get("item"), \
            f"a use-suggestion must SUBMIT a pending activation: {act}"
        item = act["item"]
        icell = kk.weave().get(item)
        assert icell is not None and icell.type == ITEM, \
            "the activation must be a durable inbox_item on the Weft"
        assert icell.content["capability_name"] == D.ACTIVATE_PREFIX + "weather_engine"
        assert item in [c.id for c in ib.pending()], "the activation awaits a HUMAN"
        # NOTHING installed yet — approval now has something to fire, but hasn't.
        assert cap_named("weather_engine") is None, "no auto-activation on submit"
        assert not w_calls, "no handler may run before approval"
        # provenance: the item links back to the manifest that raised it (Law 4).
        assert any(e["rel"] == "requested_by" and e["dst"] == d["manifest"]
                   for e in icell.edges_out), "the item must cite its manifest"
        line("  (a) discover(bundled goal) → use-suggestion + PENDING activation "
             "item (durable, provenance to the manifest); nothing installed yet ✓")

        # a DIRECT, approval-less invoke of the enactor is DENIED at the Morta gate.
        direct = kk.invoke(agent(), act["activation"],
                           {"name": "weather_engine", "manifest": d["manifest"]})
        assert "denied" in direct and "approval" in direct["denied"], \
            f"the activation enactor must be Morta-gated: {direct}"
        assert cap_named("weather_engine") is None, \
            "a gate-denied enactor invoke must install NOTHING"

        # idempotent + deterministic: re-discovery reuses the SAME pending item.
        d2 = D.discover(kk, goal, threshold=kk.DISCOVERY_THRESHOLD)
        assert d2 == d, "repeated discover() must be byte-identical (same item)"
        assert len([c for c in ib.pending()
                    if c.content.get("capability") == act["activation"]]) == 1, \
            "re-discovery must never flood the inbox with duplicate items"
        line("  fail closed pre-approval: direct enactor invoke DENIED at the gate; "
             "re-discovery reuses the same pending item (no flood) ✓")

        # the human APPROVES → the enactor runs through the gate and INSTALLS.
        res = ib.approve(item)
        assert "ok" in res, f"an approved activation must enact: {res}"
        cap = cap_named("weather_engine")
        assert cap is not None, \
            "approval must INSTALL the manifest as a capability (integrate_tool ran)"
        assert cap.content["caveats"]["effect_class"] == "READ", \
            "the installed capability must carry the MANIFEST's caveats"
        assert kk.weave().get(res["result_cell"]).content.get("installed") is True
        # …and it is now invokable through the ORDINARY kernel.invoke path.
        r = kk.invoke(agent(), cap.id, {"location": "smoke-city"})
        assert "ok" in r and r["ok"]["probe"] == "cav", r
        assert len(w_calls) == 1 and w_calls[0]["location"] == "smoke-city", \
            "the installed handler must actually run through kernel.invoke"
        line("  approve() → integrate_tool INSTALLED weather_engine (manifest "
             "caveats kept) and kernel.invoke now reaches the real handler ✓")

        # ── (a2) THE RUNNING PATH — kernel.say itself reaches the seam ──────────
        out = "\n".join(kk.say("charge a customer's credit card"))
        assert "stripe_rail" in out and "approve to activate" in out, out
        pend = [c for c in ib.pending()
                if c.content.get("capability_name") == D.ACTIVATE_PREFIX + "stripe_rail"]
        assert len(pend) == 1, \
            "kernel.say (the production caller) must have SUBMITTED the activation"
        assert cap_named("stripe_rail") is None, "say must install NOTHING by itself"
        res2 = ib.approve(pend[0].id)
        assert "ok" in res2, res2
        scap = cap_named("stripe_rail")
        assert scap is not None, "approving the say-submitted item must install"
        assert scap.content["caveats"].get("requires_approval") is True, \
            "a Morta-gated manifest must install as a Morta-gated capability"
        # installed but STILL gated: the ordinary spine gates every invoke.
        denied = kk.invoke(agent(), scap.id, {"amount_cents": 100})
        assert "denied" in denied and "approval" in denied["denied"], denied
        assert not s_calls, "no effect may run through the still-gated rail"
        kk.approve(scap.id)                       # the human operator-enables it
        ok2 = kk.invoke(agent(), scap.id, {"amount_cents": 100})
        assert "ok" in ok2 and len(s_calls) == 1, ok2
        line("  (a2) RUNNING PATH: kernel.say surfaced stripe_rail AND submitted "
             "the item; approve installed it Morta-gated — invoke denied until the "
             "capability itself is approved (ordinary authorize+Morta spine) ✓")

        # ── (b) FAIL CLOSED — deny installs nothing; unbound handler refuses ─────
        goal3 = "verify someone's identity documents"
        d3 = D.discover(kk, goal3, threshold=kk.DISCOVERY_THRESHOLD)
        assert d3["action"] == "use" and d3["name"] == "kyc", d3
        act3 = d3["activation"]
        assert act3["status"] == "pending" and cap_named("kyc") is None
        did = ib.deny(act3["item"], reason="not sanctioned")
        dec = kk.weave().get(did)
        assert dec is not None and dec.type == DECISION \
            and dec.content["decision"] == "denied" and dec.content["ran"] is False, \
            "a denied activation must record the denial Cell"
        assert cap_named("kyc") is None, "a DENIED activation installs NOTHING"
        assert not invokes_of(act3["activation"]), "deny must never invoke the enactor"
        # no handler bound (kyc was never bound): a FRESH item's approve() refuses.
        d4 = D.discover(kk, goal3, threshold=kk.DISCOVERY_THRESHOLD)
        act4 = d4["activation"]
        assert act4["status"] == "pending" and act4["item"] != act3["item"], \
            "after a denial, a fresh explicit discovery queues a fresh decision"
        r4 = ib.approve(act4["item"])
        assert "denied" in r4 and "no handler bound" in r4["denied"], \
            f"an unbound activation must fail CLOSED even when approved: {r4}"
        assert cap_named("kyc") is None, \
            "an approve with no bound handler must install NOTHING (never a stub)"
        line("  (b) deny() → denial Cell, nothing installed, enactor never invoked; "
             "an approve with NO bound handler refuses (fail closed, no stub) ✓")

        # ── (c) DATA — items and manifests describe, never instruct ─────────────
        for it in (icell, pend[0], kk.weave().get(act3["item"])):
            assert it.content.get("instruction_eligible") is False, \
                f"an activation item must be DATA (instruction_eligible=False): {it.id}"
        mc = kk.weave().get(d["manifest"])
        assert mc.content.get("instruction_eligible") is not True, \
            "manifest content must never be instruction-eligible"
        line("  (c) activation items are instruction_eligible=False; manifest "
             "content never instruction-eligible — suggestions are DATA ✓")
    finally:
        D.bind_activation_handler("weather_engine", prev_w)
        D.bind_activation_handler("stripe_rail", prev_s)

    line("  → the catalog 'use' path is live end-to-end: discover() submits a "
         "durable Morta-gated activation, kernel.say inherits it with no core "
         "edit, a human approve() installs the manifest via integrate_tool, and "
         "the installed capability rides the ordinary authorize+Morta spine — "
         "denied/unbound/unapproved all fail closed.")
