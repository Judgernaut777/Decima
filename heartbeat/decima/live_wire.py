"""LIVE-WIRE — engine-shaped transports on top of the egress gate (Phase 2 · GO LIVE).

Phase 1 (`wire.py`) made the egress gate REAL: a guard armed on `import decima`
refuses any http/https open that was not approved — per connection — by
`wire.real_transport` / `egress.live_transport`. But the ~25 real engines predate
that gate: each carried a module-level `_urllib_transport` default that called
bare `urllib.request.urlopen`, so flipping an engine live with `transport=None`
died deep inside the guard, and there was NO standard way to hand an engine a
properly gated transport.

This module is that standard way — and the ONLY live path:

  * every engine's bare default is GONE (Phase 2 sweep): `transport=None` on a
    live call now raises `NoGatedTransport` — a fail-closed, legible refusal that
    NAMES the sanctioned path — instead of a deep wire-guard traceback;
  * the adapters below build transports in the exact seam shape each engine
    already accepts, on top of `wire.real_transport` (allowlist · Morta · a
    `wire_decision` provenance Cell BEFORE the socket, single-use per-connection
    approval, no cross-host redirects). Every adapter REQUIRES (k, agent_cell,
    cap_id) — a live egress capability GRANT held in the acting agent's envelope
    — to construct; no grant → construction fails closed here, first;
  * the per-call rule of egress is untouched: construction pre-checks only the
    grant's existence, and the gate re-runs the FULL rule (allowlist, Morta
    approval, revocation, provenance) on EVERY call.

ADAPTER SHAPES (survey of the sweep set):

  gated_transport          transport(url, headers, body) -> (status, parsed_json)
                           — the canonical seam (~27 engines: stripe_rail, comms,
                           shipping, banking, crm_engine, exchange, …). `method`
                           picks the verb: POST (default) for the send/write
                           rails; GET for maps_engine / esign.fetch_status
                           (their transports receive body=None).
  gated_get_transport      transport(url, headers) -> (status, parsed_json)
                           — weather_engine's 2-arg GET seam.
  gated_method_transport   transport(url, headers, body, method="POST")
                           — sms.py's per-call verb seam (POST send, GET status).
  gated_put_transport      transport(url, headers, body) -> (status, meta)
                           — storage.py / cloud_storage.py PUT: the S3-shaped
                           success payload is parsed from response HEADERS
                           (etag / version_id / digest / checksum superset),
                           not from a JSON body.
  gated_get_raw_transport  transport(url, headers, body-ignored) -> (status,
                           {body: bytes, etag, checksum}) — storage.py GET:
                           the object BYTES come back raw, not as JSON.

Injected fake transports (every existing check, all test-mode paths) never touch
this module: an engine given `transport=<fake>` never resolves its default. The
`_open` parameter on each adapter is the same test seam `wire.real_transport`
exposes — it replaces the SOCKET, never the gate: the full rule of egress runs
(and the `wire_decision` Cell lands) before any `_open`, real or fake, so the
oracle proves the complete live construction OFFLINE.

Pure stdlib (`urllib`, `json`). Proof: heartbeat/checks/414_live_wire.py.
"""
import json
import urllib.request

from decima import wire

GATED_PATH = "live_wire.gated_transport(k, agent_cell, cap_id)"


class NoGatedTransport(wire.EgressDenied):
    """Raised FAIL-CLOSED — before any socket, DNS lookup, or guard traceback —
    when a live path is reached without a wire-gated transport: an engine's
    `transport=None` default, or an adapter constructed without a live egress
    grant. The message names the one sanctioned path to the network."""

    def __init__(self, engine: str, *, reason: str = "no gated transport constructed",
                 hint: str = GATED_PATH):
        self.engine = engine
        super().__init__(
            f"{engine}: {reason} — the ONLY live path is the egress gate: build a "
            f"wire-gated transport via {hint} (a live egress capability grant, "
            f"Morta-approved; see egress.live_transport / wire.real_transport) and "
            f"inject it as transport=. A bare urlopen default is not a path to the "
            f"network.")


def _require_grant(k, agent_cell, cap_id, *, engine: str = "live_wire"):
    """Construction-time fail-closed: refuse to BUILD an engine transport without
    a live (unretracted) egress capability held in the acting agent's envelope.
    The gate re-runs the FULL rule of egress — allowlist · Morta · revocation ·
    provenance — on every call; this pre-check only front-loads the no-grant
    refusal so a mis-wired engine dies at construction with a clear error."""
    if k is None or agent_cell is None or not cap_id:
        raise NoGatedTransport(engine, reason="a gated transport requires "
                               "(k, agent_cell, cap_id) — a granted egress capability")
    w = k.weave()
    cap = w.get(cap_id)
    if cap is None or getattr(cap, "type", None) != "capability" or cap.retracted:
        raise NoGatedTransport(engine, reason=f"{cap_id!r} is not a live egress "
                               "capability (no ambient authority)")
    from decima.capability import envelope_holds
    agent = w.get(getattr(agent_cell, "id", ""))
    if agent is None or not envelope_holds(w, agent, cap_id):
        raise NoGatedTransport(engine, reason="the egress capability is not held in "
                               "the acting agent's envelope (no ambient authority)")


