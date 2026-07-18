"""MCP-SERVE LAUNCHER (Batch U) — the missing process wiring for `serve_stdio`.

Decima had a proven `mcp_server.serve_stdio` loop (check 492 exercises it directly) but
NOTHING actually launched it as a runnable consumer: no module bound a consumer, wired
its agent cell, and pointed the loop at a stream. `decima/serve_mcp.py` is that launcher
— `serve(k, ...)` composes `mcp_server.bind_consumer` + `mcp_server.serve_stdio`, and
`main()` boots a real warm Kernel the way `run.py` does and serves real stdio. This check
proves the LAUNCHER itself (not `serve_stdio` internals, which 492 already covers)
offline + deterministically: a fresh Kernel, injected `io.StringIO` stdin/stdout, no
socket, no subprocess.

  (a) SERVING GOES THROUGH THE GATE, NOT AROUND IT: a `tools/call` to a Morta-gated
      (requires_approval) tool that `serve()` granted is REFUSED over the wire (isError,
      names "approval") and the gated effect NEVER FIRES — no unapproved effect lands on
      the Weft (no Weft events beyond the admission bookkeeping, no INVOKE landing an
      effect, the handler's own firing-list stays empty).
  (b) DEFAULT-DENY: `serve(k, tools=None)` (the default) binds a consumer holding NOTHING
      ambient — a `tools/call` to a tool the admitting agent itself holds, but that was
      never passed to `serve(...)`, is denied at the consumer's own attenuated envelope,
      not routed to the handler.
  (c) MALFORMED INPUT NEVER CRASHES THE LOOP: a non-JSON line yields a JSON-RPC -32700
      error (id null) and the very next line is still served — the loop stays alive.

Mutation-resistance (the load-bearing line): in `serve_mcp.serve`, replace the
`return mcp_server.serve_stdio(...)` call with code that answers a `tools/call` directly
(e.g. always returning `{"eof": True, ...}` without ever calling `serve_stdio`/`handle`,
or short-circuiting to auto-run the gated tool's handler before dispatch) — then (a) goes
RED: the Morta-gated call would run unapproved (the handler's firing-list would gain an
entry with no approval ever recorded), because serving would have stopped routing through
`handle`'s authorize + Morta gate.

Contract: run(k, line). Fail loud (assert). Builds its OWN fresh Kernel over a tmp db —
does not rely on the passed-in `k` (this lane's tools/consumers must not collide with
what other lanes install on the shared smoke kernel).
"""
import io
import json
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import serve_mcp


_GATE = "smu_gate"        # granted-but-Morta-gated (requires_approval) FINANCIAL tool
_UNGRANTED = "smu_ungranted"  # installed + held by the admitting agent, but never granted


def _req(method, rid=None, params=None):
    frame = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        frame["id"] = rid
    if params is not None:
        frame["params"] = params
    return json.dumps(frame) + "\n"


