"""APISURFACE1 — an inbound API / RPC surface where every endpoint is a
capability invocation (CAPABILITY_MAP B2).

The law this module makes load-bearing: **authority comes from the CALLER'S
capability TOKEN — an ocap handle — never from process or ambient identity, and
never from the request body.** An endpoint is just a routable name mapped to a
capability + the scope it requires; a request is `{path, token, args}` where:

  - `token` is the caller's capability HANDLE (the grant id they were issued).
    It is the ONLY source of authority. The caller is whoever that grant was
    issued TO (its grantee principal) — not the process, not a header, not a
    field in the body. We resolve the caller from the token and invoke AS them,
    so the kernel's full ocap path runs (possession proof, envelope membership,
    downhill delegation, caveats) and every call is audited on the Weft.
  - `args` are UNTRUSTED DATA. They are passed to the effect as the payload and
    NOTHING more. A body that tries to widen authority — name a different
    principal, assert a scope, smuggle an `approve`/`grantee`/`token` override —
    is IGNORED: authority is the token, not the payload (the same recall-vs-
    instruct law the disposition router and webhook gate obey).
  - no token, an unknown/invalid token, a token out of the endpoint's required
    scope, or a token whose ocap check fails → DENY, fail closed. Nothing runs.

Composes the PUBLIC capability / kernel / disposition APIs only — no core edit.
A new endpoint is ONE `register_endpoint` call; no kernel change.
"""
from decima.hashing import content_id, nfc
from decima.weft import ASSERT

ENDPOINT = "endpoint"
API_REQUEST = "api_request"
API_DENIAL = "api_denial"

# Body fields that name authority. A request body is DATA; if it carries any of
# these, the caller is trying to widen authority through the payload. We never
# read authority from the body — these keys are stripped from `args` before the
# effect ever sees them, so a self-elevating body is inert (authority is the
# token, not the payload).
_AUTHORITY_FIELDS = ("token", "principal", "grantee", "granter", "capability",
                     "approve", "approved", "scope", "as_principal", "envelope")


def _registry(k) -> dict:
    """The endpoint table lives on the kernel instance (a routing seam), not on
    the Log — it confers no authority, so it is plain config. Every authority
    decision is still made by the capability layer + audited on the Weft."""
    reg = getattr(k, "_api_endpoints", None)
    if reg is None:
        reg = {}
        k._api_endpoints = reg
    return reg


def register_endpoint(k, path, capability, *, scope=None) -> dict:
    """Map an endpoint `path` to a `capability` (by routable name) plus the
    `scope` (effect class) it requires. Routing only — registering an endpoint
    grants NOTHING; a request still has to present a valid capability token, and
    `authorize()` still gates it. Returns the endpoint descriptor."""
    path = nfc(path)
    ep = {"path": path, "capability": capability, "scope": scope}
    _registry(k)[path] = ep
    # Record the route on the Weft as DATA so the surface is auditable (it is not
    # an authority grant — just a legible record of what path maps to what cap).
    eid = content_id({"endpoint": path, "capability": capability, "scope": scope})
    k.weft.append(k.decima_agent_id, ASSERT, {
        "cell": eid, "type": ENDPOINT,
        "content": {"path": path, "capability": capability, "scope": scope,
                    "instruction_eligible": False},
    })
    return {**ep, "cell": eid}


def _caller_for_token(k, token):
    """Resolve the calling PRINCIPAL and its agent cell FROM THE TOKEN — the
    grant's grantee, never a body field or the process. Returns (agent_cell, cap)
    or (None, reason) if the token is unknown / not a capability / names a
    principal with no holding agent. The id being public is exactly why we then
    invoke through the kernel's ocap path: holding the id is not enough — the
    caller must be the grantee and prove possession on the INVOKE."""
    w = k.weave()
    cap = w.get(token) if token else None
    if cap is None:
        return None, "no/invalid token: unknown capability handle"
    if cap.type != "capability":
        return None, "invalid token: handle is not a capability"
    grantee = cap.content.get("grantee")
    if grantee is None:
        return None, "invalid token: ungranted capability (no grantee principal)"
    # The caller is the agent BOUND to the grantee principal that holds this grant
    # in its envelope. Authority flows to the principal the grant names — we act
    # as exactly that principal, so the kernel's possession proof can bind.
    for agent in w.of_type("agent"):
        if agent.content.get("principal") == grantee and \
                token in agent.content.get("envelope", []):
            return agent, cap
    return None, "invalid token: no agent holds this grant for its grantee"


def _clean_args(args) -> dict:
    """The request body is UNTRUSTED DATA. Strip any field that names authority
    so the payload can NEVER widen what the token allows — authority is the
    token, not the body. The effect sees only the data payload."""
    return {k: v for k, v in (args or {}).items() if k not in _AUTHORITY_FIELDS}


