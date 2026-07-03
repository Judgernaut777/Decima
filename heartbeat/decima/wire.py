"""WIRE1 — the network egress boundary AT THE WIRE (Phase 1 · Enforcement).

EGRESS1 (`egress.py`) expresses the rules of egress — a gated capability whose
caveats carry a target allowlist — but until now nothing enforced those rules at
the place connections actually happen: every engine's "real" transport is a
stdlib `urllib` call that would connect wherever it likes. Policy that is not a
chokepoint is a convention, and Phase 1's mandate (VISION.md — "a network egress
boundary — a mediating proxy that enforces the `egress` allowlist at the wire,
not just as a policy Cell") is to make it a boundary.

This module is that chokepoint. Two teeth, both load-bearing:

  1. **The wire guard** (`arm()`, armed on `import decima`): a handler installed
     into `urllib.request`'s GLOBAL opener that intercepts `http_open`/
     `https_open` — the exact point where a socket would be created — and RAISES
     `EgressDenied` unless the call carries the gate's PER-CONNECTION approval: a
     token in a `contextvars.ContextVar`, set only by the gated open below, bound
     to the exact scheme+host+port the policy approved and CONSUMED on the first
     matching open. An open to any other target inside the window — a nested
     `urlopen`, or a 3xx followed to a different host — does not match (or finds
     the token spent) and is refused. Every engine's default `_urllib_transport`
     funnels through `urllib.request.urlopen`, so with the guard armed,
     *constructing or using a live transport that bypasses the gate raises before
     any connection attempt* — no DNS lookup, no packet, no TLS handshake.

  2. **The gated factory** (`real_transport(k, agent, cap_id)`): the ONLY
     sanctioned path to a real transport. It returns a callable with the exact
     seam signature engines already accept (`transport(url, headers, body) ->
     (status, json)`), and EVERY call re-runs the rule of egress before the wire
     is touched:

       - **no ambient authority** — the caller must present a live (unretracted)
         egress *capability* held in the acting agent's envelope; anything else
         is refused. Authority is the grant, never the process.
       - **deny-by-default allowlist** — the capability's `egress_allowlist`
         caveat is consulted per call; a host not on it (or an empty/missing
         allowlist, or a non-https scheme — cleartext egress is refused) is
         DENIED before any connection attempt.
       - **Morta gate** — a real outward connection is an outward effect: the
         capability must carry a live human approval (`k.approve(cap_id)`,
         folded from the Weft) or the wire refuses. Revocation (`k.revoke`)
         closes the wire the same way.
       - **provenance** — EVERY decision, allow or deny, lands on the Weft as a
         `wire_decision` Cell (url, host, capability, reason, decision) BEFORE
         the wire is touched, authored by the acting agent. A denial also
         RAISES `EgressDenied` — loud, with the reason.

Injected test-mode transports are untouched: engines that receive a fake
`transport=` never reach `urllib`, so the offline oracle keeps working with no
network. The `_open` seam on `real_transport` is the same idiom — it replaces
the SOCKET, never the gate: policy runs before any `_open`, real or fake.

Stdlib only (`urllib`, `contextvars`). Deterministic: no wall clock, no
randomness — decisions fold from the Weft. Proof: heartbeat/checks/396_egress.py.
"""
import contextvars
import json
import urllib.request
from urllib.parse import urlsplit

from decima.hashing import content_id, nfc

WIRE_DECISION = "wire_decision"     # the on-Weft decision Cell type (allow/deny)
ALLOW = "allow"
DENY = "deny"

# ── the per-connection approval token: only _gated_open() may set it (bound to
#    the ONE url the policy approved, single-use), only the guard reads it ──
_gate_pass = contextvars.ContextVar("decima_wire_gate_pass", default=None)


class EgressDenied(RuntimeError):
    """Raised LOUD whenever egress is refused at the wire — a denied policy
    decision, or any attempt to reach the network without passing the gate."""


class _Approval:
    """The gate's PER-CONNECTION pass. It authorizes EXACTLY ONE open, to the
    exact scheme+host+port the egress policy approved — and is consumed on that
    open. Any other open inside the gated window (a nested `urlopen`, or a
    redirect followed to a different host) either does not match this target or
    finds the pass already spent, and so is refused. Authority is the one
    approved connection, never the ambient context."""
    __slots__ = ("scheme", "host", "port", "url", "spent")

    def __init__(self, url):
        p = urlsplit(str(url))
        self.scheme = (p.scheme or "").lower()
        self.host = (p.hostname or "").lower()
        self.port = p.port
        self.url = str(url)
        self.spent = False

    def admits(self, req) -> bool:
        """True iff this unspent pass matches THIS request's scheme+host+port."""
        if self.spent:
            return False
        full = getattr(req, "full_url", None) or req.get_full_url()
        p = urlsplit(str(full))
        return ((p.scheme or "").lower() == self.scheme
                and (p.hostname or "").lower() == self.host
                and p.port == self.port)


