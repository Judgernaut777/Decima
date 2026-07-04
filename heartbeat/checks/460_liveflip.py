"""LIVE-ENGINE FLIP — an APPROVED grant actually turns a named engine live (Batch A).

Phase 2's audit gap, verbatim from golive.doctor: `k.live_engines` is "a Lane B
registry, absent today" — the doctor REPORTS which engines are live, but NOTHING
populates it. So even after an operator supplies a key and a human approves an
egress grant, no code path flips a named engine live. `golive.activate_engine`
closes that: it runs the SAME approved-grant test `bind_brain` rides, constructs
the engine's wire-gated transport via `live_wire` (the ONLY live path — the gate
re-runs the FULL rule of egress per call), and REGISTERS the engine in
`k.live_engines` so the doctor reports it truthfully. This check is its
adversarial detector — entirely OFFLINE (injected fake socket seams, fresh
Kernels, no wall clock, no real key):

  (a) APPROVED GRANT FLIPS THE ENGINE LIVE (load-bearing) — grant + human-APPROVE
      an egress capability for an engine host, `activate_engine` → the engine
      appears in `k.live_engines`, the doctor reports it live, an `engine_live`
      Cell (with `flipped_via` provenance to the approving grant) lands on the
      Weft, and the constructed transport IS the wire gate: a call over an
      injected socket seam leaves the `wire_decision` ALLOW provenance naming the
      approved capability. Re-flipping is idempotent — the log does not move;
  (b) NO APPROVAL → STAYS OFFLINE (fail closed) — a host with NO grant at all,
      and a host whose grant is queued but NOT YET approved by a human, both
      refuse to flip: nothing registered, no engine_live Cell, the doctor still
      reports the engine absent;
  (c) NO SECRET / NO AMBIENT — the flip records not one secret byte on the Weft
      (sentinel scan over every event + folded cell), and MINTS NOTHING: the set
      of capability Cells and the approval set are byte-identical before/after a
      flip. A REVOKED grant un-lives the engine on the next doctor/flip — the
      registry is re-verified against the Weft, never trusted.

Mutation-resistance (the load-bearing line): drop the approved-grant check in
`activate_engine` (register live unconditionally) and (b) goes RED — an engine
with no human-approved grant flips live and the doctor lies.

Contract: run(k, line). Fail loud via assert. Owns fresh, offline Kernels.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import golive, wire
from decima.inbox import ApprovalInbox

# A sentinel no legitimate content could contain: if its bytes surface in any
# event, cell, or report after the flip, custody is broken — fail loud.
SENTINEL = "sk-liveflip-SENTINEL-7d2c91af04b6e358-shippo"


def _fresh():
    return Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)


def _world_dump(kk) -> str:
    """EVERYTHING durable: every Weft event's payload and every folded Cell's
    content, repr'd — the haystack the sentinel must never appear in."""
    parts = [repr((ev.verb, ev.author, ev.body)) for ev in kk.weft.events()]
    parts += [repr((c.id, c.type, c.content)) for c in kk.weave().cells.values()]
    return "\n".join(parts)


def _approve_grant(kk, host):
    """The operator flow 418 proved: request → durable inbox item → HUMAN approve.
    Returns the approved egress capability id."""
    res = golive.request_grant(kk, host)
    assert res["status"] == "pending", res
    out = ApprovalInbox(kk).approve(res["item"])
    assert "ok" in out, out
    assert res["capability"] in kk.approvals, "approval must land the grant"
    return res["capability"]