def run(k, line):
    line("\n== MCP-SERVE LAUNCHER (Batch U) — serve_mcp wires serve_stdio to a real "
         "consumer, without weakening the gate ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    fired_gate, fired_ungranted = [], []
    M.install(kk, M.capability_manifest(
        _GATE, description="move money", archetype="EFFECT", effect_class="FINANCIAL",
        caveats={"requires_approval": True},
        input_schema={"type": "object",
                      "properties": {"amount": {"type": "integer"}},
                      "required": ["amount"]}),
        lambda _impl, args: (fired_gate.append(dict(args))
                             or {"out": f"wired {args.get('amount')}"}))
    M.install(kk, M.capability_manifest(
        _UNGRANTED, description="realm-internal tool", archetype="COMPUTE",
        effect_class="READ"),
        lambda _impl, args: (fired_ungranted.append(True) or {"out": "realm secret"}))

    n_inv0 = len(kk.weave().invocations)

    # ── (a) a Morta-gated tool, EXPLICITLY granted to the launched consumer, is
    #        refused over the wire — the gate is not bypassed by the launcher. ──
    out_a = io.StringIO()
    summary_a = serve_mcp.serve(
        kk, consumer_name="launcher-gate", tools=[_GATE],
        stdin=io.StringIO(_req("tools/call", rid=1,
                               params={"name": _GATE, "arguments": {"amount": 900}})),
        stdout=out_a)
    resp_a = json.loads(out_a.getvalue().strip())
    assert summary_a["eof"] is True and summary_a["responses"] == 1, summary_a
    assert resp_a["result"]["isError"] is True and \
        "approval" in resp_a["result"]["content"][0]["text"].lower(), \
        f"a Morta-gated call served through the launcher must be refused, naming " \
        f"approval — never auto-run: {resp_a}"
    assert fired_gate == [], \
        "the gated effect must NEVER fire over the wire before a human approves — " \
        f"serving must not weaken the gate: {fired_gate}"
    assert len(kk.weave().invocations) == n_inv0, \
        ("a refused (Morta-gated, not-yet-approved) served call must land NO INVOKE "
         f"— no effect on the Weft: invocations {n_inv0}->"
         f"{len(kk.weave().invocations)}")
    line("  (a) serve() routes a granted, Morta-gated tools/call THROUGH the gate: "
         "refused over the wire naming approval, the handler never fired, no INVOKE "
         "landed ✓")

    # ── (b) DEFAULT-DENY: serve(k) with no `tools=` grants NOTHING ambient — a tool
    #        the admitting agent itself holds, but was never passed to serve(), is
    #        denied at the consumer's OWN envelope. ──
    n_inv1 = len(kk.weave().invocations)
    out_b = io.StringIO()
    summary_b = serve_mcp.serve(
        kk, consumer_name="launcher-default-deny",           # tools= omitted ⇒ []
        stdin=io.StringIO(
            _req("tools/list", rid=2) +
            _req("tools/call", rid=3,
                 params={"name": _UNGRANTED, "arguments": {}})),
        stdout=out_b)
    lines_b = [json.loads(ln) for ln in out_b.getvalue().splitlines() if ln.strip()]
    by_id_b = {r["id"]: r for r in lines_b}
    assert summary_b["eof"] is True and summary_b["responses"] == 2, summary_b
    assert by_id_b[2]["result"]["tools"] == [], \
        ("serve(k) with tools=None must default to [] — a default-deny consumer "
         f"discovers no invokable tools: {by_id_b[2]}")
    assert by_id_b[3]["result"]["isError"] is True and \
        "envelope" in by_id_b[3]["result"]["content"][0]["text"].lower(), \
        (f"a tool never granted via serve()'s tools= must be denied at the "
         f"consumer's own attenuated envelope, not routed to the handler: {by_id_b[3]}")
    assert fired_ungranted == [], \
        f"an ungranted tool's handler must never fire: {fired_ungranted}"
    assert len(kk.weave().invocations) == n_inv1, \
        "the default-deny consumer's refused call must land no INVOKE"
    line("  (b) serve(k) DEFAULT-DENIES — with no tools= argument the launched "
         "consumer holds nothing ambient: tools/list is empty and a call to a tool "
         "the admitting agent itself holds, but never granted here, is denied at "
         "the envelope ✓")

    # ── (c) malformed input never crashes the loop — the next line still serves. ──
    out_c = io.StringIO()
    summary_c = serve_mcp.serve(
        kk, consumer_name="launcher-malformed", tools=[],
        stdin=io.StringIO(
            "{this is not valid JSON at all\n" +
            _req("tools/list", rid=4)),
        stdout=out_c)
    lines_c = [json.loads(ln) for ln in out_c.getvalue().splitlines() if ln.strip()]
    assert summary_c["eof"] is True and summary_c["malformed"] == 1 and \
        summary_c["responses"] == 2, \
        f"a malformed line must be counted and answered, and the loop must keep " \
        f"going: {summary_c}"
    bad = next(r for r in lines_c if r.get("id") is None)
    assert bad["error"]["code"] == -32700, \
        f"a non-JSON line must yield a JSON-RPC parse error, id null: {bad}"
    ok = next(r for r in lines_c if r.get("id") == 4)
    assert ok["result"]["tools"] == [], ok
    line("  (c) a malformed (non-JSON) line yields a -32700 JSON-RPC error and the "
         "loop stays alive to serve the very next line — never a crash ✓")

    line("  -> serve_mcp.serve/main are pure composition over bind_consumer + "
         "serve_stdio: the launcher grants nothing ambient by default and cannot be "
         "used to answer a tools/call outside the gate.")
