"""APISURFACE1 — the inbound API / RPC surface, proven (heartbeat/decima/api.py).

Proves CAPABILITY_MAP B2 — each endpoint is a capability invocation; authority
comes from the CALLER'S capability TOKEN (an ocap handle), NEVER from process /
ambient identity, and NEVER from the request body:

  - register an endpoint → a capability; a request with a VALID token invokes it
    (result returned, audited on the Weft as the CALLER'S principal);
  - a request with NO token, and one with an INVALID token, are DENIED (closed);
  - the request body/args are UNTRUSTED DATA — a body that tries to widen
    authority (name a principal, smuggle approve/scope) is ignored;
  - authority is the TOKEN, not process identity: a different caller invoking the
    SAME endpoint runs as ITS OWN principal; a token out of the endpoint's scope
    is denied.

Runs on the shared kernel; composes PUBLIC api/capability/kernel APIs. Fail loud.
Contract: run(k, line).
"""
from decima import api
from decima.hashing import content_id
from decima.weft import ASSERT


def run(k, line):
    line("\n== API SURFACE (endpoint = capability invocation · authority = caller's TOKEN) ==")

    # ── a deterministic backing capability for the endpoint ───────────────────
    # Integrate a tiny 'apiecho' tool (a new effect is ONE call, no kernel edit):
    # it echoes its DATA payload deterministically, so the endpoint result is
    # stable. integrate_tool grants it to Decima with a clean grant chain.
    base_cap = k.integrate_tool(
        "apiecho", lambda impl, args: {"out": args.get("text", "")})

    # ── a caller with its OWN key + a real, downhill, granted token ──────────
    # Decima allots an attenuated apiecho grant to a worker (its own principal).
    # The grant id IS the caller's capability handle — its token at the surface.
    decima = k.weave().get(k.decima_agent_id)
    caller_id, token, caller_key = k.spawn(
        decima, "ApiClient", base_cap, stricter={"budget": 50},
        objective="call the echo endpoint")
    line(f"  Decima grants ApiClient a downhill apiecho token {token[:8]} "
         f"(its own principal {caller_key.id[:8]})")

    # ── register an endpoint → a capability ───────────────────────────────────
    ep = api.register_endpoint(k, "/v1/echo", "apiecho")
    assert k.weave().get(ep["cell"]).type == api.ENDPOINT, "endpoint recorded on Weft"
    assert k.weave().get(ep["cell"]).content["instruction_eligible"] is False
    line(f"  registered endpoint {ep['path']} → capability '{ep['capability']}' "
         f"(routing only — confers no authority)")

    # ── (1) VALID token → the endpoint INVOKES the capability, audited ────────
    r = api.handle_request(k, {"path": "/v1/echo", "token": token,
                               "args": {"text": "hello from the API"}})
    assert r["ok"] is True, r
    # acted AS the caller's principal — not Decima, not the process
    assert r["principal"] == caller_key.id, (r["principal"], caller_key.id)
    receipt = k.weave().get(r["receipt"])               # EffectReceipt on the Weft (audit)
    assert receipt.type == "result" and receipt.content["status"] == "SUCCEEDED", receipt.content
    line(f"  VALID token → invoked echo · result={r['result']!r} · "
         f"signed by {r['principal'][:8]} (=ApiClient, NOT the process) · "
         f"receipt {r['receipt'][:8]} ✓")

    # ── (2) NO token → DENIED, fail closed (no ambient authority) ─────────────
    n = api.handle_request(k, {"path": "/v1/echo", "args": {"text": "no creds"}})
    assert n["ok"] is False and "no token" in n["denied"], n
    assert k.weave().get(n["denial"]).type == api.API_DENIAL
    line(f"  NO token → DENIED (fail closed): {n['denied']} ✓")

    # ── invalid token (an id that is not a granted capability) → DENIED ───────
    bogus = content_id({"not": "a real grant"})
    iv = api.handle_request(k, {"path": "/v1/echo", "token": bogus,
                                "args": {"text": "forged"}})
    assert iv["ok"] is False and "invalid token" in iv["denied"], iv
    line(f"  INVALID token (unknown handle) → DENIED: {iv['denied']} ✓")

    # ── (3) the body is UNTRUSTED DATA — it cannot widen authority ────────────
    # A malicious body smuggles authority-naming fields: a different principal to
    # act as, a self-approval, a wider scope, and a substitute token. ALL ignored
    # — the call still runs, bounded by the TOKEN, never the payload.
    evil = api.handle_request(k, {
        "path": "/v1/echo", "token": token,
        "args": {"text": "widen me",
                 "principal": k.root.id,        # try to act as ROOT
                 "as_principal": k.root.id,
                 "approve": True,               # try to self-approve a Morta gate
                 "scope": "shell",              # try to widen the effect class
                 "token": bogus,                # try to swap in another handle
                 "grantee": caller_key.id}})
    assert evil["ok"] is True, evil
    assert evil["attempted_widen"] is True, "the widening attempt is detected"
    assert evil["result"] == "widen me", evil            # only the DATA field survived
    # authority did NOT widen: still acted as ApiClient, never root.
    assert evil["principal"] == caller_key.id, evil
    assert evil["principal"] != k.root.id, "body must not let the caller act as root"
    line(f"  body tries {{principal:root, approve:true, scope:shell, token:bogus}} → "
         f"IGNORED · still ran as {evil['principal'][:8]} (ApiClient), result={evil['result']!r} ✓")

    # ── (4) authority is the TOKEN, not process identity ──────────────────────
    # A SECOND caller, its own principal + its own granted token, hits the SAME
    # endpoint and runs as ITSELF. Same process, different token → different
    # principal: authority rode the token, not the process.
    other_id, token2, other_key = k.spawn(
        decima, "ApiClient2", base_cap, stricter={"budget": 50},
        objective="a second caller on the same endpoint")
    r2 = api.handle_request(k, {"path": "/v1/echo", "token": token2,
                                "args": {"text": "second caller"}})
    assert r2["ok"] is True and r2["principal"] == other_key.id, r2
    assert r2["principal"] != caller_key.id, "same endpoint, different token → different principal"
    line(f"  SAME endpoint, token2 {token2[:8]} → ran as {r2['principal'][:8]} "
         f"(ApiClient2, ≠ ApiClient) — authority is the token, not the process ✓")

    # ── token out of the endpoint's scope → DENIED ───────────────────────────
    # ApiClient2's apiecho token presented at a SHELL endpoint: the handle does
    # NOT grant the required capability → fail closed before any effect runs.
    api.register_endpoint(k, "/v1/shell", "shell")
    oos = api.handle_request(k, {"path": "/v1/shell", "token": token2,
                                 "args": {"cmd": "date"}})
    assert oos["ok"] is False and "out of scope" in oos["denied"], oos
    line(f"  apiecho token at /v1/shell → DENIED (out of scope): {oos['denied']} ✓")

    # ── an unrouted path → DENIED ─────────────────────────────────────────────
    miss = api.handle_request(k, {"path": "/v1/nope", "token": token})
    assert miss["ok"] is False and "no such endpoint" in miss["denied"], miss
    line(f"  unrouted /v1/nope → DENIED: {miss['denied']} ✓")

    # ── audit: every request + denial is on the Weft as DATA ──────────────────
    reqs = k.weave().of_type(api.API_REQUEST)
    denials = k.weave().of_type(api.API_DENIAL)
    assert len(reqs) >= 6 and len(denials) >= 4, (len(reqs), len(denials))
    assert all(c.content["instruction_eligible"] is False for c in reqs + denials)
    line(f"  audit: {len(reqs)} api_request + {len(denials)} api_denial Cells on the Weft "
         f"(all DATA, never obeyed) ✓")