def run(k, line):
    line("\n== LIVE-ENGINE FLIP — an approved grant populates k.live_engines (fail closed) ==")
    kk = _fresh()
    # a secret in the broker BEFORE the flip — the flip must never surface it.
    golive.intake_env(kk, environ={"DECIMA_SECRET_SHIPPO": SENTINEL})

    # ── (a) APPROVED GRANT FLIPS THE ENGINE LIVE (load-bearing) ─────────────
    ecap = _approve_grant(kk, "api.shipping.example")
    caps_before = {c.id for c in kk.weave().of_type("capability")}
    approvals_before = set(kk.approvals)

    opened = []

    def fake_open(url, headers, body, method, timeout):   # the SOCKET seam, never the gate
        opened.append((method, url))
        return 200, {"ok": True, "tracking": "SHP-1"}

    res = golive.activate_engine(kk, "shipping", "api.shipping.example",
                                 _open=fake_open)
    # snapshot IMMEDIATELY around the flip: verified under (c) — mints nothing.
    caps_after = {c.id for c in kk.weave().of_type("capability")}
    approvals_after = set(kk.approvals)
    assert res["status"] == "live" and res["capability"] == ecap, res
    assert "shipping" in kk.live_engines, \
        "an approved flip must REGISTER the engine in k.live_engines"
    assert kk.live_engines["shipping"]["capability"] == ecap
    d = golive.doctor(kk)
    assert d["engines"]["live"] == ["shipping"], \
        f"the doctor must truthfully report the populated live set: {d['engines']}"
    # the flip is on the Weft — an engine_live Cell, provenance to the grant.
    cells = kk.weave().of_type(golive.ENGINE_LIVE)
    assert len(cells) == 1 and cells[0].id == res["cell"], cells
    ec = cells[0].content
    assert ec["engine"] == "shipping" and ec["host"] == "api.shipping.example" \
        and ec["capability"] == ecap and ec["instruction_eligible"] is False, ec
    assert any(e["rel"] == "flipped_via" and e["dst"] == ecap
               for e in cells[0].edges_out), \
        "the engine_live Cell must carry flipped_via provenance to the grant"
    # the registered transport IS the wire gate: a call over the injected socket
    # seam passes the FULL rule of egress and lands the ALLOW provenance.
    status, body = res["transport"]("https://api.shipping.example/v1/ship",
                                    {"Accept": "application/json"}, "{}")
    assert status == 200 and body["tracking"] == "SHP-1" and len(opened) == 1
    allows = [c for c in kk.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.ALLOW]
    assert len(allows) == 1 and allows[0].content["host"] == "api.shipping.example" \
        and allows[0].content["capability"] == ecap, allows
    # idempotent: re-flipping the SAME engine on the SAME grant re-lands nothing.
    lam = kk.weft.lamport
    again = golive.activate_engine(kk, "shipping", "api.shipping.example",
                                   _open=fake_open)
    assert again["status"] == "live" and again["cell"] == res["cell"], again
    assert kk.weft.lamport == lam, "a re-flip must append no event (idempotent)"
    line("  (a) flip: grant → human approve → activate_engine registers the engine "
         "in k.live_engines; doctor reports it live; engine_live Cell + flipped_via "
         "provenance on the Weft; the transport passes the wire gate (ALLOW "
         "provenance, injected socket); re-flip appends nothing ✓")

    # ── (b) NO APPROVAL → STAYS OFFLINE (fail closed) ───────────────────────
    # no grant AT ALL for the host:
    off = golive.activate_engine(kk, "weather", "api.weather.example", shape="get2")
    assert off["status"] == "offline" and "no approved egress grant" in off["reason"], off
    assert "weather" not in kk.live_engines, \
        "an unapproved engine must NOT be registered (fail closed)"
    # a grant that is QUEUED but not yet decided by a human:
    pend = golive.request_grant(kk, "api.crm.example")
    assert pend["status"] == "pending" and pend["capability"] not in kk.approvals
    off2 = golive.activate_engine(kk, "crm", "api.crm.example")
    assert off2["status"] == "offline", \
        f"a pending (un-approved) grant must not flip an engine live: {off2}"
    assert "crm" not in kk.live_engines
    d2 = golive.doctor(kk)
    assert d2["engines"]["live"] == ["shipping"], \
        f"the doctor must still report the unapproved engines ABSENT: {d2['engines']}"
    assert len(kk.weave().of_type(golive.ENGINE_LIVE)) == 1, \
        "a refused flip must record NO engine_live Cell"
    line("  (b) fail closed: no grant → offline; a QUEUED-but-unapproved grant → "
         "still offline; nothing registered, no engine_live Cell, the doctor "
         "reports the engines absent ✓")

    # ── (c) NO SECRET / NO AMBIENT; a revoked grant un-lives the engine ─────
    assert SENTINEL not in _world_dump(kk), \
        "the flip must leave ZERO secret bytes in any event or folded cell"
    assert SENTINEL not in repr(golive.doctor(kk)) + "\n".join(golive.doctor_lines(kk))
    assert caps_after == caps_before, \
        "a flip must MINT no capability (it only records an approved one)"
    assert approvals_after == approvals_before, \
        "a flip must mint no approval — no ambient authority"
    # Morta revokes the grant → the engine un-lives on the very next doctor …
    kk.revoke(ecap)
    d3 = golive.doctor(kk)
    assert d3["engines"]["live"] == [], \
        f"a REVOKED grant must un-live the engine at the next doctor: {d3['engines']}"
    assert "shipping" not in kk.live_engines, \
        "the pruned registry must drop the revoked engine"
    # … and a re-flip attempt stays offline (the registry is never trusted).
    re_flip = golive.activate_engine(kk, "shipping", "api.shipping.example")
    assert re_flip["status"] == "offline" and "shipping" not in kk.live_engines, re_flip
    line("  (c) no secret / no ambient: sentinel appears NOWHERE durable; the "
         "capability + approval sets are unchanged by the flip; a Morta-revoked "
         "grant un-lives the engine on the next doctor and a re-flip stays "
         "offline ✓")

    line("  → k.live_engines is now POPULATED, not a seam: an engine flips live "
         "only behind a human-approved egress grant (the same test bind_brain "
         "rides), the flip records redacted engine_live provenance and mints "
         "nothing, every live byte still passes the wire gate per call, and a "
         "revoked grant un-lives the engine — the doctor never lies.")
