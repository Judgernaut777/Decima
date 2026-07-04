"""LIVE-WORLD TEST HARNESS — honest degradation under injected faults (Phase 4).

Phase 2 proved the gate is the ONLY live path and every wrapped engine proved its
HAPPY path offline — but nothing ever drove the gated live path through ADVERSE
conditions. `decima/liveworld.py` is the named Phase-4 harness that does: it injects
deterministic faults at the SOCKET seam (`_open` — the same seam every wrapped-engine
offline check uses; the rule of egress runs first, every time) and exposes the honest
outcome so THIS check can assert the system's degradation story:

  (a) EVERY FAULT DEGRADES HONESTLY (load-bearing): a timeout-after-submission,
      tampered response (float tick + a reflected credential), and partial/empty
      body each yield a truthful UNKNOWN receipt; a connection-refused yields a
      definite FAILED — NEVER a fabricated SUCCEEDED, and never a fabricated output
      (`out` stays None on UNKNOWN). The healthy control still SUCCEEDS (the verdict
      is falsifiable, not trivially pessimistic). `run_suite` reports fabricated == 0.
  (b) THE GATE HOLDS UNDER FAULT: a capability approved then REVOKED before the call
      is refused AT THE WIRE — a `wire_decision` DENY Cell lands, the socket never
      runs (wire_calls == 0), the receipt is a truthful FAILED denial; after the
      revocation the grant cannot even construct another gated transport; and the
      bare/ungated paths (a raw `urlopen`, an adapter without a grant) still RAISE —
      stress opens no bypass.
  (c) NO LEAK: the credential rode EVERY faulted request (secret_applied), and the
      TAMPERED response reflected it straight back — yet the raw secret appears in
      NO cell content and NO event body on any Weft the harness touched.
  (d) A REGRESSION IS CAUGHT: a mutated engine that swallows the fault and lies
      SUCCEEDED (the fabricated-success mutation) is classified FABRICATED by
      `liveworld.honest`, and `run_suite`'s fabricated count RISES from 0 to 5 —
      while `denials` still reads 1, because the gate's own DENY provenance folds
      from the wire's Cells, not from the lying engine's claims.

Mutation-resistance (the load-bearing line): neuter the verdict in
`liveworld.honest` — `return outcome.get("status") != executor.SUCCEEDED   # a fault
must NEVER read SUCCEEDED` — so a fabricated SUCCEEDED under a fault counts as honest,
and (d) goes RED (fabricated stays 0; the lying scenario reads honest).

Entirely OFFLINE + DETERMINISTIC: every fault is an injected stub at the socket seam
(no real socket, no DNS, no wall-clock — the "timeout" raises without sleeping).
Contract: run(k, line). Fail loud via assert. Owns fresh Kernels.
"""
import os
import tempfile
import urllib.request

from decima.kernel import Kernel
from decima import egress, executor, live_wire, liveworld, wire

SECRET = "lw_carrier_SECRET_454_never-on-the-weft"
HOST = "api.probe454.example"


def _fresh():
    """A fresh Kernel + a granted, Morta-APPROVED egress capability + the acting agent."""
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    cap_id, _hosts = egress.install(kk, allowlist=[HOST])
    kk.approve(cap_id)                        # the human says yes — the wire may open
    return kk, kk.weave().get(kk.decima_agent_id), cap_id


def _no_leak(kk):
    """The raw secret appears NOWHERE in recorded history: no cell content, no event body."""
    for c in kk.weave().cells.values():
        assert SECRET not in str(c.content), f"secret leaked into a {c.type} cell"
    assert not liveworld.secret_on_weft(kk, SECRET), "secret leaked into an event body"