def _deny(k, path, token, reason, attempted_widen=False) -> dict:
    """Record a fail-closed denial as an audited Cell and return it. No effect
    ran; the request never crossed the authority boundary."""
    did = content_id({"api_denial": path, "token": token, "reason": reason,
                      "at": k.weft.head})
    k.weft.append(k.decima_agent_id, ASSERT, {
        "cell": did, "type": API_DENIAL,
        "content": {"path": nfc(path or ""), "reason": reason,
                    "attempted_widen": bool(attempted_widen),
                    "instruction_eligible": False},
    })
    return {"ok": False, "denied": reason, "denial": did,
            "attempted_widen": bool(attempted_widen)}


def handle_request(k, request) -> dict:
    """Serve one inbound API request `{path, token, args}` as a capability
    invocation.

      1. route the path → endpoint (unknown path → deny);
      2. authorize the CALLER via their capability TOKEN — resolve the caller as
         the token's grantee principal, NOT the process; no/invalid token → deny;
      3. check the token matches the endpoint's capability + required scope;
      4. the body `args` are untrusted DATA — strip any authority-naming field so
         the payload cannot widen authority (authority is the token);
      5. invoke the mapped capability AS the caller's principal through the
         kernel's full ocap path (possession proof, envelope, delegation,
         caveats) — audited on the Weft; on any ocap failure → deny.

    Returns `{ok, result, ...}` on success or `{ok: False, denied: reason}` on
    any failure. Fails CLOSED — nothing runs unless every gate passes."""
    request = request or {}
    path = request.get("path")
    token = request.get("token")
    args = request.get("args", {})

    # Capture the request itself as untrusted DATA on the Weft (audit), exactly
    # like an inbound intake — instruction_eligible False, it is never obeyed.
    req_id = content_id({"api_request": path, "token": token, "at": k.weft.head})
    k.weft.append(k.decima_agent_id, ASSERT, {
        "cell": req_id, "type": API_REQUEST,
        "content": {"path": nfc(path or ""), "has_token": token is not None,
                    "instruction_eligible": False},
    })

    # 1. route
    ep = _registry(k).get(nfc(path)) if path else None
    if ep is None:
        return {**_deny(k, path, token, "no such endpoint"), "request": req_id}

    # 2. authority comes from the TOKEN — resolve the caller as its grantee.
    if not token:
        return {**_deny(k, path, token, "no token: unauthorized (no ambient authority)"),
                "request": req_id}
    caller, cap = _caller_for_token(k, token)
    if caller is None:
        return {**_deny(k, path, token, cap), "request": req_id}   # cap holds the reason

    # 3. the token must satisfy THIS endpoint's capability + scope.
    if cap.content.get("name") != ep["capability"]:
        return {**_deny(k, path, token,
                        f"token out of scope: endpoint requires "
                        f"'{ep['capability']}', token grants '{cap.content.get('name')}'"),
                "request": req_id}
    if ep.get("scope") is not None:
        eff = cap.content.get("caveats", {}).get("effect_class",
                                                 cap.content.get("effect"))
        if eff != ep["scope"]:
            return {**_deny(k, path, token,
                            f"token out of scope: endpoint requires scope "
                            f"'{ep['scope']}', token has '{eff}'"),
                    "request": req_id}

    # 4. the body is DATA — strip any authority-naming field. A request that
    #    TRIED to widen authority is served with the override ignored (the call
    #    still runs, but bounded by the token, never the payload).
    widened = bool(set((args or {})) & set(_AUTHORITY_FIELDS))
    payload = _clean_args(args)

    # 5. invoke AS THE CALLER through the kernel's ocap path (audited on the
    #    Weft). authorize()/verify_proof run inside k.invoke — possession proof,
    #    envelope, downhill delegation, caveats — so an out-of-scope or
    #    unauthorized token fails closed HERE, as the caller's principal.
    res = k.invoke(caller, token, payload)
    if "denied" in res:
        return {**_deny(k, path, token,
                        f"ocap denied: {res['denied']}", attempted_widen=widened),
                "request": req_id}

    out = res["ok"].get("out", res["ok"]) if isinstance(res.get("ok"), dict) else res.get("ok")
    return {"ok": True, "result": out, "status": res.get("status"),
            "principal": res.get("signer"),          # acted AS the caller, not the process
            "receipt": res.get("result_cell"),       # EffectReceipt — the audit trail
            "invoke_event": res.get("invoke_event"),
            "endpoint": ep["path"], "capability": ep["capability"],
            "request": req_id, "attempted_widen": widened}