# ── shape A: the canonical engine seam ─────────────────────────────────────────
def gated_transport(k, agent_cell, cap_id, *, method: str = "POST", timeout: int = 20,
                    _open=None):
    """THE engine transport: `transport(url, headers, body) -> (status, json)`,
    where every call passes the egress gate (allowlist · Morta · `wire_decision`
    provenance BEFORE the socket) — a denial raises `wire.EgressDenied`, nothing
    connects. Covers every JSON-speaking engine; `method="GET"` serves the read
    seams that pass body=None (maps_engine, esign.fetch_status). `_open` replaces
    the SOCKET (the offline test seam), never the gate."""
    _require_grant(k, agent_cell, cap_id)
    return wire.real_transport(k, agent_cell, cap_id, method=method,
                               timeout=timeout, _open=_open)


# ── shape B: weather_engine's 2-arg GET seam ────────────────────────────────────
def gated_get_transport(k, agent_cell, cap_id, *, timeout: int = 20, _open=None):
    """`transport(url, headers) -> (status, json)` — a GET with no body argument
    (weather_engine). Same gate, same provenance, per call."""
    base = gated_transport(k, agent_cell, cap_id, method="GET",
                           timeout=timeout, _open=_open)

    def transport(url, headers):
        return base(url, headers, None)

    transport.wire_gated = True
    return transport


# ── shape C: sms's per-call-verb seam ───────────────────────────────────────────
def gated_method_transport(k, agent_cell, cap_id, *, timeout: int = 20, _open=None):
    """`transport(url, headers, body, method="POST") -> (status, json)` — the verb
    arrives per call (sms.py: POST send, GET status read). One gated transport per
    verb, built lazily; EVERY call still runs the full rule of egress (the gate
    pass is single-use per connection regardless of verb)."""
    _require_grant(k, agent_cell, cap_id)
    by_verb = {}

    def transport(url, headers, body, method="POST"):
        verb = str(method or "POST").upper()
        base = by_verb.get(verb)
        if base is None:
            base = wire.real_transport(k, agent_cell, cap_id, method=verb,
                                       timeout=timeout, _open=_open)
            by_verb[verb] = base
        return base(url, headers, body if verb not in ("GET", "HEAD") else None)

    transport.wire_gated = True
    return transport


# ── shape D: the S3-shaped raw seams (storage.py / cloud_storage.py) ───────────
def _header_meta(headers) -> dict:
    """The S3-shaped success metadata, parsed from response HEADERS. A superset of
    both consumers' keys: storage.py reads `checksum`, cloud_storage.py reads
    `digest` — extra keys are inert data."""
    meta = {str(k).lower(): v for k, v in headers.items()}
    digest = meta.get("x-amz-content-digest")
    return {
        "etag": (meta.get("etag") or "").strip('"') or None,
        "version_id": meta.get("x-amz-version-id"),
        "digest": digest,
        "checksum": meta.get("x-amz-content-checksum") or digest,
    }


def _put_open(url, headers, body, method, timeout):
    """The raw PUT socket layer — same idiom as `wire._wire_open` (the dedicated
    gated opener: guard armed, redirects NOT followed; reached ONLY inside a gated
    approval window) but the success payload is header-derived, not a JSON body."""
    import urllib.error
    data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}),
                                 method=method)
    try:
        with wire._gated_opener().open(req, timeout=timeout) as r:
            return r.status, _header_meta(r.headers)
    except urllib.error.HTTPError as e:                        # 4xx/5xx carry an error body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def _raw_get_open(url, headers, _body, method, timeout):
    """The raw GET socket layer (same gated-opener idiom): the object BYTES come
    back raw in `body`, with the integrity headers alongside — never json-parsed."""
    import urllib.error
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    try:
        with wire._gated_opener().open(req, timeout=timeout) as r:
            meta = _header_meta(r.headers)
            return r.status, {"body": r.read(), "etag": meta["etag"],
                              "checksum": meta["checksum"]}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def gated_put_transport(k, agent_cell, cap_id, *, timeout: int = 20, _open=None):
    """`transport(url, headers, body) -> (status, header-meta)` — the S3-shaped
    PUT seam (storage.put_object, cloud_storage.put_object): success metadata is
    parsed from response headers. Gate-per-call, provenance-before-socket."""
    return gated_transport(k, agent_cell, cap_id, method="PUT", timeout=timeout,
                           _open=_open or _put_open)


def gated_get_raw_transport(k, agent_cell, cap_id, *, timeout: int = 20, _open=None):
    """`transport(url, headers, body-ignored) -> (status, {body: bytes, etag,
    checksum})` — storage.get_object's raw READ seam. Gate-per-call."""
    return gated_transport(k, agent_cell, cap_id, method="GET", timeout=timeout,
                           _open=_open or _raw_get_open)