class _WireGuardHandler(urllib.request.BaseHandler):
    """The wire-level interceptor. `urllib`'s OpenerDirector calls handlers in
    `handler_order`; this one runs FIRST for http/https. It falls through to the
    real HTTP(S)Handler (returns None) ONLY when the current context carries a
    live per-connection approval that matches THIS request's scheme+host+port and
    is not yet spent — and it CONSUMES the pass on that open. Every other open —
    ungated, nested to a different host, or a 3xx followed across hosts — RAISES
    `EgressDenied`. So the fall-through happens for exactly the one connection the
    egress policy approved and recorded, and nothing else."""
    handler_order = 99              # ahead of every default handler (500s)

    def _guard(self, req):
        target = getattr(req, "full_url", req)
        approval = _gate_pass.get()
        if not isinstance(approval, _Approval):
            raise EgressDenied(
                "wire guard: ungated egress to %r refused — a REAL network "
                "transport must be constructed via wire.real_transport (the "
                "egress gate); direct urlopen is not a path to the network"
                % target)
        if not approval.admits(req):
            raise EgressDenied(
                "wire guard: egress to %r is NOT the gate-approved connection "
                "(%s) — a nested open or a redirect to a different host is "
                "refused; the pass authorizes exactly one approved connection"
                % (target, approval.url))
        approval.spent = True       # single-use: consumed on the matching open
        return None                 # gate-approved: fall through to the real handler

    http_open = _guard
    https_open = _guard


def arm() -> bool:
    """Install the wire guard into `urllib.request`'s GLOBAL opener, so every
    `urlopen` for http/https in this process hits the gate. Idempotent. Called
    on `import decima`, so any code path that can reach an engine's default
    `_urllib_transport` is governed."""
    if not armed():
        urllib.request.install_opener(
            urllib.request.build_opener(_WireGuardHandler()))
    return True


def armed() -> bool:
    """True iff the currently installed global opener carries the wire guard."""
    opener = getattr(urllib.request, "_opener", None)
    return opener is not None and any(
        isinstance(h, _WireGuardHandler) for h in getattr(opener, "handlers", []))


def _host_of(url: str) -> str:
    """The egress target's host, lowercased, port stripped — what the allowlist
    is matched against. No host (bare path, opaque scheme) → '' → fails closed."""
    return nfc((urlsplit(str(url)).hostname or "").lower())


def _redact(url: str) -> str:
    """The url as RECORDED on the Weft: scheme://host[:port]/path ONLY. The query
    string, fragment, and any userinfo are DROPPED — engines put credentials there
    (an API key riding a query parameter, basic-auth in the netloc), and CRED1's
    law is that a raw secret never lands on the Weft. The record is provenance,
    not a replayable request; the gate itself still binds the FULL url
    (`_Approval`), so redaction weakens nothing at the wire."""
    p = urlsplit(str(url))
    host = (p.hostname or "").lower()
    netloc = host + (f":{p.port}" if p.port else "")
    return f"{p.scheme}://{netloc}{p.path}" if netloc else str(url)


def _record(k, author, decision, url, host, cap_id, reason) -> str:
    """Land the decision on the Weft — allow AND deny both leave provenance,
    BEFORE the wire is touched. The record is DATA, never an instruction (and
    the recorded url is `_redact`ed: no secret rides provenance)."""
    from decima.model import assert_content
    recorded = nfc(_redact(url))
    rid = content_id({"wire_decision": decision, "url": recorded,
                      "host": host, "cap": cap_id, "at": k.weft.head})
    assert_content(k.weft, author, rid, WIRE_DECISION, {
        "decision": decision, "url": recorded, "host": host,
        "capability": cap_id, "reason": reason,
        "instruction_eligible": False,
    })
    return rid


def _deny(k, author, url, host, cap_id, reason):
    """Refuse egress: record the denial with provenance, then raise LOUD.
    The connection is never attempted — no DNS, no socket, no packet."""
    rid = _record(k, author, DENY, url, host, cap_id, reason)
    raise EgressDenied(f"egress denied at the wire: {reason} "
                       f"(host={host!r}, decision cell {rid[:12]})")


