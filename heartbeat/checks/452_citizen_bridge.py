"""CITIZENS-BRIDGE HARDENING (Cycle-56 follow-up) — the scope gate binds SILENCE too,
and the MCP bridge re-checks the CITIZEN's envelope, never latest-cap-by-name.

The Cycle-56 review found two real gaps in the citizens surface. (1) `citizen_invoke`'s
target-scope gate defaulted an OMITTED target to the grant's own scope, so the gate only
bound callers who NAMED a target — omission walked straight past it. (2) `mcp_server.handle`
resolves a tools/call to the realm's LATEST capability BY NAME, so a citizen bridged through
that path was never checked against ITS OWN attenuated envelope — its narrowed target scope
did not run at all (an out-of-scope, even an exfil-shaped, call SUCCEEDED), and a later
same-named grant to ANYONE silently shadowed the citizen's own tool. Both are now closed,
fail-closed. This check proves, offline + deterministically:

  (a) OMITTED TARGET IS FAIL-CLOSED — a citizen holding a SCOPED (non-"*") grant invoking
      WITHOUT naming a target is DENIED (previously it silently passed the scope gate), the
      denial is an audited `citizen_action`, and NO effect fires (the lease is unspent); the
      SAME citizen naming its in-scope target still SUCCEEDS, and a named out-of-scope
      target stays DENIED (the named path is not weakened);
  (b) THE BRIDGE RE-CHECKS THE ENVELOPE — through `mcp_server.handle` (the bridge path a
      citizen's tools/call actually takes), an out-of-scope target and an omitted target on
      the scoped grant are DENIED with no effect fired (previously the name-resolve skipped
      the scope gate and the exfil ran); a tool the citizen does NOT hold (a live realm cap
      of that name exists) is DENIED with the envelope named as the reason; and after a
      SECOND citizen's same-named grant becomes the realm's latest cap of that name, the
      first citizen's in-scope call still SUCCEEDS through ITS OWN grant — its lease is
      spent, the other citizen's is NOT (envelope resolution, not latest-cap-by-name);
  (c) NO REGRESSION — the legitimate narrowed invoke (named, in-scope, in-allowlist) still
      works; a "*"-scoped grant still accepts an omitted target (there is no scope to
      escape); a NON-citizen realm agent's tools/list + tools/call through `mcp_server.handle`
      behave exactly as before (the gate wraps citizens only); float args through the bridge
      are refused; and every bridge action — allowed or denied — is an audited Cell.

Mutation-resistance (the load-bearing line): revert `citizen_invoke`'s omitted-target guard
(`if scope != "*" and (req is None or nfc(str(req)) != scope):` back to the old
default-to-scope `args.get("target", scope)`) and (a)'s omitted-target invoke SUCCEEDS —
this check goes RED. Neuter the bridge envelope re-check (uninstall `_install_bridge_gate`
/ resolve latest-cap-by-name again) and (b) goes RED twice over: the out-of-scope bridge
exfil SUCCEEDS and the shadowed citizen's legitimate in-scope call is wrongly denied.

Contract: run(k, line). Fail loud (assert). Owns a fresh Kernel; registers its OWN
hermetic effect (`bridge_probe`), never 'echo'.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import citizens, executor, mcp_server

_PROBE = "bridge_probe"


def _call(k1, citizen_cell, name, arguments, rid=1):
    """One citizen tools/call through the PLAIN MCP server entry — the bridge path."""
    return mcp_server.handle(k1, citizen_cell, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}})


def _text(resp):
    return resp["result"]["content"][0]["text"]


def run(k, line):
    line("\n== CITIZENS-BRIDGE HARDENING — silence is out of scope; the bridge is the envelope ==")
    k1 = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # A hermetic, check-owned effect: the probe echoes its payload back.
    executor.register(_PROBE, lambda impl, args: {
        "out": "bridge says: " + str(args.get("text", ""))})
    base = k1._assert_cap(_PROBE, _PROBE, caveats={"budget": 50})
    k1.grant(base, k1.decima_agent_id)

    # A NON-citizen realm agent drives the plain server FIRST (pre-admission, the realm's
    # latest cap named bridge_probe is the base grant Decima itself holds) — the citizen
    # gate must leave realm agents exactly as they were.
    decima = k1.weave().get(k1.decima_agent_id)
    listed = mcp_server.handle(k1, decima, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "result" in listed and "tools" in listed["result"], \
        f"tools/list for a realm agent must flow to the original handler: {listed}"
    r_realm = _call(k1, decima, _PROBE, {"text": "native"}, rid=2)
    assert r_realm["result"]["isError"] is False and "bridge says: native" in _text(r_realm), \
        f"a NON-citizen tools/call must behave exactly as before (no regression): {r_realm}"

    # Admit the citizen: one-effect allowlist, SCOPED target, bounded uses.
    adm = citizens.admit_citizen(
        k1, "gateway-1", from_cap=base,
        narrow={"effects": [_PROBE], "target": "repo:decima", "max_uses": 10})
    grant, cz = adm["grant"], adm["citizen"]

    # ── (a) OMITTED TARGET IS FAIL-CLOSED — silence no longer inherits the scope. ────
    uses0 = k1.lease_uses(k1.weave(), grant)
    r_omit = citizens.citizen_invoke(k1, cz, grant, {"text": "peek"})
    assert "denied" in r_omit and "omitted" in r_omit["denied"] and "outside" in r_omit["denied"], \
        f"an OMITTED target on a scoped grant must be DENIED (fail closed): {r_omit}"
    assert k1.lease_uses(k1.weave(), grant) == uses0, \
        "the omitted-target denial must fire NO effect (the lease is unspent)"
    act = k1.weave().get(r_omit["action_cell"])
    assert act.type == citizens.CITIZEN_ACTION and act.content["outcome"] == "denied" \
        and act.content["target"] == "(omitted)" and isinstance(act.content["at"], int), \
        "the omitted-target denial is an audited citizen_action recording '(omitted)' honestly"
    r_named = citizens.citizen_invoke(k1, cz, grant, {"target": "repo:decima", "text": "status"})
    assert r_named.get("status") == "SUCCEEDED" and \
        r_named["ok"]["out"].startswith("bridge says:"), \
        f"the SAME citizen naming its in-scope target must still SUCCEED: {r_named}"
    r_out = citizens.citizen_invoke(k1, cz, grant, {"target": "repo:other", "text": "x"})
    assert "denied" in r_out and "outside" in r_out["denied"], \
        f"a NAMED out-of-scope target must stay DENIED (the named path is not weakened): {r_out}"
    line("  omitted target fails closed: a scoped grant invoked WITHOUT a target is DENIED "
         "(audited '(omitted)', lease unspent); the in-scope NAMED invoke still succeeds and "
         "the named out-of-scope invoke stays denied ✓")

    # ── (b) THE BRIDGE RE-CHECKS THE ENVELOPE — scope + omission + holding, on the wire. ─
    cz_cell = k1.weave().get(cz)
    uses1 = k1.lease_uses(k1.weave(), grant)
    b_out = _call(k1, cz_cell, _PROBE, {"target": "repo:other", "text": "exfil"}, rid=3)
    assert b_out["result"]["isError"] is True and "outside" in _text(b_out), \
        f"an out-of-scope target THROUGH the bridge must be DENIED (was the exfil hole): {b_out}"
    b_omit = _call(k1, cz_cell, _PROBE, {"text": "peek"}, rid=4)
    assert b_omit["result"]["isError"] is True and "omitted" in _text(b_omit), \
        f"an omitted target THROUGH the bridge must be DENIED too: {b_omit}"
    assert k1.lease_uses(k1.weave(), grant) == uses1, \
        "neither bridged bypass attempt may fire an effect (the lease is unspent)"
    # A tool the citizen does NOT hold — the realm's own live 'shell' cap exists by name.
    b_shell = _call(k1, cz_cell, "shell", {"cmd": "date"}, rid=5)
    assert b_shell["result"]["isError"] is True and "envelope" in _text(b_shell), \
        f"a realm cap the citizen does not hold must be DENIED with the envelope named: {b_shell}"
    n_inv = len(k1.weave().invocations)

    # LATEST-CAP-BY-NAME SHADOWING: a second citizen admitted from the same base mints a
    # later, same-named grant — the realm's latest capability named bridge_probe is now
    # NOT gateway-1's grant. The bridge must still resolve gateway-1's OWN grant.
    adm2 = citizens.admit_citizen(
        k1, "gateway-2", from_cap=base,
        narrow={"effects": [_PROBE], "target": "repo:other", "max_uses": 3})
    latest = None
    for c in k1.weave().of_type("capability"):
        if c.content.get("name") == _PROBE:
            latest = c
    assert latest.id == adm2["grant"] != grant, \
        "the shadow is real: the realm's latest cap of that name is the OTHER citizen's grant"
    b_ok = _call(k1, cz_cell, _PROBE, {"target": "repo:decima", "text": "ping"}, rid=6)
    assert b_ok["result"]["isError"] is False and "bridge says: ping" in _text(b_ok), \
        (f"the citizen's in-scope bridge call must SUCCEED through ITS OWN grant even when "
         f"shadowed by a later same-named cap (envelope, not latest-cap-by-name): {b_ok}")
    assert k1.lease_uses(k1.weave(), grant) == uses1 + 1, \
        "the bridged call spent the CITIZEN'S OWN lease (its grant authorized the INVOKE)"
    assert k1.lease_uses(k1.weave(), adm2["grant"]) == 0, \
        "the other citizen's same-named grant authorized NOTHING (no cross-citizen reach)"
    assert len(k1.weave().invocations) == n_inv + 1, \
        "exactly ONE effect fired across the bridge scenarios — the legitimate in-scope call"
    line("  bridge re-checks the envelope: out-of-scope AND omitted targets over "
         "mcp_server.handle are DENIED with no effect (the exfil hole is shut), an unheld "
         "tool is denied BY ENVELOPE, and a later same-named grant cannot shadow the "
         "citizen's own tool — its call runs on ITS grant, spending ITS lease ✓")

    # ── (c) NO REGRESSION — "*" scope, floats, audit; the plain paths stay whole. ─────
    star = citizens.admit_citizen(k1, "gateway-star", from_cap=base,
                                  narrow={"effects": [_PROBE], "max_uses": 4})
    sgrant = k1.weave().get(star["grant"])
    assert sgrant.content["target"] == "*", "no target narrowing ⇒ the scope stays '*'"
    r_star = citizens.citizen_invoke(k1, star["citizen"], star["grant"], {"text": "hello"})
    assert r_star.get("status") == "SUCCEEDED", \
        f"a '*'-scoped grant still accepts an OMITTED target (no scope to escape): {r_star}"
    b_float = _call(k1, cz_cell, _PROBE, {"target": "repo:decima", "x": 1.5}, rid=7)
    assert b_float["result"]["isError"] is True and "float" in _text(b_float), \
        f"float args through the bridge are refused at the door (ints-not-floats): {b_float}"
    acts = k1.weave().of_type(citizens.CITIZEN_ACTION)
    # 3 direct invokes (a) + 3 bridged gateway-1 calls (b: out/omit/ok) + 1 unheld-tool
    # bridge denial (b) + 1 '*'-scope invoke (c) = 8 audited actions. The float refusal
    # RAISED before anything was asserted — a refused request writes NOTHING (fail closed).
    assert len(acts) == 8, \
        f"every citizen action — allowed or denied, direct or bridged — is audited: {len(acts)}"
    for a in acts:
        assert isinstance(a.content["at"], int), "audit ticks are ints (no wall-clock)"
    line("  no regression: '*'-scoped grants take an omitted target as before, realm agents "
         "drive the plain server unchanged, floats are refused on the bridge, and all 8 "
         "actions (allowed AND denied, direct AND bridged) fold as audit Cells ✓")

    line("  → the citizens surface fails CLOSED both ways now: a scoped grant binds SILENCE "
         "(an omitted target is out of scope, not a default), and the MCP bridge is the "
         "citizen's ENVELOPE — resolved within what it holds, gated by citizen_invoke, "
         "never merely the realm's latest capability of that name.")