def run(k, line):
    line("\n== LIVE-WORLD TEST HARNESS — fault injection over the gated live path ==")

    # ── (a) EVERY FAULT DEGRADES HONESTLY — truthful receipts, never fabricated ──
    kk, a, cap = _fresh()
    expected = {
        liveworld.NONE: executor.SUCCEEDED,               # healthy control
        liveworld.TIMEOUT: executor.UNKNOWN,              # submitted, unobservable
        liveworld.CONNECTION_REFUSED: executor.FAILED,    # never reached the world
        liveworld.TAMPERED_RESPONSE: executor.UNKNOWN,    # corrupt 200 — untrusted
        liveworld.PARTIAL_RESPONSE: executor.UNKNOWN,     # required fields missing
    }
    for fault, want in expected.items():
        out = liveworld.scenario(kk, a, cap, fault, {"secret": SECRET})
        assert out["status"] == want, f"{fault}: want {want}, got {out}"
        assert out["gate_held"], f"{fault}: the gate's provenance must be coherent: {out}"
        assert not out["leaked"], f"{fault}: the secret must never land on the Weft"
        assert out["wire_calls"] == 1 and out["secret_applied"], \
            f"{fault}: exactly one gated socket call, credential applied inside: {out}"
        rc = kk.weave().get(out["receipt"])
        assert rc is not None and rc.content["status"] == want, \
            f"{fault}: the receipt Cell must carry the truthful status verbatim"
        if want == executor.UNKNOWN:
            assert rc.content.get("out") is None, \
                f"{fault}: an UNKNOWN receipt must carry NO fabricated output (§8.3)"
        assert liveworld.honest(fault, out), f"{fault}: an honest outcome must read honest"
    line("  (a) faults degrade HONESTLY: timeout/tampered/partial → UNKNOWN (out=None, "
         "never invented), refused → definite FAILED, healthy control → SUCCEEDED — "
         "each status read verbatim from the receipt Cell ✓")

    k2, a2, cap2 = _fresh()
    rep = liveworld.run_suite(k2, a2, cap2, secret=SECRET)
    assert rep == {"scenarios": 6, "honest": 6, "fabricated": 0, "denials": 1}, rep
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in rep.values()), \
        f"the report must be int counts (ints-not-floats): {rep}"
    line(f"  (a) run_suite over the full battery: {rep['scenarios']} scenarios, "
         f"{rep['honest']} honest, fabricated == 0, exactly 1 gate denial ✓")

    # ── (b) THE GATE HOLDS UNDER FAULT — revoked mid-flight fails CLOSED ─────────
    assert cap in kk.approvals, "precondition: the egress cap was Morta-approved"
    out = liveworld.scenario(kk, a, cap, liveworld.REVOKED_MID_FLIGHT, {"secret": SECRET})
    assert out["denied"] and out["status"] == executor.FAILED, \
        f"a revoked-mid-flight cap must be DENIED with a truthful FAILED receipt: {out}"
    assert out["wire_calls"] == 0, "the effect must NOT fire after revocation (fail closed)"
    assert out["gate_held"] and not out["leaked"], out
    assert liveworld.honest(liveworld.REVOKED_MID_FLIGHT, out)
    denies = [c for c in kk.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.DENY and c.content.get("capability") == cap]
    assert denies and any("revoked" in c.content.get("reason", "") for c in denies), \
        "the wire must record its OWN DENY provenance naming the revocation"
    # the dead grant cannot even construct another gated probe (fail closed at build)…
    try:
        liveworld.scenario(kk, a, cap, liveworld.TIMEOUT, {"secret": SECRET})
        raise AssertionError("a revoked grant must not construct another gated transport")
    except live_wire.NoGatedTransport:
        pass
    # …and the bare/ungated paths still RAISE — stress opens no bypass.
    try:
        urllib.request.urlopen(f"https://{HOST}/liveworld/probe")
        raise AssertionError("an ungated bare urlopen must be refused at the armed wire")
    except wire.EgressDenied:
        pass
    try:
        live_wire.gated_transport(kk, a, "")
        raise AssertionError("an adapter without a grant must refuse (no ambient authority)")
    except live_wire.NoGatedTransport:
        pass
    line("  (b) the gate HOLDS under fault: approved-then-revoked → DENY Cell recorded, "
         "socket never ran, truthful FAILED; the dead grant builds nothing; bare urlopen "
         "and grantless adapters still raise — no bypass under stress ✓")

    # ── (c) NO LEAK — the credential rode every faulted request, landed nowhere ──
    _no_leak(kk)
    _no_leak(k2)
    line("  (c) no leak: the credential rode every faulted request (applied inside the "
         "probe) and the TAMPERED response reflected it back — yet the raw secret is in "
         "no cell content and no event body on any Weft the harness touched ✓")

    # ── (d) A REGRESSION IS CAUGHT — the fabricated-success mutation goes loud ───
    def lying_probe(transport, url, secret):
        """The MUTATION under test: an engine that swallows EVERY fault — even the
        gate's own denial — and claims SUCCEEDED. The harness must expose it."""
        def handler(_impl, _args):
            try:
                transport(url, {"Authorization": f"Bearer {secret}"}, "{}")
            except Exception:
                pass                              # swallow the timeout / denial…
            return {"out": "sent (fabricated)"}   # …and LIE: a fabricated success
        return handler

    k3, a3, cap3 = _fresh()
    lie = liveworld.scenario(k3, a3, cap3, liveworld.TIMEOUT, {"secret": SECRET},
                             _handler=lying_probe)
    assert lie["status"] == executor.SUCCEEDED, f"the mutation must really lie: {lie}"
    assert not liveworld.honest(liveworld.TIMEOUT, lie), \
        "REGRESSION MISSED: a fabricated SUCCEEDED under a timeout must be classified DISHONEST"
    k4, a4, cap4 = _fresh()
    rep_bad = liveworld.run_suite(k4, a4, cap4, secret=SECRET, _handler=lying_probe)
    assert rep_bad["fabricated"] == 5 and rep_bad["honest"] == 1, \
        f"the fabricated count must RISE for every injected fault the engine lied about: {rep_bad}"
    assert rep_bad["denials"] == 1, \
        f"the gate's DENY provenance survives a lying engine (folds from the wire's Cells): {rep_bad}"
    _no_leak(k3)
    _no_leak(k4)
    line("  (d) regression CAUGHT: an engine that lies SUCCEEDED under faults is classified "
         "fabricated (0 → 5), while denials still read 1 from the wire's own provenance — "
         "the harness cannot be lied to ✓")

    line("  → the live-world test harness is REAL: deterministic fault injection over the "
         "gated live path proves honest degradation — truthful FAILED/UNKNOWN receipts, a "
         "gate that fails closed mid-flight, credentials that never touch the Weft, and a "
         "fabricated success that cannot hide.")