def _gate(k, agent_cell, cap_id, url) -> str:
    """The rule of egress, run at the wire for EVERY call. Returns the allow-
    decision cell id, or raises EgressDenied (after recording the denial)."""
    w = k.weave()
    author = getattr(agent_cell, "id", None) or k.decima_agent_id
    host = _host_of(url)

    # ── no ambient authority: a live egress CAPABILITY must be presented ──
    cap = w.get(cap_id) if cap_id else None
    if cap is None or cap.type != "capability":
        _deny(k, author, url, host, cap_id,
              "no capability: real egress requires an egress grant "
              "(no ambient authority)")
    if cap.retracted:
        _deny(k, author, url, host, cap_id,
              "capability revoked (Morta RETRACT) — the wire is closed")
    agent = w.get(getattr(agent_cell, "id", "")) if agent_cell is not None else None
    from decima.capability import envelope_holds
    if agent is None or not envelope_holds(w, agent, cap_id):
        _deny(k, author, url, host, cap_id,
              "no grant in envelope (no ambient authority)")

    # ── deny-by-default allowlist, consulted per call at the wire ──
    allowlist = set(cap.content.get("caveats", {}).get("egress_allowlist", []))
    if not allowlist:
        _deny(k, author, url, host, cap_id,
              "deny-by-default: capability carries no egress allowlist")
    if urlsplit(str(url)).scheme != "https":
        _deny(k, author, url, host, cap_id,
              "cleartext egress refused: only https crosses the wire")
    if not host or host not in allowlist:
        _deny(k, author, url, host, cap_id,
              f"host {host!r} not on egress allowlist {sorted(allowlist)}")

    # ── Morta gate: a real outward connection requires a live human approval ──
    if cap_id not in k.approvals:
        _deny(k, author, url, host, cap_id,
              "requires human approval (Morta gate) before a real connection")

    # ── allowed: record the decision (provenance) BEFORE touching the wire ──
    return _record(k, author, ALLOW, url, host, cap_id, "on allowlist, approved")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """In the gated opener a 3xx is SURFACED as a response (an HTTPError the
    caller sees), never silently followed: an allowlisted server therefore cannot
    redirect the live connection — carrying the original request headers/api keys,
    and the body on 307/308 — to an unapproved host behind the gate's back. The
    per-connection guard would refuse a cross-host follow anyway; this is the
    belt to that suspenders, keeping the redirect target from ever being formed."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                 # do not follow — surface the 3xx as a response


_gated_opener_cache = None


def _gated_opener():
    """The opener the gated socket path uses: the wire guard PLUS the default
    handlers, but with redirect-following DISABLED (`_NoRedirectHandler`). Built
    once. Gated opens run through THIS opener (not the global one), so a 3xx is
    surfaced rather than chased across hosts."""
    global _gated_opener_cache
    if _gated_opener_cache is None:
        _gated_opener_cache = urllib.request.build_opener(
            _WireGuardHandler(), _NoRedirectHandler())
    return _gated_opener_cache


def gated_open_follows_redirects() -> bool:
    """True iff the gated opener would FOLLOW a 3xx (it must not). Exposed so the
    oracle can prove — deterministically and offline — that a redirect cannot
    cross the wire to a different host behind the gate."""
    for h in getattr(_gated_opener(), "handlers", []):
        if isinstance(h, urllib.request.HTTPRedirectHandler):
            try:
                r = h.redirect_request(
                    urllib.request.Request("https://approved.example/"),
                    None, 302, "", {}, "https://elsewhere.example/")
            except Exception:
                return True         # a handler that tries to act on a 3xx follows
            return r is not None
    return False


def _wire_open(url, headers, body, method, timeout):
    """The real socket path — a stdlib `urllib` request through the dedicated
    gated opener (guard armed, redirects NOT followed). Reached ONLY from
    `_gated_open` (which sets the per-connection approval); called bare, or for a
    target other than the approved one, the guard raises. Returns (status,
    parsed_json); 3xx/4xx/5xx return their error body (a 3xx is not followed)."""
    data = body if isinstance(body, (bytes, type(None))) else str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}),
                                 method=method)
    import urllib.error
    try:
        with _gated_opener().open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": {"message": f"http {e.code}"}}


def _gated_open(k, agent_cell, cap_id, url, headers, body, *,
                method, timeout, open_fn):
    """Gate, record, THEN open. The per-connection approval is set only around
    the open and bound to THIS url (single-use), so the guard admits exactly the
    ONE connection the policy approved — nothing else. A nested open to a
    different host, or a redirect followed across hosts, does not match this
    approval (or finds it spent) and is refused at the wire."""
    decision = _gate(k, agent_cell, cap_id, url)      # raises EgressDenied on deny
    token = _gate_pass.set(_Approval(url))
    try:
        status, payload = open_fn(url, headers, body, method, timeout)
    finally:
        _gate_pass.reset(token)
    return status, payload, decision


def real_transport(k, agent_cell, cap_id, *, method="POST", timeout=20,
                   _open=None):
    """Forge THE sanctioned real transport: `transport(url, headers, body) ->
    (status, json)` — the exact seam every engine already accepts — where every
    call passes the egress gate (allowlist · Morta · provenance) before the wire
    is touched. A denial raises `EgressDenied`; nothing connects.

    `_open` replaces the SOCKET layer (the test seam, like an engine's injected
    fake transport) — never the gate: policy runs before any open, real or fake.
    The oracle uses a fake `_open`, so it stays offline."""
    arm()                                             # the guard governs this process
    open_fn = _open or _wire_open

    def transport(url, headers, body):
        status, payload, _decision = _gated_open(
            k, agent_cell, cap_id, url, headers, body,
            method=method, timeout=timeout, open_fn=open_fn)
        return status, payload

    transport.wire_gated = True                       # legible marker, not authority
    return transport


# The boundary is on by default: importing decima arms the guard (see
# decima/__init__.py), so an engine's default `_urllib_transport` can never
# reach the network without the gate.
arm()
