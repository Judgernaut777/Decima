"""API-SERVE LAUNCHER — the API surface finally gets a transport, still gated.

`api.handle_request` (check 270) is a transport-agnostic capability-invocation
surface: an endpoint is a capability, authority is the CALLER's token, never
process/ambient identity or the body. Nothing drove it over a real wire — the
same gap `mcp_server.serve_stdio` closed for MCP. `decima/serve_api.py` is that
missing production caller for HTTP: `serve_once(k, request)` is the ONE seam
both a real `http.server` handler and this check use to reach the kernel, and
`make_handler`/`main` wrap it in a stdlib `http.server.ThreadingHTTPServer`
launcher exactly like `run.py` boots a warm Shell.

This proves, offline + deterministically (a fresh Kernel over a tmp db, no
socket bound — `serve_once` is plain dict-in/dict-out):

  (a) AUTHORIZED request (load-bearing): a valid token at a registered endpoint
      is served through `serve_once` → `api.handle_request` → the kernel's ocap
      path — the result comes back, signed by the CALLER's own principal, with
      an audited EffectReceipt on the Weft;
  (b) an UNREGISTERED path is DENIED ("no such endpoint") — no capability is
      even looked up, nothing on the Weft records an effect;
  (c) NO token and a BAD (unknown-handle) token are both DENIED — fail closed,
      no ambient authority substitutes for a missing/forged token;
  (d) a token attempting to WIDEN SCOPE — presented at an endpoint mapped to a
      DIFFERENT capability than the one it was granted — is DENIED ("token out
      of scope"); the launcher adds no scope of its own, so this is exactly the
      same denial `api.handle_request` gives natively.

  Every denial in (b)-(d) leaves ONLY its own audited api_request/api_denial
  Cells on the Weft (no EffectReceipt, no INVOKE) — the launcher confers no
  ambient authority and serving weakens NOTHING.

Mutation-resistance (the load-bearing line): change `serve_once` to fabricate
`{"ok": True, "result": "..."}` WITHOUT calling `api.handle_request` (or delete
the token check inside it) — case (a) still looks fine, but (b), (c), and (d)
all go RED: an unregistered path, a missing/forged token, and an out-of-scope
token would all be served as if authorized, because nothing routed them through
the gate. Reverting `serve_once` to its one-line `return
api.handle_request(k, request or {})` restores every denial.

Contract: run(k, line). Fail loud (assert). Owns a FRESH Kernel over a tmp db
and its own hermetic effect (`srv_echo`) — the shared `k` passed in is ignored,
matching checks/492_mcpserve.py's convention for a serving-transport check.
"""
import os
import tempfile

from decima import serve_api
from decima.hashing import content_id
from decima.kernel import Kernel


def run(k, line):
    line("\n== API-SERVE LAUNCHER — serve_once drives handle_request through the gate ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # ── a hermetic backing capability + a real, downhill, granted token ────────
    base_cap = kk.integrate_tool(
        "srv_echo", lambda impl, args: {"out": args.get("text", "")})
    decima = kk.weave().get(kk.decima_agent_id)
    caller_id, token, caller_key = kk.spawn(
        decima, "SrvClient", base_cap, stricter={"budget": 50},
        objective="call the served echo endpoint")

    # register the endpoint under test, plus a SECOND endpoint mapped to a
    # DIFFERENT capability so a scope-widening attempt has somewhere to fail.
    from decima import api
    api.register_endpoint(kk, "/v1/echo", "srv_echo")
    kk.integrate_tool("srv_admin", lambda impl, args: {"out": "admin ran"})
    api.register_endpoint(kk, "/v1/admin", "srv_admin")

    # sanity: the launcher's other two pieces exist and have the right shape,
    # without ever binding a socket.
    import http.server
    handler_cls = serve_api.make_handler(kk)
    assert issubclass(handler_cls, http.server.BaseHTTPRequestHandler), handler_cls
    assert callable(handler_cls.do_GET) and callable(handler_cls.do_POST), \
        "make_handler must produce a handler with do_GET/do_POST"
    assert callable(serve_api.main), "serve_api.main must exist (the boot entrypoint)"
    line("  make_handler(k) returns a BaseHTTPRequestHandler subclass with "
         "do_GET/do_POST; main is present — the transport shape is right ✓")

    # ── (a) AUTHORIZED request → handled through the gate (load-bearing) ──────
    ok = serve_api.serve_once(kk, {"path": "/v1/echo", "token": token,
                                   "args": {"text": "hello over http"}})
    assert ok["ok"] is True, ok
    assert ok["principal"] == caller_key.id, \
        f"the served call must run AS the caller's own principal, not the process: {ok}"
    assert ok["result"] == "hello over http", ok
    receipt = kk.weave().get(ok["receipt"])
    assert receipt is not None and receipt.type == "result" \
        and receipt.content["status"] == "SUCCEEDED", \
        "an authorized served call must leave a real, audited EffectReceipt"
    line(f"  (a) valid token → serve_once routed it through handle_request → "
         f"result={ok['result']!r}, signed by {ok['principal'][:8]} (=SrvClient), "
         f"receipt {ok['receipt'][:8]} ✓")

    # ── (b) UNREGISTERED path → DENIED, no effect ──────────────────────────────
    bad_path = serve_api.serve_once(kk, {"path": "/v1/nope", "token": token,
                                         "args": {"text": "x"}})
    assert bad_path["ok"] is False and "no such endpoint" in bad_path["denied"], bad_path
    assert kk.weave().get(bad_path["denial"]).type == api.API_DENIAL
    line(f"  (b) unregistered /v1/nope → DENIED: {bad_path['denied']!r} ✓")

    # ── (c) NO token and a BAD token → DENIED, fail closed ─────────────────────
    no_tok = serve_api.serve_once(kk, {"path": "/v1/echo", "args": {"text": "no creds"}})
    assert no_tok["ok"] is False and "no token" in no_tok["denied"], no_tok

    bogus = content_id({"not": "a real grant"})
    bad_tok = serve_api.serve_once(kk, {"path": "/v1/echo", "token": bogus,
                                        "args": {"text": "forged"}})
    assert bad_tok["ok"] is False and "invalid token" in bad_tok["denied"], bad_tok
    line(f"  (c) no token → DENIED ({no_tok['denied']!r}); forged token → "
         f"DENIED ({bad_tok['denied']!r}) ✓")

    # ── (d) a token attempting to WIDEN SCOPE (used at another cap's endpoint) ──
    widen = serve_api.serve_once(kk, {"path": "/v1/admin", "token": token,
                                      "args": {"text": "try to run admin"}})
    assert widen["ok"] is False and "out of scope" in widen["denied"], widen
    line(f"  (d) srv_echo token presented at /v1/admin (a DIFFERENT capability) "
         f"→ DENIED: {widen['denied']!r} ✓")

    # ── every denial above ran NOTHING — no EffectReceipt, no ambient authority ─
    # handle_request always records ONE api_request Cell per call (success or
    # denial), plus ONE api_denial Cell per denial — both DATA, never an effect.
    # Four denials happened above: (b), (c)x2, (d).
    denials = kk.weave().of_type(api.API_DENIAL)
    reqs = kk.weave().of_type(api.API_REQUEST)
    assert len(denials) == 4, f"expected exactly 4 denial Cells (b,c,c,d): {denials}"
    assert len(reqs) == 5, f"expected exactly 5 api_request Cells (a,b,c,c,d): {reqs}"
    assert all(c.content["instruction_eligible"] is False for c in denials + reqs)
    results_after = [c for c in kk.weave().of_type("result")
                     if c.content.get("cap") in ("srv_echo", "srv_admin")]
    assert len(results_after) == 1, \
        f"only the ONE authorized call (a) may leave an EffectReceipt: {results_after}"
    line(f"  denials leave no ambient authority: {len(reqs)} api_request + "
         f"{len(denials)} api_denial Cells (all DATA), exactly 1 EffectReceipt "
         f"total (the authorized call) ✓")

    line("  → the API surface is now actually SERVEABLE: serve_once is the single "
         "seam an HTTP handler (make_handler) or a raw dict-driven check both use "
         "to reach api.handle_request — an unregistered path, a missing/forged "
         "token, and a scope-widening token are all refused exactly as they would "
         "be in-process, and the one authorized call runs AS the caller, audited "
         "on the Weft. Serving added no authority.")
